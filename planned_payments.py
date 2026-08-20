from datetime import date
from decimal import Decimal, ROUND_CEILING

from storage import db


ZERO = Decimal("0")


def months_remaining(due_date: str, today: date | None = None) -> int:
    today = today or date.today()
    due = date.fromisoformat(due_date)
    if due <= today:
        return 1
    return max(1, (due.year - today.year) * 12 + due.month - today.month)


def refresh_planned_payment_targets(
    telegram_id: int, allocator, today: date | None = None
) -> None:
    """Подтягивает месячный взнос к фактическому остатку и сроку."""
    obligations = db.load_planned_payments(telegram_id)
    old_by_envelope: dict[str, Decimal] = {}
    new_by_envelope: dict[str, Decimal] = {}
    for item in obligations:
        envelope = item["envelope_name"]
        old_by_envelope[envelope] = old_by_envelope.get(envelope, ZERO) + item["monthly_amount"]
        remaining = max(ZERO, item["target_amount"] - item["saved_amount"])
        monthly = (remaining / Decimal(months_remaining(item["due_date"], today))).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        new_by_envelope[envelope] = new_by_envelope.get(envelope, ZERO) + monthly
        if monthly != item["monthly_amount"]:
            db.update_planned_payment_monthly(telegram_id, item["id"], monthly)

    for envelope, new_total in new_by_envelope.items():
        delta = new_total - old_by_envelope.get(envelope, ZERO)
        if delta == ZERO:
            continue
        allocator.settings.life_categories[envelope] = max(
            ZERO, allocator.settings.life_categories.get(envelope, ZERO) + delta
        )
        allocator.settings.critical_life = max(
            sum(allocator.settings.life_categories.values(), ZERO),
            allocator.settings.critical_life + delta,
        )


def apply_planned_payment_allocation(
    telegram_id: int,
    allocator,
    envelope_name: str,
    amount: Decimal,
) -> None:
    """Зачисляет пополнение конверта в активные плановые платежи."""
    amount = Decimal(str(amount))
    obligations = [
        item
        for item in db.load_planned_payments(telegram_id)
        if item["envelope_name"] == envelope_name
    ]
    target_total = sum((item["monthly_amount"] for item in obligations), ZERO)
    if amount <= ZERO or target_total <= ZERO:
        return
    envelope_target = allocator.settings.life_categories.get(envelope_name, target_total)
    if envelope_target > target_total:
        amount = (amount * target_total / envelope_target).quantize(Decimal("0.01"))
    amount = min(amount, target_total)

    remaining_amount = amount
    active = list(obligations)
    while remaining_amount > ZERO and active:
        weight = sum((item["monthly_amount"] for item in active), ZERO)
        distributed = ZERO
        overflow = ZERO
        for index, item in enumerate(active):
            share = remaining_amount - distributed if index == len(active) - 1 else (
                remaining_amount * item["monthly_amount"] / weight
            ).quantize(Decimal("0.01"))
            distributed += share
            need = max(ZERO, item["target_amount"] - item["saved_amount"])
            credited = min(share, need)
            item["saved_amount"] += credited
            overflow += share - credited
        remaining_amount = overflow
        active = [item for item in active if item["saved_amount"] < item["target_amount"]]
        if overflow == ZERO:
            break

    completed_monthly = ZERO
    for item in obligations:
        completed = item["saved_amount"] >= item["target_amount"]
        db.update_planned_payment_saved(
            telegram_id, item["id"], item["saved_amount"], not completed
        )
        if completed:
            completed_monthly += item["monthly_amount"]

    if completed_monthly > ZERO:
        current = allocator.settings.life_categories.get(envelope_name, ZERO)
        next_target = max(ZERO, current - completed_monthly)
        if next_target > ZERO:
            allocator.settings.life_categories[envelope_name] = next_target
        else:
            allocator.settings.life_categories.pop(envelope_name, None)
        allocator.settings.critical_life = max(
            sum(allocator.settings.life_categories.values(), ZERO),
            allocator.settings.critical_life - completed_monthly,
        )
