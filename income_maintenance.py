"""Maintenance rules for the average income used by forecasts."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation

from financial_engine import FinancialAllocator


def add_calendar_months(value: date, months: int) -> date:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def first_full_month(started_on: date) -> date:
    month_start = started_on.replace(day=1)
    return month_start if started_on.day == 1 else add_calendar_months(month_start, 1)


def completed_piecework_average(
    allocator: FinancialAllocator,
    *,
    started_on: date,
    today: date,
    history: list[dict] | None = None,
) -> tuple[Decimal, str] | None:
    """Return the rolling mean of the last six complete calendar months."""
    current_month = today.replace(day=1)
    oldest_month = add_calendar_months(current_month, -6)
    if history is None:
        history = list(getattr(allocator.state, "distribution_history", ()) or ())
        if not history:
            history = list(getattr(allocator.state, "operation_log", ()) or ())
    dated_operations: list[tuple[date, Decimal]] = []
    for operation in history:
        if operation.get("type") != "income_distribution":
            continue
        try:
            operation_date = date.fromisoformat(str(operation.get("date", ""))[:10])
            income = Decimal(operation.get("income", 0))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if income > 0:
            dated_operations.append((operation_date, income))

    if not dated_operations:
        return None
    history_started_on = min(operation_date for operation_date, _ in dated_operations)
    effective_start = max(started_on, history_started_on)
    if first_full_month(effective_start) > oldest_month:
        return None

    month_keys = [
        (add_calendar_months(oldest_month, offset).year,
         add_calendar_months(oldest_month, offset).month)
        for offset in range(6)
    ]
    totals = {key: Decimal("0") for key in month_keys}
    for operation_date, income in dated_operations:
        key = (operation_date.year, operation_date.month)
        if key in totals:
            totals[key] += income

    average = sum(totals.values(), Decimal("0")) / Decimal("6")
    completed_month = add_calendar_months(current_month, -1)
    return average, completed_month.strftime("%Y-%m")


def completed_cyclic_average(
    allocator: FinancialAllocator,
    *,
    started_on: date,
    today: date,
    history: list[dict] | None = None,
) -> tuple[Decimal, str, int] | None:
    """Average the latest whole cycles, covering at least six full months.

    A five-month work/break cycle therefore uses ten months, while a two-month
    cycle uses six. Empty break months deliberately remain zero in the mean.
    """
    cycle_months = int(
        allocator.settings.income_work_months
        + allocator.settings.income_gap_months
    )
    cycle_months = max(1, cycle_months)
    cycles_in_window = (6 + cycle_months - 1) // cycle_months
    window_months = cycle_months * cycles_in_window
    current_month = today.replace(day=1)
    oldest_month = add_calendar_months(current_month, -window_months)

    if history is None:
        history = list(getattr(allocator.state, "distribution_history", ()) or ())
        if not history:
            history = list(getattr(allocator.state, "operation_log", ()) or ())

    dated_operations: list[tuple[date, Decimal]] = []
    for operation in history:
        if operation.get("type") != "income_distribution":
            continue
        try:
            operation_date = date.fromisoformat(str(operation.get("date", ""))[:10])
            income = Decimal(operation.get("income", 0))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if income > 0:
            dated_operations.append((operation_date, income))

    if not dated_operations:
        return None
    history_started_on = min(operation_date for operation_date, _ in dated_operations)
    effective_start = max(started_on, history_started_on)
    if first_full_month(effective_start) > oldest_month:
        return None

    month_keys = []
    totals: dict[tuple[int, int], Decimal] = {}
    for offset in range(window_months):
        month = add_calendar_months(oldest_month, offset)
        key = (month.year, month.month)
        month_keys.append(key)
        totals[key] = Decimal("0")
    for operation_date, income in dated_operations:
        key = (operation_date.year, operation_date.month)
        if key in totals:
            totals[key] += income

    average = sum((totals[key] for key in month_keys), Decimal("0")) / Decimal(window_months)
    completed_month = add_calendar_months(current_month, -1)
    return average, completed_month.strftime("%Y-%m"), window_months


def stable_review_is_due(
    *,
    started_on: date,
    today: date,
    last_reviewed_on: date | None = None,
    last_reminded_on: date | None = None,
) -> bool:
    """A stable profile gets one reminder six months after the latest anchor."""
    anchors = [started_on]
    if last_reviewed_on is not None:
        anchors.append(last_reviewed_on)
    if last_reminded_on is not None:
        anchors.append(last_reminded_on)
    return today >= add_calendar_months(max(anchors), 6)
