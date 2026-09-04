from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, getcontext
from math import log
from typing import Dict, List, Optional, Tuple
from datetime import date, datetime, timedelta
from calendar import monthrange


# ============================================================
# ФИНАНСОВЫЙ AI-АЛЛОКАТОР
# Версия ядра: 1.0
#
# ВАЖНО:
# - Telegram здесь отсутствует намеренно.
# - Ядро не знает о сообщениях, кнопках и пользователях Telegram.
# - Все денежные вычисления выполняются через Decimal.
# - Округление до 2 знаков происходит только при выводе.
# ============================================================


getcontext().prec = 40

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")

VACATION_BUDGET_ITEMS = (
    ("tickets", "Билеты"),
    ("accommodation", "Проживание"),
    ("food", "Еда, кафе и рестораны"),
    ("transport", "Местный транспорт"),
    ("entertainment", "Экскурсии и развлечения"),
    ("insurance", "Страховка и виза"),
    ("communication", "Связь и интернет"),
    ("purchases", "Покупки, сувениры и подарки"),
)

CHEST_DISPLAY_NAMES = {
    "подарки": "Сундук Подарков",
    "замена техники": "Сундук Техники",
    "техника": "Сундук Техники",
    "хотелки": "Сундук Хотелок",
}


def goal_display_name(name: str, is_chest: bool = False) -> str:
    """Возвращает единообразное пользовательское название Цели или Сундука."""
    clean_name = str(name).strip()
    if not is_chest:
        return clean_name
    if clean_name.casefold().startswith("сундук "):
        return clean_name
    return CHEST_DISPLAY_NAMES.get(clean_name.casefold(), f"Сундук {clean_name}")


def D(value) -> Decimal:
    """
    Безопасное преобразование значения в Decimal.
    """
    if isinstance(value, Decimal):
        return value

    if value is None:
        return ZERO

    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    """
    Финальное округление для пользовательского вывода.
    """
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal) -> str:
    """
    Форматирование рублей для Telegram.
    """
    value = money(value)
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def pct(value: Decimal) -> Decimal:
    return D(value) / HUNDRED


def vacation_budget(
    amounts: Dict[str, Decimal],
    buffer_percent: Decimal = Decimal("10"),
) -> Dict[str, Decimal]:
    """Считает отпускной бюджет и отдельный запас, не смешивая статьи."""
    known_keys = {key for key, _ in VACATION_BUDGET_ITEMS}
    normalized = {
        key: max(ZERO, D(value))
        for key, value in amounts.items()
        if key in known_keys
    }
    subtotal = sum(normalized.values(), ZERO)
    buffer_percent = max(ZERO, D(buffer_percent))
    reserve = money(subtotal * buffer_percent / HUNDRED)
    return {
        **normalized,
        "subtotal": money(subtotal),
        "buffer_percent": buffer_percent,
        "buffer": reserve,
        "total": money(subtotal + reserve),
    }


def goal_percentage_bounds(
    assigned_percentages: List[Decimal],
    remaining_positions_after: int,
) -> Tuple[Decimal, Decimal]:
    """Допустимый целый процент для очередной позиции.

    За каждой ещё не настроенной позицией заранее сохраняется минимум 1%.
    Поэтому для пяти позиций максимум первой равен 96%, а после выбранных
    30% и 30% максимум третьей равен 38%.
    """
    assigned = sum((D(value) for value in assigned_percentages), ZERO)
    maximum = HUNDRED - assigned - Decimal(max(0, remaining_positions_after))
    return ONE, max(ZERO, maximum)


def sequential_goal_percentages(
    chosen_percentages: List[Decimal],
    positions_count: int,
) -> List[Decimal]:
    """Собирает последовательную настройку; последняя доля — остаток до 100%."""
    if positions_count < 1:
        return []
    if len(chosen_percentages) != positions_count - 1:
        raise ValueError("Процент последней позиции рассчитывается автоматически.")

    result: List[Decimal] = []
    for index, raw_value in enumerate(chosen_percentages):
        value = D(raw_value)
        minimum, maximum = goal_percentage_bounds(
            result,
            positions_count - index - 1,
        )
        if value != value.to_integral_value() or not minimum <= value <= maximum:
            raise ValueError(
                f"Процент позиции должен быть целым числом от {minimum} до {maximum}."
            )
        result.append(value)

    remainder = HUNDRED - sum(result, ZERO)
    if remainder < ONE or remainder != remainder.to_integral_value():
        raise ValueError("Последней позиции должно остаться не меньше 1%.")
    return [*result, remainder]


def normalize_active_goal_percentages(goals: List[Goal]) -> List[Goal]:
    """Распределяет 100 целых процентов между активными позициями.

    Используется после удаления или паузы. Существующие пропорции сохраняются
    настолько точно, насколько позволяют целые проценты; последняя позиция
    поглощает только неизбежный остаток округления.
    """
    active = [goal for goal in goals if goal.status == "active"]
    for goal in goals:
        goal.is_auto_percentage = False
    if not active:
        return goals
    if len(active) == 1:
        active[0].percentage = HUNDRED
        active[0].is_auto_percentage = True
        return goals

    weights = [max(ZERO, D(goal.percentage)) for goal in active]
    weight_total = sum(weights, ZERO)
    if weight_total <= ZERO:
        weights = [ONE] * len(active)
        weight_total = Decimal(len(active))

    distributable = HUNDRED - Decimal(len(active))
    exact_extras = [distributable * weight / weight_total for weight in weights]
    integer_extras = [int(value) for value in exact_extras]
    left = int(distributable - sum(integer_extras))
    remainder_order = sorted(
        range(len(active)),
        key=lambda index: (exact_extras[index] - integer_extras[index], -index),
        reverse=True,
    )
    for index in remainder_order[:left]:
        integer_extras[index] += 1
    for goal, extra in zip(active, integer_extras):
        goal.percentage = ONE + Decimal(extra)
    active[-1].is_auto_percentage = True
    return goals


def update_goal_percentage(
    goals: List[Goal],
    goal_index: int,
    new_percentage: Decimal,
) -> List[Goal]:
    """Меняет ручную долю, автоматически пересчитывая последнюю позицию."""
    active = [goal for goal in goals if goal.status == "active"]
    if not 0 <= goal_index < len(active):
        raise IndexError("Цель не найдена.")
    if len(active) == 1:
        active[0].percentage = HUNDRED
        active[0].is_auto_percentage = True
        return goals

    residual = next(
        (goal for goal in reversed(active) if goal.is_auto_percentage),
        active[-1],
    )
    edited = active[goal_index]
    if edited is residual:
        raise ValueError("Доля последней позиции рассчитывается автоматически.")
    value = D(new_percentage)
    if value != value.to_integral_value() or value < ONE:
        raise ValueError("Процент должен быть целым числом не меньше 1.")

    fixed_total = sum(
        (
            goal.percentage
            for goal in active
            if goal is not edited and goal is not residual
        ),
        ZERO,
    )
    remainder = HUNDRED - fixed_total - value
    if remainder < ONE:
        maximum = HUNDRED - fixed_total - ONE
        raise ValueError(f"Для этой позиции доступно не больше {maximum}%.")
    edited.percentage = value
    edited.is_auto_percentage = False
    residual.percentage = remainder
    residual.is_auto_percentage = True
    return goals


# ============================================================
# КОНСТАНТЫ РЕЖИМОВ
# ============================================================

MODE_1 = 1
MODE_2 = 2
MODE_3 = 3
MODE_4 = 4
MODE_5 = 5
MODE_6 = 6

PROFILE_STABLE = "stable"
PROFILE_PIECEWORK = "piecework"
PROFILE_CYCLIC = "cyclic"

PROFILE_MODE_TITLES = {
    PROFILE_STABLE: {
        1: "Небо помогает тому, кто помогает себе.",
        2: "Ланистеры всегда платят свои долги.",
        3: "Подготовка к Апокалипсису.",
        4: "Философский камень найден.",
    },
    PROFILE_PIECEWORK: {
        1: "Небо помогает тому, кто помогает себе.",
        2: "Ланистеры всегда платят свои долги.",
        3: "Подготовка к Апокалипсису.",
        4: "Заказов нет. Паники тоже.",
        5: "Защита есть. Пора расти.",
        6: "Философский камень найден.",
    },
    PROFILE_CYCLIC: {
        1: "Небо помогает тому, кто помогает себе.",
        2: "Ланистеры всегда платят свои долги.",
        3: "Заплати будущему себе.",
        4: "Не на хлебе и воде.",
        5: "Подготовка к Апокалипсису.",
        6: "Контракт задержался. Паники нет.",
        7: "Защита есть. Пора расти.",
        8: "Философский камень найден.",
    },
}


def normalize_profile_id(profile_type: str | None, employment_type: str, income_rhythm: str) -> str:
    """Возвращает новый ID профиля и понимает старые сохранённые значения."""
    if income_rhythm == "cyclic":
        return PROFILE_CYCLIC
    if income_rhythm == "irregular":
        return PROFILE_PIECEWORK
    if profile_type in {PROFILE_STABLE, PROFILE_PIECEWORK}:
        return profile_type
    if employment_type == "Фрилансер":
        return PROFILE_PIECEWORK
    return PROFILE_STABLE


MODE_NAMES = {
    MODE_1: "Режим 1",
    MODE_2: "Режим 2",
    MODE_3: "Режим 3",
    MODE_4: "Режим 4",
    MODE_5: "Режим 5",
    MODE_6: "Максимальный режим",
}


MODE_TITLES = {
    MODE_1: "Небо помогает тому, кто помогает себе.",
    MODE_2: "Ланистеры всегда платят свои долги.",
    MODE_3: "Подготовка к Апокалипсису.",
    MODE_4: "Заказов нет. Паники тоже.",
    MODE_5: "Защита есть. Пора расти.",
    MODE_6: "Философский камень найден.",
}


# ============================================================
# КРЕДИТ
# ============================================================

@dataclass
class Credit:
    name: str

    principal_balance: Decimal
    full_repayment_amount: Optional[Decimal]

    annual_rate: Decimal
    minimum_payment: Decimal

    payment_type: str = "Аннуитетный"
    early_repayment_action: str = "Уменьшать срок"

    status: str = "Активный"

    def __post_init__(self):
        self.principal_balance = D(self.principal_balance)

        if self.full_repayment_amount is not None:
            self.full_repayment_amount = D(self.full_repayment_amount)

        self.annual_rate = D(self.annual_rate)
        self.minimum_payment = D(self.minimum_payment)

    @property
    def active(self) -> bool:
        return self.status == "Активный" and self.principal_balance > ZERO

    def process_minimum_payment(self) -> dict:
        """
        Обработка фактически внесённого минимального платежа.

        Формулы спецификации:

        Проценты = Остаток × (Ставка / 12)
        Погашение тела = Минимальный платёж - Проценты
        Новый остаток = Старый остаток - Погашение тела
        """

        if not self.active:
            raise ValueError(
                f"Кредит '{self.name}' уже погашен."
            )

        old_balance = self.principal_balance

        monthly_rate = self.annual_rate / HUNDRED / Decimal("12")

        interest = old_balance * monthly_rate

        body_payment = self.minimum_payment - interest

        if body_payment < ZERO:
            body_payment = ZERO

        new_balance = old_balance - body_payment

        if new_balance <= ZERO:
            new_balance = ZERO
            self.status = "Погашен"

        self.principal_balance = new_balance

        return {
            "credit": self.name,
            "old_balance": old_balance,
            "interest": interest,
            "body_payment": body_payment,
            "new_balance": new_balance,
            "status": self.status,
        }


# ============================================================
# ЦЕЛИ И СУНДУКИ
# ============================================================

@dataclass
class Goal:
    """Одна позиция в разделе накоплений на желания.

    ``goal`` — конечная ⭐️ Цель с известной суммой и, возможно, сроком.
    ``chest`` — постоянный 🧳 Сундук без конечной суммы и автозавершения.

    Поле ``balance`` сохранено для обратной совместимости. Для Сундука это
    только сумма взносов, зафиксированных Аллокатором, а не обещание точно
    повторять баланс банковского счёта с ежедневно начисляемыми процентами.
    """

    name: str
    percentage: Decimal
    balance: Decimal = ZERO
    position_type: str = "goal"
    order_index: int = 0
    is_auto_percentage: bool = False
    currency_code: str = "RUB"
    target_amount: Optional[Decimal] = None
    deadline: Optional[str] = None
    buffer_enabled: bool = False
    buffer_percent: Decimal = ZERO
    status: str = "active"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    archived_at: Optional[str] = None
    previous_percentage: Optional[Decimal] = None

    def __post_init__(self):
        self.name = str(self.name).strip()
        self.percentage = D(self.percentage)
        self.balance = max(ZERO, D(self.balance))
        self.position_type = str(self.position_type).strip().lower()
        if self.position_type not in {"goal", "chest"}:
            self.position_type = "goal"
        self.order_index = max(0, int(self.order_index or 0))
        self.is_auto_percentage = bool(self.is_auto_percentage)
        self.currency_code = str(self.currency_code or "RUB").strip().upper()
        self.target_amount = (
            None if self.target_amount is None
            else max(ZERO, D(self.target_amount))
        )
        self.deadline = str(self.deadline).strip() if self.deadline else None
        self.buffer_enabled = bool(self.buffer_enabled)
        self.buffer_percent = max(ZERO, D(self.buffer_percent))
        self.status = str(self.status or "active").strip().lower()
        if self.status not in {"active", "paused", "completed", "archived"}:
            self.status = "active"
        self.previous_percentage = (
            None if self.previous_percentage is None
            else D(self.previous_percentage)
        )

        if self.position_type == "chest":
            # У постоянного Сундука нет финишной суммы, срока и запаса.
            self.target_amount = None
            self.deadline = None
            self.buffer_enabled = False
            self.buffer_percent = ZERO

    @property
    def is_goal(self) -> bool:
        return self.position_type == "goal"

    @property
    def is_chest(self) -> bool:
        return self.position_type == "chest"

    @property
    def full_target_amount(self) -> Optional[Decimal]:
        if self.target_amount is None:
            return None
        if not self.buffer_enabled:
            return self.target_amount
        return self.target_amount * (ONE + pct(self.buffer_percent))


@dataclass
class PhaseLifeBudget:
    """Стоимость жизни и валюта одной фазы циклического профиля."""

    critical_life: Decimal = ZERO
    household_reserve: Decimal = ZERO
    life_categories: Dict[str, Decimal] = field(default_factory=dict)
    household_reserve_categories: Dict[str, Decimal] = field(default_factory=dict)
    historical_gifts_monthly: Decimal = ZERO
    currency_code: str = "RUB"
    currency_symbol: str = "₽"
    exchange_rate_to_rub: Decimal = ONE
    exchange_rate_mode: str = "official"
    exchange_rate_updated_at: Optional[str] = None
    completed: bool = False

    def __post_init__(self):
        self.critical_life = max(ZERO, D(self.critical_life))
        self.household_reserve = max(ZERO, D(self.household_reserve))
        self.life_categories = {
            str(name): max(ZERO, D(amount))
            for name, amount in self.life_categories.items()
        }
        self.household_reserve_categories = {
            str(name): max(ZERO, D(amount))
            for name, amount in self.household_reserve_categories.items()
        }
        self.historical_gifts_monthly = max(ZERO, D(self.historical_gifts_monthly))
        self.currency_code = str(self.currency_code or "RUB").strip().upper()
        self.currency_symbol = str(self.currency_symbol or self.currency_code).strip()
        self.exchange_rate_to_rub = max(ZERO, D(self.exchange_rate_to_rub))
        if self.currency_code == "RUB":
            self.currency_symbol = "₽"
            self.exchange_rate_to_rub = ONE
            self.exchange_rate_mode = "official"
        if self.exchange_rate_mode not in {"official", "manual"}:
            self.exchange_rate_mode = "official"
        self.completed = bool(self.completed)

    @property
    def critical_life_rub(self) -> Decimal:
        return money(self.critical_life * self.exchange_rate_to_rub)

    @property
    def household_reserve_rub(self) -> Decimal:
        return money(self.household_reserve * self.exchange_rate_to_rub)

    @property
    def household_life(self) -> Decimal:
        return self.critical_life + self.household_reserve

    def rub(self, amount: Decimal) -> Decimal:
        return money(D(amount) * self.exchange_rate_to_rub)


# ============================================================
# НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ
# ============================================================

@dataclass
class UserSettings:
    # ----------------------------
    # Профиль
    # ----------------------------

    has_debts: bool

    employment_type: str
    # "Фрилансер" или "Наёмный"

    # ----------------------------
    # Основные суммы
    # ----------------------------

    critical_life: Decimal
    household_reserve: Decimal
    average_income: Decimal

    # Канонический технический ID. employment_type оставлен для чтения
    # профилей, созданных до появления трёх финансовых маршрутов.
    profile_type: str = ""

    # Ритм поступлений — отдельная характеристика от формы занятости.
    # monthly: деньги приходят каждый месяц;
    # irregular: сумма меняется, но длинного известного перерыва нет;
    # cyclic: между рабочими циклами бывают месяцы без надёжного дохода.
    income_rhythm: str = "monthly"
    income_gap_months: Decimal = Decimal("1")
    income_work_months: Decimal = Decimal("1")
    reliable_gap_income: Decimal = Decimal("0")
    stabilizer_target_months: Decimal = Decimal("1")
    contract_obligations: Dict[str, Decimal] = field(default_factory=dict)
    # Физический конверт каждой части обязательств, заранее подготовленной
    # на рабочие месяцы. Сам расход остаётся единственным: здесь хранится
    # только место денег, а не его вторая копия.
    contract_obligation_storage: Dict[str, str] = field(default_factory=dict)
    use_contract_obligations_fund: bool = False
    # Две независимые стоимости жизни циклического профиля. Старые поля
    # critical_life/household_reserve остаются канонической базой алгоритма
    # до включения фазового интерфейса и обеспечивают обратную совместимость.
    phase_life_budgets: Dict[str, PhaseLifeBudget] = field(default_factory=dict)

    # ----------------------------
    # Налог
    # ----------------------------

    tax_rate: Decimal = Decimal("0")
    taxable_income_types: List[str] = field(default_factory=list)
    income_type_tax_rates: Dict[str, Decimal] = field(default_factory=dict)
    # Плановые налоги, которые входят в Критический минимум.
    # Ключ — понятное пользователю обязательство/объект,
    # значение — его среднемесячная сумма.
    planned_taxes: Dict[str, Decimal] = field(default_factory=dict)
    track_tax_payments: bool = False

    # ----------------------------
    # Подушка
    # ----------------------------

    minimum_reserve_months: Decimal = Decimal("0")
    force_majeure_months: Decimal = Decimal("0")

    # ----------------------------
    # Бракеты
    # ----------------------------

    bracket_a: Decimal = Decimal("20")
    bracket_b: Decimal = Decimal("25")
    bracket_c: Decimal = Decimal("30")
    bracket_d: Decimal = Decimal("35")
    bracket_e: Decimal = Decimal("40")

    goals_share_c: Decimal = Decimal("50")
    pillow_share_c: Decimal = Decimal("50")
    protective_stage_c_goals_share: Decimal = Decimal("35")
    # На защитных ступенях Этап C можно либо делить между защитой
    # и целями (65/35), либо направлять 100% в текущий резерв.
    protective_stage_c_strategy: str = "balanced"

    # ----------------------------
    # Категории КЖ
    #
    # Здесь НЕ хранится "Зарплата".
    # Она рассчитывается автоматически.
    # ----------------------------

    life_categories: Dict[str, Decimal] = field(default_factory=dict)
    household_reserve_categories: Dict[str, Decimal] = field(default_factory=dict)
    # Подарки вводятся в меню Жизни только как история будущей Цели.
    # Они не входят в БР и УЖ.
    historical_gifts_monthly: Decimal = ZERO
    gift_guideline_min: Decimal = Decimal("3")
    gift_guideline_max: Decimal = Decimal("7")
    gift_warning_limit: Decimal = Decimal("10")

    # ----------------------------
    # Цели
    # ----------------------------

    goals: List[Goal] = field(default_factory=list)

    # ----------------------------
    # Кредиты
    # ----------------------------

    credits: List[Credit] = field(default_factory=list)

    debt_strategy: str = "Лавина"

    # ----------------------------
    # Дополнительно
    # ----------------------------

    calculate_interest_savings: bool = False
    developer_mode: bool = False

    def __post_init__(self):
        self.profile_type = normalize_profile_id(
            self.profile_type,
            self.employment_type,
            self.income_rhythm,
        )
        self.critical_life = D(self.critical_life)
        self.household_reserve = D(self.household_reserve)
        self.average_income = D(self.average_income)
        self.income_gap_months = max(ONE, D(self.income_gap_months))
        self.income_work_months = max(ONE, D(self.income_work_months))
        self.reliable_gap_income = max(ZERO, D(self.reliable_gap_income))
        self.stabilizer_target_months = max(ONE, D(self.stabilizer_target_months))
        self.contract_obligations = {
            str(name): max(ZERO, D(amount))
            for name, amount in self.contract_obligations.items()
        }
        self.contract_obligation_storage = {
            str(name): str(envelope).strip()
            for name, envelope in self.contract_obligation_storage.items()
            if str(name) in self.contract_obligations and str(envelope).strip()
        }
        normalized_phase_budgets: Dict[str, PhaseLifeBudget] = {}
        for phase, budget in self.phase_life_budgets.items():
            phase_id = str(phase).strip().lower()
            if phase_id not in {"work", "break"}:
                continue
            normalized_phase_budgets[phase_id] = (
                budget if isinstance(budget, PhaseLifeBudget)
                else PhaseLifeBudget(**budget)
            )
        if self.income_rhythm == "cyclic" and "break" not in normalized_phase_budgets:
            # Старые циклические профили считали российскую/домашнюю жизнь.
            normalized_phase_budgets["break"] = PhaseLifeBudget(
                critical_life=self.critical_life,
                household_reserve=self.household_reserve,
                life_categories=self.life_categories,
                household_reserve_categories=self.household_reserve_categories,
                completed=True,
            )
        self.phase_life_budgets = normalized_phase_budgets

        self.tax_rate = D(self.tax_rate)
        self.income_type_tax_rates = {
            str(name).strip(): D(rate)
            for name, rate in self.income_type_tax_rates.items()
            if str(name).strip()
        }
        if not self.income_type_tax_rates and self.taxable_income_types:
            self.income_type_tax_rates = {
                name: self.tax_rate
                for name in self.taxable_income_types
            }
        self.taxable_income_types = [
            name
            for name, rate in self.income_type_tax_rates.items()
            if rate > ZERO
        ]
        self.planned_taxes = {
            name: D(amount)
            for name, amount in self.planned_taxes.items()
        }

        self.minimum_reserve_months = D(
            self.minimum_reserve_months
        )

        self.force_majeure_months = D(
            self.force_majeure_months
        )

        self.bracket_a = D(self.bracket_a)
        self.bracket_b = D(self.bracket_b)
        self.bracket_c = D(self.bracket_c)
        self.bracket_d = D(self.bracket_d)
        self.bracket_e = D(self.bracket_e)

        self.goals_share_c = D(self.goals_share_c)
        self.pillow_share_c = D(self.pillow_share_c)
        self.protective_stage_c_goals_share = D(self.protective_stage_c_goals_share)
        if self.protective_stage_c_strategy not in {"balanced", "protection"}:
            self.protective_stage_c_strategy = "balanced"

        self.life_categories = {
            name: D(amount)
            for name, amount in self.life_categories.items()
        }
        self.household_reserve_categories = {
            name: D(amount)
            for name, amount in self.household_reserve_categories.items()
        }
        self.historical_gifts_monthly = max(ZERO, D(self.historical_gifts_monthly))
        self.gift_guideline_min = D(self.gift_guideline_min)
        self.gift_guideline_max = D(self.gift_guideline_max)
        self.gift_warning_limit = D(self.gift_warning_limit)

    # ========================================================
    # ПРОИЗВОДНЫЕ ПЕРЕМЕННЫЕ
    # ========================================================

    @property
    def household_life(self) -> Decimal:
        """
        УЖ = КЖ + БР
        """
        return self.critical_life + self.household_reserve

    def phase_life(self, phase: str) -> Optional[PhaseLifeBudget]:
        return self.phase_life_budgets.get(str(phase).strip().lower())

    @property
    def minimum_payment_total(self) -> Decimal:
        """
        Сумма минимальных платежей активных кредитов.
        """
        return sum(
            (
                credit.minimum_payment
                for credit in self.credits
                if credit.active
            ),
            ZERO,
        )

    @property
    def total_critical_life(self) -> Decimal:
        """
        КЖ_общая = КЖ + Мин_платеж.
        """
        return (
            self.critical_life
            + self.minimum_payment_total
        )

    @property
    def minimum_reserve_limit(self) -> Decimal:
        return (
            self.minimum_reserve_months
            * self.critical_life
        )

    @property
    def force_majeure_limit(self) -> Decimal:
        return (
            self.force_majeure_months
            * self.critical_life
        )

    @property
    def stabilizer_life_limit(self) -> Decimal:
        """
        СтабД-КЖ.
        """
        return self.critical_life * self.stabilizer_months

    @property
    def stabilizer_full_limit(self) -> Decimal:
        """
        СтабД-Полный = УЖ.
        """
        return self.household_life * self.stabilizer_months

    @property
    def needs_stabilizer(self) -> bool:
        return normalize_profile_id(
            self.profile_type,
            self.employment_type,
            self.income_rhythm,
        ) in {PROFILE_PIECEWORK, PROFILE_CYCLIC}

    @property
    def stabilizer_months(self) -> Decimal:
        return self.stabilizer_target_months if self.needs_stabilizer else ONE

    @property
    def needs_intercontract_reserve(self) -> bool:
        return self.income_rhythm == "cyclic" and self.intercontract_full_limit > ZERO

    @property
    def intercontract_life_limit(self) -> Decimal:
        """Критическая часть Фонда Зарплаты на весь плановый перерыв."""
        if self.income_rhythm != "cyclic":
            return ZERO
        return self.critical_life * self.income_gap_months

    @property
    def intercontract_full_limit(self) -> Decimal:
        """Полный Фонд Зарплаты: Устойчивая жизнь на каждый месяц перерыва."""
        if self.income_rhythm != "cyclic":
            return ZERO
        return self.household_life * self.income_gap_months

    @property
    def contract_obligations_total(self) -> Decimal:
        return sum(self.contract_obligations.values(), ZERO)

    def contract_obligation_envelope(self, name: str) -> str:
        name = str(name)
        explicit = self.contract_obligation_storage.get(name)
        if explicit:
            return explicit

        # Совместимость с профилями, созданными до сохранения явной карты.
        # Используем только однозначные названия; сомнительные расходы не
        # притворяются принадлежащими конверту, которого пользователь не выбирал.
        lowered = name.casefold()
        if "налог" in lowered:
            return "Налоги"
        if any(word in lowered for word in ("жкх", "аренд", "ипотек")):
            return "Недвижимость"
        if any(word in lowered for word in ("аптек", "стомат", "медицин", "оптик")):
            return "Здоровье"
        if any(word in lowered for word in ("питом", "ветеринар")):
            return "Питомцы"
        if any(word in lowered for word in ("детский сад", "школа", "алимент")):
            return "Дети"
        return "Фонд Обязательств" if self.use_contract_obligations_fund else "Бытовой резерв"

    @property
    def contract_obligations_by_envelope(self) -> Dict[str, Decimal]:
        result: Dict[str, Decimal] = {}
        for name, amount in self.contract_obligations.items():
            envelope = self.contract_obligation_envelope(name)
            result[envelope] = result.get(envelope, ZERO) + amount
        return result

    def split_contract_obligation_amount(self, amount: Decimal) -> Dict[str, Decimal]:
        """Пропорционально раскладывает часть общего резерва по расходам."""
        amount = max(ZERO, D(amount))
        total = self.contract_obligations_total
        if amount <= ZERO or total <= ZERO:
            return {}
        result: Dict[str, Decimal] = {}
        distributed = ZERO
        items = list(self.contract_obligations.items())
        for index, (name, target) in enumerate(items):
            share = (
                amount - distributed
                if index == len(items) - 1
                else money(amount * target / total)
            )
            result[name] = share
            distributed += share
        return result

    def contract_obligation_reserve_by_envelope(self, reserved: Decimal) -> Dict[str, Decimal]:
        result: Dict[str, Decimal] = {}
        for name, share in self.split_contract_obligation_amount(reserved).items():
            envelope = self.contract_obligation_envelope(name)
            result[envelope] = result.get(envelope, ZERO) + share
        return result

    @property
    def cycle_regular_income_limit(self) -> Decimal:
        """Ожидаемая обычная доходная база полного финансового цикла."""
        if self.income_rhythm != "cyclic":
            return self.average_income
        return self.average_income * (
            self.income_work_months + self.income_gap_months
        )

    @property
    def total_goals_percentage(self) -> Decimal:
        return sum(
            (goal.percentage for goal in self.active_goals),
            ZERO,
        )

    @property
    def active_goals(self) -> List[Goal]:
        return [goal for goal in self.goals if goal.status == "active"]

    # ========================================================
    # ВАЛИДАЦИЯ
    # ========================================================

    def validate(self) -> List[str]:
        errors = []

        if self.critical_life <= ZERO:
            errors.append(
                "Критический минимум должен быть больше 0."
            )

        if self.household_reserve < ZERO:
            errors.append(
                "Бытовой резерв не может быть отрицательным."
            )

        if sum(self.household_reserve_categories.values(), ZERO) > self.household_reserve:
            errors.append(
                "Сумма отдельных категорий Бытового резерва превышает его общий размер."
            )

        if self.average_income < ZERO:
            errors.append(
                "Среднемесячный доход не может быть отрицательным."
            )

        if self.tax_rate < ZERO or self.tax_rate > HUNDRED:
            errors.append(
                "Ставка налога должна быть от 0 до 100%."
            )

        for name, rate in self.income_type_tax_rates.items():
            if rate < ZERO or rate > HUNDRED:
                errors.append(
                    f"Ставка налога для типа дохода «{name}» должна быть от 0 до 100%."
                )

        # Критическое правило:
        # УЖ = КЖ + БР и поэтому УЖ не может быть меньше КЖ.
        if self.household_life < self.critical_life:
            errors.append(
                "Устойчивая жизнь не может быть меньше "
                "Критического минимума."
            )

        if (
            self.active_goals
            and abs(self.total_goals_percentage - HUNDRED)
            > Decimal("0.0001")
        ):
            errors.append(
                "Сумма процентов финансовых целей должна "
                "равняться 100%."
            )

        if (
            abs(
                self.goals_share_c
                + self.pillow_share_c
                - HUNDRED
            )
            > Decimal("0.0001")
        ):
            errors.append(
                "Доля целей C + Доля подушки C должна "
                "равняться 100%."
            )

        if not ZERO <= self.protective_stage_c_goals_share <= HUNDRED:
            errors.append("Доля целей защитного этапа C должна быть от 0 до 100%.")

        if not (
            ZERO <= self.gift_guideline_min
            <= self.gift_guideline_max
            <= self.gift_warning_limit
            <= HUNDRED
        ):
            errors.append(
                "Ориентиры Подарков должны возрастать и находиться в диапазоне 0–100%."
            )

        if self.employment_type not in {
            "Фрилансер",
            "Наёмный",
            PROFILE_STABLE,
            PROFILE_PIECEWORK,
            PROFILE_CYCLIC,
        }:
            errors.append(
                "Форма занятости должна быть "
                "старым или новым техническим ID профиля."
            )

        if self.income_rhythm not in {"monthly", "irregular", "cyclic"}:
            errors.append("Неизвестный ритм поступления дохода.")

        if self.income_gap_months < ONE:
            errors.append("Период без дохода должен быть не меньше одного месяца.")

        if self.income_work_months < ONE:
            errors.append("Рабочая часть цикла должна быть не меньше одного месяца.")

        for phase, budget in self.phase_life_budgets.items():
            if budget.completed and budget.critical_life <= ZERO:
                errors.append(
                    f"Критический минимум фазы «{phase}» должен быть больше 0."
                )
            if (
                budget.completed
                and budget.currency_code != "RUB"
                and budget.exchange_rate_to_rub <= ZERO
            ):
                errors.append(
                    f"Для валюты {budget.currency_code} фазы «{phase}» нужен курс к рублю."
                )

        if self.debt_strategy not in {
            "Лавина",
            "Снежный ком",
            "Ручной выбор",
        }:
            errors.append(
                "Неизвестная стратегия погашения кредитов."
            )

        return errors


# ============================================================
# СОСТОЯНИЕ АЛГОРИТМА
# ============================================================

@dataclass
class AllocatorState:
    # ----------------------------
    # Баланс жизни
    # ----------------------------

    life_balance: Decimal = ZERO

    # Накоплено из обязательных платежей
    # внутри текущего расчётного периода.
    accumulated_minimum_payments: Decimal = ZERO

    # ----------------------------
    # Подушка
    # ----------------------------

    pillow_minimum: Decimal = ZERO
    intercontract_reserve: Decimal = ZERO
    intercontract_months_remaining: Decimal = ZERO
    intercontract_break_active: bool = False
    current_cycle_phase: str = ""
    current_phase_months_remaining: Decimal = ZERO
    contract_obligations_reserve: Decimal = ZERO
    pillow_force_majeure: Decimal = ZERO
    pillow_stabilizer: Decimal = ZERO

    # ----------------------------
    # Капитал
    # ----------------------------

    investments: Decimal = ZERO
    early_repayment: Decimal = ZERO

    # ----------------------------
    # Цели
    # ----------------------------

    goal_balances: Dict[str, Decimal] = field(
        default_factory=dict
    )

    # ----------------------------
    # Накопления КЖ за период
    # ----------------------------

    period_life_topups: Dict[str, Decimal] = field(
        default_factory=dict
    )

    # ----------------------------
    # Аналитика периода
    # ----------------------------

    period_income: Decimal = ZERO
    # Валовой доход с начала текущего финансового цикла. Для циклического
    # профиля не сбрасывается вместе с обычным расчётным периодом.
    cycle_income: Decimal = ZERO
    period_tax: Decimal = ZERO

    # Все направления распределения
    # за текущий расчётный период.
    period_allocations: Dict[str, Decimal] = field(
        default_factory=dict
    )

    # ----------------------------
    # Журнал операций
    # ----------------------------

    operation_log: List[dict] = field(
        default_factory=list
    )

    # ----------------------------
    # История распределений
    # ----------------------------

    distribution_history: List[dict] = field(
        default_factory=list
    )

    # ----------------------------
    # Период
    # ----------------------------

    period_started_at: Optional[str] = None
    period_ends_at: Optional[str] = None
    period_anchor_day: int = 0
    period_status: str = "legacy"
    period_activation_date: Optional[str] = None
    period_reminder_sent_for: Optional[str] = None
    initial_distribution_completed: bool = False
    break_period_salary_paid: bool = False

    # Фактические деньги Фонда Зарплаты могут храниться в разных валютах.
    # Курсы фиксируются пользователем на расчётный период: ежедневные колебания
    # биржевого ориентира не должны самопроизвольно менять финансовый режим.
    fund_salary_currencies: Dict[str, Decimal] = field(default_factory=dict)
    fund_salary_period_rates: Dict[str, Decimal] = field(default_factory=dict)
    fund_salary_start_reserves: Dict[str, Decimal] = field(default_factory=dict)
    fund_salary_rates_locked_at: Optional[str] = None

    def __post_init__(self):
        self.life_balance = D(self.life_balance)
        self.accumulated_minimum_payments = D(
            self.accumulated_minimum_payments
        )

        self.pillow_minimum = D(self.pillow_minimum)
        self.intercontract_reserve = D(self.intercontract_reserve)
        self.intercontract_months_remaining = max(ZERO, D(self.intercontract_months_remaining))
        self.intercontract_break_active = bool(self.intercontract_break_active)
        self.current_cycle_phase = str(self.current_cycle_phase or "").strip().lower()
        if self.current_cycle_phase not in {"", "work", "break"}:
            self.current_cycle_phase = ""
        if not self.current_cycle_phase and self.intercontract_break_active:
            self.current_cycle_phase = "break"
        self.current_phase_months_remaining = max(
            ZERO, D(self.current_phase_months_remaining)
        )
        if (
            self.current_phase_months_remaining == ZERO
            and self.current_cycle_phase == "break"
        ):
            self.current_phase_months_remaining = self.intercontract_months_remaining
        self.contract_obligations_reserve = max(ZERO, D(self.contract_obligations_reserve))
        self.pillow_force_majeure = D(
            self.pillow_force_majeure
        )
        self.pillow_stabilizer = D(
            self.pillow_stabilizer
        )

        self.investments = D(self.investments)
        self.early_repayment = D(
            self.early_repayment
        )

        self.period_anchor_day = max(0, min(31, int(self.period_anchor_day or 0)))
        self.period_status = str(self.period_status or "legacy")
        if self.period_status not in {"legacy", "not_started", "scheduled", "active"}:
            self.period_status = "legacy"
        self.initial_distribution_completed = bool(self.initial_distribution_completed)
        self.break_period_salary_paid = bool(self.break_period_salary_paid)

        self.fund_salary_currencies = {
            str(code).strip().upper(): max(ZERO, D(amount))
            for code, amount in (self.fund_salary_currencies or {}).items()
            if str(code).strip() and D(amount) > ZERO
        }
        self.fund_salary_start_reserves = {
            str(code).upper(): max(ZERO, D(amount))
            for code, amount in (self.fund_salary_start_reserves or {}).items()
        }
        self.fund_salary_period_rates = {
            str(code).strip().upper(): D(rate)
            for code, rate in (self.fund_salary_period_rates or {}).items()
            if str(code).strip() and D(rate) > ZERO
        }
        if "RUB" in self.fund_salary_currencies:
            self.fund_salary_period_rates["RUB"] = Decimal("1")

        self.period_income = D(self.period_income)
        self.cycle_income = D(self.cycle_income)
        self.period_tax = D(self.period_tax)

        self.goal_balances = {
            name: D(balance)
            for name, balance in self.goal_balances.items()
        }

        self.period_life_topups = {
            name: D(balance)
            for name, balance in self.period_life_topups.items()
        }

        self.period_allocations = {
            name: D(balance)
            for name, balance in self.period_allocations.items()
        }

    def activate_budget_period(self, start: date) -> tuple[date, date]:
        """Активирует личный финансовый месяц без сброса введённых остатков."""
        next_start = next_anchor_date(start, start.day)
        self.period_started_at = datetime.combine(start, datetime.min.time()).isoformat()
        self.period_ends_at = (next_start - timedelta(days=1)).isoformat()
        self.period_anchor_day = start.day
        self.period_status = "active"
        self.period_activation_date = None
        return start, next_start - timedelta(days=1)

    def schedule_budget_period(self, start: date) -> None:
        self.period_status = "scheduled"
        self.period_activation_date = start.isoformat()
        self.period_anchor_day = start.day

    def fund_salary_rub_equivalent(self) -> Decimal:
        return money(sum(
            amount * self.fund_salary_period_rates.get(code, Decimal("1") if code == "RUB" else ZERO)
            for code, amount in self.fund_salary_currencies.items()
        ))

    def set_fund_salary_currency(
        self,
        currency_code: str,
        amount: Decimal,
        rub_per_unit: Decimal,
        *,
        locked_at: Optional[str] = None,
    ) -> Decimal:
        code = str(currency_code or "RUB").strip().upper()
        balance = max(ZERO, D(amount))
        rate = Decimal("1") if code == "RUB" else D(rub_per_unit)
        if rate <= ZERO:
            raise ValueError("Курс валюты должен быть больше нуля.")
        if balance == ZERO:
            self.fund_salary_currencies.pop(code, None)
            self.fund_salary_period_rates.pop(code, None)
        else:
            self.fund_salary_currencies[code] = balance
            self.fund_salary_period_rates[code] = rate
        self.fund_salary_rates_locked_at = locked_at or datetime.now().isoformat()
        self.intercontract_reserve = self.fund_salary_rub_equivalent()
        return self.intercontract_reserve

    def convert_fund_salary_currency(
        self,
        from_code: str,
        to_code: str,
        amount_spent: Decimal,
        amount_received: Decimal,
        to_rub_per_unit: Decimal,
    ) -> Decimal:
        """Фиксирует реальный обмен внутри Фонда без создания нового дохода."""
        source = str(from_code).strip().upper()
        target = str(to_code).strip().upper()
        spent = D(amount_spent)
        received = D(amount_received)
        if source == target or spent <= ZERO or received < ZERO:
            raise ValueError("Проверьте валюты и суммы обмена.")
        available = self.fund_salary_currencies.get(source, ZERO)
        if spent > available:
            raise ValueError("В Фонде Зарплаты недостаточно этой валюты.")
        source_rate = self.fund_salary_period_rates.get(source, Decimal("1") if source == "RUB" else ZERO)
        self.set_fund_salary_currency(source, available - spent, source_rate)
        target_balance = self.fund_salary_currencies.get(target, ZERO) + received
        self.set_fund_salary_currency(target, target_balance, to_rub_per_unit)
        return self.intercontract_reserve

    def reconcile_fund_salary_currencies(self) -> None:
        """Подгоняет фактический валютный состав к уже рассчитанному размеру Фонда."""
        if not self.fund_salary_currencies:
            return
        target = money(self.intercontract_reserve)
        current = self.fund_salary_rub_equivalent()
        difference = target - current
        if difference > ZERO:
            self.fund_salary_currencies["RUB"] = self.fund_salary_currencies.get("RUB", ZERO) + difference
            self.fund_salary_period_rates["RUB"] = Decimal("1")
            return
        to_remove = -difference
        for code in (["RUB"] if "RUB" in self.fund_salary_currencies else []) + [
            code for code in self.fund_salary_currencies if code != "RUB"
        ]:
            if to_remove <= ZERO:
                break
            rate = self.fund_salary_period_rates.get(code, ZERO)
            if rate <= ZERO:
                continue
            balance = self.fund_salary_currencies.get(code, ZERO)
            units = min(balance, to_remove / rate)
            self.fund_salary_currencies[code] = balance - units
            to_remove -= units * rate
            if self.fund_salary_currencies[code] <= CENT:
                self.fund_salary_currencies.pop(code, None)
                self.fund_salary_period_rates.pop(code, None)

    @property
    def pillow_balance(self) -> Decimal:
        """
        Подушка включает только минимальный и форс-мажорный слои.

        Стабилизатор дохода и Фонд Зарплаты — самостоятельные сущности.
        """
        return (
            self.pillow_minimum
            + self.pillow_force_majeure
        )

    @property
    def stabilizer_balance(self) -> Decimal:
        return self.pillow_stabilizer

    def reset_period(self):
        """
        Начало нового расчётного периода.

        Сбрасываются только показатели,
        которые должны начаться заново.

        НЕ сбрасываются:
        - Подушка;
        - инвестиции;
        - цели;
        - остатки кредитов;
        - общий объём досрочного погашения;
        - история операций.
        """

        self.life_balance = ZERO

        self.accumulated_minimum_payments = ZERO

        self.period_income = ZERO
        self.period_tax = ZERO

        self.period_life_topups = {}
        self.period_allocations = {}
        self.break_period_salary_paid = False


def next_anchor_date(current_start: date, anchor_day: int) -> date:
    """Следующая дата периода; 29–31 безопасно сокращаются в коротких месяцах."""
    if current_start.month == 12:
        year, month = current_start.year + 1, 1
    else:
        year, month = current_start.year, current_start.month + 1
    day = min(max(1, anchor_day), monthrange(year, month)[1])
    return date(year, month, day)


# ============================================================
# РЕЗУЛЬТАТ РАСПРЕДЕЛЕНИЯ
# ============================================================

@dataclass
class DistributionResult:
    income: Decimal
    tax: Decimal
    amount_to_distribute: Decimal

    mode_before: int
    mode_after: int

    allocations: Dict[str, Decimal]

    steps: List[str]

    checks: Dict[str, object]

    transition_message: Optional[str] = None
    regular_income_part: Decimal = ZERO
    super_income_part: Decimal = ZERO

    def total_allocated_after_tax(self) -> Decimal:
        return sum(
            self.allocations.values(),
            ZERO,
        )

    def total_allocated_with_tax(self) -> Decimal:
        return (
            self.tax
            + self.total_allocated_after_tax()
        )


# ============================================================
# ОСНОВНОЙ ДВИЖОК
# ============================================================

class FinancialAllocator:
    """
    Детерминированное ядро финансового аллокатора.
    """

    def __init__(
        self,
        settings: UserSettings,
        state: Optional[AllocatorState] = None,
    ):
        self.settings = settings
        self.state = state or AllocatorState()

        errors = self.settings.validate()

        if errors:
            raise ValueError(
                "Ошибки настроек:\n"
                + "\n".join(
                    f"• {error}"
                    for error in errors
                )
            )

        self._ensure_goal_balances()
        self._ensure_life_categories()

    # ========================================================
    # ИНИЦИАЛИЗАЦИЯ
    # ========================================================

    def _ensure_goal_balances(self):
        for goal in self.settings.goals:
            if goal.name not in self.state.goal_balances:
                self.state.goal_balances[
                    goal.name
                ] = goal.balance

    def _ensure_life_categories(self):
        for name in self.settings.life_categories:
            if name not in self.state.period_life_topups:
                self.state.period_life_topups[name] = ZERO

        if "Зарплата" not in self.state.period_life_topups:
            self.state.period_life_topups[
                "Зарплата"
            ] = ZERO

    # ========================================================
    # КАТЕГОРИИ КЖ
    # ========================================================

    def life_category_targets(self) -> Dict[str, Decimal]:
        """
        Целевые суммы Этапа A.

        КЖ_общая = КЖ + минимальные платежи по кредитам.

        Минимальные платежи учитываются отдельно,
        чтобы не возникал двойной учёт.

        Зарплата является автоматическим остатком
        внутри обычной Критической жизни:

        Зарплата = КЖ - сумма пользовательских категорий КЖ.
        """
        result = dict(self.settings.life_categories)

        explicit_sum = sum(
            result.values(),
            ZERO,
        )

        salary = (
            self.settings.critical_life
            - explicit_sum
        )

        if salary < ZERO:
            raise ValueError(
                "Сумма категорий обязательных расходов "
                "превышает Критический минимум."
            )

        result["Зарплата"] = salary

        minimum_payment = (
            self.settings.minimum_payment_total
        )

        if minimum_payment > ZERO:
            result["Мин. платеж"] = minimum_payment

        return result

    def life_category_shares(self) -> Dict[str, Decimal]:
        """
        Доля категории:

        сумма категории / КЖ_общая
        """

        total = self.settings.total_critical_life

        if total <= ZERO:
            return {
                name: ZERO
                for name in self.life_category_targets()
            }

        targets = self.life_category_targets()

        return {
            name: amount / total
            for name, amount in targets.items()
        }

    # ========================================================
    # НАЛОГ
    # ========================================================

    def calculate_tax(
        self,
        income: Decimal,
        income_type: str,
    ) -> Decimal:

        income = D(income)

        if (
            self.settings.income_type_tax_rates
            and income_type not in self.settings.income_type_tax_rates
        ):
            raise ValueError(
                f"Тип дохода «{income_type}» не найден в профиле. "
                "Выберите сохранённый тип или добавьте новый."
            )

        rate = self.settings.income_type_tax_rates.get(income_type, ZERO)
        return income * rate / HUNDRED

    # ========================================================
    # БАЛАНСЫ ЗАЩИТНЫХ РЕЗЕРВОВ
    # ========================================================

    def pillow_layer_limit(
        self,
        layer: str,
    ) -> Decimal:

        if layer == "МП":
            return max(
                ZERO,
                self.settings.minimum_reserve_limit - self.state.pillow_force_majeure,
            )

        if layer == "МР":
            return self.intercontract_current_limit

        if layer == "ФМ":
            # МП и ФМ — виртуальные слои одного физического счёта.
            # Полная цель Подушки не складывается с уже сохранённой МП.
            return max(
                ZERO,
                self.settings.force_majeure_limit - self.state.pillow_minimum,
            )

        if layer == "СтабД":
            return self.settings.stabilizer_full_limit

        raise ValueError(
            f"Неизвестный слой Подушки: {layer}"
        )

    def pillow_layer_balance(
        self,
        layer: str,
    ) -> Decimal:

        if layer == "МП":
            return self.state.pillow_minimum

        if layer == "МР":
            return self.state.intercontract_reserve

        if layer == "ФМ":
            return self.state.pillow_force_majeure

        if layer == "СтабД":
            return self.state.pillow_stabilizer

        raise ValueError(
            f"Неизвестный слой Подушки: {layer}"
        )

    def add_to_pillow_layer(
        self,
        layer: str,
        amount: Decimal,
    ) -> Decimal:
        """
        Добавляет сумму в конкретный защитный резерв.

        Возвращает переполнение, которое не удалось
        разместить в этом слое.
        """

        amount = D(amount)

        if amount <= ZERO:
            return ZERO

        limit = self.pillow_layer_limit(layer)
        current = self.pillow_layer_balance(layer)

        free_space = max(
            ZERO,
            limit - current,
        )

        actual = min(
            amount,
            free_space,
        )

        overflow = amount - actual

        if layer == "МП":
            self.state.pillow_minimum += actual

        elif layer == "МР":
            self.state.intercontract_reserve += actual

        elif layer == "ФМ":
            self.state.pillow_force_majeure += actual

        elif layer == "СтабД":
            self.state.pillow_stabilizer += actual

        else:
            raise ValueError(
                f"Неизвестный слой Подушки: {layer}"
            )

        return overflow

    def waterfall_pillow(
        self,
        amount: Decimal,
        start_layer: str,
    ) -> Decimal:
        """
        Технический водопад защитных сущностей в порядке финансовых режимов.

        Возвращает остаток, если все предусмотренные резервы заполнены.
        """

        amount = D(amount)

        if amount <= ZERO:
            return ZERO

        if self.profile_id == PROFILE_CYCLIC:
            layers = ["МП", "МР", "ФМ", "СтабД"]
        elif self.profile_id == PROFILE_PIECEWORK:
            layers = ["МП", "ФМ", "СтабД"]
        else:
            layers = ["МП", "ФМ"]

        try:
            start_index = layers.index(start_layer)
        except ValueError:
            raise ValueError(
                f"Неизвестный стартовый слой: {start_layer}"
            )

        remaining = amount

        for layer in layers[start_index:]:
            if remaining <= ZERO:
                break

            remaining = self.add_to_pillow_layer(
                layer,
                remaining,
            )

        return remaining

    def allocate_protection_waterfall(
        self,
        amount: Decimal,
        start_layer: str,
        allocations: Dict[str, Decimal],
    ) -> Decimal:
        """Проводит защитный водопад и записывает деньги в правильные конверты."""
        pillow_before = self.pillow_total_balance
        fund_before = self.state.intercontract_reserve
        stabilizer_before = self.state.pillow_stabilizer
        overflow = self.waterfall_pillow(amount, start_layer)
        allocations["Подушка"] = allocations.get("Подушка", ZERO) + (
            self.pillow_total_balance - pillow_before
        )
        allocations["Фонд Зарплаты"] = allocations.get("Фонд Зарплаты", ZERO) + (
            self.state.intercontract_reserve - fund_before
        )
        allocations["Стабилизатор дохода"] = allocations.get("Стабилизатор дохода", ZERO) + (
            self.state.pillow_stabilizer - stabilizer_before
        )
        return overflow

    @property
    def pillow_total_balance(self) -> Decimal:
        """Общий остаток единого счёта Подушки: МП + слой ФМ."""
        return self.state.pillow_minimum + self.state.pillow_force_majeure

    def minimum_pillow_is_funded(self) -> bool:
        return self.pillow_total_balance >= self.settings.minimum_reserve_limit

    def force_majeure_pillow_is_funded(self) -> bool:
        return self.pillow_total_balance >= self.settings.force_majeure_limit

    def first_distribution_source_total(self) -> Decimal:
        """Деньги, заявленные в онбординге как текущие накопления и остатки."""
        st = self.state
        return money(
            st.life_balance
            + st.accumulated_minimum_payments
            + st.contract_obligations_reserve
            + st.pillow_minimum
            + st.intercontract_reserve
            + st.pillow_stabilizer
            + st.pillow_force_majeure
        )

    def first_distribution_balances(self) -> Dict[str, Decimal]:
        """Текущие остатки, которые участвуют в первом перераспределении."""
        st = self.state
        return {
            "Текущая жизнь": money(st.life_balance),
            "Минимальная подушка": money(st.pillow_minimum),
            "Обязательства на время работы": money(st.contract_obligations_reserve),
            "Фонд Зарплаты": money(st.intercontract_reserve),
            "Стабилизатор дохода": money(st.pillow_stabilizer),
            "Форс-мажорная подушка": money(st.pillow_force_majeure),
        }

    def rebalance_first_distribution(self) -> tuple[Dict[str, Decimal], Dict[str, Decimal]]:
        """Перекладывает уже разделённые деньги и возвращает (до, после)."""
        before = self.first_distribution_balances()
        self.apply_first_distribution(sum(before.values(), ZERO))
        after = self.first_distribution_balances()
        return before, after

    def apply_first_distribution(self, total: Decimal) -> Dict[str, Decimal]:
        """Раскладывает уже имеющиеся деньги по актуальному защитному водопаду.

        Это не доход: налог, средний доход и аналитика поступлений не меняются.
        """
        total = max(ZERO, D(total))
        s = self.settings
        st = self.state
        result: Dict[str, Decimal] = {}

        st.life_balance = ZERO
        st.accumulated_minimum_payments = ZERO
        st.contract_obligations_reserve = ZERO
        st.pillow_minimum = ZERO
        st.intercontract_reserve = ZERO
        st.pillow_stabilizer = ZERO
        st.pillow_force_majeure = ZERO

        life_part = min(total, s.household_life)
        st.life_balance = life_part
        result["Текущая жизнь"] = life_part
        total -= life_part

        if s.income_rhythm == "cyclic" and s.contract_obligations_total > ZERO:
            part = min(total, s.contract_obligations_total)
            st.contract_obligations_reserve = part
            for name, share in s.split_contract_obligation_amount(part).items():
                envelope = s.contract_obligation_envelope(name)
                result[f"Обязательства · {envelope} · {name}"] = share
            total -= part

        has_debts = any(credit.active for credit in s.credits)
        if has_debts:
            start_layer = "МП"
        elif self.profile_id == PROFILE_CYCLIC:
            start_layer = "МР"
        elif self.profile_id == PROFILE_PIECEWORK:
            start_layer = "ФМ"
        else:
            start_layer = "ФМ"

        before_minimum = st.pillow_minimum
        before_fund = st.intercontract_reserve
        before_stabilizer = st.pillow_stabilizer
        before_force = st.pillow_force_majeure
        overflow = self.waterfall_pillow(total, start_layer)
        result["Минимальная подушка"] = st.pillow_minimum - before_minimum
        result["Фонд Зарплаты"] = st.intercontract_reserve - before_fund
        result["Стабилизатор дохода"] = st.pillow_stabilizer - before_stabilizer
        result["Форс-мажорная подушка"] = st.pillow_force_majeure - before_force

        if overflow > ZERO:
            if has_debts:
                # Аллокатор не совершает досрочное погашение без отдельного
                # решения пользователя: остаток остаётся на «Зарплате».
                st.life_balance += overflow
                result["Свободный остаток на Зарплате"] = overflow
            else:
                st.investments += overflow
                result["Инвестиции"] = overflow

        st.reconcile_fund_salary_currencies()
        st.initial_distribution_completed = True

        return {name: money(value) for name, value in result.items() if value > ZERO}

    # ========================================================
    # ОПРЕДЕЛЕНИЕ РЕЖИМА
    # ========================================================

    @property
    def intercontract_monthly_salary(self) -> Decimal:
        return self.settings.household_life

    @property
    def intercontract_current_limit(self) -> Decimal:
        if self.settings.income_rhythm != "cyclic":
            return ZERO
        if self.state.intercontract_months_remaining > ZERO:
            months = self.state.intercontract_months_remaining
            if (
                self.state.intercontract_break_active
                and self.state.life_balance >= self.settings.household_life
                and not self.state.break_period_salary_paid
            ):
                months = max(ZERO, months - ONE)
            return self.intercontract_monthly_salary * months
        return self.settings.intercontract_full_limit

    @property
    def intercontract_current_life_limit(self) -> Decimal:
        if self.settings.income_rhythm != "cyclic":
            return ZERO
        if self.state.intercontract_months_remaining > ZERO:
            months = self.state.intercontract_months_remaining
            if (
                self.state.intercontract_break_active
                and self.state.life_balance >= self.settings.household_life
                and not self.state.break_period_salary_paid
            ):
                months = max(ZERO, months - ONE)
            return self.settings.critical_life * months
        return self.settings.intercontract_life_limit

    def start_intercontract_break(self) -> dict:
        if self.settings.income_rhythm != "cyclic":
            raise ValueError("Межконтрактный период доступен только циклическому профилю.")
        if self.state.intercontract_break_active:
            raise ValueError("Межконтрактный период уже начат.")
        self.state.intercontract_break_active = True
        self.state.intercontract_months_remaining = self.settings.income_gap_months
        self.state.current_cycle_phase = "break"
        self.state.current_phase_months_remaining = self.settings.income_gap_months
        # Резерв завершившейся рабочей части считается использованным. С этого
        # момента новые деньги заранее готовят обязательства следующей работы.
        self.state.contract_obligations_reserve = ZERO
        return {
            "months_remaining": self.state.intercontract_months_remaining,
            "monthly_salary": self.intercontract_monthly_salary,
            "next_work_obligations": self.settings.contract_obligations_total,
            "next_work_obligations_missing": self.settings.contract_obligations_total,
        }

    def pay_intercontract_salary(self, requested_amount: Optional[Decimal] = None) -> Decimal:
        if not self.state.intercontract_break_active:
            raise ValueError("Сначала начните межконтрактный период.")
        if self.state.intercontract_months_remaining <= ZERO:
            raise ValueError("Все месяцы перерыва уже проведены. Начните новую рабочую часть.")
        missing_life = max(ZERO, self.settings.household_life - self.state.life_balance)
        requested = self.intercontract_monthly_salary if requested_amount is None else D(requested_amount)
        if requested < ZERO:
            raise ValueError("Сумма выплаты не может быть отрицательной.")
        amount = (
            min(requested, missing_life, self.state.intercontract_reserve)
            if requested_amount is None
            else min(requested, self.state.intercontract_reserve)
        )
        self.state.intercontract_reserve -= amount
        life_part = min(amount, missing_life)
        self.state.life_balance += life_part
        remaining = amount - life_part

        obligation_missing = max(
            ZERO,
            self.settings.contract_obligations_total - self.state.contract_obligations_reserve,
        )
        obligation_part = min(remaining, obligation_missing)
        self.state.contract_obligations_reserve += obligation_part
        remaining -= obligation_part

        self.state.intercontract_months_remaining -= ONE
        self.state.break_period_salary_paid = True
        self.state.current_phase_months_remaining = self.state.intercontract_months_remaining
        if remaining > ZERO:
            if any(credit.active for credit in self.settings.credits) and self.minimum_pillow_is_funded():
                remaining -= self.apply_early_repayment(remaining, [])
            if remaining <= ZERO:
                overflow = ZERO
            elif any(credit.active for credit in self.settings.credits):
                start_layer = "МП"
                overflow = self.waterfall_pillow(remaining, start_layer)
            elif self.state.intercontract_reserve < self.intercontract_current_limit:
                start_layer = "МР"
                overflow = self.waterfall_pillow(remaining, start_layer)
            elif not self.force_majeure_pillow_is_funded():
                start_layer = "ФМ"
                overflow = self.waterfall_pillow(remaining, start_layer)
            else:
                start_layer = "СтабД"
                overflow = self.waterfall_pillow(remaining, start_layer)
            if overflow > ZERO:
                self.state.investments += overflow
        self.state.reconcile_fund_salary_currencies()
        return amount

    def extend_intercontract_break(self, periods: Decimal = ONE) -> dict:
        """Продлевает фактический перерыв без попытки угадать дату контракта.

        Метод меняет только прогноз количества периодов. Деньги между конвертами
        не перемещаются: при дефиците пользователь сам решает, какой резерв
        допустимо использовать.
        """
        if self.settings.income_rhythm != "cyclic":
            raise ValueError("Продление перерыва доступно только циклическому профилю.")
        if not self.state.intercontract_break_active:
            raise ValueError("Сначала начните перерыв между рабочими частями.")
        periods = D(periods)
        if periods <= ZERO or periods != periods.to_integral_value():
            raise ValueError("Перерыв можно продлить только на целое число периодов.")

        self.state.intercontract_months_remaining += periods
        self.state.current_phase_months_remaining = self.state.intercontract_months_remaining
        required = money(self.intercontract_monthly_salary * periods)
        available = money(self.state.intercontract_reserve)
        shortfall = money(max(ZERO, required - available))
        return {
            "periods_added": periods,
            "periods_remaining": self.state.intercontract_months_remaining,
            "required": required,
            "available_in_salary_fund": available,
            "shortfall": shortfall,
        }

    def transfer_salary_remainder(self, amount: Decimal, target: str) -> str:
        """Фиксирует выбранный пользователем внутренний перенос остатка Зарплаты."""
        amount = max(ZERO, D(amount))
        if amount <= ZERO:
            return ""
        target = str(target or "priority")
        if target == "salary_fund" and self.profile_id == PROFILE_CYCLIC:
            self.state.intercontract_reserve += amount
            self.state.reconcile_fund_salary_currencies()
            return "Фонд Зарплаты"
        if target == "household":
            self.state.life_balance += amount
            return "Бытовой резерв"
        if target == "pillow":
            if any(c.active for c in self.settings.credits) and not self.minimum_pillow_is_funded():
                self.state.pillow_minimum += amount
            else:
                self.state.pillow_force_majeure += amount
            return "Подушка"
        if target == "stabilizer" and self.settings.needs_stabilizer:
            self.state.pillow_stabilizer += amount
            return "Стабилизатор дохода"
        if target == "goals":
            self._allocate_goals(amount, {})
            return "Цели"
        if target.startswith("goal:"):
            try:
                index = int(target.split(":", 1)[1])
                goal = self.settings.goals[index]
            except (ValueError, IndexError):
                raise ValueError("Выбранная цель не найдена.")
            if goal.status != "active":
                raise ValueError("Выбранная позиция сейчас не активна.")
            self.state.goal_balances[goal.name] = (
                self.state.goal_balances.get(goal.name, ZERO) + amount
            )
            noun = "Сундук" if goal.is_chest else "Цель"
            name = goal_display_name(goal.name, goal.is_chest)
            if goal.is_chest:
                return f"«{name}»"
            return f"{noun} «{name}»"
        if target == "investments":
            self.state.investments += amount
            return "Инвестиции"

        return self._transfer_salary_remainder_to_priority(amount)

    def _transfer_salary_remainder_to_priority(self, amount: Decimal) -> str:
        """Проводит внутренний остаток по защите, не теряя переполнение слоя."""
        remaining = max(ZERO, D(amount))
        first_target = ""
        has_debts = any(credit.active for credit in self.settings.credits)

        if has_debts and not self.minimum_pillow_is_funded():
            first_target = "Минимальная Подушка"
            remaining = self.add_to_pillow_layer("МП", remaining)
        if remaining > ZERO and any(credit.active for credit in self.settings.credits):
            first_target = first_target or "Долги"
            remaining = self.apply_early_repayment(remaining, [])

        if remaining > ZERO:
            if self.profile_id == PROFILE_CYCLIC:
                first_target = first_target or "Фонд Зарплаты"
                remaining = self.waterfall_pillow(remaining, "МР")
            else:
                first_target = first_target or "Подушка"
                remaining = self.waterfall_pillow(remaining, "ФМ")

        if remaining > ZERO:
            self.state.investments += remaining
            first_target = first_target or "Инвестиции"
        self.state.reconcile_fund_salary_currencies()
        return first_target or "Текущий финансовый приоритет"

    def start_new_work_phase(self, allow_early: bool = False) -> None:
        if not self.state.intercontract_break_active:
            raise ValueError("Межконтрактный период ещё не начат.")
        if self.state.intercontract_months_remaining > ZERO and not allow_early:
            raise ValueError("Сначала проведите все запланированные месяцы перерыва.")
        self.state.intercontract_break_active = False
        self.state.current_cycle_phase = "work"
        self.state.current_phase_months_remaining = self.settings.income_work_months
        self.state.cycle_income = ZERO

    def advance_work_month(self) -> Decimal:
        """Уменьшает счётчик рабочей части при начале нового периода."""
        if self.settings.income_rhythm != "cyclic" or self.state.current_cycle_phase != "work":
            return self.state.current_phase_months_remaining
        self.state.current_phase_months_remaining = max(
            ZERO,
            self.state.current_phase_months_remaining - ONE,
        )
        return self.state.current_phase_months_remaining

    @property
    def profile_id(self) -> str:
        return normalize_profile_id(
            self.settings.profile_type,
            self.settings.employment_type,
            self.settings.income_rhythm,
        )

    @property
    def profile_mode_total(self) -> int:
        return len(PROFILE_MODE_TITLES[self.profile_id])

    @property
    def protective_capital_balance(self) -> Decimal:
        """Совокупный капитал защитных резервов, определяющий кубки."""
        total = self.pillow_total_balance
        if self.settings.needs_stabilizer:
            total += self.state.pillow_stabilizer
        if self.profile_id == PROFILE_CYCLIC:
            total += self.state.intercontract_reserve
        return money(total)

    def resilience_transition_targets(self) -> List[Tuple[int, Decimal, str]]:
        """Накопительные отметки виртуального сосуда финансовой устойчивости."""
        s = self.settings
        if self.profile_id == PROFILE_STABLE:
            return [(4, s.force_majeure_limit, "Подушка")]
        if self.profile_id == PROFILE_PIECEWORK:
            return [
                (4, s.force_majeure_limit, "Подушка"),
                (5, s.force_majeure_limit + s.stabilizer_life_limit, "Стабилизатор-КМ"),
                (6, s.force_majeure_limit + s.stabilizer_full_limit, "Стабилизатор-УЖ"),
            ]
        salary_critical = self.intercontract_current_life_limit
        salary_full = self.intercontract_current_limit
        return [
            (4, salary_critical, "Фонд Зарплаты-КМ"),
            (5, salary_full, "Фонд Зарплаты-УЖ"),
            (6, salary_full + s.force_majeure_limit, "Подушка"),
            (7, salary_full + s.force_majeure_limit + s.stabilizer_life_limit, "Стабилизатор-КМ"),
            (8, salary_full + s.force_majeure_limit + s.stabilizer_full_limit, "Стабилизатор-УЖ"),
        ]

    @property
    def protective_capital_target(self) -> Decimal:
        """Полная цель защитного сосуда для текущей фазы профиля."""
        return money(self.resilience_transition_targets()[-1][1])

    def current_protection_priority(self) -> Optional[Dict[str, Decimal | str]]:
        """Первый фактически незаполненный счёт; кубки на этот выбор не влияют."""
        s = self.settings
        st = self.state
        active_debt = any(credit.active for credit in s.credits)
        if active_debt and self.pillow_total_balance < s.minimum_reserve_limit:
            target = s.minimum_reserve_limit
            balance = self.pillow_total_balance
            return {"name": "Минимальная подушка", "target": target, "balance": balance, "deficit": target - balance}
        if active_debt:
            debt = sum((credit.principal_balance for credit in s.credits if credit.active), ZERO)
            return {"name": "Долги", "target": debt, "balance": ZERO, "deficit": debt}

        priorities: List[Tuple[str, Decimal, Decimal]] = []
        if self.profile_id == PROFILE_CYCLIC:
            priorities.extend([
                ("Фонд Зарплаты-КМ", self.intercontract_current_life_limit, st.intercontract_reserve),
                ("Фонд Зарплаты-УЖ", self.intercontract_current_limit, st.intercontract_reserve),
            ])
        priorities.append(("Подушка", s.force_majeure_limit, self.pillow_total_balance))
        if s.needs_stabilizer:
            priorities.extend([
                ("Стабилизатор-КМ", s.stabilizer_life_limit, st.pillow_stabilizer),
                ("Стабилизатор-УЖ", s.stabilizer_full_limit, st.pillow_stabilizer),
            ])
        for name, target, balance in priorities:
            if balance < target:
                return {"name": name, "target": target, "balance": balance, "deficit": target - balance}
        return None

    def protection_reserve_accounts(self) -> List[Tuple[str, Decimal, Decimal]]:
        """Фактические защитные счета в порядке финансового водопада."""
        accounts: List[Tuple[str, Decimal, Decimal]] = []
        if self.profile_id == PROFILE_CYCLIC:
            accounts.append(("Фонд Зарплаты", self.intercontract_current_limit, self.state.intercontract_reserve))
        accounts.append(("Подушка", self.settings.force_majeure_limit, self.pillow_total_balance))
        if self.settings.needs_stabilizer:
            accounts.append(("Стабилизатор дохода", self.settings.stabilizer_full_limit, self.state.pillow_stabilizer))
        return accounts

    def reserve_rebalancing_plan(self) -> Dict[str, object]:
        """Переводит только профицит счетов; целевой остаток источника не затрагивается."""
        if any(credit.active for credit in self.settings.credits):
            return {
                "accounts": [],
                "transfers": [],
                "free_surplus": ZERO,
                "blocked_reason": "active_debt",
            }
        accounts = self.protection_reserve_accounts()
        available = {
            name: max(ZERO, balance - target)
            for name, target, balance in accounts
        }
        deficits = {
            name: max(ZERO, target - balance)
            for name, target, balance in accounts
        }
        transfers: List[Dict[str, Decimal | str]] = []
        for destination, _target, _balance in accounts:
            needed = deficits[destination]
            if needed <= ZERO:
                continue
            for source, _source_target, _source_balance in accounts:
                if source == destination or available[source] <= ZERO:
                    continue
                amount = min(needed, available[source])
                if amount <= ZERO:
                    continue
                transfers.append({"source": source, "destination": destination, "amount": money(amount)})
                available[source] -= amount
                needed -= amount
                if needed <= ZERO:
                    break
        return {
            "accounts": [
                {
                    "name": name,
                    "target": money(target),
                    "balance": money(balance),
                    "surplus": money(max(ZERO, balance - target)),
                    "deficit": money(max(ZERO, target - balance)),
                }
                for name, target, balance in accounts
            ],
            "transfers": transfers,
            "free_surplus": money(sum(available.values(), ZERO)),
            "blocked_reason": None,
        }

    def _move_protection_reserve(self, name: str, amount: Decimal) -> None:
        """Меняет учётный баланс после подтверждённого банковского перевода."""
        amount = D(amount)
        if name == "Фонд Зарплаты":
            self.state.intercontract_reserve += amount
            return
        if name == "Стабилизатор дохода":
            self.state.pillow_stabilizer += amount
            return
        if name == "Подушка":
            if amount >= ZERO:
                self.state.pillow_force_majeure += amount
                return
            withdrawal = -amount
            from_force = min(withdrawal, self.state.pillow_force_majeure)
            self.state.pillow_force_majeure -= from_force
            withdrawal -= from_force
            if withdrawal > ZERO:
                self.state.pillow_minimum = max(ZERO, self.state.pillow_minimum - withdrawal)
            return
        raise ValueError(f"Неизвестный защитный счёт: {name}")

    def apply_reserve_rebalancing(self) -> Dict[str, object]:
        """Записывает только переводы, которые пользователь подтвердил как выполненные."""
        plan = self.reserve_rebalancing_plan()
        for transfer in plan["transfers"]:
            amount = D(transfer["amount"])
            self._move_protection_reserve(str(transfer["source"]), -amount)
            self._move_protection_reserve(str(transfer["destination"]), amount)
        return plan

    @property
    def estimated_average_tax_rate(self) -> Decimal:
        """Консервативная ставка прогноза, когда доли типов будущего дохода неизвестны."""
        rates = list(self.settings.income_type_tax_rates.values())
        if not rates:
            rates = [self.settings.tax_rate]
        return max([ZERO, *rates])

    @property
    def estimated_net_average_income(self) -> Decimal:
        return money(
            self.settings.average_income
            * (ONE - self.estimated_average_tax_rate / HUNDRED)
        )

    def current_stage_c_goal_share(self) -> Decimal:
        """Доля этапа C для Целей из параметров текущего режима и стратегии."""
        mode = self.allocation_mode()
        s = self.settings
        if mode in {MODE_1, MODE_2}:
            return ZERO
        if mode == MODE_3:
            filling_salary_fund = (
                self.profile_id == PROFILE_CYCLIC
                and self.state.intercontract_reserve < self.intercontract_current_limit
            )
            if filling_salary_fund or s.protective_stage_c_strategy == "protection":
                return ZERO
            return s.protective_stage_c_goals_share / HUNDRED
        if mode == MODE_4:
            if s.protective_stage_c_strategy == "protection":
                return ZERO
            return s.protective_stage_c_goals_share / HUNDRED
        if mode == MODE_5:
            if s.protective_stage_c_strategy == "protection":
                return ZERO
            return (
                (ONE - s.bracket_c / HUNDRED)
                * s.goals_share_c
                / HUNDRED
            )
        return ONE - s.bracket_c / HUNDRED

    def _estimated_goals_capacity_for_income(
        self,
        income: Decimal,
    ) -> Dict[str, Decimal | str | bool]:
        """Бюджет Целей при заданном доходе после налога."""
        s = self.settings
        income = max(ZERO, D(income))
        share_a = s.bracket_a / HUNDRED
        share_b = s.bracket_b / HUNDRED
        life_share_a = ONE - share_a
        life_share_b = ONE - share_b
        if life_share_a <= ZERO or life_share_b <= ZERO:
            return {
                "net_income": income,
                "stage_a_required": ZERO,
                "stage_b_required": ZERO,
                "stage_c_available": ZERO,
                "goal_share": ZERO,
                "capacity": ZERO,
                "blocked_stage": "settings",
                "tax_estimate_is_conservative": len(set(s.income_type_tax_rates.values())) > 1,
            }
        stage_a_required = s.critical_life / life_share_a
        if income < stage_a_required:
            blocked_stage = "A"
            stage_b_required = s.household_reserve / life_share_b
            stage_c_available = ZERO
        else:
            stage_b_required = s.household_reserve / life_share_b
            remaining = income - stage_a_required
            if remaining < stage_b_required:
                blocked_stage = "B"
                stage_c_available = ZERO
            else:
                blocked_stage = ""
                stage_c_available = remaining - stage_b_required
        goal_share = self.current_stage_c_goal_share()
        capacity = money(stage_c_available * goal_share)
        return {
            "net_income": money(income),
            "stage_a_required": money(stage_a_required),
            "stage_b_required": money(stage_b_required),
            "stage_c_available": money(stage_c_available),
            "goal_share": goal_share,
            "capacity": capacity,
            "blocked_stage": blocked_stage,
            "tax_estimate_is_conservative": len(set(s.income_type_tax_rates.values())) > 1,
        }

    def estimated_goals_capacity(self) -> Dict[str, Decimal | str | bool]:
        """Консервативный бюджет Целей с максимальной известной ставкой налога."""
        return self._estimated_goals_capacity_for_income(
            self.estimated_net_average_income
        )

    def estimated_goals_capacity_range(self) -> Dict[str, Decimal | bool]:
        """Диапазон бюджета Целей: от полного налога до дохода без налога."""
        conservative = self._estimated_goals_capacity_for_income(
            self.estimated_net_average_income
        )
        optimistic = self._estimated_goals_capacity_for_income(
            self.settings.average_income
        )
        lower = min(D(conservative["capacity"]), D(optimistic["capacity"]))
        upper = max(D(conservative["capacity"]), D(optimistic["capacity"]))
        return {
            "minimum": money(lower),
            "maximum": money(upper),
            "tax_changes_range": lower != upper,
        }

    def goal_forecast(
        self,
        goal: Goal,
        today: Optional[date] = None,
    ) -> Dict[str, Decimal | int | str | bool | None]:
        """Прогноз одной Цели без требования вручную вести банковский баланс."""
        today = today or date.today()
        capacity = self.estimated_goals_capacity_range()
        share = max(ZERO, goal.percentage) / HUNDRED
        monthly_minimum = money(D(capacity["minimum"]) * share)
        monthly_maximum = money(D(capacity["maximum"]) * share)
        current = max(
            ZERO,
            D(self.state.goal_balances.get(goal.name, goal.balance)),
        )
        target = goal.full_target_amount
        remaining = None if target is None else max(ZERO, target - current)
        months_left: Optional[int] = None
        required_monthly: Optional[Decimal] = None
        status = "no_target" if target is None else "no_deadline"

        if target is not None and remaining <= ZERO:
            status = "completed"
        elif target is not None and goal.deadline:
            try:
                deadline = date.fromisoformat(goal.deadline)
            except ValueError:
                status = "invalid_deadline"
            else:
                raw_months = (
                    (deadline.year - today.year) * 12
                    + deadline.month - today.month
                )
                if deadline.day > today.day:
                    raw_months += 1
                months_left = max(0, raw_months)
                if deadline < today or months_left == 0:
                    status = "overdue"
                else:
                    required_monthly = money(D(remaining) / Decimal(months_left))
                    if required_monthly <= monthly_minimum:
                        status = "on_track"
                    elif required_monthly <= monthly_maximum:
                        status = "depends_on_income"
                    else:
                        status = "unreachable"

        def estimated_months(monthly_amount: Decimal) -> Optional[int]:
            if remaining is None or remaining <= ZERO:
                return 0 if remaining is not None else None
            if monthly_amount <= ZERO:
                return None
            return int(
                (D(remaining) / monthly_amount).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )

        return {
            "name": goal.name,
            "position_type": goal.position_type,
            "percentage": goal.percentage,
            "current": money(current),
            "target": money(target) if target is not None else None,
            "remaining": money(remaining) if remaining is not None else None,
            "months_left": months_left,
            "required_monthly": required_monthly,
            "monthly_minimum": monthly_minimum,
            "monthly_maximum": monthly_maximum,
            "estimated_months_fast": estimated_months(monthly_maximum),
            "estimated_months_conservative": estimated_months(monthly_minimum),
            "status": status,
            "tax_changes_range": bool(capacity["tax_changes_range"]),
        }

    def gift_goal_recommendation(self) -> Dict[str, Decimal | str | bool]:
        """Переводит историю Подарков в рекомендацию будущей Цели."""
        s = self.settings
        forecast = self.estimated_goals_capacity()
        history = money(s.historical_gifts_monthly)
        income = D(forecast["net_income"])
        capacity = D(forecast["capacity"])
        income_share = money(history / income * HUNDRED) if income > ZERO else ZERO
        goals_share = money(history / capacity * HUNDRED) if capacity > ZERO else ZERO
        comfort_limit = money(income * s.gift_guideline_max / HUNDRED)
        recommendation = money(min(history, comfort_limit, capacity)) if capacity > ZERO else ZERO
        if income_share > s.gift_warning_limit:
            status = "above_warning"
        elif income_share > s.gift_guideline_max:
            status = "above_guideline"
        else:
            status = "comfortable"
        return {
            **forecast,
            "historical_monthly": history,
            "income_share": income_share,
            "goals_share": goals_share,
            "recommended_monthly": recommendation,
            "guideline_min": s.gift_guideline_min,
            "guideline_max": s.gift_guideline_max,
            "warning_limit": s.gift_warning_limit,
            "status": status,
        }

    def active_mode(self) -> int:
        """Кубки по общему защитному капиталу; долги остаются жёстким шлюзом."""
        s = self.settings
        has_debts = any(credit.active for credit in s.credits)

        if has_debts and not self.minimum_pillow_is_funded():
            return 1
        if has_debts:
            return 2

        capital = self.protective_capital_balance
        mode = 3
        for reached_mode, target, _name in self.resilience_transition_targets():
            if capital < target:
                break
            mode = reached_mode
        return mode

    def mode_title(self, mode: Optional[int] = None) -> str:
        selected = self.active_mode() if mode is None else mode
        return PROFILE_MODE_TITLES[self.profile_id][selected]

    def mode_display_name(self, mode: Optional[int] = None) -> str:
        selected = self.active_mode() if mode is None else mode
        if selected == self.profile_mode_total:
            return "Максимальный режим"
        return f"Режим {selected}"

    def allocation_mode(self) -> int:
        """
        Возвращает совместимый режим финансовых правил MODE_1–MODE_6.

        Несколько последовательных ступеней циклического маршрута используют
        одинаковые правила распределения, но остаются отдельными достижениями.
        """
        has_debts = any(
            credit.active
            for credit in self.settings.credits
        )

        if has_debts:
            if not self.minimum_pillow_is_funded():
                return MODE_1

            return MODE_2

        if self.profile_id == PROFILE_CYCLIC:
            if self.state.intercontract_reserve < self.intercontract_current_limit:
                return MODE_3
            if not self.force_majeure_pillow_is_funded():
                return MODE_3
            if self.state.pillow_stabilizer < self.settings.stabilizer_life_limit:
                return MODE_4
            if self.state.pillow_stabilizer < self.settings.stabilizer_full_limit:
                return MODE_5
            return MODE_6

        if not self.force_majeure_pillow_is_funded():
            return MODE_3

        if self.profile_id == PROFILE_PIECEWORK:
            if self.state.pillow_stabilizer < self.settings.stabilizer_life_limit:
                return MODE_4
            if self.state.pillow_stabilizer < self.settings.stabilizer_full_limit:
                return MODE_5

        return MODE_6

    def next_mode_candidates(
        self,
        current_mode: int,
    ) -> List[Tuple[int, Decimal]]:
        """
        Возвращает возможные переходы в виде:
        (следующий режим, остаток до перехода).
        """
        s = self.settings
        st = self.state
        candidates = []

        if current_mode == MODE_1:
            remaining = max(
                ZERO,
                s.minimum_reserve_limit
                - self.pillow_total_balance,
            )

            if remaining > ZERO:
                candidates.append((MODE_2, remaining))

        elif current_mode == MODE_2:
            total_debt = sum(
                (
                    credit.principal_balance
                    for credit in s.credits
                    if credit.active
                ),
                ZERO,
            )

            if total_debt > ZERO:
                candidates.append((MODE_3, total_debt))

        elif current_mode == MODE_3:
            if self.profile_id == PROFILE_CYCLIC and (
                st.intercontract_reserve < self.intercontract_current_limit
            ):
                remaining = max(
                    ZERO,
                    self.intercontract_current_limit - st.intercontract_reserve,
                )
            else:
                remaining = max(
                    ZERO,
                    s.force_majeure_limit - self.pillow_total_balance,
                )

            if remaining > ZERO:
                if s.needs_stabilizer:
                    candidates.append((MODE_4, remaining))
                else:
                    candidates.append((MODE_6, remaining))

        elif current_mode == MODE_4:
            remaining = max(
                ZERO,
                s.stabilizer_life_limit
                - st.pillow_stabilizer,
            )

            if remaining > ZERO:
                candidates.append((MODE_5, remaining))

        elif current_mode == MODE_5:
            remaining = max(
                ZERO,
                s.stabilizer_full_limit
                - st.pillow_stabilizer,
            )

            if remaining > ZERO:
                candidates.append((MODE_6, remaining))

        return [
            candidate
            for candidate in candidates
            if candidate[1] > ZERO
        ]

    def nearest_next_mode(
        self,
        current_mode: int,
    ) -> Optional[Tuple[int, Decimal]]:
        candidates = self.next_mode_candidates(
            current_mode
        )

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda item: item[1],
        )

    def transition_message(
        self,
        before: int,
        after: int,
    ) -> Optional[str]:

        if before == after:
            remaining = self.remaining_to_profile_transition()
            if remaining is not None and remaining > ZERO:
                return (
                    f"❌ Перехода нет. "
                    f"Режим: {self.mode_display_name(before)}. "
                    f"До следующего режима "
                    f"осталось {fmt_money(remaining)} ₽."
                )

            return None

        return (
            f"✅ ПЕРЕХОД: "
            f"{self.mode_display_name(before)} → "
            f"{self.mode_display_name(after)}"
        )

    def remaining_to_profile_transition(self) -> Optional[Decimal]:
        """Остаток до следующей профильной ступени."""
        s = self.settings
        mode = self.active_mode()
        if mode == self.profile_mode_total:
            return None
        if mode == 1:
            return max(ZERO, s.minimum_reserve_limit - self.pillow_total_balance)
        if mode == 2:
            return sum((c.principal_balance for c in s.credits if c.active), ZERO)
        next_target = next(
            (target for reached_mode, target, _name in self.resilience_transition_targets() if reached_mode > mode),
            None,
        )
        if next_target is None:
            return None
        return max(ZERO, next_target - self.protective_capital_balance)

    # ========================================================
    # НАПРАВЛЕНИЕ БРАКЕТА A/B
    # ========================================================

    def bracket_up_target(
        self,
        mode: int,
    ) -> str:

        mapping = {
            MODE_1: "МП",
            MODE_2: "Досрочное",
            MODE_3: "МР" if self.settings.needs_intercontract_reserve else "ФМ",
            MODE_4: "СтабД",
            MODE_5: "СтабД",
            MODE_6: "Инвест",
        }

        return mapping[mode]

    def remaining_to_mode_transition(
        self,
        mode: int,
    ) -> Optional[Decimal]:
        """Сумма в направлении роста до следующего режима."""
        s = self.settings
        st = self.state

        if mode == MODE_1:
            return max(
                ZERO,
                s.minimum_reserve_limit - self.pillow_total_balance,
            )

        if mode == MODE_2:
            return sum(
                (
                    credit.principal_balance
                    for credit in s.credits
                    if credit.active
                ),
                ZERO,
            )

        if mode == MODE_3:
            if self.settings.needs_intercontract_reserve and self.state.intercontract_reserve < self.intercontract_current_limit:
                return max(ZERO, self.intercontract_current_limit - self.state.intercontract_reserve)
            return max(
                ZERO,
                s.force_majeure_limit - self.pillow_total_balance,
            )

        if mode == MODE_4:
            return max(
                ZERO,
                s.stabilizer_life_limit - st.pillow_stabilizer,
            )

        if mode == MODE_5:
            return max(
                ZERO,
                s.stabilizer_full_limit - st.pillow_stabilizer,
            )

        return None

    def amount_until_mode_transition(
        self,
        amount: Decimal,
        mode: int,
        transition_share: Decimal,
    ) -> Decimal:
        """
        Ограничивает обрабатываемую базу ближайшей границей режима.

        transition_share — доля базы, которая продвигает пользователя
        к следующему режиму. Остаток после границы должен быть повторно
        обработан на том же этапе уже по правилам нового режима.
        """
        amount = D(amount)
        transition_share = D(transition_share)

        remaining = self.remaining_to_mode_transition(mode)

        if remaining is None or transition_share <= ZERO:
            return amount

        required_base = remaining / transition_share

        return min(amount, required_base)

    def run_stage_with_mode_transitions(
        self,
        amount: Decimal,
        stage,
    ) -> Decimal:
        """Повторяет один и тот же этап после каждой смены режима."""
        amount = D(amount)

        while amount > ZERO:
            previous = amount
            amount = D(stage(amount, self.allocation_mode()))

            if amount >= previous:
                break

        return amount

    # ========================================================
    # ЭТАП A
    # ========================================================

    def stage_a(
        self,
        amount: Decimal,
        mode: int,
        steps: List[str],
        allocations: Dict[str, Decimal],
    ) -> Decimal:
        s = self.settings
        st = self.state

        target = s.total_critical_life
        accumulated_kzh = (
            st.life_balance
            + st.accumulated_minimum_payments
        )

        if accumulated_kzh >= target:
            return amount

        missing = target - accumulated_kzh
        bracket = s.bracket_a
        required_base = (
            missing
            / (ONE - bracket / HUNDRED)
        )
        part_a = min(amount, required_base)

        if mode in {MODE_1, MODE_2, MODE_3, MODE_4, MODE_5}:
            part_a = self.amount_until_mode_transition(
                part_a,
                mode,
                bracket / HUNDRED,
            )

        if part_a <= ZERO:
            return amount

        up_calculated = (
            part_a
            * bracket
            / HUNDRED
        )
        up_target = self.bracket_up_target(mode)
        final_overflow = ZERO

        if up_target in {"МП", "МР", "ФМ", "СтабД"}:
            final_overflow = self.allocate_protection_waterfall(
                up_calculated, up_target, allocations
            )

        elif up_target == "Инвест":
            st.investments += up_calculated
            allocations["Инвестиции"] += up_calculated

        elif up_target == "Досрочное":
            applied = self.apply_early_repayment(
                up_calculated,
                steps,
            )
            allocations["Досрочное"] += applied
            final_overflow = up_calculated - applied

        else:
            raise ValueError(
                f"Неизвестное направление Бракет_A: {up_target}"
            )

        life_part = part_a - up_calculated
        self._allocate_to_life(
            life_part,
            allocations,
        )

        steps.append(
            f"""ЭТАП A — Критический минимум
Недостаёт: {missing}
Необходимая база: {required_base}
Часть A: {part_a}
Бракет A ({bracket}%): {up_calculated}
Направление вверх: {up_target}
В Критический минимум: {life_part}
Переполнение: {final_overflow}"""
        )

        return (
            amount
            - part_a
            + final_overflow
        )

    def _allocate_to_life(
        self,
        amount: Decimal,
        allocations: Dict[str, Decimal],
    ):
        """
        Распределяет часть Этапа A между категориями КЖ.
        Минимальные платежи учитываются отдельно от Баланса жизни.
        """
        if amount <= ZERO:
            return

        shares = self.life_category_shares()
        category_names = list(shares.keys())
        distributed = ZERO
        life_added = ZERO

        for index, name in enumerate(category_names):
            share = shares[name]

            if index == len(category_names) - 1:
                part = amount - distributed
            else:
                part = amount * share

            distributed += part

            if name == "Мин. платеж":
                self.state.accumulated_minimum_payments += part
                allocations["Мин. платеж"] = (
                    allocations.get("Мин. платеж", ZERO)
                    + part
                )
                continue

            self.state.period_life_topups[name] = (
                self.state.period_life_topups.get(name, ZERO)
                + part
            )

            allocations[f"КЖ:{name}"] = (
                allocations.get(f"КЖ:{name}", ZERO)
                + part
            )

            life_added += part

        self.state.life_balance += life_added

    def stage_b(
        self,
        amount: Decimal,
        mode: int,
        steps: List[str],
        allocations: Dict[str, Decimal],
    ) -> Decimal:
        s = self.settings
        st = self.state

        if not (
            s.critical_life
            <= st.life_balance
            < s.household_life
        ):
            return amount

        missing = s.household_life - st.life_balance
        bracket = s.bracket_b
        required_base = (
            missing
            / (ONE - bracket / HUNDRED)
        )
        part_b = min(amount, required_base)

        if mode in {MODE_1, MODE_2, MODE_3, MODE_4, MODE_5}:
            part_b = self.amount_until_mode_transition(
                part_b,
                mode,
                bracket / HUNDRED,
            )

        if part_b <= ZERO:
            return amount

        up_calculated = (
            part_b
            * bracket
            / HUNDRED
        )
        up_target = self.bracket_up_target(mode)
        final_overflow = ZERO

        if up_target in {"МП", "МР", "ФМ", "СтабД"}:
            final_overflow = self.allocate_protection_waterfall(
                up_calculated, up_target, allocations
            )

        elif up_target == "Инвест":
            st.investments += up_calculated
            allocations["Инвестиции"] += up_calculated

        elif up_target == "Досрочное":
            applied = self.apply_early_repayment(
                up_calculated,
                steps,
            )
            allocations["Досрочное"] += applied
            final_overflow = up_calculated - applied

        else:
            raise ValueError(
                f"Неизвестное направление Бракет_B: {up_target}"
            )

        reserve_part = part_b - up_calculated
        st.life_balance += reserve_part
        reserve_total = s.household_reserve
        detailed = ZERO
        if reserve_total > ZERO:
            for name, target in s.household_reserve_categories.items():
                part = reserve_part * target / reserve_total
                detailed += part
                allocations[f"БР:{name}"] = allocations.get(f"БР:{name}", ZERO) + part
        allocations["Бытовой резерв"] = (
            allocations.get("Бытовой резерв", ZERO)
            + reserve_part - detailed
        )

        steps.append(
            f"""ЭТАП B — бытовой резерв
Недостаёт: {missing}
Необходимая база: {required_base}
Часть B: {part_b}
Бракет B ({bracket}%): {up_calculated}
Направление вверх: {up_target}
В бытовой резерв: {reserve_part}
Переполнение: {final_overflow}"""
        )

        return (
            amount
            - part_b
            + final_overflow
        )

    def stage_c(
        self,
        amount: Decimal,
        mode: int,
        steps: List[str],
        allocations: Dict[str, Decimal],
    ) -> Decimal:

        if amount <= ZERO:
            return ZERO

        if self.state.life_balance < (
            self.settings.household_life
        ):
            return amount

        s = self.settings
        st = self.state

        if mode == MODE_1:
            part = self.amount_until_mode_transition(
                amount,
                mode,
                ONE,
            )
            overflow = self.allocate_protection_waterfall(part, "МП", allocations)

            return amount - part + overflow

        if mode == MODE_2:
            part = self.amount_until_mode_transition(
                amount,
                mode,
                ONE,
            )
            applied = self.apply_early_repayment(
                part,
                steps,
            )

            allocations[
                "Досрочное"
            ] += applied

            return amount - applied

        if mode == MODE_3:
            filling_salary_fund = (
                self.profile_id == PROFILE_CYCLIC
                and st.intercontract_reserve < self.intercontract_current_limit
            )
            protection_share = (
                ONE
                if filling_salary_fund or s.protective_stage_c_strategy == "protection"
                else ONE - s.protective_stage_c_goals_share / HUNDRED
            )
            part = self.amount_until_mode_transition(
                amount,
                mode,
                protection_share,
            )
            protection_part = part * protection_share
            goal_part = part - protection_part
            overflow = self.allocate_protection_waterfall(
                protection_part,
                "МР" if s.needs_intercontract_reserve else "ФМ",
                allocations,
            )
            self._allocate_goals(goal_part, allocations)

            return amount - part + overflow

        if mode == MODE_4:
            protection_share = (
                ONE
                if s.protective_stage_c_strategy == "protection"
                else ONE - s.protective_stage_c_goals_share / HUNDRED
            )
            part = self.amount_until_mode_transition(
                amount,
                mode,
                protection_share,
            )
            protection_part = part * protection_share
            goal_part = part - protection_part
            overflow = self.allocate_protection_waterfall(
                protection_part, "СтабД", allocations
            )
            self._allocate_goals(goal_part, allocations)

            return amount - part + overflow

        if mode == MODE_5:
            pillow_share = (
                (ONE - s.bracket_c / HUNDRED)
                * s.pillow_share_c
                / HUNDRED
            )
            part = self.amount_until_mode_transition(
                amount,
                mode,
                pillow_share,
            )
            investment_part = (
                part
                * s.bracket_c
                / HUNDRED
            )

            remaining = (
                part
                - investment_part
            )

            st.investments += investment_part

            allocations[
                "Инвестиции"
            ] += investment_part

            if s.protective_stage_c_strategy == "protection":
                goal_part = ZERO
                pillow_part = remaining
            else:
                goal_part = remaining / Decimal("2")
                pillow_part = remaining - goal_part

            self._allocate_goals(
                goal_part,
                allocations,
            )

            pillow_overflow = self.allocate_protection_waterfall(
                pillow_part, "СтабД", allocations
            )

            return amount - part + pillow_overflow

        if mode == MODE_6:
            investment_part = (
                amount
                * s.bracket_c
                / HUNDRED
            )

            remaining = (
                amount
                - investment_part
            )

            st.investments += investment_part

            allocations[
                "Инвестиции"
            ] += investment_part

            self._allocate_goals(
                remaining,
                allocations,
            )

            return ZERO

        raise ValueError(
            f"Неизвестный режим: {mode}"
        )

    # ========================================================
    # ЦЕЛИ
    # ========================================================

    def _allocate_goals(
        self,
        amount: Decimal,
        allocations: Dict[str, Decimal],
    ):

        if amount <= ZERO:
            return

        goals = self.settings.active_goals

        if not goals:
            allocations[
                "Цели:ЦЕЛИ (всего)"
            ] = (
                allocations.get(
                    "Цели:ЦЕЛИ (всего)",
                    ZERO,
                )
                + amount
            )
            return

        split = self.split_goal_amount(amount)

        for goal in goals:
            part = split[goal.name]

            self.state.goal_balances[
                goal.name
            ] = (
                self.state.goal_balances.get(
                    goal.name,
                    ZERO,
                )
                + part
            )

            key = f"Цели:{goal.name}"

            allocations[key] = (
                allocations.get(
                    key,
                    ZERO,
                )
                + part
            )

    def split_goal_amount(self, amount: Decimal) -> Dict[str, Decimal]:
        """Делит сумму между активными позициями без потери копеек."""
        amount = max(ZERO, D(amount))
        goals = self.settings.active_goals
        if not goals or amount <= ZERO:
            return {}
        result: Dict[str, Decimal] = {}
        distributed = ZERO
        for index, goal in enumerate(goals):
            part = (
                amount - distributed
                if index == len(goals) - 1
                else amount * goal.percentage / HUNDRED
            )
            result[goal.name] = result.get(goal.name, ZERO) + part
            distributed += part
        return result

    # ========================================================
    # ДОСРОЧНОЕ ПОГАШЕНИЕ
    # ========================================================

    def _ordered_credits(self) -> List[Credit]:

        active = [
            credit
            for credit in self.settings.credits
            if credit.active
        ]

        if self.settings.debt_strategy == "Лавина":
            return sorted(
                active,
                key=lambda credit: (
                    -credit.annual_rate,
                    credit.name,
                ),
            )

        if self.settings.debt_strategy == "Снежный ком":
            return sorted(
                active,
                key=lambda credit: (
                    credit.principal_balance,
                    credit.name,
                ),
            )

        # Ручной выбор.
        # В базовой версии сохраняем порядок реестра.
        return active

    def apply_early_repayment(
        self,
        amount: Decimal,
        steps: List[str],
    ) -> Decimal:

        amount = D(amount)

        if amount <= ZERO:
            return ZERO

        remaining = amount
        applied_total = ZERO

        credits = self._ordered_credits()

        for credit in credits:

            if remaining <= ZERO:
                break

            if not credit.active:
                continue

            # В спецификации досрочное погашение уменьшает
            # основной долг.
            applied = min(
                remaining,
                credit.principal_balance,
            )

            credit.principal_balance -= applied

            if credit.principal_balance <= ZERO:
                credit.principal_balance = ZERO
                credit.status = "Погашен"

            applied_total += applied
            remaining -= applied

            steps.append(
                f"Досрочное погашение: "
                f"{credit.name} = {applied}"
            )

        self.state.early_repayment += applied_total

        return applied_total

    # ========================================================
    # БРАКЕТ D/E — СВЕРХДОХОД
    # ========================================================

    def super_income(
        self,
        amount: Decimal,
        mode: int,
        allocations: Dict[str, Decimal],
    ) -> Decimal:

        if amount <= ZERO:
            return ZERO

        s = self.settings

        if mode == MODE_1:
            part = self.amount_until_mode_transition(
                amount,
                mode,
                ONE,
            )
            overflow = self.allocate_protection_waterfall(part, "МП", allocations)

            return amount - part + overflow

        if mode == MODE_2:
            part = self.amount_until_mode_transition(
                amount,
                mode,
                ONE,
            )
            applied = self.apply_early_repayment(
                part,
                [],
            )

            allocations[
                "Досрочное"
            ] += applied

            return amount - applied

        if mode == MODE_3:
            part = self.amount_until_mode_transition(
                amount,
                mode,
                ONE,
            )
            overflow = self.allocate_protection_waterfall(
                part,
                "МР" if s.needs_intercontract_reserve else "ФМ",
                allocations,
            )

            return amount - part + overflow

        if mode == MODE_4:
            part = self.amount_until_mode_transition(
                amount,
                mode,
                ONE,
            )
            overflow = self.allocate_protection_waterfall(part, "СтабД", allocations)

            return amount - part + overflow

        if mode == MODE_5:
            pillow_share = (
                ONE - s.bracket_d / HUNDRED
            )
            part = self.amount_until_mode_transition(
                amount,
                mode,
                pillow_share,
            )
            invest = (
                part
                * s.bracket_d
                / HUNDRED
            )

            remainder = (
                part
                - invest
            )

            self.state.investments += invest

            allocations[
                "Инвестиции"
            ] += invest

            overflow = self.allocate_protection_waterfall(
                remainder, "СтабД", allocations
            )

            return amount - part + overflow

        if mode == MODE_6:
            invest = (
                amount
                * s.bracket_e
                / HUNDRED
            )

            goals = (
                amount
                - invest
            )

            self.state.investments += invest

            allocations[
                "Инвестиции"
            ] += invest

            self._allocate_goals(
                goals,
                allocations,
            )

            return ZERO

        raise ValueError(
            f"Неизвестный режим: {mode}"
        )

    # ========================================================
    # ПРОВЕРКА КЖ
    # ========================================================

    def check_life_categories(self) -> Decimal:
        """
        Проверяет сумму накоплений КЖ за текущий период.
        """

        return sum(
            self.state.period_life_topups.values(),
            ZERO,
        )

    # ========================================================
    # ГЛОБАЛЬНАЯ ПРОВЕРКА
    # ========================================================

    def global_check(
        self,
        income: Decimal,
        tax: Decimal,
        allocations: Dict[str, Decimal],
    ) -> Dict[str, object]:

        income = D(income)
        tax = D(tax)

        distributed = sum(
            allocations.values(),
            ZERO,
        )

        total = tax + distributed

        difference = (
            income - total
        )

        return {
            "income": income,
            "tax": tax,
            "distributed_after_tax": distributed,
            "total": total,
            "difference": difference,
            "ok": abs(difference) <= CENT,
        }

    # ========================================================
    # ОБРАБОТКА ПОСТУПЛЕНИЯ
    # ========================================================

    def process_income(
        self,
        income: Decimal,
        income_type: str,
        income_date: Optional[date] = None,
        reset_period: bool = False,
        tax_override: Optional[Decimal] = None,
    ) -> DistributionResult:

        income = D(income)

        if income <= ZERO:
            raise ValueError(
                "Сумма поступления должна быть больше 0."
            )

        if reset_period:
            self.state.reset_period()

            self.state.period_started_at = (
                datetime.now().isoformat()
            )

        self._ensure_life_categories()

        mode_before = self.active_mode()
        pillow_before = self.state.pillow_minimum + self.state.pillow_force_majeure
        fund_salary_before = self.state.intercontract_reserve
        stabilizer_before = self.state.pillow_stabilizer

        if tax_override is None:

            tax = self.calculate_tax(
                income,
                income_type,
            )

        else:

            tax = D(
                tax_override
            )

            if tax < ZERO:
                raise ValueError(
                    "Налог не может быть отрицательным."
                )

            if tax > income:
                raise ValueError(
                    "Налог не может быть больше "
                    "суммы поступления."
                )

        amount = (
            income
            - tax
        )

        # Обычная база определяется по совокупному валовому доходу периода.
        # Налог делится пропорционально, поэтому части после налога всегда
        # сходятся с фактической суммой к распределению.
        if self.settings.average_income > ZERO:
            if self.settings.income_rhythm == "cyclic":
                regular_gross_capacity = max(
                    ZERO,
                    self.settings.cycle_regular_income_limit - self.state.cycle_income,
                )
            else:
                regular_gross_capacity = max(
                    ZERO,
                    self.settings.average_income - self.state.period_income,
                )
            regular_gross = min(income, regular_gross_capacity)
            regular_net = amount * regular_gross / income
        else:
            regular_net = amount
        super_net = amount - regular_net

        allocations: Dict[str, Decimal] = {
            "Подушка": ZERO,
            "Фонд Зарплаты": ZERO,
            "Стабилизатор дохода": ZERO,
            "Инвестиции": ZERO,
            "Досрочное": ZERO,
            "Бытовой резерв": ZERO,
        }

        steps: List[str] = []

        steps.append(
            f"Доход: {income}"
        )

        steps.append(
            f"Налог: {tax}"
        )

        steps.append(
            f"К распределению: {amount}"
        )

        # --------------------------------------------
        # ЭТАП A
        # --------------------------------------------

        before_required_stages = amount
        amount = self.run_stage_with_mode_transitions(
            amount,
            lambda stage_amount, mode: self.stage_a(
                stage_amount,
                mode,
                steps,
                allocations,
            ),
        )

        # --------------------------------------------
        # После A проверяем изменение режима.
        # --------------------------------------------

        # --------------------------------------------
        # ЭТАП B
        # --------------------------------------------

        amount = self.run_stage_with_mode_transitions(
            amount,
            lambda stage_amount, mode: self.stage_b(
                stage_amount,
                mode,
                steps,
                allocations,
            ),
        )

        # Сначала обеспечиваются КМ и БР текущего периода, затем заранее
        # резервируются обязательства, продолжающиеся в рабочей части.
        if self.settings.income_rhythm == "cyclic":
            obligation_missing = max(
                ZERO,
                self.settings.contract_obligations_total
                - self.state.contract_obligations_reserve,
            )
            obligation_part = min(amount, obligation_missing)
            if obligation_part > ZERO:
                for name, share in self.settings.split_contract_obligation_amount(obligation_part).items():
                    envelope = self.settings.contract_obligation_envelope(name)
                    allocations[f"Рабочие обязательства:{envelope}:{name}"] = share
                self.state.contract_obligations_reserve += obligation_part
                amount -= obligation_part
                steps.append(f"Обязательства рабочей части: {obligation_part}")

        # Этапы A/B в первую очередь расходуют обычную месячную базу.
        # Если её недостаточно, обязательная жизнь вправе использовать и
        # сверхдоход: инвестиции не могут быть важнее текущих обязательств.
        required_consumed = before_required_stages - amount
        regular_remaining = max(ZERO, regular_net - required_consumed)
        super_remaining = amount - regular_remaining

        # --------------------------------------------
        # ЭТАП C
        # --------------------------------------------

        regular_remaining = self.run_stage_with_mode_transitions(
            regular_remaining,
            lambda stage_amount, mode: self.stage_c(
                stage_amount,
                mode,
                steps,
                allocations,
            ),
        )

        # --------------------------------------------
        # СВЕРХДОХОД
        # --------------------------------------------

        # Защитный остаток обычной части (например, после переполнения слоя)
        # также считается сверхдоходом, чтобы ни одна копейка не потерялась.
        super_remaining += regular_remaining
        if super_remaining > ZERO:
            super_remaining = self.run_stage_with_mode_transitions(
                super_remaining,
                lambda stage_amount, mode: self.super_income(
                    stage_amount,
                    mode,
                    allocations,
                ),
            )

        # --------------------------------------------
        # Режим после всего распределения
        # --------------------------------------------

        mode_after = self.active_mode()

        # Пользовательские сущности не смешиваются, даже если внутри одного
        # поступления водопад последовательно заполнил несколько защитных слоёв.
        allocations["Подушка"] = (
            self.state.pillow_minimum + self.state.pillow_force_majeure - pillow_before
        )
        allocations["Фонд Зарплаты"] = self.state.intercontract_reserve - fund_salary_before
        allocations["Стабилизатор дохода"] = self.state.pillow_stabilizer - stabilizer_before

        # --------------------------------------------
        # Периодическая аналитика
        # --------------------------------------------

        self.state.period_income += income
        if self.settings.income_rhythm == "cyclic":
            self.state.cycle_income += income
        self.state.period_tax += tax

        # --------------------------------------------
        # Глобальная проверка
        # --------------------------------------------

        checks = self.global_check(
            income,
            tax,
            allocations,
        )

        # --------------------------------------------
        # Критическая корректировка.
        #
        # Разница добавляется в Зарплату только
        # если она действительно существует.
        # --------------------------------------------

        if not checks["ok"]:

            difference = D(
                checks["difference"]
            )

            if (
                abs(difference) > CENT
                and "КЖ:Зарплата" in allocations
            ):
                allocations[
                    "КЖ:Зарплата"
                ] += difference

                self.state.period_life_topups[
                    "Зарплата"
                ] += difference

                checks = self.global_check(
                    income,
                    tax,
                    allocations,
                )

        transition = self.transition_message(
            mode_before,
            mode_after,
        )

        # --------------------------------------------
        # НАКОПИТЕЛЬНАЯ АНАЛИТИКА ПЕРИОДА
        # --------------------------------------------

        # Совместимость с профилями,
        # сохранёнными до появления этого счётчика.
        if not hasattr(
            self.state,
            "period_allocations",
        ):
            self.state.period_allocations = {}

        for key, value in allocations.items():

            self.state.period_allocations[
                key
            ] = (
                self.state.period_allocations.get(
                    key,
                    ZERO,
                )
                + D(value)
            )
            
        # --------------------------------------------
        # Журнал
        # --------------------------------------------

        planned_tax_details: Dict[str, Decimal] = {}
        planned_tax_allocation = D(allocations.get("КЖ:Налоги", ZERO))
        planned_tax_target = sum(self.settings.planned_taxes.values(), ZERO)
        if planned_tax_allocation > ZERO and planned_tax_target > ZERO:
            distributed_tax = ZERO
            tax_items = list(self.settings.planned_taxes.items())
            for index, (name, target) in enumerate(tax_items):
                share = (
                    planned_tax_allocation - distributed_tax
                    if index == len(tax_items) - 1
                    else money(
                        planned_tax_allocation * target / planned_tax_target
                    )
                )
                planned_tax_details[name] = share
                distributed_tax += share

        operation = {
            "type": "income_distribution",
            "date": (
                income_date.isoformat()
                if income_date
                else date.today().isoformat()
            ),
            "income_type": income_type,
            "income": income,
            "tax": tax,
            "tax_overridden": (
                tax_override is not None
            ),
            "regular_income_part": regular_net,
            "super_income_part": super_net,
            "planned_tax_details": planned_tax_details,
            "allocations": dict(allocations),
            "mode_before": mode_before,
            "mode_after": mode_after,
            "checks": checks,
        }

        self.state.operation_log.append(
            operation
        )

        self.state.distribution_history.append(
            operation
        )

        self.state.reconcile_fund_salary_currencies()

        return DistributionResult(
            income=income,
            tax=tax,
            amount_to_distribute=income - tax,
            mode_before=mode_before,
            mode_after=mode_after,
            allocations=allocations,
            steps=steps,
            checks=checks,
            transition_message=transition,
            regular_income_part=regular_net,
            super_income_part=super_net,
        )

    # ========================================================
    # ОБЯЗАТЕЛЬНЫЙ ПЛАТЁЖ
    # ========================================================

    def process_minimum_payment(
        self,
        credit_name: str,
    ) -> dict:

        credit = None

        for item in self.settings.credits:
            if item.name.lower() == credit_name.lower():
                credit = item
                break

        if credit is None:
            raise ValueError(
                f"Кредит '{credit_name}' не найден."
            )

        result = (
            credit.process_minimum_payment()
        )

        # После погашения последнего кредита
        # новые производные переменные рассчитываются
        # автоматически через свойства settings.
        #
        # Накопления НЕ уменьшаются.

        self.state.operation_log.append({
            "type": "minimum_payment",
            "date": date.today().isoformat(),
            "credit": credit.name,
            "result": result,
        })

        return result

    # ========================================================
    # СБРОС ПЕРИОДА
    # ========================================================

    def reset_period(self):
        self.state.reset_period()

        return {
            "status": "ok",
            "message": (
                "Расчётный период сброшен. "
                "Баланс жизни и накопление минимальных "
                "платежей обнулены. Подушка сохранена."
            ),
        }

    # ========================================================
    # СОСТОЯНИЕ
    # ========================================================

    def get_state_snapshot(self) -> dict:

        priority = self.current_protection_priority()

        return {
            "mode": self.active_mode(),
            "mode_name": self.mode_display_name(),
            "mode_title": self.mode_title(),
            "mode_total": self.profile_mode_total,
            "profile_type": self.profile_id,

            "financial_resilience": {
                "capital": self.protective_capital_balance,
                "target": self.protective_capital_target,
                "current_priority": priority,
                "thresholds": [
                    {"mode": mode, "target": target, "name": name}
                    for mode, target, name in self.resilience_transition_targets()
                ],
            },

            "goal_forecast": self.estimated_goals_capacity(),
            "gift_goal_recommendation": self.gift_goal_recommendation(),

            "critical_life":
                self.settings.critical_life,

            "household_reserve":
                self.settings.household_reserve,

            "household_life":
                self.settings.household_life,

            "total_critical_life":
                self.settings.total_critical_life,

            "life_balance":
                self.state.life_balance,

            "cycle_income": self.state.cycle_income,
            "cycle_regular_income_limit": self.settings.cycle_regular_income_limit,

            "pillow": {
                "total":
                    self.state.pillow_balance,
                "МП":
                    self.state.pillow_minimum,
                "ФМ":
                    self.state.pillow_force_majeure,
            },

            "stabilizer": {
                "balance": self.state.pillow_stabilizer,
                "critical_target": self.settings.stabilizer_life_limit,
                "full_target": self.settings.stabilizer_full_limit,
            },

            "contract_obligations_reserve": {
                "balance": self.state.contract_obligations_reserve,
                "target": self.settings.contract_obligations_total,
            },

            "intercontract_reserve": {
                "balance": self.state.intercontract_reserve,
                "critical_target": self.settings.intercontract_life_limit,
                "full_target": self.settings.intercontract_full_limit,
                "current_target": self.intercontract_current_limit,
                "current_critical_target": self.intercontract_current_life_limit,
                "months_remaining": self.state.intercontract_months_remaining,
            },

            "investments":
                self.state.investments,

            "early_repayment":
                self.state.early_repayment,

            "goals":
                dict(self.state.goal_balances),

            "credits": [
                {
                    "name": credit.name,
                    "principal_balance":
                        credit.principal_balance,
                    "full_repayment_amount":
                        credit.full_repayment_amount,
                    "annual_rate":
                        credit.annual_rate,
                    "minimum_payment":
                        credit.minimum_payment,
                    "status":
                        credit.status,
                }
                for credit in self.settings.credits
            ],
        }

    # ========================================================
    # БЛИЖАЙШИЙ ПЕРЕХОД
    # ========================================================

    def next_mode_info(self) -> Optional[dict]:

        current = self.active_mode()

        remaining = self.remaining_to_profile_transition()
        if remaining is None:
            return None
        mode = current + 1

        return {
            "current_mode": current,
            "next_mode": mode,
            "remaining": remaining,
            "current_name": self.mode_display_name(current),
            "next_name": self.mode_display_name(mode),
        }

    # ========================================================
    # ПРОВЕРКА ЧЕК-ЛИСТА
    # ========================================================

    def checklist(self) -> Dict[str, bool]:

        s = self.settings
        st = self.state

        pillow_ok = (
            st.pillow_minimum
            <= s.minimum_reserve_limit
            + CENT
            and st.intercontract_reserve
            <= max(s.intercontract_full_limit, self.intercontract_current_limit)
            + CENT
            and st.pillow_force_majeure
            <= s.force_majeure_limit
            + CENT
            and st.pillow_stabilizer
            <= s.stabilizer_full_limit
            + CENT
        )

        debts_ok = all(
            credit.principal_balance >= ZERO
            for credit in s.credits
        )

        employment_mode_ok = True

        if s.profile_type == PROFILE_STABLE:
            employment_mode_ok = (
                self.active_mode()
                <= self.profile_mode_total
            )

        goals_ok = (
            not s.goals
            or abs(
                s.total_goals_percentage
                - HUNDRED
            ) <= Decimal("0.0001")
        )

        life_targets = (
            self.life_category_targets()
        )

        life_sum_ok = (
            abs(
                sum(
                    life_targets.values(),
                    ZERO,
                )
                - s.total_critical_life
            )
            <= CENT
        )

        return {
            "pillow_limits": pillow_ok,
            "debts_non_negative": debts_ok,
            "employment_mode": employment_mode_ok,
            "goals_100_percent": goals_ok,
            "life_categories_sum": life_sum_ok,
            "household_life_not_below_critical":
                s.household_life >= s.critical_life,
        }
