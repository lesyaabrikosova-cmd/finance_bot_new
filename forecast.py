from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from dashboard import send_text_with_image
from html import escape
from typing import Callable

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from financial_engine import FinancialAllocator, fmt_money, goal_display_name
from financial_path_card import render_financial_path_card
from allocation_table import allocation_table_from_text
from income_maintenance import add_calendar_months
from planned_payments import refresh_planned_payment_targets
from storage import db
from taxes import refresh_planned_tax_targets
from time_utils import moscow_today
from ui import keyboard, reserve_fraction


router = Router()


class ForecastStates(StatesGroup):
    available_before_purchases = State()
    gap_months = State()
    investment_rate = State()
    investment_inflation = State()
    investment_income_growth = State()


def parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    raw = text.replace("₽", "").replace(" ", "").replace("\u00a0", "")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        value = Decimal(raw)
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def rub(value: Decimal) -> str:
    return f"{fmt_money(value)} ₽"

def rub_plain(value: Decimal) -> str:
    return fmt_money(value)


def last_income_distribution_strategy(
    source: FinancialAllocator,
) -> tuple[str, bool]:
    """Return the user's latest explicit Stage C choice.

    Historical operations created before the explicit-choice marker cannot
    prove that the user actually pressed either button. They deliberately
    fall back to the safer protection-only route.
    """
    history = list(getattr(source.state, "distribution_history", ()) or ())
    if not history:
        history = list(getattr(source.state, "operation_log", ()) or ())
    telegram_id = getattr(source, "telegram_id", None)
    if telegram_id is not None:
        history = db.load_income_distribution_payloads(telegram_id)
    for raw in reversed(history):
        operation = raw.get("payload", raw)
        if operation.get("type") != "income_distribution":
            continue
        if operation.get("distribution_strategy_selected") is not True:
            continue
        strategy = operation.get("distribution_strategy")
        if strategy in {"balanced", "protection"}:
            return str(strategy), True
    return "protection", False


def _forecast_copy(source: FinancialAllocator) -> FinancialAllocator:
    simulated = deepcopy(source)
    simulated.settings.protective_stage_c_strategy = (
        last_income_distribution_strategy(source)[0]
    )
    return simulated


def _refresh_distribution_forecast_targets(
    source: FinancialAllocator,
    simulated: FinancialAllocator,
    *,
    today: date,
) -> None:
    """Use the same due-date recalculation as a real New Income operation."""
    telegram_id = getattr(source, "telegram_id", None)
    if telegram_id is None:
        return
    refresh_planned_payment_targets(
        telegram_id,
        simulated,
        today,
        persist=False,
    )
    refresh_planned_tax_targets(
        telegram_id,
        simulated,
        today,
        persist=False,
    )


@dataclass(frozen=True)
class PathMilestone:
    key: str
    months: Decimal
    label: str
    icon: str
    kind: str
    order: int
    amount: Decimal | None = None
    due_date: date | None = None


@dataclass
class ForecastObligation:
    key: str
    kind: str
    title: str
    target: Decimal
    saved: Decimal
    due_date: date
    monthly: Decimal
    applied_monthly: Decimal
    envelope: str = ""
    settings_key: str = ""
    active: bool = True


@dataclass(frozen=True)
class InvestmentProjection:
    years: int
    capital: Decimal
    own_funds: Decimal
    profit: Decimal
    real_capital: Decimal
    monthly_contribution: Decimal


@dataclass(frozen=True)
class DebtProjection:
    name: str
    starting_balance: Decimal
    annual_rate: Decimal
    minimum_payment: Decimal
    payoff_months: Decimal | None
    remaining_balance: Decimal
    interest_paid: Decimal
    total_paid: Decimal


@dataclass(frozen=True)
class GoalProjection:
    name: str
    currency_code: str
    current: Decimal
    target: Decimal | None
    remaining: Decimal | None
    percentage: Decimal
    status: str
    completion_months: Decimal | None
    completion_date: date | None
    deadline: date | None
    deadline_on_track: bool | None
    required_monthly: Decimal | None


@dataclass(frozen=True)
class LevelProjection:
    level: int
    display_name: str
    title: str
    requirement: str
    months: Decimal | None


def _plural(value: int, one: str, few: str, many: str) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return one
    if value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
        return few
    return many


def human_path_duration(months: Decimal) -> str:
    """Показывать дробный результат как честный диапазон полных месяцев."""
    value = max(Decimal("0"), Decimal(months))
    lower = int(value.to_integral_value(rounding=ROUND_FLOOR))
    upper = int(value.to_integral_value(rounding=ROUND_CEILING))

    def whole_duration(total_months: int) -> str:
        if total_months <= 0:
            return "0 месяцев"
        years, rest_months = divmod(total_months, 12)
        parts: list[str] = []
        if years:
            parts.append(f"{years} {_plural(years, 'год', 'года', 'лет')}")
        if rest_months:
            parts.append(
                f"{rest_months} {_plural(rest_months, 'месяц', 'месяца', 'месяцев')}"
            )
        return " и ".join(parts)

    if lower == upper:
        return whole_duration(lower)
    if lower == 0:
        return "1 месяц"
    lower_years, lower_months = divmod(lower, 12)
    upper_years, upper_months = divmod(upper, 12)
    if lower_years == upper_years == 0:
        return f"{lower}–{upper} {_plural(upper, 'месяц', 'месяца', 'месяцев')}"
    if lower_years == upper_years and lower_months and upper_months:
        years_text = f"{lower_years} {_plural(lower_years, 'год', 'года', 'лет')}"
        months_text = (
            f"{lower_months}–{upper_months} "
            f"{_plural(upper_months, 'месяц', 'месяца', 'месяцев')}"
        )
        return f"{years_text} и {months_text}"
    return f"{whole_duration(lower)} – {whole_duration(upper)}"


_RUSSIAN_MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def goal_completion_calendar_text(today: date, months: Decimal) -> str:
    """Перевести дробный срок Цели в честный диапазон календарных месяцев."""
    value = max(Decimal("0"), Decimal(months))
    lower = int(value.to_integral_value(rounding=ROUND_FLOOR))
    upper = int(value.to_integral_value(rounding=ROUND_CEILING))
    if value > 0 and lower == 0:
        lower = 1
    if value > 0 and upper == 0:
        upper = 1

    early = add_calendar_months(today, lower)
    late = add_calendar_months(today, upper)
    early_month = _RUSSIAN_MONTHS[early.month - 1]
    late_month = _RUSSIAN_MONTHS[late.month - 1]
    if (early.year, early.month) == (late.year, late.month):
        return f"{early_month} {early.year}"
    if early.year == late.year:
        return f"{early_month}–{late_month} {early.year}"
    return f"{early_month} {early.year} – {late_month} {late.year}"


def _crossing_month(
    month: int,
    before: Decimal,
    after: Decimal,
    target: Decimal,
) -> Decimal:
    if after <= before:
        return Decimal(month)
    share = (target - before) / (after - before)
    share = min(Decimal("1"), max(Decimal("0"), share))
    return Decimal(month - 1) + share


def _configured_fallback_tax_rate(allocator: FinancialAllocator) -> Decimal:
    rates = [
        Decimal(rate)
        for rate in allocator.settings.income_type_tax_rates.values()
    ]
    if not rates:
        rates = [Decimal(allocator.settings.tax_rate)]
    return max([Decimal("0"), *rates])


def historical_income_tax_rate(
    allocator: FinancialAllocator,
    *,
    today: date | None = None,
) -> tuple[Decimal, int]:
    """Return the arithmetic mean of monthly effective income-tax rates.

    Only completed calendar months and direct tax withheld from income are
    used. Fixed and planned taxes live inside allocations and therefore never
    enter this calculation. With no completed history the highest configured
    income-tax rate is the deliberately conservative fallback.
    """
    today = today or moscow_today()
    month_start = today.replace(day=1)
    month_keys: list[tuple[int, int]] = []
    cursor = month_start
    for _ in range(6):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        month_keys.append((cursor.year, cursor.month))
    allowed = set(month_keys)
    monthly: dict[tuple[int, int], list[Decimal]] = {
        key: [Decimal("0"), Decimal("0")]
        for key in month_keys
    }
    history = list(getattr(allocator.state, "distribution_history", ()) or ())
    if not history:
        history = list(getattr(allocator.state, "operation_log", ()) or ())
    if not history and getattr(allocator, "telegram_id", None) is not None:
        history = db.load_income_distribution_payloads(allocator.telegram_id)
    for operation in history:
        if operation.get("type") != "income_distribution":
            continue
        try:
            operation_date = date.fromisoformat(str(operation.get("date", ""))[:10])
            income = Decimal(operation.get("income", 0))
            tax = Decimal(operation.get("tax", 0))
        except (ValueError, TypeError, InvalidOperation):
            continue
        key = (operation_date.year, operation_date.month)
        if key not in allowed or income <= 0:
            continue
        monthly[key][0] += income
        monthly[key][1] += max(Decimal("0"), tax)

    rates = [
        tax / income * Decimal("100")
        for income, tax in monthly.values()
        if income > 0
    ]
    if not rates:
        return _configured_fallback_tax_rate(allocator), 0
    return sum(rates, Decimal("0")) / Decimal(len(rates)), len(rates)


def _path_trackers(source: FinancialAllocator) -> list[dict]:
    """Собрать конечные финансовые сущности, доступные для прогноза."""
    trackers: list[dict] = []
    order = 0

    def add(
        key: str,
        label: str,
        icon: str,
        kind: str,
        target: Decimal,
        value: Callable[[FinancialAllocator], Decimal],
    ) -> None:
        nonlocal order
        target = Decimal(target)
        if target <= 0:
            return
        trackers.append({
            "key": key,
            "label": label,
            "icon": icon,
            "kind": kind,
            "target": target,
            "value": value,
            "order": order,
        })
        order += 1

    active_credits = [credit for credit in source.settings.credits if credit.active]

    if active_credits:
        add(
            "pillow:min",
            "Минимальная подушка",
            "🛡",
            "reserve",
            source.settings.minimum_reserve_limit,
            lambda allocator: allocator.pillow_total_balance,
        )

    if source.profile_id == "cyclic":
        add(
            "salary:min",
            "Фонд зарплаты: минимальный слой",
            "🏦",
            "reserve",
            source.intercontract_current_life_limit,
            lambda allocator: allocator.state.intercontract_reserve,
        )
        add(
            "salary:full",
            "Фонд зарплаты: полный размер",
            "🏦",
            "reserve",
            source.intercontract_current_limit,
            lambda allocator: allocator.state.intercontract_reserve,
        )

    # После долгов единый резерв достраивается до форс-мажорного размера.
    add(
        "pillow",
        "Подушка",
        "🛡",
        "reserve",
        source.settings.force_majeure_limit,
        lambda allocator: allocator.pillow_total_balance,
    )

    if source.settings.needs_stabilizer:
        add(
            "stabilizer:min",
            "Стабилизатор: минимальный слой",
            "🛟",
            "reserve",
            source.settings.stabilizer_life_limit,
            lambda allocator: allocator.state.pillow_stabilizer,
        )
        add(
            "stabilizer:full",
            "Стабилизатор: полный размер",
            "🛟",
            "reserve",
            source.settings.stabilizer_full_limit,
            lambda allocator: allocator.state.pillow_stabilizer,
        )

    for index, credit in enumerate(active_credits):
        initial = Decimal(credit.principal_balance)
        add(
            f"debt:{index}",
            f"Долг «{credit.name}» погашен",
            "💳",
            "debt",
            initial,
            lambda allocator, name=credit.name, initial=initial: initial - next(
                (
                    item.principal_balance
                    for item in allocator.settings.credits
                    if item.name == name
                ),
                Decimal("0"),
            ),
        )
    for goal in source.settings.goals:
        if (
            goal.status != "active"
            or not goal.is_goal
            or goal.full_target_amount is None
            or goal.currency_code != "RUB"
        ):
            continue
        add(
            f"goal:{goal.uid}",
            f"Достигнута цель «{goal_display_name(goal.name, False)}»",
            "⭐",
            "goal",
            goal.full_target_amount,
            lambda allocator, name=goal.name, fallback=goal.balance: Decimal(
                allocator.state.goal_balances.get(name, fallback)
            ),
        )
    return trackers


def _forecast_obligations(
    source: FinancialAllocator,
    *,
    today: date,
) -> list[ForecastObligation]:
    telegram_id = getattr(source, "telegram_id", None)
    if telegram_id is None:
        return []
    obligations: list[ForecastObligation] = []
    for item in db.load_planned_payments(telegram_id):
        due = date.fromisoformat(item["due_date"])
        key = f"payment:{item['id']}"
        obligations.append(ForecastObligation(
            key=key,
            kind="payment",
            title=f"Платёж «{item['payment_name']}»",
            target=Decimal(item["target_amount"]),
            saved=Decimal(item["saved_amount"]),
            due_date=due,
            monthly=Decimal(item["monthly_amount"]),
            applied_monthly=Decimal(
                source.settings.automatic_life_obligations.get(
                    key,
                    item["monthly_amount"],
                )
            ),
            envelope=str(item["envelope_name"]),
            settings_key=key,
        ))

    for item in db.load_tax_obligations(telegram_id):
        if item.get("due_date"):
            due = date.fromisoformat(item["due_date"])
        else:
            due = add_calendar_months(today, max(1, int(item.get("months") or 1)))
        object_name = str(item.get("object_name") or "").strip()
        title = str(item["tax_type"])
        if object_name:
            title = f"{title} · {object_name}"
        settings_key = f"{item['tax_type']} · {item['object_name']}"
        obligations.append(ForecastObligation(
            key=f"tax:{item['id']}",
            kind="tax",
            title=title,
            target=Decimal(item["target_amount"]),
            saved=Decimal(item["saved_before"]),
            due_date=due,
            monthly=Decimal(item["monthly_amount"]),
            applied_monthly=Decimal(item["monthly_amount"]),
            envelope="Налоги",
            settings_key=settings_key,
        ))
    return obligations


def _remaining_obligation_months(due: date, as_of: date) -> int:
    if due <= as_of:
        return 1
    return max(1, (due.year - as_of.year) * 12 + due.month - as_of.month)


def _ceil_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _prepare_forecast_obligations(
    allocator: FinancialAllocator,
    obligations: list[ForecastObligation],
    *,
    as_of: date,
) -> None:
    """Recalculate finite obligations before a simulated monthly income."""
    for item in obligations:
        if not item.active:
            continue
        remaining = max(Decimal("0"), item.target - item.saved)
        if remaining <= 0:
            item.active = False
            continue
        monthly = _ceil_money(
            remaining / Decimal(_remaining_obligation_months(item.due_date, as_of))
        )
        if item.kind == "payment":
            delta = monthly - item.applied_monthly
            current = Decimal(allocator.settings.life_categories.get(item.envelope, 0))
            updated = max(Decimal("0"), current + delta)
            if updated > 0:
                allocator.settings.life_categories[item.envelope] = updated
            else:
                allocator.settings.life_categories.pop(item.envelope, None)
            allocator.settings.set_automatic_life_obligation(item.settings_key, monthly)
        else:
            allocator.settings.tax_catchups[item.settings_key] = monthly
        item.monthly = monthly
        item.applied_monthly = monthly


def _distribute_forecast_funding(
    items: list[ForecastObligation],
    amount: Decimal,
) -> dict[str, tuple[Decimal, Decimal]]:
    """Distribute funded money proportionally, respecting each remaining target."""
    result: dict[str, tuple[Decimal, Decimal]] = {}
    active = [item for item in items if item.active and item.saved < item.target]
    remaining_amount = max(Decimal("0"), Decimal(amount))
    while remaining_amount > 0 and active:
        weight = sum((item.monthly for item in active), Decimal("0"))
        if weight <= 0:
            break
        distributed = Decimal("0")
        overflow = Decimal("0")
        for index, item in enumerate(active):
            share = (
                remaining_amount - distributed
                if index == len(active) - 1
                else _ceil_money(remaining_amount * item.monthly / weight)
            )
            share = min(share, remaining_amount - distributed)
            distributed += share
            before = item.saved
            credited = min(share, max(Decimal("0"), item.target - item.saved))
            item.saved += credited
            result[item.key] = (before, item.saved)
            overflow += share - credited
        remaining_amount = overflow
        active = [item for item in active if item.saved < item.target]
        if overflow <= 0:
            break
    return result


def _apply_forecast_obligation_funding(
    allocator: FinancialAllocator,
    obligations: list[ForecastObligation],
    distribution,
) -> dict[str, tuple[Decimal, Decimal]]:
    if distribution is None:
        return {}
    changes: dict[str, tuple[Decimal, Decimal]] = {}
    payments_by_envelope: dict[str, list[ForecastObligation]] = {}
    for item in obligations:
        if item.active and item.kind == "payment":
            payments_by_envelope.setdefault(item.envelope, []).append(item)
    for envelope, items in payments_by_envelope.items():
        allocation = Decimal(distribution.allocations.get(f"КЖ:{envelope}", 0))
        monthly_total = sum((item.monthly for item in items), Decimal("0"))
        envelope_target = Decimal(
            allocator.settings.life_categories.get(envelope, monthly_total)
        )
        funded = (
            min(monthly_total, allocation * monthly_total / envelope_target)
            if envelope_target > 0
            else Decimal("0")
        )
        changes.update(_distribute_forecast_funding(items, funded))

    taxes = [
        item for item in obligations
        if item.active and item.kind == "tax"
    ]
    tax_funded = Decimal(distribution.allocations.get("КЖ:Налоги", 0))
    changes.update(_distribute_forecast_funding(
        taxes,
        min(tax_funded, sum((item.monthly for item in taxes), Decimal("0"))),
    ))
    return changes


def _complete_forecast_obligation(
    allocator: FinancialAllocator,
    item: ForecastObligation,
) -> None:
    item.active = False
    if item.kind == "payment":
        current = Decimal(allocator.settings.life_categories.get(item.envelope, 0))
        updated = max(Decimal("0"), current - item.applied_monthly)
        if updated > 0:
            allocator.settings.life_categories[item.envelope] = updated
        else:
            allocator.settings.life_categories.pop(item.envelope, None)
        allocator.settings.remove_automatic_life_obligation(item.settings_key)
    else:
        allocator.settings.tax_catchups.pop(item.settings_key, None)


def _settle_forecast_minimum_payments(
    allocator: FinancialAllocator,
    distribution,
) -> dict:
    """Apply only minimum payments that the simulated allocation can fund."""
    active = [
        (index, credit)
        for index, credit in enumerate(allocator.settings.credits)
        if credit.active
    ]
    required = sum((credit.minimum_payment for _, credit in active), Decimal("0"))
    funded = (
        Decimal(distribution.allocations.get("Мин. платеж", 0))
        if distribution is not None
        else Decimal("0")
    )
    if required > 0 and funded + Decimal("0.01") < required:
        return {
            "distribution": distribution,
            "payments": [],
            "minimum_payment_shortfall": required - funded,
        }
    payments = [
        (index, credit.process_minimum_payment())
        for index, credit in active
    ]
    return {
        "distribution": distribution,
        "payments": payments,
        "minimum_payment_shortfall": Decimal("0"),
    }


def _cyclic_work_income(allocator: FinancialAllocator) -> Decimal:
    """Translate the user's cycle-wide monthly average into a work-month income."""
    work_months = max(Decimal("1"), Decimal(allocator.settings.income_work_months))
    gap_months = max(Decimal("1"), Decimal(allocator.settings.income_gap_months))
    cycle_income = Decimal(allocator.settings.average_income) * (work_months + gap_months)
    gap_income = Decimal(allocator.settings.reliable_gap_income) * gap_months
    return max(Decimal("0"), (cycle_income - gap_income) / work_months)


def _process_cyclic_forecast_month(
    allocator: FinancialAllocator,
    *,
    tax_rate: Decimal,
) -> dict:
    """Run one real work/break month for the cyclic forecast profile."""
    if not allocator.state.current_cycle_phase:
        allocator.state.current_cycle_phase = "work"
        allocator.state.current_phase_months_remaining = (
            allocator.settings.income_work_months
        )

    income_type = next(iter(allocator.settings.income_type_tax_rates), "Основной доход")
    distribution = None
    if allocator.state.current_cycle_phase == "break":
        allocator.state.reset_period()
        gap_income = max(Decimal("0"), Decimal(allocator.settings.reliable_gap_income))
        if gap_income > 0:
            distribution = allocator.process_income(
                gap_income,
                income_type,
                reset_period=False,
                tax_override=gap_income * Decimal(tax_rate) / Decimal("100"),
            )
        allocator.pay_intercontract_salary()
        if allocator.state.intercontract_months_remaining <= 0:
            allocator.start_new_work_phase()
    else:
        work_income = _cyclic_work_income(allocator)
        if work_income > 0:
            distribution = allocator.process_income(
                work_income,
                income_type,
                reset_period=True,
                tax_override=work_income * Decimal(tax_rate) / Decimal("100"),
            )
        else:
            allocator.state.reset_period()
        if allocator.advance_work_month() <= 0:
            allocator.start_intercontract_break()

    return _settle_forecast_minimum_payments(allocator, distribution)


def _process_average_forecast_month(
    allocator: FinancialAllocator,
    month: int,
    *,
    tax_rate: Decimal,
    obligations: list[ForecastObligation] | None = None,
    forecast_today: date | None = None,
):
    """Провести один усреднённый месяц на прогнозной копии профиля."""
    average_income = Decimal(allocator.settings.average_income)
    if average_income <= 0:
        return None
    obligations = obligations or []
    forecast_today = forecast_today or moscow_today()
    as_of = add_calendar_months(forecast_today, month - 1)
    _prepare_forecast_obligations(allocator, obligations, as_of=as_of)
    if allocator.profile_id == "cyclic":
        month_result = _process_cyclic_forecast_month(
            allocator,
            tax_rate=tax_rate,
        )
    else:
        income_type = next(iter(allocator.settings.income_type_tax_rates), "Основной доход")
        result = allocator.process_income(
            average_income,
            income_type,
            reset_period=True,
            tax_override=average_income * Decimal(tax_rate) / Decimal("100"),
        )
        month_result = _settle_forecast_minimum_payments(allocator, result)

    changes = _apply_forecast_obligation_funding(
        allocator,
        obligations,
        month_result["distribution"],
    )
    completed: list[tuple[ForecastObligation, Decimal, Decimal]] = []
    for item in obligations:
        if not item.active or item.saved < item.target:
            continue
        before, after = changes.get(item.key, (item.target, item.saved))
        completed.append((item, before, after))
        _complete_forecast_obligation(allocator, item)

    next_checkpoint = add_calendar_months(forecast_today, month)
    overdue = [
        {
            "key": item.key,
            "kind": item.kind,
            "title": item.title,
            "target": item.target,
            "saved": item.saved,
            "amount": max(Decimal("0"), item.target - item.saved),
            "due_date": item.due_date,
            "month": Decimal(month),
        }
        for item in obligations
        if item.active and item.due_date <= next_checkpoint and item.saved < item.target
    ]
    month_result["completed_obligations"] = completed
    month_result["obligation_shortfalls"] = overdue
    return month_result


def simulate_financial_path(
    source: FinancialAllocator,
    *,
    max_months: int = 360,
    today: date | None = None,
) -> dict:
    """Построить маршрут на копии профиля без изменения реальных балансов."""
    simulated = _forecast_copy(source)
    today = today or moscow_today()
    trackers = _path_trackers(source)
    obligations = _forecast_obligations(source, today=today)
    milestones: list[PathMilestone] = []
    achieved: set[str] = set()
    starting_mode = source.active_mode()

    for tracker in trackers:
        if tracker["value"](simulated) >= tracker["target"]:
            achieved.add(tracker["key"])

    average_income = Decimal(simulated.settings.average_income)
    forecast_tax_rate, tax_history_months = historical_income_tax_rate(source)
    if average_income <= 0:
        return {
            "milestones": milestones,
            "achieved": achieved,
            "unresolved": (
                [item["key"] for item in trackers if item["key"] not in achieved]
                + [item.key for item in obligations if item.active]
            ),
            "average_income": average_income,
            "starting_mode": starting_mode,
            "income_tax_rate": forecast_tax_rate,
            "income_tax_history_months": tax_history_months,
            "debt_payment_shortfall": None,
            "obligation_shortfalls": [],
        }

    previous_mode = starting_mode
    debt_payment_shortfall = None
    obligation_shortfalls: list[dict] = []

    for month in range(1, max_months + 1):
        before = {
            item["key"]: Decimal(item["value"](simulated))
            for item in trackers
            if item["key"] not in achieved
        }

        month_result = _process_average_forecast_month(
            simulated,
            month,
            tax_rate=forecast_tax_rate,
            obligations=obligations,
            forecast_today=today,
        )

        shortfall = Decimal(month_result["minimum_payment_shortfall"])
        if shortfall > 0:
            debt_payment_shortfall = {
                "month": Decimal(month),
                "amount": shortfall,
            }
            break

        for item, before_saved, after_saved in month_result["completed_obligations"]:
            milestones.append(PathMilestone(
                key=item.key,
                months=_crossing_month(
                    month,
                    before_saved,
                    after_saved,
                    item.target,
                ),
                label=item.title,
                icon="🏛" if item.kind == "tax" else "🧾",
                kind=item.kind,
                order=-40 if item.kind == "tax" else -30,
                amount=item.target,
                due_date=item.due_date,
            ))

        if month_result["obligation_shortfalls"]:
            obligation_shortfalls = list(month_result["obligation_shortfalls"])
            break

        current_mode = simulated.active_mode()
        # Во время перерыва циклическая цель Фонда зарплаты временно уменьшается
        # вместе с числом оставшихся месяцев. Для карты это не достижение полного
        # резервного слоя: кубки 4–6 привязываются ниже к фиксированным сущностям.
        reported_mode = (
            min(current_mode, 3)
            if simulated.profile_id == "cyclic"
            else current_mode
        )
        if reported_mode > previous_mode:
            for level in range(previous_mode + 1, reported_mode + 1):
                milestones.append(PathMilestone(
                    key=f"level:{level}",
                    months=Decimal(month),
                    label=(
                        f"{simulated.mode_display_name(level)} — "
                        f"{simulated.mode_title(level)}"
                    ),
                    icon="🏆",
                    kind="level",
                    order=-100 + level,
                ))
            previous_mode = reported_mode

        for tracker in trackers:
            key = tracker["key"]
            if key in achieved:
                continue
            after = Decimal(tracker["value"](simulated))
            if after < tracker["target"]:
                continue
            crossing_month = _crossing_month(
                month,
                before[key],
                after,
                tracker["target"],
            )
            milestones.append(PathMilestone(
                key=key,
                months=crossing_month,
                label=tracker["label"],
                icon=tracker["icon"],
                kind=tracker["kind"],
                order=tracker["order"],
            ))
            achieved.add(key)

            if simulated.profile_id == "cyclic":
                reached_level = {
                    "salary:min": 4,
                    "salary:full": 5,
                    "pillow": 6,
                }.get(key)
                if reached_level is not None and reached_level > previous_mode:
                    milestones.append(PathMilestone(
                        key=f"level:{reached_level}",
                        months=crossing_month,
                        label=(
                            f"{simulated.mode_display_name(reached_level)} — "
                            f"{simulated.mode_title(reached_level)}"
                        ),
                        icon="🏆",
                        kind="level",
                        order=-100 + reached_level,
                    ))
                    previous_mode = reached_level

        if (
            len(achieved) == len(trackers)
            and previous_mode >= simulated.profile_mode_total
            and not any(item.active for item in obligations)
        ):
            break

    milestones.sort(key=lambda item: (item.months, item.order, item.label))
    debt_milestones = sorted(
        (item for item in milestones if item.kind == "debt"),
        key=lambda item: (item.months, item.order, item.label),
    )
    if debt_milestones:
        last_debt = debt_milestones[-1]
        milestones[milestones.index(last_debt)] = PathMilestone(
            key="debts:all",
            months=last_debt.months,
            label="Долгов нет",
            icon="💳",
            kind="debt",
            order=last_debt.order,
        )
        milestones.sort(key=lambda item: (item.months, item.order, item.label))
    return {
        "milestones": milestones,
        "achieved": achieved,
        "unresolved": (
            [item["key"] for item in trackers if item["key"] not in achieved]
            + [item.key for item in obligations if item.active]
        ),
        "average_income": average_income,
        "starting_mode": starting_mode,
        "income_tax_rate": forecast_tax_rate,
        "income_tax_history_months": tax_history_months,
        "debt_payment_shortfall": debt_payment_shortfall,
        "obligation_shortfalls": obligation_shortfalls,
    }


def _milestone_by_key(result: dict) -> dict[str, PathMilestone]:
    return {item.key: item for item in result["milestones"]}


def _reserve_eta(
    result: dict,
    key: str,
    current: Decimal,
    target: Decimal,
) -> str:
    if current >= target:
        return "уже сформирован"
    milestone = _milestone_by_key(result).get(key)
    if milestone is None:
        return "срок пока не удалось рассчитать"
    return f"примерно {human_path_duration(milestone.months)}"


def financial_path_text(source: FinancialAllocator, result: dict) -> str:
    average = result["average_income"]
    lines = [
        "<b>МОЙ ФИНАНСОВЫЙ ПУТЬ</b>",
        "",
        f"Расчёт построен на среднем доходе <b>{rub(average)}</b> и текущих балансах.",
        f"Сейчас: <b>{escape(source.mode_display_name())}</b> — {escape(source.mode_title())}",
    ]
    tax_months = int(result.get("income_tax_history_months", 0))
    tax_rate = Decimal(result.get("income_tax_rate", 0))
    if tax_months:
        lines.append(
            f"Налог на доход — средняя фактическая ставка за "
            f"<b>{tax_months} {_plural(tax_months, 'месяц', 'месяца', 'месяцев')}</b>: "
            f"<b>{_rate_text(tax_rate)}%</b>."
        )
    else:
        lines.append(
            f"Пока нет полной истории налогов: прогноз использует максимальную "
            f"настроенную ставку <b>{_rate_text(tax_rate)}%</b>."
        )
    lines.extend(["", "<b>ОТДЕЛЬНЫЕ РЕЗЕРВЫ</b>", ""])

    if source.profile_id == "cyclic":
        current = source.state.intercontract_reserve
        minimum = source.intercontract_current_life_limit
        full = source.intercontract_current_limit
        lines.extend([
            f"🏦 <b>Фонд зарплаты</b> — {rub(current)} из {rub(full)}",
            f"Минимальный слой: {_reserve_eta(result, 'salary:min', current, minimum)}",
            f"Полный размер: {_reserve_eta(result, 'salary:full', current, full)}",
            "",
        ])

    pillow = source.pillow_total_balance
    pillow_target = source.settings.force_majeure_limit
    lines.extend([
        f"🛡 <b>Подушка</b> — {rub(pillow)} из {rub(pillow_target)}",
        f"Срок: {_reserve_eta(result, 'pillow', pillow, pillow_target)}",
        "",
    ])

    if source.settings.needs_stabilizer:
        current = source.state.pillow_stabilizer
        minimum = source.settings.stabilizer_life_limit
        full = source.settings.stabilizer_full_limit
        lines.extend([
            f"🛟 <b>Стабилизатор</b> — {rub(current)} из {rub(full)}",
            f"Минимальный слой: {_reserve_eta(result, 'stabilizer:min', current, minimum)}",
            f"Полный размер: {_reserve_eta(result, 'stabilizer:full', current, full)}",
            "",
        ])

    route_events = [item for item in result["milestones"] if item.kind != "reserve"]
    lines.extend(["<b>БЛИЖАЙШИЕ ЭТАПЫ</b>", ""])
    if route_events:
        for milestone in route_events:
            lines.extend([
                f"Через <b>{human_path_duration(milestone.months)}</b>",
                f"{milestone.icon} {escape(milestone.label)}",
            ])
            if milestone.kind in {"payment", "tax"}:
                details = rub(Decimal(milestone.amount or 0))
                if milestone.due_date is not None:
                    details += f" · до {milestone.due_date.strftime('%d.%m.%Y')}"
                lines.append(details)
            lines.append("")
    else:
        lines.extend([
            "Новых конечных этапов пока нет: возможно, они уже достигнуты или для них не задана сумма.",
            "",
        ])

    if result["unresolved"]:
        lines.extend([
            "Часть этапов не удалось рассчитать в пределах 30 лет при текущих условиях.",
            "",
        ])
    shortfall = result.get("debt_payment_shortfall")
    if shortfall:
        lines.extend([
            "⚠️ <b>ДАЛЬНЕЙШИЙ ПУТЬ ПОКА НЕ РАССЧИТАН</b>",
            f"При текущем среднем доходе на минимальные платежи не хватает "
            f"<b>{rub(Decimal(shortfall['amount']))} в месяц</b>.",
            "Аллокатор не стал придумывать срок погашения и последующие даты.",
            "",
        ])
    for shortfall in result.get("obligation_shortfalls", ()):
        due = shortfall.get("due_date")
        due_text = due.strftime("%d.%m.%Y") if due is not None else "указанному сроку"
        lines.extend([
            f"⚠️ <b>{escape(str(shortfall['title']))}</b>",
            f"К {due_text} не хватает <b>{rub(Decimal(shortfall['amount']))}</b>.",
            "Последующие даты не рассчитаны: сначала нужно изменить сумму, срок или план.",
            "",
        ])
    note = (
        "Это ориентир, а не обещание: сверхдоход может сократить сроки, "
        "а доход ниже среднего — увеличить."
    )
    if source.profile_id == "cyclic":
        note += (
            " Для циклического профиля рабочие месяцы и перерыв моделируются отдельно: "
            "во время перерыва Аллокатор расходует Фонд зарплаты и учитывает только "
            "указанный надёжный доход."
        )
    lines.append(f"<i>{note}</i>")
    return "\n".join(lines)


def _level_requirement(source: FinancialAllocator, level: int) -> str:
    if level == 2:
        return "Подготовить базовую защиту на время погашения долгов"
    if level == 3:
        return "Закрыть все активные долги"
    if source.profile_id == "stable":
        return "Сформировать Подушку"
    if source.profile_id == "piecework":
        return {
            4: "Сформировать Подушку",
            5: "Сформировать минимальный слой Стабилизатора",
            6: "Сформировать полный размер Стабилизатора",
        }.get(level, "Выполнить условия предыдущего уровня")
    return {
        4: "Сформировать минимальный слой Фонда зарплаты",
        5: "Сформировать полный размер Фонда зарплаты",
        6: "Сформировать Подушку",
    }.get(level, "Выполнить условия предыдущего уровня")


def simulate_level_forecast(
    source: FinancialAllocator,
    *,
    max_months: int = 360,
) -> dict:
    """Построить последовательность переходов от текущего до максимального уровня."""
    path = simulate_financial_path(source, max_months=max_months)
    milestone_map = _milestone_by_key(path)
    current_level = source.active_mode()
    projections = []
    for level in range(current_level + 1, source.profile_mode_total + 1):
        milestone = milestone_map.get(f"level:{level}")
        projections.append(LevelProjection(
            level=level,
            display_name=source.mode_display_name(level),
            title=source.mode_title(level),
            requirement=_level_requirement(source, level),
            months=milestone.months if milestone else None,
        ))
    return {
        "average_income": Decimal(source.settings.average_income),
        "current_level": current_level,
        "current_display_name": source.mode_display_name(),
        "current_title": source.mode_title(),
        "maximum_level": source.profile_mode_total,
        "projections": projections,
        "max_months": max_months,
        "debt_payment_shortfall": path.get("debt_payment_shortfall"),
        "obligation_shortfalls": path.get("obligation_shortfalls", []),
    }


def level_forecast_text(result: dict) -> str:
    lines = [
        "<b>ПРОГНОЗ УРОВНЕЙ</b>",
        "",
        f"Средний доход — <b>{rub(result['average_income'])}</b>",
        f"Сейчас: <b>{escape(result['current_display_name'])}</b> — {escape(result['current_title'])}",
        "",
    ]
    if not result["projections"]:
        lines.extend([
            "🏆 Максимальный уровень уже достигнут.",
            "",
            "Новые поступления распределяются по правилам максимального уровня.",
        ])
        return "\n".join(lines)

    debt_shortfall = result.get("debt_payment_shortfall")
    obligation_shortfalls = result.get("obligation_shortfalls", ())
    blocked = bool(debt_shortfall or obligation_shortfalls)
    if debt_shortfall:
        lines.extend([
            "⚠️ Дальнейшие переходы не рассчитаны: на минимальные платежи "
            f"по долгам не хватает {rub(Decimal(debt_shortfall['amount']))} в месяц.",
            "",
        ])
    for obligation in obligation_shortfalls:
        due = obligation.get("due_date")
        due_text = due.strftime("%d.%m.%Y") if due is not None else "сроку"
        lines.extend([
            f"⚠️ <b>{escape(str(obligation['title']))}</b>: к {due_text} "
            f"не хватает {rub(Decimal(obligation['amount']))}.",
            "Сроки следующих уровней после этой даты не придуманы.",
            "",
        ])

    for projection in result["projections"]:
        duration = (
            f"примерно {human_path_duration(projection.months)}"
            if projection.months is not None
            else (
                "дальнейший расчёт остановлен"
                if blocked
                else f"не удалось рассчитать в пределах {result['max_months'] // 12} лет"
            )
        )
        lines.extend([
            f"🏆 <b>{escape(projection.display_name)}</b> — {escape(projection.title)}",
            f"Условие — {escape(projection.requirement)}",
            f"Срок от сегодняшнего уровня — <b>{duration}</b>",
            "",
        ])
    lines.append(
        "<i>Все сроки накопительные и считаются от текущего состояния. "
        "Сверхдоход может приблизить переходы, а доход ниже среднего — отдалить.</i>"
    )
    return "\n".join(lines)


def simulate_debt_forecast(
    source: FinancialAllocator,
    *,
    max_months: int = 360,
    today: date | None = None,
) -> dict:
    """Рассчитать погашение каждого долга по действующему маршруту Аллокатора."""
    simulated = _forecast_copy(source)
    today = today or moscow_today()
    obligations = _forecast_obligations(source, today=today)
    active_indices = [
        index
        for index, credit in enumerate(simulated.settings.credits)
        if credit.active
    ]
    records = {
        index: {
            "name": simulated.settings.credits[index].name,
            "starting_balance": Decimal(simulated.settings.credits[index].principal_balance),
            "annual_rate": Decimal(simulated.settings.credits[index].annual_rate),
            "minimum_payment": Decimal(simulated.settings.credits[index].minimum_payment),
            "payoff_months": None,
            "interest_paid": Decimal("0"),
        }
        for index in active_indices
    }
    average_income = Decimal(simulated.settings.average_income)
    forecast_tax_rate, tax_history_months = historical_income_tax_rate(source)
    debt_payment_shortfall = None
    obligation_shortfalls: list[dict] = []

    if average_income > 0:
        for month in range(1, max_months + 1):
            before = {
                index: Decimal(simulated.settings.credits[index].principal_balance)
                for index in active_indices
                if records[index]["payoff_months"] is None
            }
            if not before:
                break
            month_result = _process_average_forecast_month(
                simulated,
                month,
                tax_rate=forecast_tax_rate,
                obligations=obligations,
                forecast_today=today,
            )
            shortfall = Decimal(month_result["minimum_payment_shortfall"])
            if shortfall > 0:
                debt_payment_shortfall = {
                    "month": Decimal(month),
                    "amount": shortfall,
                }
                break
            if month_result["obligation_shortfalls"]:
                obligation_shortfalls = list(month_result["obligation_shortfalls"])
                break
            for index, payment in month_result["payments"]:
                if index in records:
                    records[index]["interest_paid"] += Decimal(payment["interest"])

            for index in active_indices:
                credit = simulated.settings.credits[index]
                if records[index]["payoff_months"] is not None:
                    continue
                after = Decimal(credit.principal_balance)
                if after > 0:
                    continue
                starting = records[index]["starting_balance"]
                progress_before = starting - before[index]
                progress_after = starting - after
                records[index]["payoff_months"] = _crossing_month(
                    month,
                    progress_before,
                    progress_after,
                    starting,
                )
    projections: list[DebtProjection] = []
    for index in active_indices:
        record = records[index]
        current_balance = Decimal(simulated.settings.credits[index].principal_balance)
        principal_paid = record["starting_balance"] - current_balance
        projections.append(DebtProjection(
            name=record["name"],
            starting_balance=record["starting_balance"],
            annual_rate=record["annual_rate"],
            minimum_payment=record["minimum_payment"],
            payoff_months=record["payoff_months"],
            remaining_balance=current_balance,
            interest_paid=record["interest_paid"],
            total_paid=principal_paid + record["interest_paid"],
        ))

    payoff_values = [item.payoff_months for item in projections]
    overall_months = (
        max(payoff_values)
        if payoff_values and all(value is not None for value in payoff_values)
        else None
    )
    return {
        "average_income": average_income,
        "strategy": source.settings.debt_strategy,
        "minimum_payment_total": sum(
            (item.minimum_payment for item in projections),
            Decimal("0"),
        ),
        "starting_balance_total": sum(
            (item.starting_balance for item in projections),
            Decimal("0"),
        ),
        "interest_total": sum(
            (item.interest_paid for item in projections),
            Decimal("0"),
        ),
        "total_paid": sum(
            (item.total_paid for item in projections),
            Decimal("0"),
        ),
        "remaining_balance_total": sum(
            (item.remaining_balance for item in projections),
            Decimal("0"),
        ),
        "overall_months": overall_months,
        "projections": projections,
        "max_months": max_months,
        "income_tax_rate": forecast_tax_rate,
        "income_tax_history_months": tax_history_months,
        "debt_payment_shortfall": debt_payment_shortfall,
        "obligation_shortfalls": obligation_shortfalls,
    }


def _debt_duration(months: Decimal | None, max_months: int) -> str:
    if months is None:
        years = max(1, max_months // 12)
        return f"не удалось рассчитать в пределах {years} {_plural(years, 'года', 'лет', 'лет')}"
    return f"примерно {human_path_duration(months)}"


def debt_forecast_text(source: FinancialAllocator, result: dict) -> str:
    lines = [
        "<b>ПРОГНОЗ ПОГАШЕНИЯ ДОЛГОВ</b>",
        "",
        f"Средний доход — <b>{rub(result['average_income'])}</b>",
        f"Стратегия — <b>{escape(result['strategy'])}</b>",
        f"Общий остаток — <b>{rub(result['starting_balance_total'])}</b>",
        f"Минимальные платежи — <b>{rub(result['minimum_payment_total'])} в месяц</b>",
        "",
    ]
    if not result["projections"]:
        lines.append("Активных долгов сейчас нет.")
        return "\n".join(lines)

    shortfall = result.get("debt_payment_shortfall")
    if shortfall:
        lines.extend([
            "⚠️ <b>СРОК ПОГАШЕНИЯ СЕЙЧАС РАССЧИТАТЬ НЕЛЬЗЯ</b>",
            "",
            "При текущем среднем доходе Аллокатор не может полностью "
            "профинансировать обязательные минимальные платежи.",
            f"Не хватает — <b>{rub(Decimal(shortfall['amount']))} в месяц</b>.",
            "Ниже показано состояние долгов на момент, когда возник дефицит.",
            "",
        ])
    obligation_shortfalls = result.get("obligation_shortfalls", ())
    for obligation in obligation_shortfalls:
        due = obligation.get("due_date")
        due_text = due.strftime("%d.%m.%Y") if due is not None else "сроку"
        lines.extend([
            f"⚠️ <b>{escape(str(obligation['title']))}</b>",
            f"К {due_text} не хватает <b>{rub(Decimal(obligation['amount']))}</b>.",
            "Поэтому дальнейший прогноз долгов остановлен.",
            "",
        ])
    blocked = bool(shortfall or obligation_shortfalls)

    for projection in result["projections"]:
        limit_years = max(1, result["max_months"] // 12)
        limit_text = f"{limit_years} {_plural(limit_years, 'год', 'года', 'лет')}"
        payment_label = (
            "Всего к выплате"
            if projection.payoff_months is not None
            else f"Выплачено за {limit_text}"
        )
        lines.extend([
            f"💳 <b>{escape(projection.name)}</b>",
            f"Остаток — {rub(projection.starting_balance)}",
            f"Ставка — {_rate_text(projection.annual_rate)}% годовых",
            f"Минимальный платёж — {rub(projection.minimum_payment)}",
            f"Погашение — <b>{'срок рассчитать нельзя' if blocked else _debt_duration(projection.payoff_months, result['max_months'])}</b>",
            f"Проценты с текущего момента — {rub(projection.interest_paid)}",
            f"{payment_label} — {rub(projection.total_paid)}",
            *(
                [f"Остаток после прогноза — {rub(projection.remaining_balance)}"]
                if projection.payoff_months is None
                else []
            ),
            "",
        ])

    total_payment_label = (
        "Всего к выплате"
        if result["overall_months"] is not None
        else f"Выплачено за {limit_text}"
    )
    lines.extend([
        "<b>ВСЕ ДОЛГИ</b>",
        f"Полное погашение — <b>{'срок рассчитать нельзя' if blocked else _debt_duration(result['overall_months'], result['max_months'])}</b>",
        f"Основной долг — {rub(result['starting_balance_total'])}",
        f"Ожидаемые проценты — {rub(result['interest_total'])}",
        f"{total_payment_label} — <b>{rub(result['total_paid'])}</b>",
        *(
            [f"Остаток после прогноза — {rub(result['remaining_balance_total'])}"]
            if result["overall_months"] is None
            else []
        ),
        "",
        "<i>Прогноз учитывает минимальные и досрочные платежи по текущим правилам Аллокатора. "
        "Он предполагает средний доход без новых долгов, просрочек, изменения ставок и условий кредитов.</i>",
    ])
    return "\n".join(lines)


def simulate_goal_forecast(
    source: FinancialAllocator,
    *,
    max_months: int = 360,
    today: date | None = None,
) -> dict:
    """Собрать подробный прогноз конечных Целей, не включая Сундуки."""
    today = today or moscow_today()
    path = simulate_financial_path(source, max_months=max_months, today=today)
    milestones = _milestone_by_key(path)
    projections: list[GoalProjection] = []

    for goal in source.settings.goals:
        if not goal.is_goal or goal.status not in {"active", "paused"}:
            continue
        current = max(
            Decimal("0"),
            Decimal(source.state.goal_balances.get(goal.name, goal.balance)),
        )
        target = (
            None
            if goal.full_target_amount is None
            else Decimal(goal.full_target_amount)
        )
        remaining = None if target is None else max(Decimal("0"), target - current)
        milestone = milestones.get(f"goal:{goal.uid}")
        if remaining is not None and remaining <= 0:
            completion_months: Decimal | None = Decimal("0")
        elif goal.status != "active" or goal.currency_code != "RUB":
            completion_months = None
        else:
            completion_months = milestone.months if milestone else None

        completion_date = (
            today + timedelta(days=float(completion_months * Decimal("30.4375")))
            if completion_months is not None and completion_months > 0
            else today if completion_months == 0 else None
        )
        deadline = None
        if goal.deadline:
            try:
                deadline = date.fromisoformat(goal.deadline)
            except ValueError:
                deadline = None
        deadline_on_track = (
            completion_date <= deadline
            if completion_date is not None and deadline is not None
            else None
        )
        engine_forecast = source.goal_forecast(goal, today=today)
        required_monthly = engine_forecast.get("required_monthly")
        projections.append(GoalProjection(
            name=goal_display_name(goal.name, False),
            currency_code=goal.currency_code,
            current=current,
            target=target,
            remaining=remaining,
            percentage=Decimal(goal.percentage),
            status=goal.status,
            completion_months=completion_months,
            completion_date=completion_date,
            deadline=deadline,
            deadline_on_track=deadline_on_track,
            required_monthly=(
                None if required_monthly is None else Decimal(required_monthly)
            ),
        ))

    projections.sort(key=lambda item: (
        item.completion_months is None,
        item.completion_months if item.completion_months is not None else Decimal("Infinity"),
        item.name.casefold(),
    ))
    return {
        "today": today,
        "average_income": Decimal(source.settings.average_income),
        "projections": projections,
        "max_months": max_months,
        "debt_payment_shortfall": path.get("debt_payment_shortfall"),
        "obligation_shortfalls": path.get("obligation_shortfalls", []),
    }


def _goal_amount(value: Decimal, currency_code: str) -> str:
    if currency_code == "RUB":
        return rub(value)
    return f"{fmt_money(value)} {escape(currency_code)}"


def _goal_completion_text(projection: GoalProjection, max_months: int) -> str:
    if projection.remaining is not None and projection.remaining <= 0:
        return "уже достигнута"
    if projection.status == "paused":
        return "прогноз приостановлен вместе с Целью"
    if projection.currency_code != "RUB":
        return "нельзя рассчитать без курса к рублю"
    if projection.completion_months is None:
        return f"не удалось рассчитать в пределах {max_months // 12} лет"
    return f"примерно {human_path_duration(projection.completion_months)}"


def goal_forecast_text(result: dict) -> str:
    lines = [
        "<b>ПРОГНОЗ ЦЕЛЕЙ</b>",
        "",
        f"Средний доход — <b>{rub(result['average_income'])}</b>",
        "Сундуки в прогноз не входят: у них нет конечной суммы.",
        "",
    ]
    if not result["projections"]:
        lines.append("Текущих Целей с конечной суммой сейчас нет.")
        return "\n".join(lines)

    debt_shortfall = result.get("debt_payment_shortfall")
    if debt_shortfall:
        lines.extend([
            "⚠️ Дальнейшие сроки не рассчитаны: среднего дохода не хватает "
            f"на минимальные платежи по долгам на {rub(Decimal(debt_shortfall['amount']))} в месяц.",
            "",
        ])
    for obligation in result.get("obligation_shortfalls", ()):
        due = obligation.get("due_date")
        due_text = due.strftime("%d.%m.%Y") if due is not None else "сроку"
        lines.extend([
            f"⚠️ <b>{escape(str(obligation['title']))}</b>: к {due_text} "
            f"не хватает {rub(Decimal(obligation['amount']))}.",
            "Сроки Целей после этой даты не придуманы — сначала нужно изменить сумму, срок или план.",
            "",
        ])

    for projection in result["projections"]:
        lines.append(f"⭐ <b>{escape(projection.name)}</b>")
        if projection.target is None:
            lines.append("Конечная сумма не задана.")
        else:
            lines.extend([
                f"Накоплено — {_goal_amount(projection.current, projection.currency_code)} "
                f"из {_goal_amount(projection.target, projection.currency_code)}",
                f"Осталось — {_goal_amount(projection.remaining, projection.currency_code)}",
            ])
        lines.append(f"Доля распределения — {_rate_text(projection.percentage)}%")
        lines.append(
            f"Достижение — <b>{_goal_completion_text(projection, result['max_months'])}</b>"
        )
        if projection.completion_date is not None and projection.completion_months != 0:
            lines.append(
                "Ориентировочно — "
                f"{goal_completion_calendar_text(result.get('today', moscow_today()), projection.completion_months)}"
            )
        if projection.deadline is not None:
            lines.append(f"Запланированный срок — {projection.deadline.strftime('%d.%m.%Y')}")
            if projection.deadline_on_track is True:
                lines.append("По текущему прогнозу — <b>успеваете к сроку</b>")
            elif projection.deadline_on_track is False:
                lines.append("По текущему прогнозу — <b>не успеваете к сроку</b>")
            else:
                lines.append("Сравнить прогноз со сроком пока невозможно.")
            if (
                projection.required_monthly is not None
                and projection.currency_code == "RUB"
            ):
                lines.append(
                    f"Чтобы успеть, нужно в среднем — {_goal_amount(projection.required_monthly, projection.currency_code)} в месяц"
                )
        lines.append("")

    lines.append(
        "<i>Сроки учитывают уже накопленные суммы, текущие доли Целей и последовательное "
        "перераспределение денег после достижения предыдущих Целей. Сверхдоход может сократить сроки.</i>"
    )
    return "\n".join(lines)


def _apply_forecast_indexation(
    allocator: FinancialAllocator,
    inflation_rate: Decimal,
    income_growth_rate: Decimal,
) -> None:
    """Проиндексировать доходы, стоимость жизни, резервы и активные цели."""
    settings = allocator.settings
    price_factor = Decimal("1") + Decimal(inflation_rate) / Decimal("100")
    income_factor = Decimal("1") + Decimal(income_growth_rate) / Decimal("100")

    settings.average_income *= income_factor
    settings.reliable_gap_income *= income_factor

    settings.critical_life *= price_factor
    settings.base_critical_life *= price_factor
    settings.household_reserve *= price_factor
    settings.life_categories = {
        name: amount * price_factor
        for name, amount in settings.life_categories.items()
    }
    settings.household_reserve_categories = {
        name: amount * price_factor
        for name, amount in settings.household_reserve_categories.items()
    }
    settings.contract_obligations = {
        name: amount * price_factor
        for name, amount in settings.contract_obligations.items()
    }
    settings.historical_gifts_monthly *= price_factor

    for budget in settings.phase_life_budgets.values():
        budget.critical_life *= price_factor
        budget.household_reserve *= price_factor
        budget.life_categories = {
            name: amount * price_factor
            for name, amount in budget.life_categories.items()
        }
        budget.household_reserve_categories = {
            name: amount * price_factor
            for name, amount in budget.household_reserve_categories.items()
        }

    for goal in settings.goals:
        if (
            goal.status == "active"
            and goal.is_goal
            and goal.target_amount is not None
            and goal.currency_code == "RUB"
        ):
            goal.target_amount *= price_factor


def simulate_investment_forecast(
    source: FinancialAllocator,
    annual_rate: Decimal,
    *,
    inflation_rate: Decimal = Decimal("0"),
    income_growth_rate: Decimal = Decimal("0"),
    horizons: tuple[int, ...] = (5, 10, 15, 20, 30),
    today: date | None = None,
) -> dict:
    """Прогнозировать капитал с фактическими будущими взносами Аллокатора."""
    rate = Decimal(annual_rate)
    inflation = Decimal(inflation_rate)
    income_growth = Decimal(income_growth_rate)
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError("Доходность должна быть от 0 до 100 процентов.")
    if not inflation.is_finite() or inflation < 0 or inflation > 100:
        raise ValueError("Инфляция должна быть от 0 до 100 процентов.")
    if not income_growth.is_finite() or income_growth < 0 or income_growth > 100:
        raise ValueError("Индексация дохода должна быть от 0 до 100 процентов.")
    normalized_horizons = tuple(sorted({int(year) for year in horizons if int(year) > 0}))
    if not normalized_horizons:
        raise ValueError("Нужен хотя бы один срок прогноза.")

    simulated = _forecast_copy(source)
    today = today or moscow_today()
    obligations = _forecast_obligations(source, today=today)
    starting_capital = max(Decimal("0"), Decimal(simulated.state.investments))
    capital = starting_capital
    contributions = Decimal("0")
    first_year_contributions = Decimal("0")
    current_year_contributions = Decimal("0")
    monthly_rate = (
        Decimal("0")
        if rate == 0
        else Decimal(str((1 + float(rate / Decimal("100"))) ** (1 / 12) - 1))
    )
    horizon_months = {year * 12: year for year in normalized_horizons}
    projections: list[InvestmentProjection] = []
    forecast_tax_rate, _ = historical_income_tax_rate(source)
    allocation_blocked = False
    debt_payment_shortfall = None
    obligation_shortfalls: list[dict] = []

    for month in range(1, max(horizon_months) + 1):
        if month > 1 and (month - 1) % 12 == 0:
            _apply_forecast_indexation(simulated, inflation, income_growth)
            current_year_contributions = Decimal("0")
        month_result = (
            None
            if allocation_blocked
            else _process_average_forecast_month(
                simulated,
                month,
                tax_rate=forecast_tax_rate,
                obligations=obligations,
                forecast_today=today,
            )
        )
        if (
            month_result is not None
            and Decimal(month_result["minimum_payment_shortfall"]) > 0
        ):
            allocation_blocked = True
            debt_payment_shortfall = {
                "month": Decimal(month),
                "amount": Decimal(month_result["minimum_payment_shortfall"]),
            }
        if month_result is not None and month_result["obligation_shortfalls"]:
            allocation_blocked = True
            obligation_shortfalls = list(month_result["obligation_shortfalls"])
        result = (
            month_result["distribution"]
            if month_result is not None and not allocation_blocked
            else None
        )
        contribution = (
            Decimal(result.allocations.get("Инвестиции", Decimal("0")))
            if result is not None
            else Decimal("0")
        )
        contribution = max(Decimal("0"), contribution)
        capital = capital * (Decimal("1") + monthly_rate) + contribution
        contributions += contribution
        current_year_contributions += contribution
        if month <= 12:
            first_year_contributions += contribution

        if month in horizon_months:
            own_funds = starting_capital + contributions
            years = horizon_months[month]
            inflation_factor = (
                Decimal("1") + inflation / Decimal("100")
            ) ** years
            projections.append(InvestmentProjection(
                years=years,
                capital=capital,
                own_funds=own_funds,
                profit=max(Decimal("0"), capital - own_funds),
                real_capital=capital / inflation_factor,
                monthly_contribution=current_year_contributions / Decimal("12"),
            ))

    return {
        "annual_rate": rate,
        "inflation_rate": inflation,
        "income_growth_rate": income_growth,
        "starting_capital": starting_capital,
        "average_income": Decimal(source.settings.average_income),
        "first_year_monthly_contribution": first_year_contributions / Decimal("12"),
        "projections": projections,
        "debt_payment_shortfall": debt_payment_shortfall,
        "obligation_shortfalls": obligation_shortfalls,
    }


def _rate_text(rate: Decimal) -> str:
    normalized = Decimal(rate).normalize()
    return format(normalized, "f").replace(".", ",")


def investment_forecast_text(source: FinancialAllocator, result: dict) -> str:
    lines = [
        "<b>ПРОГНОЗ ИНВЕСТИЦИЙ</b>",
        "",
        f"Сейчас инвестировано — <b>{rub(result['starting_capital'])}</b>",
        f"Средний доход — <b>{rub(result['average_income'])}</b>",
        f"Ожидаемая доходность — <b>{_rate_text(result['annual_rate'])}% годовых</b>",
        f"Инфляция — <b>{_rate_text(result['inflation_rate'])}% в год</b>",
        f"Индексация дохода — <b>{_rate_text(result['income_growth_rate'])}% в год</b>",
        f"Среднее пополнение в первый год — <b>{rub(result['first_year_monthly_contribution'])} в месяц</b>",
        "",
    ]
    debt_shortfall = result.get("debt_payment_shortfall")
    if debt_shortfall:
        lines.extend([
            "⚠️ Среднего дохода не хватает на минимальные платежи по долгам. "
            "После этой точки новые инвестиции в прогноз не добавляются; растёт только уже накопленный капитал.",
            "",
        ])
    for obligation in result.get("obligation_shortfalls", ()):
        due = obligation.get("due_date")
        due_text = due.strftime("%d.%m.%Y") if due is not None else "сроку"
        lines.extend([
            f"⚠️ <b>{escape(str(obligation['title']))}</b>: к {due_text} "
            f"не хватает {rub(Decimal(obligation['amount']))}.",
            "После этой точки новые инвестиции в прогноз не добавляются; растёт только уже накопленный капитал.",
            "",
        ])
    for projection in result["projections"]:
        lines.extend([
            f"<b>ЧЕРЕЗ {projection.years} {_plural(projection.years, 'ГОД', 'ГОДА', 'ЛЕТ')}</b>",
            f"Среднее пополнение — <b>{rub(projection.monthly_contribution)} в месяц</b>",
            f"Капитал — <b>{rub(projection.capital)}</b>",
            f"В сегодняшних деньгах — <b>{rub(projection.real_capital)}</b>",
            f"Ваши деньги — {rub(projection.own_funds)}",
            f"Инвестиционная прибыль — {rub(projection.profit)}",
            "",
        ])
    note = (
        "Расчёт ежемесячно прогоняет текущий финансовый маршрут: сначала обязательные расходы, "
        "долги и защитные резервы, затем инвестиции. Поэтому будущие пополнения могут меняться "
        "по мере перехода на новые уровни. Раз в год доход индексируется по выбранной ставке, "
        "а стоимость жизни, размеры резервов и активные рублёвые цели — по инфляции. "
        "Доходность начисляется ежемесячно."
    )
    if source.profile_id == "cyclic":
        note += (
            " Для циклического профиля рабочие месяцы и перерыв моделируются отдельно, "
            "включая расходование Фонда зарплаты."
        )
    lines.extend([
        f"<i>{note}</i>",
        "",
        "<i>Не учтены комиссии, налоги брокера и изменение рыночной доходности. "
        "Это сценарий, а не гарантия и не инвестиционная рекомендация.</i>",
    ])
    return "\n".join(lines)


async def render_investment_forecast(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    annual_rate: Decimal,
    inflation_rate: Decimal,
    income_growth_rate: Decimal,
) -> None:
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала настройте финансовый профиль.")
        return
    result = await asyncio.to_thread(
        simulate_investment_forecast,
        allocator,
        annual_rate,
        inflation_rate=inflation_rate,
        income_growth_rate=income_growth_rate,
    )
    await state.clear()
    await message.answer(
        investment_forecast_text(allocator, result),
        reply_markup=keyboard([
            [("Изменить доходность", "forecast:investments")],
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "menu:forecast")
async def show_forecast_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    await state.clear()
    await callback.message.answer(
        "<b>ПРОГНОЗ</b>\n\n"
        "Посмотрите, как распределится будущая сумма, когда могут быть достигнуты "
        "ваши финансовые этапы и как со временем может расти инвестиционный капитал.",
        reply_markup=keyboard([
            [("Распределить будущую сумму", "forecast:distribution")],
            [("Мой финансовый путь", "forecast:path")],
            [("Инвестиции", "forecast:investments")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "forecast:distribution")
async def start_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    await state.clear()
    await state.set_state(ForecastStates.available_before_purchases)
    await state.update_data(forecast_user_id=callback.from_user.id)
    await send_text_with_image(
        callback.message,
        "<b>ПРОГНОЗ РАСПРЕДЕЛЕНИЯ</b>\n\n"
        "Хотите проверить, как Аллокатор распределил бы ваш будущий доход прямо сейчас?\n"
        "Экспериментируйте сколько угодно, балансы не изменятся.\n"
        "——————\n<b>→ Введите сумму после налога.</b>",
        Path(__file__).resolve().parent / "assets/menu/distribution_forecast.png",
        reply_markup=keyboard([[("Отмена", "forecast:cancel")]]),
    )


@router.callback_query(F.data == "forecast:path")
async def show_financial_path(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    result = await asyncio.to_thread(simulate_financial_path, allocator)
    rows = [[("Прогноз уровней", "forecast:levels")]]
    if any(
        goal.is_goal and goal.status in {"active", "paused"}
        for goal in allocator.settings.goals
    ):
        rows.append([("Прогноз целей", "forecast:goals")])
    if any(credit.active for credit in allocator.settings.credits):
        rows.append([("Прогноз долгов", "forecast:debts")])
    rows.extend([
        [("Обновить прогноз", "forecast:path")],
        [("К прогнозам", "menu:forecast")],
        [("Главное меню", "menu:back")],
    ])
    navigation = keyboard(rows)
    try:
        image = await asyncio.to_thread(
            render_financial_path_card,
            allocator,
            result,
        )
        await callback.message.answer_photo(
            photo=BufferedInputFile(image, filename="financial-path.png"),
            caption=(
                "<b>МОЙ ФИНАНСОВЫЙ ПУТЬ</b>\n\n"
                "Сроки рассчитаны по текущему среднему доходу и действующему плану. "
                "После нового дохода или изменения настроек карта обновится."
            ),
            reply_markup=navigation,
        )
    except Exception:
        await callback.message.answer(
            financial_path_text(allocator, result),
            reply_markup=navigation,
        )


@router.callback_query(F.data == "forecast:levels")
async def show_level_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    result = await asyncio.to_thread(simulate_level_forecast, allocator)
    await callback.message.answer(
        level_forecast_text(result),
        reply_markup=keyboard([
            [("Обновить прогноз", "forecast:levels")],
            [("К финансовому пути", "forecast:path")],
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "forecast:goals")
async def show_goal_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    result = await asyncio.to_thread(simulate_goal_forecast, allocator)
    await callback.message.answer(
        goal_forecast_text(result),
        reply_markup=keyboard([
            [("Обновить прогноз", "forecast:goals")],
            [("К финансовому пути", "forecast:path")],
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "forecast:debts")
async def show_debt_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    result = await asyncio.to_thread(simulate_debt_forecast, allocator)
    await callback.message.answer(
        debt_forecast_text(allocator, result),
        reply_markup=keyboard([
            [("Обновить прогноз", "forecast:debts")],
            [("К финансовому пути", "forecast:path")],
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "forecast:investments")
async def show_investment_forecast_intro(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    await callback.message.answer(
        "<b>ИНВЕСТИЦИИ</b>\n\n"
        f"Сейчас инвестировано — <b>{rub(allocator.state.investments)}</b>\n"
        f"Средний доход — <b>{rub(allocator.settings.average_income)}</b>\n\n"
        "Выберите ожидаемую среднегодовую доходность. Её можно будет изменить и пересчитать сценарий.",
        reply_markup=keyboard([
            [("5%", "forecastinvest:rate:5"), ("10%", "forecastinvest:rate:10"), ("15%", "forecastinvest:rate:15")],
            [("Указать свою доходность", "forecastinvest:custom")],
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data.startswith("forecastinvest:rate:"))
async def choose_investment_rate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    rate = parse_decimal(callback.data.rsplit(":", 1)[-1])
    if rate is None:
        await callback.message.answer("Не удалось прочитать доходность. Выберите её ещё раз.")
        return
    await ask_investment_inflation(callback.message, state, callback.from_user.id, rate)


@router.callback_query(F.data == "forecastinvest:custom")
async def ask_custom_investment_rate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ForecastStates.investment_rate)
    await state.update_data(forecast_user_id=callback.from_user.id)
    await callback.message.answer(
        "<b>ОЖИДАЕМАЯ ДОХОДНОСТЬ</b>\n\n"
        "Введите среднегодовую доходность от 0 до 100%.\n"
        "Например: <code>12,5</code>",
        reply_markup=keyboard([
            [("Назад", "forecast:investments")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.message(ForecastStates.investment_rate)
async def save_custom_investment_rate(message: Message, state: FSMContext):
    rate = parse_decimal((message.text or "").replace("%", ""))
    if rate is None or rate < 0 or rate > 100:
        await message.answer("Введите число от 0 до 100. Например: <code>12,5</code>")
        return
    data = await state.get_data()
    telegram_id = int(data.get("forecast_user_id", message.from_user.id))
    await ask_investment_inflation(message, state, telegram_id, rate)


async def ask_investment_inflation(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    annual_rate: Decimal,
) -> None:
    await state.set_state(ForecastStates.investment_inflation)
    await state.update_data(
        forecast_user_id=telegram_id,
        investment_rate=str(annual_rate),
    )
    await message.answer(
        "<b>ИНФЛЯЦИЯ</b>\n\n"
        "Она будет ежегодно увеличивать стоимость жизни, размеры резервов и активные рублёвые цели, "
        "а также позволит показать капитал в сегодняшних деньгах.\n\n"
        "Выберите ожидаемую среднегодовую инфляцию.",
        reply_markup=keyboard([
            [("4%", "forecastinvest:inflation:4"), ("5%", "forecastinvest:inflation:5"), ("7%", "forecastinvest:inflation:7")],
            [("Указать свою инфляцию", "forecastinvest:inflation-custom")],
            [("Назад", "forecast:investments")],
        ]),
    )


@router.callback_query(F.data.startswith("forecastinvest:inflation:"))
async def choose_investment_inflation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    inflation = parse_decimal(callback.data.rsplit(":", 1)[-1])
    if inflation is None:
        await callback.message.answer("Не удалось прочитать инфляцию. Выберите её ещё раз.")
        return
    data = await state.get_data()
    if "investment_rate" not in data:
        await callback.message.answer(
            "Этот сценарий уже завершён. Начните новый расчёт.",
            reply_markup=keyboard([[("Начать расчёт", "forecast:investments")]]),
        )
        return
    await ask_investment_income_growth(
        callback.message,
        state,
        callback.from_user.id,
        Decimal(data["investment_rate"]),
        inflation,
    )


@router.callback_query(F.data == "forecastinvest:inflation-custom")
async def ask_custom_investment_inflation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ForecastStates.investment_inflation)
    await callback.message.answer(
        "<b>ОЖИДАЕМАЯ ИНФЛЯЦИЯ</b>\n\n"
        "Введите среднегодовую инфляцию от 0 до 100%.\n"
        "Например: <code>6,5</code>",
        reply_markup=keyboard([[("Назад", "forecast:investments")]]),
    )


@router.message(ForecastStates.investment_inflation)
async def save_custom_investment_inflation(message: Message, state: FSMContext):
    inflation = parse_decimal((message.text or "").replace("%", ""))
    if inflation is None or inflation < 0 or inflation > 100:
        await message.answer("Введите число от 0 до 100. Например: <code>6,5</code>")
        return
    data = await state.get_data()
    if "investment_rate" not in data:
        await state.clear()
        await message.answer(
            "Этот сценарий уже завершён. Начните новый расчёт.",
            reply_markup=keyboard([[("Начать расчёт", "forecast:investments")]]),
        )
        return
    await ask_investment_income_growth(
        message,
        state,
        int(data.get("forecast_user_id", message.from_user.id)),
        Decimal(data["investment_rate"]),
        inflation,
    )


async def ask_investment_income_growth(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    annual_rate: Decimal,
    inflation_rate: Decimal,
) -> None:
    await state.set_state(ForecastStates.investment_income_growth)
    await state.update_data(
        forecast_user_id=telegram_id,
        investment_rate=str(annual_rate),
        investment_inflation=str(inflation_rate),
    )
    await message.answer(
        "<b>ИНДЕКСАЦИЯ ДОХОДА</b>\n\n"
        "На столько процентов будет ежегодно увеличиваться средний доход. "
        "Если доход растёт медленнее инфляции, свободная сумма для инвестиций может уменьшаться.\n\n"
        "Выберите ожидаемый ежегодный рост дохода.",
        reply_markup=keyboard([
            [("0%", "forecastinvest:growth:0"), ("3%", "forecastinvest:growth:3"), ("5%", "forecastinvest:growth:5")],
            [("Как инфляция", "forecastinvest:growth-inflation")],
            [("Указать свой рост", "forecastinvest:growth-custom")],
            [("Назад", "forecast:investments")],
        ]),
    )


async def _render_saved_investment_scenario(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    income_growth: Decimal,
) -> None:
    data = await state.get_data()
    if "investment_rate" not in data or "investment_inflation" not in data:
        await state.clear()
        await message.answer(
            "Этот сценарий уже завершён. Начните новый расчёт.",
            reply_markup=keyboard([[("Начать расчёт", "forecast:investments")]]),
        )
        return
    await message.answer(
        "Рассчитываю. Ожидайте, симуляция может занять некоторое время"
    )
    await render_investment_forecast(
        message,
        state,
        telegram_id,
        Decimal(data["investment_rate"]),
        Decimal(data["investment_inflation"]),
        income_growth,
    )


@router.callback_query(F.data.startswith("forecastinvest:growth:"))
async def choose_investment_income_growth(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except TelegramBadRequest:
        # A long-running forecast or a temporarily busy small instance can
        # make Telegram expire the callback acknowledgement.  The selected
        # value is still valid, so continue instead of stranding the user.
        pass
    growth = parse_decimal(callback.data.rsplit(":", 1)[-1])
    if growth is None:
        await callback.message.answer("Не удалось прочитать индексацию. Выберите её ещё раз.")
        return
    await _render_saved_investment_scenario(
        callback.message,
        state,
        callback.from_user.id,
        growth,
    )


@router.callback_query(F.data == "forecastinvest:growth-inflation")
async def use_inflation_as_income_growth(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass
    data = await state.get_data()
    if "investment_inflation" not in data:
        await callback.message.answer(
            "Этот сценарий уже завершён. Начните новый расчёт.",
            reply_markup=keyboard([[("Начать расчёт", "forecast:investments")]]),
        )
        return
    await _render_saved_investment_scenario(
        callback.message,
        state,
        callback.from_user.id,
        Decimal(data["investment_inflation"]),
    )


@router.callback_query(F.data == "forecastinvest:growth-custom")
async def ask_custom_investment_income_growth(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ForecastStates.investment_income_growth)
    await callback.message.answer(
        "<b>ОЖИДАЕМЫЙ РОСТ ДОХОДА</b>\n\n"
        "Введите ежегодную индексацию дохода от 0 до 100%.\n"
        "Например: <code>3</code>",
        reply_markup=keyboard([[("Назад", "forecast:investments")]]),
    )


@router.message(ForecastStates.investment_income_growth)
async def save_custom_investment_income_growth(message: Message, state: FSMContext):
    growth = parse_decimal((message.text or "").replace("%", ""))
    if growth is None or growth < 0 or growth > 100:
        await message.answer("Введите число от 0 до 100. Например: <code>3</code>")
        return
    data = await state.get_data()
    await _render_saved_investment_scenario(
        message,
        state,
        int(data.get("forecast_user_id", message.from_user.id)),
        growth,
    )


@router.message(ForecastStates.available_before_purchases)
async def save_available_forecast(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму.")
        return
    await state.update_data(forecast_available=str(value), forecast_user_id=message.from_user.id)
    allocator = db.load_allocator(message.from_user.id)
    if allocator is None:
        await message.answer("Сначала настройте финансовый профиль.")
        return
    if allocator.settings.income_rhythm != "cyclic":
        await render_forecast(message, state, None)
        return
    months = allocator.settings.income_gap_months
    await state.set_state(ForecastStates.gap_months)
    await message.answer(
        "<b>СКОЛЬКО МЕСЯЦЕВ НУЖНО БУДЕТ ЖИТЬ ДО СЛЕДУЮЩЕГО ДОХОДА?</b>\n\n"
        f"В профиле указано: <b>{months} мес.</b>",
        reply_markup=keyboard([
            [(f"Оставить {months} мес.", "forecastmonths:profile")],
            [("Указать другое значение", "forecastmonths:custom")],
            [("Отмена", "forecast:cancel")],
        ]),
    )


@router.callback_query(ForecastStates.gap_months, F.data.startswith("forecastmonths:"))
async def choose_forecast_months(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data.endswith(":custom"):
        await callback.message.answer("Введите количество месяцев от 1 до 24.")
        return
    allocator = db.load_allocator(callback.from_user.id)
    await render_forecast(callback.message, state, allocator.settings.income_gap_months)


@router.message(ForecastStates.gap_months)
async def save_custom_forecast_months(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 24:
        await message.answer("Введите количество месяцев от 1 до 24.")
        return
    await render_forecast(message, state, value)


def forecast_allocation_text(source, allocations, plain: bool = False):
    groups = [[], [], [], [], [], []]

    def amount_text(value: Decimal) -> str:
        return fmt_money(value) if plain else rub(value)

    def add(group: int, label: str, amount) -> None:
        amount = Decimal(str(amount))
        if amount > 0:
            groups[group].append(f"{label} — {amount_text(amount)}")

    planned_tax = sum(
        (
            Decimal(str(value))
            for key, value in allocations.items()
            if key in {"КЖ:Налог", "КЖ:Налоги"}
        ),
        Decimal("0"),
    )
    add(0, "🏛️ Налоги", planned_tax)

    if getattr(source, "profile_id", "stable") == "cyclic":
        add(1, "🏦 Фонд Зарплаты", allocations.get("Фонд Зарплаты", 0))
    add(1, "🛡️ Подушка", allocations.get("Подушка", 0))
    if getattr(source.settings, "needs_stabilizer", False):
        add(1, "🛟 Стабилизатор", allocations.get("Стабилизатор дохода", 0))
    add(1, "📈 Инвестиции", allocations.get("Инвестиции", 0))

    add(2, "💳 Минимальные платежи", allocations.get("Мин. платеж", 0))
    add(2, "💳 Досрочное погашение", allocations.get("Досрочное", 0))
    for key, value in allocations.items():
        if not key.startswith("Рабочие обязательства:"):
            continue
        parts = key.split(":", 2)
        label = (
            f"{parts[2]} → {parts[1]}"
            if len(parts) == 3
            else key.split(":", 1)[1]
        )
        add(2, f"💳 {escape(label)}", value)

    life_names = [
        name
        for name in getattr(source.settings, "life_categories", {})
        if name not in {"Налог", "Налоги", "Зарплата"}
    ]
    if not life_names:
        life_names = [
            key[3:]
            for key in allocations
            if key.startswith("КЖ:")
            and key not in {"КЖ:Налог", "КЖ:Налоги", "КЖ:Зарплата"}
        ]
    for name in sorted(
        life_names,
        key=lambda item: Decimal(str(allocations.get(f"КЖ:{item}", 0))),
        reverse=True,
    ):
        add(3, f"❤️ {escape(name)}", allocations.get(f"КЖ:{name}", 0))
    add(3, "❤️ Зарплата", allocations.get("КЖ:Зарплата", 0))

    add(4, "💚 Бытовой резерв", allocations.get("Бытовой резерв", 0))

    active_goals = list(
        getattr(source.settings, "active_goals", source.settings.goals)
    )
    if active_goals:
        for goal in sorted(
            active_goals,
            key=lambda item: Decimal(str(allocations.get(f"Цели:{item.name}", 0))),
            reverse=True,
        ):
            add(
                5,
                ("🧳 " if goal.is_chest else "⭐️ ")
                + escape(goal_display_name(goal.name, goal.is_chest)),
                allocations.get(f"Цели:{goal.name}", 0),
            )
    else:
        add(5, "⭐️ Цели", allocations.get("Цели:ЦЕЛИ (всего)", 0))

    return "\n\n".join("\n".join(group) for group in groups if group) or "Нет свободной суммы для распределения"


async def render_forecast(message: Message, state: FSMContext, months: Decimal | None):
    data = await state.get_data()
    source = db.load_allocator(data.get("forecast_user_id", message.from_user.id))
    if source is None:
        return
    available = Decimal(data["forecast_available"])
    if source.settings.income_rhythm == "cyclic":
        simulated, result, obligations, distributable = simulate_cyclic_forecast(
            source, available, Decimal("0"), Decimal(months)
        )
    else:
        simulated, result, obligations, distributable = simulate_standard_forecast(
            source, available, Decimal("0")
        )
    lines = ["<b>ПРОГНОЗ РАСПРЕДЕЛЕНИЯ</b>", "",
             f"Ожидаемая сумма — <b>{rub_plain(available)}</b>"]
    strategy, explicitly_selected = last_income_distribution_strategy(source)
    strategy_label = (
        "защита и цели"
        if strategy == "balanced"
        else "всё в защиту"
    )
    if not explicitly_selected:
        strategy_label += " (по умолчанию)"
    lines.append(f"Свободная часть — <b>{strategy_label}</b>")
    if source.profile_id == "cyclic":
        lines.extend([f"Обязательства на время контракта — <b>{rub(obligations)}</b>",
                      f"К распределению после возвращения — <b>{rub(distributable)}</b>",
                      f"Период без дохода — <b>{months} мес.</b>"])
    lines.extend(["", "<b>ПРЕДПОЛАГАЕМОЕ РАСПРЕДЕЛЕНИЕ</b>", "",
                  allocation_table_from_text(
                      forecast_allocation_text(source, result.allocations if result else {}, plain=True)
                  )])
    critical = max(
        Decimal("0"),
        simulated.settings.critical_life - simulated.critical_life_progress,
    )
    sustainable = simulated.sustainable_life_remaining
    lines.extend(["", "<b>ОЖИДАЕМЫЙ УРОВЕНЬ</b>", "",
                  f"{'🏆' * simulated.active_mode()}", "",
                  "—————————",
                  "",
                  f"↺ <b>Баланс жизни</b> — {rub_plain(simulated.state.life_balance)}",
                  f"➤ До <b>Критич. минимума</b> — {rub_plain(critical)}",
                  f"➤ До <b>Устойч. жизни</b> — {rub_plain(sustainable)}", "",
                  "🛡️ Подушка — "
                  f"<b>{reserve_fraction(rub_plain(simulated.state.pillow_balance), rub_plain(simulated.settings.force_majeure_limit))}</b>"])
    if simulated.settings.needs_stabilizer:
        lines.append(
            "🛟 Стабилизатор — "
            f"<b>{reserve_fraction(rub_plain(simulated.state.stabilizer_balance), rub_plain(simulated.settings.stabilizer_full_limit))}</b>"
        )
    if simulated.profile_id == "cyclic":
        lines.append(
            "🏦 Фонд Зарплаты — "
            f"<b>{reserve_fraction(rub_plain(simulated.state.intercontract_reserve), rub_plain(simulated.intercontract_current_limit))}</b>"
        )
    await state.clear()
    await message.answer(
        "\n".join(lines),
        reply_markup=keyboard([
            [("Повторить прогноз", "forecast:distribution")],
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "forecast:cancel")
async def cancel_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "Прогноз отменён.",
        reply_markup=keyboard([
            [("К прогнозам", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


def simulate_cyclic_forecast(
    source: FinancialAllocator,
    available: Decimal,
    purchases: Decimal,
    months: Decimal,
    *,
    today: date | None = None,
):
    """Рассчитать прогноз на копии профиля, не меняя реальные балансы."""
    available = Decimal(available)
    purchases = Decimal(purchases)
    months = Decimal(months)
    if source.settings.income_rhythm != "cyclic":
        raise ValueError("Прогноз доступен только для циклического профиля.")
    if available < 0 or purchases < 0 or purchases > available:
        raise ValueError("Некорректные суммы прогноза.")
    if months < 1 or months > 24:
        raise ValueError("Период прогноза должен быть от 1 до 24 месяцев.")

    obligations = source.settings.contract_obligations_total
    distributable = max(Decimal("0"), available - purchases - obligations)
    simulated = _forecast_copy(source)
    _refresh_distribution_forecast_targets(
        source,
        simulated,
        today=today or moscow_today(),
    )
    simulated.settings.income_gap_months = months

    # Доход ожидается к окончанию рабочей части. Текущий рабочий месяц не должен
    # повторно забирать деньги на российскую жизнь: её плановая нехватка уже
    # целиком представлена Фондом Зарплаты.
    simulated.state.life_balance = simulated.settings.household_life
    simulated.state.household_reserve_progress = simulated.settings.household_reserve
    simulated.state.period_income = Decimal("0")
    simulated.state.period_allocations = {}
    simulated.state.period_life_topups = {}

    result = (
        simulated.process_income(distributable, "Прогноз", tax_override=Decimal("0"))
        if distributable > 0
        else None
    )
    return simulated, result, obligations, distributable


def simulate_standard_forecast(
    source: FinancialAllocator,
    available: Decimal,
    purchases: Decimal,
    *,
    today: date | None = None,
):
    """Прогноз обычного поступления для стабильного или сдельного профиля."""
    available = Decimal(available)
    purchases = Decimal(purchases)
    if source.settings.income_rhythm == "cyclic":
        raise ValueError("Для циклического профиля нужен прогноз с периодом перерыва.")
    if available < 0 or purchases < 0 or purchases > available:
        raise ValueError("Некорректные суммы прогноза.")
    distributable = available - purchases
    simulated = _forecast_copy(source)
    _refresh_distribution_forecast_targets(
        source,
        simulated,
        today=today or moscow_today(),
    )
    result = (
        simulated.process_income(distributable, "Прогноз", tax_override=Decimal("0"))
        if distributable > 0
        else None
    )
    return simulated, result, Decimal("0"), distributable
