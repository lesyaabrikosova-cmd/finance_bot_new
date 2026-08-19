from decimal import Decimal

from storage import db


ZERO = Decimal("0")


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

    distributed = ZERO
    for index, item in enumerate(obligations):
        share = (
            amount - distributed
            if index == len(obligations) - 1
            else (amount * item["monthly_amount"] / target_total).quantize(Decimal("0.01"))
        )
        distributed += share
        remaining = max(ZERO, item["target_amount"] - item["saved_amount"])
        credited = min(share, remaining)
        saved = item["saved_amount"] + credited
        completed = saved >= item["target_amount"]
        db.update_planned_payment_saved(telegram_id, item["id"], saved, not completed)
        if not completed:
            continue

        current = allocator.settings.life_categories.get(envelope_name, ZERO)
        next_target = max(ZERO, current - item["monthly_amount"])
        if next_target > ZERO:
            allocator.settings.life_categories[envelope_name] = next_target
        else:
            allocator.settings.life_categories.pop(envelope_name, None)
        allocator.settings.critical_life = max(
            sum(allocator.settings.life_categories.values(), ZERO),
            allocator.settings.critical_life - item["monthly_amount"],
        )
