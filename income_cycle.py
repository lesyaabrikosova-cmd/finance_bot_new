"""Чистая модель финансовых профилей и циклического дохода.

Модуль пока не подключён к Telegram и financial_engine: он фиксирует формулы,
которые должны быть подтверждены сценарными тестами до миграции режимов.
"""

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")

STABLE_EMPLOYEE = "stable_employee"
STABLE_FREELANCER = "stable_freelancer"
CYCLIC = "cyclic"


def financial_profile(employment_type: str, income_rhythm: str) -> str:
    if income_rhythm == "cyclic":
        return CYCLIC
    if employment_type == "Фрилансер":
        return STABLE_FREELANCER
    return STABLE_EMPLOYEE


def profile_route(profile: str, include_debt_stages: bool = True) -> tuple[str, ...]:
    debt = ("Минимальная подушка", "Погашение долгов") if include_debt_stages else ()
    routes = {
        STABLE_EMPLOYEE: ("ФМ-подушка", "Максимальный режим"),
        STABLE_FREELANCER: (
            "ФМ-подушка", "Стабилизатор-КМ", "Стабилизатор-УЖ", "Максимальный режим"
        ),
        CYCLIC: (
            "Межконтрактный резерв-КМ",
            "Межконтрактный резерв-УЖ",
            "ФМ-подушка",
            "Стабилизатор-КМ",
            "Стабилизатор-УЖ",
            "Максимальный режим",
        ),
    }
    if profile not in routes:
        raise ValueError("Неизвестный финансовый профиль.")
    return debt + routes[profile]


@dataclass(frozen=True)
class CyclicIncomePlan:
    paid_months: int
    gap_months: int
    critical_life: Decimal
    sustainable_life: Decimal
    delay_months: int = 2
    force_majeure_months: int = 6

    def __post_init__(self):
        if self.paid_months < 1 or self.gap_months < 1:
            raise ValueError("Рабочая и межконтрактная части цикла должны быть положительными.")
        if self.critical_life <= ZERO or self.sustainable_life < self.critical_life:
            raise ValueError("Устойчивая жизнь не может быть меньше Критического минимума.")
        if self.delay_months < 1 or not 3 <= self.force_majeure_months <= 6:
            raise ValueError("Некорректный горизонт защитных резервов.")

    @property
    def intercontract_critical(self) -> Decimal:
        return self.critical_life * self.gap_months

    @property
    def intercontract_full(self) -> Decimal:
        return self.sustainable_life * self.gap_months

    @property
    def monthly_self_salary(self) -> Decimal:
        return self.sustainable_life

    @property
    def contract_month_contribution(self) -> Decimal:
        return (self.intercontract_full / self.paid_months).quantize(Decimal("0.01"))

    @property
    def contract_contribution_schedule(self) -> tuple[Decimal, ...]:
        regular = self.contract_month_contribution
        if self.paid_months == 1:
            return (self.intercontract_full,)
        return (regular,) * (self.paid_months - 1) + (
            self.intercontract_full - regular * (self.paid_months - 1),
        )

    @property
    def force_majeure(self) -> Decimal:
        return self.critical_life * self.force_majeure_months

    @property
    def stabilizer_critical(self) -> Decimal:
        return self.critical_life * self.delay_months

    @property
    def stabilizer_full(self) -> Decimal:
        return self.sustainable_life * self.delay_months

    def remaining_intercontract_target(self, months_left: int) -> Decimal:
        if not 0 <= months_left <= self.gap_months:
            raise ValueError("Остаток месяцев находится за пределами цикла.")
        return self.sustainable_life * months_left

    def allocate_protection(self, available: Decimal) -> dict[str, Decimal]:
        """Водопад без долгов: сначала известная жизнь, затем защиты."""
        available = Decimal(available)
        if available < ZERO:
            raise ValueError("Доступная сумма не может быть отрицательной.")
        result: dict[str, Decimal] = {}
        layers = (
            ("Межконтрактный резерв-КМ", self.intercontract_critical),
            ("Межконтрактный резерв-УЖ", self.intercontract_full - self.intercontract_critical),
            ("ФМ-подушка", self.force_majeure),
            ("Стабилизатор-КМ", self.stabilizer_critical),
            ("Стабилизатор-УЖ", self.stabilizer_full - self.stabilizer_critical),
        )
        remaining = available
        for name, target in layers:
            part = min(remaining, target)
            result[name] = part
            remaining -= part
        result["Свободный остаток"] = remaining
        return result
