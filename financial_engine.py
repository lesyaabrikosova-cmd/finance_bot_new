from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, getcontext
from math import log
from typing import Dict, List, Optional, Tuple
from datetime import date, datetime


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


# ============================================================
# КОНСТАНТЫ РЕЖИМОВ
# ============================================================

MODE_1 = 1
MODE_2 = 2
MODE_3 = 3
MODE_4 = 4
MODE_5 = 5
MODE_6 = 6


MODE_NAMES = {
    MODE_1: "🟤 1",
    MODE_2: "🔴 2",
    MODE_3: "🟠 3",
    MODE_4: "🟣 4",
    MODE_5: "🔵 5",
    MODE_6: "🟢 6",
}


MODE_TITLES = {
    MODE_1: "Говно-жопа, авось пронесёт",
    MODE_2: "Ланистеры всегда платят свои долги",
    MODE_3: "Подготовка к апокалипсису",
    MODE_4: "Хорошо, но недостаточно",
    MODE_5: "Вижу цель, не вижу препятствий",
    MODE_6: "Бронепоезд мчится в светлое будущее",
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
# ЦЕЛЬ
# ============================================================

@dataclass
class Goal:
    name: str
    percentage: Decimal
    balance: Decimal = ZERO

    def __post_init__(self):
        self.percentage = D(self.percentage)
        self.balance = D(self.balance)


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

    # ----------------------------
    # Налог
    # ----------------------------

    tax_rate: Decimal = Decimal("0")
    taxable_income_types: List[str] = field(default_factory=list)

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

    # ----------------------------
    # Категории КЖ
    #
    # Здесь НЕ хранится "Зарплата".
    # Она рассчитывается автоматически.
    # ----------------------------

    life_categories: Dict[str, Decimal] = field(default_factory=dict)

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
        self.critical_life = D(self.critical_life)
        self.household_reserve = D(self.household_reserve)
        self.average_income = D(self.average_income)

        self.tax_rate = D(self.tax_rate)

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

        self.life_categories = {
            name: D(amount)
            for name, amount in self.life_categories.items()
        }

    # ========================================================
    # ПРОИЗВОДНЫЕ ПЕРЕМЕННЫЕ
    # ========================================================

    @property
    def household_life(self) -> Decimal:
        """
        УЖ = КЖ + БР
        """
        return self.critical_life + self.household_reserve

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
        return self.critical_life

    @property
    def stabilizer_full_limit(self) -> Decimal:
        """
        СтабД-Полный = УЖ.
        """
        return self.household_life

    @property
    def total_goals_percentage(self) -> Decimal:
        return sum(
            (goal.percentage for goal in self.goals),
            ZERO,
        )

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

        if self.average_income < ZERO:
            errors.append(
                "Среднемесячный доход не может быть отрицательным."
            )

        if self.tax_rate < ZERO or self.tax_rate > HUNDRED:
            errors.append(
                "Ставка налога должна быть от 0 до 100%."
            )

        # Критическое правило:
        # УЖ = КЖ + БР и поэтому УЖ не может быть меньше КЖ.
        if self.household_life < self.critical_life:
            errors.append(
                "Устойчивая жизнь не может быть меньше "
                "Критического минимума."
            )

        if (
            self.goals
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

        if self.employment_type not in {
            "Фрилансер",
            "Наёмный",
        }:
            errors.append(
                "Форма занятости должна быть "
                "'Фрилансер' или 'Наёмный'."
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

    def __post_init__(self):
        self.life_balance = D(self.life_balance)
        self.accumulated_minimum_payments = D(
            self.accumulated_minimum_payments
        )

        self.pillow_minimum = D(self.pillow_minimum)
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

        self.period_income = D(self.period_income)
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

    @property
    def pillow_balance(self) -> Decimal:
        """
        Единый баланс Подушки.

        Внутри алгоритма слои учитываются отдельно,
        но пользовательский баланс Подушки является
        их суммой.
        """
        return (
            self.pillow_minimum
            + self.pillow_force_majeure
            + self.pillow_stabilizer
        )

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
                    f"- {error}"
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

        if income_type not in (
            self.settings.taxable_income_types
        ):
            return ZERO

        return money(
            income
            * self.settings.tax_rate
            / HUNDRED
        )

    # ========================================================
    # БАЛАНС ПОДУШКИ
    # ========================================================

    def pillow_layer_limit(
        self,
        layer: str,
    ) -> Decimal:

        if layer == "МП":
            return self.settings.minimum_reserve_limit

        if layer == "ФМ":
            return self.settings.force_majeure_limit

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
        Денежная проводка в конкретный слой Подушки.
        Все фактически зачисляемые суммы фиксируются до копейки.
        """
        amount = money(
            D(amount)
        )

        if amount <= ZERO:
            return ZERO

        limit = money(
            self.pillow_layer_limit(layer)
        )

        current = money(
            self.pillow_layer_balance(layer)
        )

        free_space = max(
            ZERO,
            money(limit - current),
        )

        actual = min(
            amount,
            free_space,
        )

        overflow = money(
            amount - actual
        )

        if layer == "МП":
            self.state.pillow_minimum = money(
                current + actual
            )

        elif layer == "ФМ":
            self.state.pillow_force_majeure = money(
                current + actual
            )

        elif layer == "СтабД":
            self.state.pillow_stabilizer = money(
                current + actual
            )

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
        Водопад:

        МП → ФМ → СтабД

        Возвращает остаток, если вся Подушка заполнена.
        """

        amount = money(
            D(amount)
        )

        if amount <= ZERO:
            return ZERO

        layers = ["МП", "ФМ", "СтабД"]

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

    # ========================================================
    # ОПРЕДЕЛЕНИЕ РЕЖИМА
    # ========================================================

    def active_mode(self) -> int:
        """
        Определяет финансовый режим пользователя.

        Режим 1 зависит от заполненности минимального слоя
        Подушки, а не от денег, зарезервированных на очередной
        платёж банку.
        """
        has_debts = any(
            credit.active
            for credit in self.settings.credits
        )

        if has_debts:
            if (
                money(self.state.pillow_minimum)
                < money(self.settings.minimum_reserve_limit)
            ):
                return MODE_1

            return MODE_2

        if (
            money(self.state.pillow_force_majeure)
            < money(self.settings.force_majeure_limit)
        ):
            return MODE_3

        if self.settings.employment_type == "Наёмный":
            return MODE_6

        if (
            money(self.state.pillow_stabilizer)
            < money(self.settings.stabilizer_life_limit)
        ):
            return MODE_4

        if (
            money(self.state.pillow_stabilizer)
            < money(self.settings.stabilizer_full_limit)
        ):
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
                money(
                    s.minimum_reserve_limit
                    - st.pillow_minimum
                ),
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

            total_debt = money(
                total_debt
            )

            if total_debt > ZERO:
                candidates.append((MODE_3, total_debt))

        elif current_mode == MODE_3:
            remaining = max(
                ZERO,
                money(
                    s.force_majeure_limit
                    - st.pillow_force_majeure
                ),
            )

            if remaining > ZERO:
                if s.employment_type == "Фрилансер":
                    candidates.append((MODE_4, remaining))
                else:
                    candidates.append((MODE_6, remaining))

        elif current_mode == MODE_4:
            remaining = max(
                ZERO,
                money(
                    s.stabilizer_life_limit
                    - st.pillow_stabilizer
                ),
            )

            if remaining > ZERO:
                candidates.append((MODE_5, remaining))

        elif current_mode == MODE_5:
            remaining = max(
                ZERO,
                money(
                    s.stabilizer_full_limit
                    - st.pillow_stabilizer
                ),
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
            nearest = self.nearest_next_mode(
                before
            )

            if nearest:
                mode, remaining = nearest

                return (
                    f"❌ Перехода нет. "
                    f"Режим: {MODE_NAMES[before]}. "
                    f"До {MODE_NAMES[mode]} "
                    f"осталось {fmt_money(remaining)} ₽."
                )

            return None

        return (
            f"✅ ПЕРЕХОД: "
            f"{MODE_NAMES[before]} → "
            f"{MODE_NAMES[after]}"
        )

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
            MODE_3: "ФМ",
            MODE_4: "СтабД",
            MODE_5: "Инвест",
            MODE_6: "Инвест",
        }

        return mapping[mode]

    # ========================================================
    # ГРАНИЦА РЕЖИМА ВНУТРИ ТЕКУЩЕГО ЭТАПА
    # ========================================================

    def _mode_base_limit_for_bracket(
        self,
        mode: int,
        bracket: Decimal,
    ) -> Optional[Decimal]:
        """
        Сколько базы можно обработать на этапе A/B по текущему
        режиму, прежде чем будет достигнут ближайший порог режима.

        На режимах 1–4 переход зависит от направления "вверх":
        Подушка / долги / Подушка / Стабилизатор.
        На режимах 5–6 этапы A/B сами по себе режим не меняют.
        """
        if mode not in {
            MODE_1,
            MODE_2,
            MODE_3,
            MODE_4,
        }:
            return None

        candidate = self.nearest_next_mode(
            mode
        )

        if not candidate:
            return None

        _, remaining = candidate

        rate = D(bracket) / HUNDRED

        if rate <= ZERO:
            return None

        return remaining / rate

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
        """
        Этап A может пересечь несколько режимов внутри одного
        поступления. Этап меняется только после заполнения
        Критического минимума.
        """
        s = self.settings
        st = self.state

        remaining_amount = money(
            D(amount)
        )

        guard = 0

        while remaining_amount > ZERO:

            guard += 1

            if guard > 50:
                raise RuntimeError(
                    "Слишком много переходов режима на этапе A."
                )

            target = money(
                s.total_critical_life
            )

            accumulated_kzh = money(
                st.life_balance
                + st.accumulated_minimum_payments
            )

            if accumulated_kzh >= target:
                break

            current_mode = self.active_mode()

            missing = money(
                target - accumulated_kzh
            )

            bracket = s.bracket_a
            rate = bracket / HUNDRED

            required_base = (
                missing
                / (ONE - rate)
            )

            candidate = (
                self.nearest_next_mode(
                    current_mode
                )
            )

            mode_remaining = None
            mode_base = None

            if (
                candidate
                and current_mode in {
                    MODE_1,
                    MODE_2,
                    MODE_3,
                    MODE_4,
                }
                and rate > ZERO
            ):
                _, mode_remaining = candidate
                mode_remaining = money(
                    mode_remaining
                )
                mode_base = (
                    mode_remaining
                    / rate
                )

            # Определяем, какая граница встретится первой:
            # конец этапа, переход режима или конец денег.
            raw_part = min(
                remaining_amount,
                required_base,
                mode_base
                if mode_base is not None
                else remaining_amount,
            )

            part_a = min(
                remaining_amount,
                money(raw_part),
            )

            if part_a <= ZERO:
                break

            stage_hits = (
                required_base
                <= remaining_amount
                and (
                    mode_base is None
                    or required_base <= mode_base
                )
            )

            mode_hits = (
                mode_base is not None
                and mode_base <= remaining_amount
                and mode_base <= required_base
            )

            # Если обе границы почти совпали, закрываем обе
            # денежными суммами до копейки.
            if (
                stage_hits
                and mode_hits
                and mode_remaining is not None
                and abs(required_base - mode_base) <= CENT
            ):
                life_part = missing
                up_calculated = mode_remaining
                part_a = money(
                    life_part
                    + up_calculated
                )

            elif mode_hits and mode_remaining is not None:
                up_calculated = mode_remaining
                life_part = money(
                    part_a
                    - up_calculated
                )

            elif stage_hits:
                life_part = missing
                up_calculated = money(
                    part_a
                    - life_part
                )

            else:
                up_calculated = money(
                    part_a * rate
                )
                life_part = money(
                    part_a
                    - up_calculated
                )

            up_target = self.bracket_up_target(
                current_mode
            )

            final_overflow = ZERO

            if up_target in {
                "МП",
                "ФМ",
                "СтабД",
            }:
                final_overflow = (
                    self.add_to_pillow_layer(
                        up_target,
                        up_calculated,
                    )
                )

                actual_up = money(
                    up_calculated
                    - final_overflow
                )

                allocations["Подушка"] = money(
                    allocations["Подушка"]
                    + actual_up
                )

            elif up_target == "Инвест":
                st.investments = money(
                    st.investments
                    + up_calculated
                )

                allocations["Инвестиции"] = money(
                    allocations["Инвестиции"]
                    + up_calculated
                )

            elif up_target == "Досрочное":
                applied = (
                    self.apply_early_repayment(
                        up_calculated,
                        steps,
                    )
                )

                allocations["Досрочное"] = money(
                    allocations["Досрочное"]
                    + applied
                )

                final_overflow = money(
                    up_calculated
                    - applied
                )

            else:
                raise ValueError(
                    "Неизвестное направление "
                    f"Бракет_A: {up_target}"
                )

            self._allocate_to_life(
                life_part,
                allocations,
            )

            steps.append(
                f"""ЭТАП A — Критический минимум
Режим: {MODE_NAMES[current_mode]}
Недостаёт: {missing}
Обработано базы: {part_a}
Бракет A: {up_calculated}
Направление вверх: {up_target}
В Критический минимум: {life_part}
Переполнение: {final_overflow}"""
            )

            new_remaining = money(
                remaining_amount
                - part_a
                + final_overflow
            )

            if new_remaining >= remaining_amount:
                raise RuntimeError(
                    "Этап A не уменьшил остаток."
                )

            remaining_amount = new_remaining

        return remaining_amount

    def _allocate_to_life(
        self,
        amount: Decimal,
        allocations: Dict[str, Decimal],
    ):
        """
        Распределяет денежную часть Этапа A между категориями.
        Каждая проводка округляется до копейки; последняя категория
        получает остаток, чтобы сумма сошлась ровно.
        """
        amount = money(
            D(amount)
        )

        if amount <= ZERO:
            return

        shares = self.life_category_shares()
        category_names = list(
            shares.keys()
        )

        distributed = ZERO
        life_added = ZERO

        for index, name in enumerate(
            category_names
        ):
            share = shares[name]

            if index == len(category_names) - 1:
                part = money(
                    amount - distributed
                )
            else:
                part = money(
                    amount * share
                )

            distributed = money(
                distributed + part
            )

            if name == "Мин. платеж":

                self.state.accumulated_minimum_payments = money(
                    self.state.accumulated_minimum_payments
                    + part
                )

                allocations["Мин. платеж"] = money(
                    allocations.get(
                        "Мин. платеж",
                        ZERO,
                    )
                    + part
                )

                continue

            self.state.period_life_topups[name] = money(
                self.state.period_life_topups.get(
                    name,
                    ZERO,
                )
                + part
            )

            key = f"КЖ:{name}"

            allocations[key] = money(
                allocations.get(
                    key,
                    ZERO,
                )
                + part
            )

            life_added = money(
                life_added + part
            )

        self.state.life_balance = money(
            self.state.life_balance
            + life_added
        )

    def stage_b(
        self,
        amount: Decimal,
        mode: int,
        steps: List[str],
        allocations: Dict[str, Decimal],
    ) -> Decimal:
        """
        Этап B сохраняется при смене режима внутри поступления.
        Режим пересчитывается после каждого денежного куска.
        """
        s = self.settings
        st = self.state

        remaining_amount = money(
            D(amount)
        )

        guard = 0

        while remaining_amount > ZERO:

            guard += 1

            if guard > 50:
                raise RuntimeError(
                    "Слишком много переходов режима на этапе B."
                )

            critical = money(
                s.critical_life
            )
            household = money(
                s.household_life
            )
            life_balance = money(
                st.life_balance
            )

            if not (
                critical
                <= life_balance
                < household
            ):
                break

            current_mode = self.active_mode()

            missing = money(
                household
                - life_balance
            )

            bracket = s.bracket_b
            rate = bracket / HUNDRED

            required_base = (
                missing
                / (ONE - rate)
            )

            candidate = (
                self.nearest_next_mode(
                    current_mode
                )
            )

            mode_remaining = None
            mode_base = None

            if (
                candidate
                and current_mode in {
                    MODE_1,
                    MODE_2,
                    MODE_3,
                    MODE_4,
                }
                and rate > ZERO
            ):
                _, mode_remaining = candidate
                mode_remaining = money(
                    mode_remaining
                )
                mode_base = (
                    mode_remaining
                    / rate
                )

            raw_part = min(
                remaining_amount,
                required_base,
                mode_base
                if mode_base is not None
                else remaining_amount,
            )

            part_b = min(
                remaining_amount,
                money(raw_part),
            )

            if part_b <= ZERO:
                break

            stage_hits = (
                required_base
                <= remaining_amount
                and (
                    mode_base is None
                    or required_base <= mode_base
                )
            )

            mode_hits = (
                mode_base is not None
                and mode_base <= remaining_amount
                and mode_base <= required_base
            )

            if (
                stage_hits
                and mode_hits
                and mode_remaining is not None
                and abs(required_base - mode_base) <= CENT
            ):
                reserve_part = missing
                up_calculated = mode_remaining
                part_b = money(
                    reserve_part
                    + up_calculated
                )

            elif mode_hits and mode_remaining is not None:
                up_calculated = mode_remaining
                reserve_part = money(
                    part_b
                    - up_calculated
                )

            elif stage_hits:
                reserve_part = missing
                up_calculated = money(
                    part_b
                    - reserve_part
                )

            else:
                up_calculated = money(
                    part_b * rate
                )
                reserve_part = money(
                    part_b
                    - up_calculated
                )

            up_target = self.bracket_up_target(
                current_mode
            )

            final_overflow = ZERO

            if up_target in {
                "МП",
                "ФМ",
                "СтабД",
            }:
                final_overflow = (
                    self.add_to_pillow_layer(
                        up_target,
                        up_calculated,
                    )
                )

                actual_up = money(
                    up_calculated
                    - final_overflow
                )

                allocations["Подушка"] = money(
                    allocations["Подушка"]
                    + actual_up
                )

            elif up_target == "Инвест":
                st.investments = money(
                    st.investments
                    + up_calculated
                )

                allocations["Инвестиции"] = money(
                    allocations["Инвестиции"]
                    + up_calculated
                )

            elif up_target == "Досрочное":
                applied = (
                    self.apply_early_repayment(
                        up_calculated,
                        steps,
                    )
                )

                allocations["Досрочное"] = money(
                    allocations["Досрочное"]
                    + applied
                )

                final_overflow = money(
                    up_calculated
                    - applied
                )

            else:
                raise ValueError(
                    "Неизвестное направление "
                    f"Бракет_B: {up_target}"
                )

            st.life_balance = money(
                st.life_balance
                + reserve_part
            )

            allocations[
                "Бытовой резерв"
            ] = money(
                allocations.get(
                    "Бытовой резерв",
                    ZERO,
                )
                + reserve_part
            )

            steps.append(
                f"""ЭТАП B — Бытовой резерв
Режим: {MODE_NAMES[current_mode]}
Недостаёт: {missing}
Обработано базы: {part_b}
Бракет B: {up_calculated}
Направление вверх: {up_target}
В Бытовой резерв: {reserve_part}
Переполнение: {final_overflow}"""
            )

            new_remaining = money(
                remaining_amount
                - part_b
                + final_overflow
            )

            if new_remaining >= remaining_amount:
                raise RuntimeError(
                    "Этап B не уменьшил остаток."
                )

            remaining_amount = new_remaining

        return remaining_amount

    def stage_c(
        self,
        amount: Decimal,
        mode: int,
        steps: List[str],
        allocations: Dict[str, Decimal],
    ) -> Decimal:
        """
        Этап C остаётся этапом C даже если одно крупное поступление
        переводит пользователя через несколько режимов.
        """
        if amount <= ZERO:
            return ZERO

        if money(self.state.life_balance) < money(
            self.settings.household_life
        ):
            return money(amount)

        s = self.settings
        st = self.state

        remaining_amount = money(
            D(amount)
        )

        guard = 0

        while remaining_amount > ZERO:

            guard += 1

            if guard > 50:
                raise RuntimeError(
                    "Слишком много переходов режима на этапе C."
                )

            current_mode = self.active_mode()
            before_amount = remaining_amount

            if current_mode in {
                MODE_1,
                MODE_2,
                MODE_3,
                MODE_4,
            }:

                candidate = self.nearest_next_mode(
                    current_mode
                )

                if not candidate:
                    break

                _, until_transition = candidate
                until_transition = money(
                    until_transition
                )

                chunk = min(
                    remaining_amount,
                    until_transition,
                )

                if current_mode == MODE_1:
                    overflow = self.add_to_pillow_layer(
                        "МП",
                        chunk,
                    )
                    actual = money(
                        chunk - overflow
                    )
                    allocations["Подушка"] = money(
                        allocations["Подушка"]
                        + actual
                    )

                elif current_mode == MODE_2:
                    applied = self.apply_early_repayment(
                        chunk,
                        steps,
                    )
                    overflow = money(
                        chunk - applied
                    )
                    allocations["Досрочное"] = money(
                        allocations["Досрочное"]
                        + applied
                    )

                elif current_mode == MODE_3:
                    overflow = self.add_to_pillow_layer(
                        "ФМ",
                        chunk,
                    )
                    actual = money(
                        chunk - overflow
                    )
                    allocations["Подушка"] = money(
                        allocations["Подушка"]
                        + actual
                    )

                else:
                    overflow = self.add_to_pillow_layer(
                        "СтабД",
                        chunk,
                    )
                    actual = money(
                        chunk - overflow
                    )
                    allocations["Подушка"] = money(
                        allocations["Подушка"]
                        + actual
                    )

                remaining_amount = money(
                    remaining_amount
                    - chunk
                    + overflow
                )

            elif current_mode == MODE_5:

                candidate = self.nearest_next_mode(
                    current_mode
                )

                pillow_fraction = (
                    (
                        ONE
                        - s.bracket_c / HUNDRED
                    )
                    * s.pillow_share_c
                    / HUNDRED
                )

                until_transition = None
                transition_base = None

                if (
                    candidate
                    and pillow_fraction > ZERO
                ):
                    _, until_transition = candidate
                    until_transition = money(
                        until_transition
                    )
                    transition_base = (
                        until_transition
                        / pillow_fraction
                    )

                hits_transition = (
                    transition_base is not None
                    and transition_base <= remaining_amount
                )

                if hits_transition:
                    chunk = min(
                        remaining_amount,
                        money(transition_base),
                    )
                else:
                    chunk = remaining_amount

                investment_part = money(
                    chunk
                    * s.bracket_c
                    / HUNDRED
                )

                after_invest = money(
                    chunk - investment_part
                )

                if (
                    hits_transition
                    and until_transition is not None
                ):
                    # Закрываем Стабилизатор ровно до порога.
                    pillow_part = until_transition
                    goal_part = money(
                        after_invest
                        - pillow_part
                    )
                else:
                    # Половины/доли считаем до копейки.
                    # Подушка округляется первой, цели получают остаток.
                    pillow_part = money(
                        after_invest
                        * s.pillow_share_c
                        / HUNDRED
                    )
                    goal_part = money(
                        after_invest
                        - pillow_part
                    )

                st.investments = money(
                    st.investments
                    + investment_part
                )
                allocations["Инвестиции"] = money(
                    allocations["Инвестиции"]
                    + investment_part
                )

                self._allocate_goals(
                    goal_part,
                    allocations,
                )

                pillow_overflow = self.add_to_pillow_layer(
                    "СтабД",
                    pillow_part,
                )

                pillow_actual = money(
                    pillow_part
                    - pillow_overflow
                )

                allocations["Подушка"] = money(
                    allocations["Подушка"]
                    + pillow_actual
                )

                remaining_amount = money(
                    remaining_amount
                    - chunk
                    + pillow_overflow
                )

            elif current_mode == MODE_6:

                investment_part = money(
                    remaining_amount
                    * s.bracket_c
                    / HUNDRED
                )

                goals_part = money(
                    remaining_amount
                    - investment_part
                )

                st.investments = money(
                    st.investments
                    + investment_part
                )

                allocations["Инвестиции"] = money(
                    allocations["Инвестиции"]
                    + investment_part
                )

                self._allocate_goals(
                    goals_part,
                    allocations,
                )

                remaining_amount = ZERO

            else:
                raise ValueError(
                    f"Неизвестный режим: {current_mode}"
                )

            steps.append(
                "ЭТАП C — "
                f"режим {MODE_NAMES[current_mode]}, "
                f"остаток после куска: {remaining_amount}"
            )

            if (
                remaining_amount >= before_amount
                and remaining_amount > ZERO
            ):
                raise RuntimeError(
                    "Этап C не уменьшил остаток."
                )

        return money(
            remaining_amount
        )

    # ========================================================
    # ЦЕЛИ
    # ========================================================

    def _allocate_goals(
        self,
        amount: Decimal,
        allocations: Dict[str, Decimal],
    ):

        amount = money(
            D(amount)
        )

        if amount <= ZERO:
            return

        goals = self.settings.goals

        if not goals:
            allocations[
                "Цели:ЦЕЛИ (всего)"
            ] = money(
                allocations.get(
                    "Цели:ЦЕЛИ (всего)",
                    ZERO,
                )
                + amount
            )
            return

        distributed = ZERO

        for index, goal in enumerate(
            goals
        ):

            if index == len(goals) - 1:
                part = money(
                    amount - distributed
                )
            else:
                part = money(
                    amount
                    * goal.percentage
                    / HUNDRED
                )

            distributed = money(
                distributed + part
            )

            self.state.goal_balances[
                goal.name
            ] = money(
                self.state.goal_balances.get(
                    goal.name,
                    ZERO,
                )
                + part
            )

            key = f"Цели:{goal.name}"

            allocations[key] = money(
                allocations.get(
                    key,
                    ZERO,
                )
                + part
            )

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

        amount = money(
            D(amount)
        )

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

            balance = money(
                credit.principal_balance
            )

            applied = min(
                remaining,
                balance,
            )

            credit.principal_balance = money(
                balance - applied
            )

            if credit.principal_balance <= ZERO:
                credit.principal_balance = ZERO
                credit.status = "Погашен"

            applied_total = money(
                applied_total + applied
            )

            remaining = money(
                remaining - applied
            )

            steps.append(
                f"Досрочное погашение: "
                f"{credit.name} = {applied}"
            )

        self.state.early_repayment = money(
            self.state.early_repayment
            + applied_total
        )

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
        """
        Сверхдоход тоже сохраняет этап при смене режима.
        """
        if amount <= ZERO:
            return ZERO

        s = self.settings

        remaining_amount = money(
            D(amount)
        )

        guard = 0

        while remaining_amount > ZERO:

            guard += 1

            if guard > 50:
                raise RuntimeError(
                    "Слишком много переходов режима "
                    "на этапе сверхдохода."
                )

            current_mode = self.active_mode()
            before_amount = remaining_amount

            if current_mode in {
                MODE_1,
                MODE_2,
                MODE_3,
                MODE_4,
            }:

                candidate = self.nearest_next_mode(
                    current_mode
                )

                if not candidate:
                    break

                _, until_transition = candidate
                until_transition = money(
                    until_transition
                )

                chunk = min(
                    remaining_amount,
                    until_transition,
                )

                if current_mode == MODE_1:
                    overflow = self.add_to_pillow_layer(
                        "МП",
                        chunk,
                    )
                    actual = money(
                        chunk - overflow
                    )
                    allocations["Подушка"] = money(
                        allocations["Подушка"]
                        + actual
                    )

                elif current_mode == MODE_2:
                    applied = self.apply_early_repayment(
                        chunk,
                        [],
                    )
                    overflow = money(
                        chunk - applied
                    )
                    allocations["Досрочное"] = money(
                        allocations["Досрочное"]
                        + applied
                    )

                elif current_mode == MODE_3:
                    overflow = self.add_to_pillow_layer(
                        "ФМ",
                        chunk,
                    )
                    actual = money(
                        chunk - overflow
                    )
                    allocations["Подушка"] = money(
                        allocations["Подушка"]
                        + actual
                    )

                else:
                    overflow = self.add_to_pillow_layer(
                        "СтабД",
                        chunk,
                    )
                    actual = money(
                        chunk - overflow
                    )
                    allocations["Подушка"] = money(
                        allocations["Подушка"]
                        + actual
                    )

                remaining_amount = money(
                    remaining_amount
                    - chunk
                    + overflow
                )

            elif current_mode == MODE_5:

                candidate = self.nearest_next_mode(
                    current_mode
                )

                pillow_fraction = (
                    ONE
                    - s.bracket_d / HUNDRED
                )

                until_transition = None
                transition_base = None

                if (
                    candidate
                    and pillow_fraction > ZERO
                ):
                    _, until_transition = candidate
                    until_transition = money(
                        until_transition
                    )
                    transition_base = (
                        until_transition
                        / pillow_fraction
                    )

                hits_transition = (
                    transition_base is not None
                    and transition_base <= remaining_amount
                )

                if hits_transition:
                    chunk = min(
                        remaining_amount,
                        money(transition_base),
                    )
                else:
                    chunk = remaining_amount

                if (
                    hits_transition
                    and until_transition is not None
                ):
                    pillow_part = until_transition
                    invest = money(
                        chunk - pillow_part
                    )
                else:
                    invest = money(
                        chunk
                        * s.bracket_d
                        / HUNDRED
                    )
                    pillow_part = money(
                        chunk - invest
                    )

                self.state.investments = money(
                    self.state.investments
                    + invest
                )

                allocations["Инвестиции"] = money(
                    allocations["Инвестиции"]
                    + invest
                )

                overflow = self.add_to_pillow_layer(
                    "СтабД",
                    pillow_part,
                )

                allocations["Подушка"] = money(
                    allocations["Подушка"]
                    + pillow_part
                    - overflow
                )

                remaining_amount = money(
                    remaining_amount
                    - chunk
                    + overflow
                )

            elif current_mode == MODE_6:

                invest = money(
                    remaining_amount
                    * s.bracket_e
                    / HUNDRED
                )

                goals = money(
                    remaining_amount
                    - invest
                )

                self.state.investments = money(
                    self.state.investments
                    + invest
                )

                allocations["Инвестиции"] = money(
                    allocations["Инвестиции"]
                    + invest
                )

                self._allocate_goals(
                    goals,
                    allocations,
                )

                remaining_amount = ZERO

            else:
                raise ValueError(
                    f"Неизвестный режим: {current_mode}"
                )

            if (
                remaining_amount >= before_amount
                and remaining_amount > ZERO
            ):
                raise RuntimeError(
                    "Сверхдоход не уменьшил остаток."
                )

        return money(
            remaining_amount
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

        income = money(
            D(income)
        )

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

        if tax_override is None:

            tax = money(
                self.calculate_tax(
                    income,
                    income_type,
                )
            )

        else:

            tax = money(
                D(tax_override)
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

        amount = money(
            income
            - tax
        )

        allocations: Dict[str, Decimal] = {
            "Подушка": ZERO,
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

        amount = self.stage_a(
            amount,
            mode_before,
            steps,
            allocations,
        )

        # --------------------------------------------
        # После A проверяем изменение режима.
        # --------------------------------------------

        current_mode = self.active_mode()

        # --------------------------------------------
        # ЭТАП B
        # --------------------------------------------

        amount = self.stage_b(
            amount,
            current_mode,
            steps,
            allocations,
        )

        current_mode = self.active_mode()

        # --------------------------------------------
        # ЭТАП C
        # --------------------------------------------

        amount = self.stage_c(
            amount,
            current_mode,
            steps,
            allocations,
        )

        # --------------------------------------------
        # СВЕРХДОХОД
        # --------------------------------------------

        if amount > ZERO:
            amount = self.super_income(
                amount,
                self.active_mode(),
                allocations,
            )

        # --------------------------------------------
        # Режим после всего распределения
        # --------------------------------------------

        mode_after = self.active_mode()

        # --------------------------------------------
        # Периодическая аналитика
        # --------------------------------------------

        self.state.period_income = money(
            self.state.period_income
            + income
        )
        self.state.period_tax = money(
            self.state.period_tax
            + tax
        )

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
            ] = money(
                self.state.period_allocations.get(
                    key,
                    ZERO,
                )
                + D(value)
            )
            
        # --------------------------------------------
        # Журнал
        # --------------------------------------------

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

        return {
            "mode": self.active_mode(),
            "mode_name": MODE_NAMES[
                self.active_mode()
            ],

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

            "pillow": {
                "total":
                    self.state.pillow_balance,
                "МП":
                    self.state.pillow_minimum,
                "ФМ":
                    self.state.pillow_force_majeure,
                "СтабД":
                    self.state.pillow_stabilizer,
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

        candidate = (
            self.nearest_next_mode(current)
        )

        if not candidate:
            return None

        mode, remaining = candidate

        return {
            "current_mode": current,
            "next_mode": mode,
            "remaining": remaining,
            "current_name":
                MODE_NAMES[current],
            "next_name":
                MODE_NAMES[mode],
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

        if s.employment_type == "Наёмный":
            employment_mode_ok = (
                self.active_mode()
                not in {MODE_4, MODE_5}
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
