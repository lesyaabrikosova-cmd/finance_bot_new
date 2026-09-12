from __future__ import annotations

import asyncio
import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from html import escape
from io import BytesIO
from secrets import token_urlsafe

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message
try:
    from PIL import Image, ImageDraw
except ImportError:  # Диаграмма не должна мешать работе налогового учёта.
    Image = None
    ImageDraw = None

from financial_engine import fmt_money
from mode_presentation import mode_image_path
from storage import db
from time_utils import moscow_today
from ui import keyboard, main_menu_keyboard
from charts import make_chart, send_chart_report


router = Router()
ZERO = Decimal("0")
MAX_MONEY_INPUT = Decimal("1000000000000")


def tax_obligation_key(tax_type: str, object_name: str) -> str:
    return f"{tax_type} · {object_name}"


def parse_tax_object_name(text: str | None) -> str | None:
    """Return a clean human-readable name, rejecting amounts and commands."""
    value = " ".join((text or "").split())
    if not 2 <= len(value) <= 60:
        return None
    if value.startswith("/") or not any(char.isalpha() for char in value):
        return None
    return value


def set_tax_monthly_target(allocator, key: str, monthly: Decimal) -> None:
    """Synchronise the recurring annual tax norm with the hidden part of KМ."""
    monthly = max(ZERO, Decimal(str(monthly)))
    if monthly > ZERO:
        allocator.settings.planned_taxes[key] = monthly
    else:
        allocator.settings.planned_taxes.pop(key, None)
    allocator.settings.set_automatic_life_obligation(f"tax:{key}", monthly)
    total = sum(allocator.settings.planned_taxes.values(), ZERO)
    if total > ZERO:
        allocator.settings.ensure_life_category_id("Налоги")
        allocator.settings.life_categories["Налоги"] = total
    else:
        allocator.settings.life_categories.pop("Налоги", None)


def annual_tax_monthly_norm(item: dict) -> Decimal:
    """Recurring monthly cost of an annual property tax.

    `monthly_amount` is deliberately not used here: it is a temporary
    catch-up amount needed before the next payment date.
    """
    # Only property, transport and land taxes are predictable annual household
    # costs. Patent and custom dated payments are temporary obligations: their
    # catch-up belongs in the tax envelope, but must not inflate KМ or reserves.
    if item["tax_type"] not in ANNUAL_PROPERTY_TAXES:
        return ZERO
    stored = Decimal(str(item.get("annual_monthly_amount", ZERO)))
    if stored > ZERO:
        return stored
    return (item["target_amount"] / Decimal("12")).quantize(
        Decimal("0.01"), rounding=ROUND_CEILING,
    )


def applied_annual_tax_monthly_norm(item: dict) -> Decimal:
    """Confirmed annual norm currently included in KМ and reserve targets."""
    if item["tax_type"] not in ANNUAL_PROPERTY_TAXES:
        return ZERO
    return max(
        ZERO,
        Decimal(str(item.get("applied_annual_monthly_amount", ZERO))),
    )

TAX_GROUPS = (
    "Налог на доход",
    "Налог на имущество",
    "Транспортный налог",
    "Земельный налог",
    "Другой налог",
)

TAX_COLORS = {
    "Налог на доход": "#7656D8",
    "Налог на имущество": "#E2B93B",
    "Транспортный налог": "#7A7F87",
    "Земельный налог": "#8B5A2B",
    "Другой налог": "#C87452",
}

# Each tax kind has its own visual family. Different profiles or objects use
# neighbouring shades without becoming visually confused with another kind.
INCOME_TAX_PURPLES = (
    "#7656D8", "#8B6FE0", "#A187E8", "#B6A0EE",
    "#C9B9F3", "#6947C8", "#9472D6", "#AD90E3",
    "#D8CBF7", "#5C3AB6", "#8062CE", "#BFA8EC",
)
PROPERTY_TAX_YELLOWS = (
    "#E2B93B", "#F2C94C", "#D6A92D", "#F5D76E",
    "#C99720", "#FFE08A", "#B8860B", "#EBCB64",
    "#CCA43B", "#F7C948", "#D4AF37", "#F0D264",
)
TRANSPORT_TAX_GRAYS = (
    "#7A7F87", "#9298A1", "#626871", "#A9AFB7",
    "#50565E", "#BEC3CA", "#858B94", "#6C727B",
    "#9EA4AC", "#454A51", "#B2B8C0", "#747A82",
)
LAND_TAX_BROWNS = (
    "#8B5A2B", "#A66A3F", "#704522", "#B77A50",
    "#5E3A20", "#C18B62", "#7D4E2A", "#9A6035",
    "#AD7651", "#684126", "#BC825A", "#87532F",
)
OTHER_TAX_ORANGES = (
    "#C87452", "#D88763", "#B56243", "#E29A76",
    "#9E5037", "#ECAF91", "#C06B49", "#D17D59",
)

TAX_DETAIL_PALETTES = {
    "Налог на доход": INCOME_TAX_PURPLES,
    "Налог на имущество": PROPERTY_TAX_YELLOWS,
    "Транспортный налог": TRANSPORT_TAX_GRAYS,
    "Земельный налог": LAND_TAX_BROWNS,
    "Другой налог": OTHER_TAX_ORANGES,
}


class TaxStates(StatesGroup):
    payment_name = State()
    payment_amount = State()
    payment_edit_amount = State()
    payment_review = State()
    obligation_type = State()
    obligation_name = State()
    obligation_amount = State()
    obligation_saved = State()
    obligation_months = State()
    obligation_due_date = State()
    next_obligation_amount = State()
    edit_obligation_name = State()
    edit_obligation_amount = State()


_PAYMENT_CONFIRM_LOCKS: dict[int, asyncio.Lock] = {}
_PROFILE_SAVE_LOCKS: dict[int, asyncio.Lock] = {}


ANNUAL_PROPERTY_TAXES = {
    "Налог на имущество",
    "Транспортный налог",
    "Земельный налог",
}


def annual_tax_due_date(today: date | None = None) -> date:
    """Ближайший российский срок уплаты трёх имущественных налогов."""
    today = today or moscow_today()
    candidate = date(today.year, 12, 1)
    return candidate if today <= candidate else date(today.year + 1, 12, 1)


def tax_funding_date(tax_type: str, due_date: date) -> date:
    """Аллокатор готовит имущественные налоги за месяц до крайнего срока."""
    if tax_type in ANNUAL_PROPERTY_TAXES:
        return date(due_date.year, 11, 1)
    return due_date


def tax_months_remaining(tax_type: str, due_date: date, today: date) -> int:
    ready = tax_funding_date(tax_type, due_date)
    if ready <= today:
        return 1
    return max(1, (ready.year - today.year) * 12 + ready.month - today.month)


def tax_notice_months_remaining(due_date: date, today: date) -> int:
    """Monthly top-ups available after the official notice is received."""
    if due_date <= today:
        return 1
    return max(1, (due_date.year - today.year) * 12 + due_date.month - today.month)


def _tax_ledger(telegram_id: int) -> tuple[dict[tuple, dict], Decimal]:
    """Reconstruct the one real tax account and its virtual buckets.

    A payment leaves the shared bank account once.  It first consumes the tax
    selected by the user, then other buckets in a deterministic order.  This
    keeps the chart total equal to real deposits minus payments even when the
    selected virtual bucket contained less than the amount paid.
    """
    obligations = db.load_tax_obligations(telegram_id, active_only=False)
    allocator = db.load_allocator(telegram_id)
    settings = getattr(allocator, "settings", None)
    buckets: dict[tuple, dict] = {}
    total_contributed = ZERO

    def ensure_bucket(identity: tuple, *, key: str, group: str, detail: str,
                      obligation_id: int | None = None,
                      aliases: set[str] | None = None) -> dict:
        if identity not in buckets:
            buckets[identity] = {
                "amount": ZERO,
                "key": key,
                "group": group,
                "detail": detail,
                "obligation_id": obligation_id,
                "aliases": set(aliases or ()),
            }
        elif aliases:
            buckets[identity]["aliases"].update(aliases)
        return buckets[identity]

    by_key: dict[str, list[dict]] = defaultdict(list)
    for item in obligations:
        key = tax_obligation_key(item["tax_type"], item["object_name"])
        by_key[key].append(item)
        bucket = ensure_bucket(
            ("obligation", int(item["id"])), key=key,
            group=tax_group(item["tax_type"]), detail=item["object_name"],
            obligation_id=int(item["id"]),
        )
        opening = max(ZERO, Decimal(str(item.get("opening_amount", ZERO))))
        bucket["amount"] += opening
        total_contributed += opening

    raw_operations = list(db.load_operations(telegram_id, limit=-1))
    operations = sorted(
        enumerate(raw_operations, start=1),
        key=lambda pair: int(pair[1].get("id", pair[0])),
    )
    for fallback_id, operation in operations:
        payload = operation.get("payload", {})
        if payload.get("type") != "income_distribution":
            continue
        operation_id = int(operation.get("id", fallback_id))
        income_tax = max(ZERO, Decimal(str(payload.get("tax", ZERO))))
        if income_tax > ZERO:
            source = str(payload.get("income_type") or "Другой доход")
            detail = income_tax_detail_label(payload, source, income_tax, settings)
            bucket = ensure_bucket(
                ("income", detail), key=f"Налог на доход · {detail}",
                group="Налог на доход", detail=detail,
                aliases={f"Налог на доход · {source}"},
            )
            bucket["amount"] += income_tax
            total_contributed += income_tax

        planned_details = payload.get("planned_tax_details") or {}
        for key, raw_value in planned_details.items():
            amount = max(ZERO, Decimal(str(raw_value)))
            if amount <= ZERO:
                continue
            candidates = []
            for item in by_key.get(str(key), []):
                start = int(item.get("tracking_started_operation_id") or 0)
                closed = item.get("tracking_closed_operation_id")
                if operation_id > start and (closed is None or operation_id <= int(closed)):
                    candidates.append(item)
            if candidates:
                # A newly added lifecycle has a larger start boundary.  The ID
                # resolves ties in legacy rows that predate lifecycle metadata.
                item = max(
                    candidates,
                    key=lambda row: (
                        int(row.get("tracking_started_operation_id") or 0),
                        int(row["id"]),
                    ),
                )
                bucket = ensure_bucket(
                    ("obligation", int(item["id"])), key=str(key),
                    group=tax_group(item["tax_type"]), detail=item["object_name"],
                    obligation_id=int(item["id"]),
                )
            else:
                group = tax_group(str(key))
                detail = str(key).split(" · ", 1)[-1]
                bucket = ensure_bucket(
                    ("legacy", str(key)), key=str(key), group=group, detail=detail,
                )
            bucket["amount"] += amount
            total_contributed += amount
        if not planned_details:
            legacy_amount = max(
                ZERO,
                Decimal(str((payload.get("allocations") or {}).get("КЖ:Налоги", ZERO))),
            )
            if legacy_amount > ZERO:
                bucket = ensure_bucket(
                    ("legacy", "Налоги Критического минимума"),
                    key="Налоги Критического минимума",
                    group="Налог на имущество",
                    detail="Налоги Критического минимума",
                )
                bucket["amount"] += legacy_amount
                total_contributed += legacy_amount

    group_order = {name: index for index, name in enumerate(TAX_GROUPS)}
    for payment in sorted(db.load_tax_payments(telegram_id), key=lambda row: row["id"]):
        remaining = max(ZERO, Decimal(str(payment["amount"])))
        selected_identity = (
            ("obligation", int(payment["obligation_id"]))
            if payment.get("obligation_id") is not None else None
        )
        selected_group = tax_group(str(payment["tax_name"]))
        ordered = sorted(
            buckets.items(),
            key=lambda pair: (
                0 if selected_identity is not None and pair[0] == selected_identity else
                1 if pair[1]["key"] == payment["tax_name"] else
                2 if payment["tax_name"] in pair[1].get("aliases", set()) else
                3 if pair[1]["group"] == selected_group else 4,
                group_order.get(pair[1]["group"], len(group_order)),
                str(pair[0]),
            ),
        )
        for _, bucket in ordered:
            if remaining <= ZERO:
                break
            consumed = min(bucket["amount"], remaining)
            bucket["amount"] -= consumed
            remaining -= consumed

    # A deleted/closed plan does not remove money from the one real bank
    # account.  If the same tax is added again (or its next annual cycle is
    # created), move the unspent virtual remainder to the current lifecycle.
    # This is a reassignment only: the chart/account total is unchanged.
    active_by_key: dict[str, dict] = {}
    inactive_ids: set[int] = set()
    for item in obligations:
        key = tax_obligation_key(item["tax_type"], item["object_name"])
        if item.get("active"):
            previous = active_by_key.get(key)
            if previous is None or (
                int(item.get("tracking_started_operation_id") or 0), int(item["id"])
            ) > (
                int(previous.get("tracking_started_operation_id") or 0), int(previous["id"])
            ):
                active_by_key[key] = item
        else:
            inactive_ids.add(int(item["id"]))
    for key, current in active_by_key.items():
        destination = ensure_bucket(
            ("obligation", int(current["id"])), key=key,
            group=tax_group(current["tax_type"]), detail=current["object_name"],
            obligation_id=int(current["id"]),
        )
        for identity, bucket in list(buckets.items()):
            if identity == ("obligation", int(current["id"])) or bucket["key"] != key:
                continue
            transferable = (
                identity[0] == "legacy"
                or (identity[0] == "obligation" and int(identity[1]) in inactive_ids)
            )
            if not transferable or bucket["amount"] <= ZERO:
                continue
            destination["amount"] += bucket["amount"]
            bucket["amount"] = ZERO
    return buckets, total_contributed


def virtual_tax_balance(
    telegram_id: int,
    key: str,
    obligation_id: int | None = None,
) -> Decimal:
    """Money virtually assigned to one lifecycle inside the shared account."""
    buckets, _ = _tax_ledger(telegram_id)
    if obligation_id is not None:
        return max(
            ZERO,
            buckets.get(("obligation", int(obligation_id)), {}).get("amount", ZERO),
        )
    return sum(
        (item["amount"] for item in buckets.values() if item["key"] == key), ZERO,
    )


def new_tax_plan_funding(
    telegram_id: int,
    key: str,
    target: Decimal,
    declared_saved: Decimal = ZERO,
) -> tuple[Decimal, Decimal]:
    """Return funded progress and genuinely new opening money for a new plan.

    Money left by an older lifecycle with the same tax name is already in the
    shared bank account.  Reusing it as progress avoids asking the user to save
    the same rubles twice, while ``opening_amount`` includes only additional
    funds that were not previously present in the ledger.
    """
    target = max(ZERO, Decimal(str(target)))
    declared_saved = max(ZERO, Decimal(str(declared_saved)))
    reusable = min(target, virtual_tax_balance(telegram_id, key))
    return max(declared_saved, reusable), max(ZERO, declared_saved - reusable)


def calculate_notice_plan(
    notice_amount: Decimal,
    saved_amount: Decimal,
    due_date: date,
    today: date,
) -> tuple[Decimal, int, Decimal, Decimal]:
    """Return remaining, months, temporary catch-up and stable annual norm."""
    notice_amount = max(ZERO, Decimal(str(notice_amount)))
    saved_amount = max(ZERO, Decimal(str(saved_amount)))
    remaining = max(ZERO, notice_amount - saved_amount)
    months = tax_notice_months_remaining(due_date, today)
    catchup = (
        (remaining / Decimal(months)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING,
        )
        if remaining > ZERO else ZERO
    )
    annual_monthly = (notice_amount / Decimal("12")).quantize(
        Decimal("0.01"), rounding=ROUND_CEILING,
    )
    return remaining, months, catchup, annual_monthly


def calculate_payment_progress(
    target_amount: Decimal,
    paid_before: Decimal,
    current_payment: Decimal,
) -> tuple[Decimal, Decimal, bool]:
    """Return the cumulative payment, remainder and whether the tax is closed."""
    target_amount = max(ZERO, Decimal(str(target_amount)))
    paid_before = max(ZERO, Decimal(str(paid_before)))
    current_payment = max(ZERO, Decimal(str(current_payment)))
    paid_after = paid_before + current_payment
    remaining = max(ZERO, target_amount - paid_after)
    return paid_after, remaining, remaining <= ZERO


def start_next_annual_tax_cycle(
    telegram_id: int,
    item: dict,
    allocator,
    today: date,
) -> dict:
    """Continue saving after payment and schedule next autumn's reminder."""
    targets_before = tax_financial_targets(allocator)
    key = tax_obligation_key(item["tax_type"], item["object_name"])
    try:
        previous_due = date.fromisoformat(item.get("due_date") or "")
        next_due = date(previous_due.year + 1, 12, 1)
    except ValueError:
        next_due = annual_tax_due_date(today)
    if next_due <= today:
        next_due = annual_tax_due_date(today)

    target = Decimal(str(item["target_amount"]))
    virtually_saved = virtual_tax_balance(telegram_id, key, item.get("id"))
    saved_for_next_cycle = min(virtually_saved, target)
    months = tax_months_remaining(item["tax_type"], next_due, today)
    remaining = max(ZERO, target - saved_for_next_cycle)
    monthly = (
        (remaining / Decimal(months)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING,
        )
        if remaining > ZERO else ZERO
    )
    annual_monthly = annual_tax_monthly_norm(item)
    obligation_id = db.add_tax_obligation(
        telegram_id,
        item["tax_type"],
        item["object_name"],
        target,
        saved_for_next_cycle,
        months,
        monthly,
        next_due.isoformat(),
        annual_monthly,
        notice_received=False,
        opening_amount=ZERO,
        source_obligation_id=item.get("id"),
        monthly_period=today.strftime("%Y-%m"),
        applied_annual_monthly_amount=annual_monthly,
    )
    set_tax_monthly_target(allocator, key, annual_monthly)
    if monthly > ZERO:
        allocator.settings.tax_catchups[key] = monthly
    else:
        allocator.settings.tax_catchups.pop(key, None)
    targets_after = tax_financial_targets(allocator)
    return {
        "id": obligation_id,
        "due_date": next_due,
        "monthly_amount": monthly,
        "saved_before": saved_for_next_cycle,
        "annual_monthly_amount": annual_monthly,
        "targets_before": targets_before,
        "targets_after": targets_after,
    }


def tax_financial_targets(allocator) -> dict[str, Decimal | int | str]:
    """Long-term targets affected when a confirmed annual norm is activated."""
    settings = allocator.settings
    priority = allocator.current_protection_priority()
    return {
        "critical_life": settings.critical_life,
        "household_life": settings.household_life,
        "household_reserve": settings.household_reserve,
        "minimum_pillow": settings.minimum_reserve_limit,
        "force_majeure": settings.force_majeure_limit,
        "stabilizer": (
            settings.stabilizer_full_limit if settings.needs_stabilizer else ZERO
        ),
        "salary_fund": (
            allocator.intercontract_current_limit
            if settings.needs_intercontract_reserve else ZERO
        ),
        "mode": allocator.active_mode(),
        "priority": str(priority["name"]) if priority else "Все защитные нормативы сформированы",
    }


def money(value: Decimal) -> str:
    return f"{fmt_money(value)} ₽"


def tax_rate_label(rate: Decimal) -> str:
    """Render an effective tax rate without artificial trailing zeroes."""
    return f"{fmt_money(rate)}%"


def compact_income_tax_profile(profile: str) -> str:
    """Normalize old verbose profile names to the compact UI vocabulary."""
    value = str(profile).strip()
    replacements = (
        ("Самозанятость · Физики", "НПД · ФЛ"),
        ("Самозанятость · Юрики", "НПД · ЮЛ"),
        ("ИП · УСН «Доходы»", "ИП · УСН"),
    )
    for old, new in replacements:
        if value.startswith(old):
            return new + value[len(old):]
    return value


def is_income_tax_profile_label(
    name: str, known_profiles: set[str] | None = None,
) -> bool:
    """Identify legacy tax rules that were stored as income-type names."""
    compact = compact_income_tax_profile(name)
    if known_profiles and compact in known_profiles:
        return True
    return bool(re.fullmatch(
        r"(?:НПД · (?:ФЛ|ЮЛ)|ИП · УСН) · [0-9]+(?:[.,][0-9]+)?%",
        compact,
        flags=re.IGNORECASE,
    ))


def income_tax_detail_label(payload: dict, source: str, tax: Decimal, settings=None) -> str:
    """Give every income-tax bucket a human-readable source and rule.

    Older operations did not store a named rule. They remain grouped by their
    real income source rather than being assigned a regime retrospectively.
    """
    profile = str(payload.get("tax_profile") or "").strip()
    if not profile and settings is not None:
        bound = getattr(settings, "income_type_tax_profiles", {})
        if isinstance(bound, dict):
            profile = str(bound.get(source) or "").strip()
    if not profile and settings is not None:
        try:
            income = Decimal(str(payload.get("income", ZERO)))
            effective_rate = tax * Decimal("100") / income if income > ZERO else None
        except (ArithmeticError, ValueError, TypeError):
            effective_rate = None
        candidates = set()
        catalog = getattr(settings, "income_tax_profiles", {})
        if effective_rate is not None and isinstance(catalog, dict):
            for name, metadata in catalog.items():
                if not isinstance(metadata, dict):
                    continue
                try:
                    profile_rate = Decimal(str(metadata.get("rate")))
                except (ArithmeticError, ValueError, TypeError):
                    continue
                if profile_rate == effective_rate:
                    candidates.add(compact_income_tax_profile(name))
        if len(candidates) == 1:
            profile = candidates.pop()
    if profile and profile != source:
        return f"{source} · {compact_income_tax_profile(profile)}"
    return compact_income_tax_profile(source)


def tax_group(name: str) -> str:
    lowered = name.lower()
    if "имуще" in lowered or "имущество" in lowered:
        return "Налог на имущество"
    if "транспорт" in lowered:
        return "Транспортный налог"
    if "земел" in lowered:
        return "Земельный налог"
    if "налог на доход" in lowered or "патент" in lowered:
        return "Налог на доход"
    return "Другой налог"


def collect_tax_statistics(telegram_id: int, year: int) -> tuple[dict, Decimal, Decimal]:
    """Build current virtual balances inside the shared bank tax envelope.

    ``year`` is retained for call compatibility.  The diagram is a current
    account balance, so deposits do not disappear at a calendar boundary.
    """
    groups: dict[str, dict] = {
        name: {"total": ZERO, "details": defaultdict(lambda: ZERO)}
        for name in TAX_GROUPS
    }
    buckets, total_all_time = _tax_ledger(telegram_id)
    for bucket in buckets.values():
        amount = max(ZERO, bucket["amount"])
        if amount <= ZERO:
            continue
        group = bucket["group"]
        detail = bucket["detail"]
        # Obligations are identified by both their tax kind and their object.
        # Income receipts already carry a complete profile name, while a
        # patent needs to retain its own label inside the income-tax family.
        if group in {
            "Налог на имущество", "Транспортный налог", "Земельный налог", "Другой налог",
        } or str(bucket["key"]).startswith("Патент ·"):
            detail = bucket["key"]
        groups[group]["total"] += amount
        groups[group]["details"][detail] += amount
    current_total = sum((item["total"] for item in groups.values()), ZERO)
    return groups, current_total, total_all_time


def tax_chart_values_and_colors(groups: dict) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Flatten every virtual tax destination into its own coloured sector."""
    values: dict[str, Decimal] = {}
    colors: dict[str, str] = {}
    for group in TAX_GROUPS:
        data = groups.get(group, {})
        details = data.get("details", {})
        positive_details = [
            (str(detail), Decimal(str(amount)))
            for detail, amount in details.items()
            if Decimal(str(amount)) > ZERO
        ]
        positive_details.sort(key=lambda item: item[0].casefold())
        palette = TAX_DETAIL_PALETTES[group]
        used_indexes: set[int] = set()
        detailed_total = ZERO
        for detail, amount in positive_details:
            if group == "Налог на доход" or detail.casefold().startswith(f"{group} · ".casefold()):
                label = detail
            elif group == "Другой налог" and " · " in detail:
                label = detail
            else:
                label = f"{group} · {detail}"
            values[label] = values.get(label, ZERO) + amount
            index = int.from_bytes(
                hashlib.sha256(label.casefold().encode("utf-8")).digest()[:2], "big",
            ) % len(palette)
            while index in used_indexes and len(used_indexes) < len(palette):
                index = (index + 1) % len(palette)
            used_indexes.add(index)
            colors[label] = palette[index]
            detailed_total += amount
        # Preserve old or incomplete records that have a group total but no
        # usable object breakdown; otherwise money could disappear from chart.
        remainder = max(ZERO, Decimal(str(data.get("total", ZERO))) - detailed_total)
        if remainder > ZERO:
            values[group] = values.get(group, ZERO) + remainder
            colors[group] = TAX_COLORS[group]
    return values, colors


def apply_planned_tax_allocation(telegram_id: int, allocator, amount: Decimal) -> None:
    """Зачисляет фактическое пополнение КЖ в активные налоговые цели."""
    amount = Decimal(str(amount))
    obligations = db.load_tax_obligations(telegram_id)
    target_total = sum((item["monthly_amount"] for item in obligations), ZERO)
    if amount <= ZERO or target_total <= ZERO:
        return

    remaining_amount = amount
    active = list(obligations)
    credited_by_id = {int(item["id"]): ZERO for item in obligations}
    while remaining_amount > ZERO and active:
        weight = sum((item["monthly_amount"] for item in active), ZERO)
        distributed = ZERO
        overflow = ZERO
        for index, item in enumerate(active):
            share = remaining_amount - distributed if index == len(active) - 1 else (
                remaining_amount * item["monthly_amount"] / weight
            ).quantize(Decimal("0.01"))
            distributed += share
            need = max(ZERO, item["target_amount"] - item["saved_before"])
            credited = min(share, need)
            item["saved_before"] += credited
            credited_by_id[int(item["id"])] += credited
            overflow += share - credited
        remaining_amount = overflow
        active = [item for item in active if item["saved_before"] < item["target_amount"]]
        if overflow == ZERO:
            break

    for item in obligations:
        completed = item["saved_before"] >= item["target_amount"]
        key = tax_obligation_key(item['tax_type'], item['object_name'])
        db.update_tax_obligation_saved(
            telegram_id,
            item["id"],
            item["saved_before"],
            True,
        )
        monthly_left = max(
            ZERO,
            Decimal(str(item["monthly_amount"])) - credited_by_id[int(item["id"])],
        )
        db.update_tax_obligation_monthly(
            telegram_id,
            item["id"],
            monthly_left,
            item.get("monthly_period") or moscow_today().strftime("%Y-%m"),
        )
        allocator.settings.tax_catchups[key] = monthly_left
        if not completed:
            continue

        # После оплаты имущественный налог остаётся ежегодной статьёй КМ.
        # Обычный разовый налог, напротив, больше не влияет на стоимость жизни.
        if item["tax_type"] not in ANNUAL_PROPERTY_TAXES:
            set_tax_monthly_target(allocator, key, ZERO)
            allocator.settings.tax_catchups.pop(key, None)
        else:
            # Keep an explicit zero so the core does not fall back to the
            # recurring annual norm after this month's target is complete.
            allocator.settings.tax_catchups[key] = ZERO
        db.update_tax_obligation_monthly(
            telegram_id,
            item["id"],
            ZERO,
            item.get("monthly_period") or moscow_today().strftime("%Y-%m"),
        )


def refresh_planned_tax_targets(
    telegram_id: int,
    allocator,
    today: date | None = None,
    *,
    persist: bool = True,
    reset_current_period: bool = False,
) -> None:
    """Separates annual KМ norm from temporary catch-up before due date."""
    today = today or moscow_today()
    obligations = db.load_tax_obligations(telegram_id)
    monthly_period = today.strftime("%Y-%m")
    allocator.settings.tax_catchups = {}
    for item in obligations:
        annual_monthly = Decimal(str(item.get("annual_monthly_amount", ZERO)))
        if (
            item["tax_type"] in ANNUAL_PROPERTY_TAXES
            and annual_monthly <= ZERO
            and (
                item.get("notice_received")
                or applied_annual_tax_monthly_norm(item) > ZERO
            )
        ):
            annual_monthly = (item["target_amount"] / Decimal("12")).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING,
            )
        if persist and annual_monthly != item.get("annual_monthly_amount", ZERO):
            db.update_tax_obligation_annual_monthly(
                telegram_id, item["id"], annual_monthly,
            )
        set_tax_monthly_target(
            allocator,
            tax_obligation_key(item['tax_type'], item['object_name']),
            applied_annual_tax_monthly_norm(item),
        )
        if item.get("due_date"):
            due = date.fromisoformat(item["due_date"])
            months = (
                tax_notice_months_remaining(due, today)
                if item.get("notice_received")
                else tax_months_remaining(item["tax_type"], due, today)
            )
        else:
            months = max(1, int(item.get("months", 1)))
        remaining = max(ZERO, item["target_amount"] - item["saved_before"])
        if item.get("monthly_period") == monthly_period and not reset_current_period:
            monthly = min(remaining, Decimal(str(item["monthly_amount"])))
        else:
            monthly = (remaining / Decimal(months)).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING
            )
        if persist and (
            monthly != item["monthly_amount"]
            or item.get("monthly_period") != monthly_period
        ):
            db.update_tax_obligation_monthly(
                telegram_id, item["id"], monthly, monthly_period,
            )
        # Keep zeroes as explicit current-month quotas.  An empty mapping has
        # legacy meaning in the core (fall back to the recurring annual norm),
        # which would otherwise fund a completed monthly quota a second time.
        allocator.settings.tax_catchups[
            tax_obligation_key(item['tax_type'], item['object_name'])
        ] = max(ZERO, monthly)


def reconcile_tax_obligation_balances(
    telegram_id: int,
    allocator,
    today: date | None = None,
) -> None:
    """Match cached plans to the virtual split of the one real tax account."""
    today = today or moscow_today()
    for item in db.load_tax_obligations(telegram_id):
        key = tax_obligation_key(item["tax_type"], item["object_name"])
        actual = min(
            item["target_amount"],
            virtual_tax_balance(telegram_id, key, item["id"]),
        )
        if actual == item["saved_before"]:
            continue
        db.update_tax_obligation_saved(telegram_id, item["id"], actual, True)
        if item.get("due_date"):
            due = date.fromisoformat(item["due_date"])
            months = (
                tax_notice_months_remaining(due, today)
                if item.get("notice_received")
                else tax_months_remaining(item["tax_type"], due, today)
            )
        else:
            months = max(1, int(item.get("months", 1)))
        remaining = max(ZERO, item["target_amount"] - actual)
        monthly = (
            (remaining / Decimal(months)).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING,
            )
            if remaining > ZERO else ZERO
        )
        db.update_tax_obligation_monthly(
            telegram_id, item["id"], monthly, today.strftime("%Y-%m"),
        )
    # Unchanged obligations keep the remainder of the quota they have already
    # met this month; only buckets affected by the real bank withdrawal above
    # were recalculated.
    refresh_planned_tax_targets(telegram_id, allocator, today)


def make_pie_chart(groups: dict) -> bytes | None:
    if Image is None:
        return None
    values, colors = tax_chart_values_and_colors(groups)
    return make_chart(
        values, "НАЛОГИ", "Фактически отложено за год", colors,
        preserve_order=True,
        legend_columns=1,
    )


def report_text(
    groups: dict,
    annual_total: Decimal,
    year: int,
    annual_payments: Decimal,
    calculated_balance: Decimal | None,
    detailed: bool,
) -> str:
    return (
        "🏛️ <b>НАЛОГИ</b>\n\n"
        "Все налоги храним на одном накопительном счёте в банке. "
        "В Аллокаторе всегда видно, сколько денег отложено на каждый налог, "
        "поэтому суммы не смешаются."
    )


def tax_obligations_overview(
    obligations: list[dict],
    income_details: dict | None = None,
    settings=None,
) -> str:
    """One screen for accrued income taxes and dated tax obligations."""
    income_names = {
        str(name) for name, amount in (income_details or {}).items()
        if Decimal(str(amount)) > ZERO
    }
    if not income_names and not obligations:
        return "<b>НАЛОГИ В АЛЛОКАТОРЕ</b>\n\nПока нет настроенных или накопленных налогов."
    lines = ["<b>НАЛОГИ В АЛЛОКАТОРЕ</b>", ""]
    if income_names:
        lines.append("<b>С доходов</b>")
        lines.extend(f"• {escape(name)}" for name in sorted(income_names, key=str.casefold))
    if obligations:
        if income_names:
            lines.append("")
        lines.append("<b>Плановые</b>")
        for item in obligations:
            lines.append(
                f"• {escape(item['tax_type'])} · {escape(item['object_name'])}"
            )
    elif income_names:
        lines.extend(["", "<b>Плановые</b>", "Пока нет."])
    return "\n".join(lines)


_RUSSIAN_MONTHS = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def tax_date_words(value: date) -> str:
    return f"{value.day} {_RUSSIAN_MONTHS[value.month]}"


def tax_obligation_card_text(
    item: dict,
    saved: Decimal,
    allocator=None,
    *,
    show_changes: bool = False,
) -> str:
    tax_type = str(item["tax_type"])
    target = Decimal(str(item["target_amount"]))
    monthly = Decimal(str(item["monthly_amount"]))
    annual_monthly = annual_tax_monthly_norm(item)
    applied_annual = applied_annual_tax_monthly_norm(item)
    due = date.fromisoformat(item["due_date"]) if item.get("due_date") else None
    lines = [
        f"<b>{escape(tax_type.upper())}</b>",
        "",
        f"<b>{escape(str(item['object_name']).upper())}</b>",
        "",
        f"Нужно накопить — {money(target)}",
        f"Уже есть — {money(saved)}",
        f"Сейчас откладываем — {money(monthly)}/мес",
    ]

    if tax_type in ANNUAL_PROPERTY_TAXES:
        if (
            monthly > annual_monthly
            and (item.get("notice_received") or applied_annual > ZERO)
        ):
            lines.extend([
                "",
                "ℹ️ Если бы копили весь год, было бы достаточно "
                f"{money(annual_monthly)} в месяц. Сейчас сумма выше, потому что "
                "до оплаты осталось мало времени.",
            ])
        if due is not None:
            ready = tax_funding_date(tax_type, due)
            today = moscow_today()
            lines.append("————————————")
            if today <= ready:
                lines.append(
                    f"До {tax_date_words(ready)} предварительная сумма будет собрана."
                )
            elif today <= due:
                lines.append(
                    "Предварительный срок уже прошёл; направляем недостающее "
                    f"до {tax_date_words(due)}."
                )
            else:
                lines.append(
                    f"Срок оплаты {tax_date_words(due)} уже прошёл. "
                    "Налог остаётся активным до отметки об оплате."
                )
            if today <= due:
                lines.append(f"До {tax_date_words(due)} — налог должен быть оплачен.")
        if show_changes and allocator is not None and applied_annual > ZERO:
            settings = allocator.settings
            lines.extend([
                "————————————",
                "<b>ИЗМЕНЕНИЕ СТОИМОСТИ ЖИЗНИ И РЕЗЕРВОВ</b>",
                "",
                f"➤ Критический минимум увеличен до {money(settings.critical_life)}.",
                f"➤ Устойчивая жизнь увеличена до {money(settings.household_life)}.",
                f"🛡️ Подушка увеличена до {money(settings.force_majeure_limit)}.",
            ])
            if settings.needs_stabilizer:
                lines.append(
                    f"🛟 Стабилизатор увеличен до {money(settings.stabilizer_full_limit)}."
                )
            if settings.needs_intercontract_reserve:
                lines.append(
                    f"🏦 Фонд Зарплаты увеличен до {money(allocator.intercontract_current_limit)}."
                )
        elif show_changes:
            lines.extend([
                "————————————",
                "<b>СТОИМОСТЬ ЖИЗНИ И РЕЗЕРВЫ</b>",
                "",
                "Критический минимум и долгосрочные резервы пока не изменились. "
                "Годовая норма включится после подтверждённой оплаты этого налога.",
            ])
    else:
        if due is not None:
            noun = "патент" if tax_type == "Патент" else "налог"
            lines.append("————————————")
            if moscow_today() <= due:
                lines.extend([
                    f"К {tax_date_words(due)} сумма будет собрана.",
                    f"До {tax_date_words(due)} — {noun} должен быть оплачен.",
                ])
            else:
                lines.append(
                    f"Срок оплаты {tax_date_words(due)} уже прошёл. "
                    f"{noun.capitalize()} остаётся активным до отметки об оплате."
                )
        if show_changes:
            lines.extend([
                "————————————",
                "<b>СТОИМОСТЬ ЖИЗНИ И РЕЗЕРВЫ</b>",
                "",
                "Критический минимум и резервы не изменились. "
                "Этот платёж учтён отдельно и будет собираться к указанной дате.",
            ])
    return "\n".join(lines)


def tax_navigation(back_callback: str) -> list[tuple[str, str]]:
    """Standard navigation row for every nested tax screen."""
    return [
        ("← Главное меню", "taxes:back"),
        ("← Назад", back_callback),
    ]


def tax_completion_navigation(done_callback: str) -> list[tuple[str, str]]:
    """Navigation after a tax change has already been saved."""
    return [
        ("← Главное меню", "taxes:back"),
        ("✓ Готово", done_callback),
    ]


def tax_goal_navigation(data: dict, default_back: str = "taxes:add") -> list[tuple[str, str]]:
    """Return to the income-tax parent when a patent was opened from there."""
    back = "taxes:income" if data.get("tax_goal_return") == "taxes:income" else default_back
    return tax_navigation(back)


async def show_taxes(message: Message, telegram_id: int, detailed: bool = False) -> None:
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль командой /start.")
        return
    # Dates and shortfalls can change while the user is away.  Opening the
    # section always refreshes the plan before rendering its chart and cards.
    with db.transaction():
        refresh_planned_tax_targets(telegram_id, allocator, moscow_today())
        db.save_allocator(telegram_id, allocator)
    year = moscow_today().year
    groups, annual_total, total_all_time = collect_tax_statistics(telegram_id, year)
    annual_payments = sum((item["amount"] for item in db.load_tax_payments(telegram_id, year)), ZERO)
    all_payments = sum((item["amount"] for item in db.load_tax_payments(telegram_id)), ZERO)
    calculated_balance = None
    if allocator.settings.track_tax_payments:
        calculated_balance = max(ZERO, total_all_time - all_payments)
    text = report_text(
        groups,
        annual_total,
        year,
        annual_payments,
        calculated_balance,
        detailed,
    )
    obligations = db.load_tax_obligations(telegram_id)
    overview = tax_obligations_overview(
        obligations,
        groups.get("Налог на доход", {}).get("details", {}),
        allocator.settings,
    )
    text += f"\n\n————————————\n{overview}"
    rows = [[("+ Добавить налог", "taxes:add")]]
    rows.append([("✎ Изменить налоги", "taxes:edit")])
    rows.append([("Получено уведомление ФНС", "taxes:notice")])
    rows.append([("Налог оплачен", "taxes:payment")])
    rows.append([("ℹ️ Как это работает", "taxes:help")])
    rows.append([("← Главное меню", "taxes:back")])

    tax_values, chart_colors = tax_chart_values_and_colors(groups)
    await send_chart_report(
        message, tax_values,
        "НАЛОГИ", text, reply_markup=keyboard(rows),
        subtitle="Доходные налоги разделены по профилям", colors=chart_colors,
        preserve_order=True,
        legend_columns=1,
        center_amount=annual_total,
        center_label="Отложено",
        center_suffix="₽ на налоги",
    )


async def show_income_tax_profile_menu(message: Message, telegram_id: int | None = None) -> None:
    """Income-tax setup: profiles reserve a share of every income receipt."""
    allocator = db.load_allocator(telegram_id) if telegram_id is not None else None
    settings = getattr(allocator, "settings", None)
    stored_profiles = list(getattr(settings, "income_tax_profiles", {}))
    profiles = [
        compact_income_tax_profile(name)
        for name in stored_profiles
    ]
    known_rules = list(dict.fromkeys(profiles))
    added = (
        "\n\n<b>Уже настроены:</b>\n" + "\n".join(f"• {escape(name)}" for name in known_rules)
        if known_rules else ""
    )
    await message.answer(
        "<b>НАЛОГ НА ДОХОД</b>\n\n"
        "Здесь хранятся налоговые правила, а не отдельные источники дохода. "
        "При добавлении поступления одно и то же название — например, «Зарплата» — "
        "можно провести по разным правилам; в диаграмме они станут разными секторами."
        + added,
        reply_markup=keyboard([
            [("Самозанятость (НПД)", "taxincome:self_employed")],
            [("ИП на УСН «Доходы»", "taxincome:subject:ip")],
            [("ИП на ПСН (патент)", "taxincome:patent")],
            [("✓ Готово", "menu:taxes")],
            tax_navigation("taxes:add"),
        ]),
    )


async def show_self_employed_rate_menu(message: Message) -> None:
    await message.answer(
        "<b>САМОЗАНЯТОСТЬ (НПД)</b>\n\n"
        "<b>НПД</b> — налог на профессиональный доход или самозанятость.\n"
        "<b>ФЛ</b> — физические лица,\n"
        "<b>ЮЛ</b> — юридические лица.\n\n"
        "<b>Обычные ставки:</b>\n"
        "• 4% с доходов от ФЛ\n"
        "• 6% от ЮЛ.\n\n"
        "<b>Пониженые ставки:</b>\n"
        "• 3% с доходов от ФЛ\n"
        "• 4% от ЮЛ.\n\n"
        "<b>P.S.:</b> Ставки понижаются при приветственном налоговом бонусе "
        "в 10 000 <b>₽</b>. Аллокатор не знает остаток бонуса — после его "
        "исчерпания выберите обычную ставку.",
        reply_markup=keyboard([
            [("НПД · ФЛ · 4%", "taxincome:npd:physical:4"), ("НПД · ФЛ · 3%", "taxincome:npd:physical:3")],
            [("НПД · ЮЛ · 6%", "taxincome:npd:business:6"), ("НПД · ЮЛ · 4%", "taxincome:npd:business:4")],
            [("Своя ставка", "taxincome:subject:self_employed")],
            [("← Главное меню", "taxes:back"), ("← Назад", "taxes:income")],
        ]),
    )


async def show_ip_usn_rate_menu(message: Message) -> None:
    await message.answer(
        "<b>ИП НА УСН «ДОХОДЫ»</b>\n\n"
        "<b>ИП</b> — индивидуальный предприниматель.\n"
        "<b>УСН «Доходы»</b> — упрощённая система налогообложения с объектом "
        "«Доходы».\n\n"
        "Выберите, сколько откладывать с каждого поступления.\n\n"
        "Базовый вариант — 6%; если у вас действует другая ставка, укажите её вручную.",
        reply_markup=keyboard([
            [("ИП · УСН · 6%", "taxincome:usn:6"), ("Своя ставка", "taxincome:usn:custom")],
            [("← Главное меню", "taxes:back"), ("← Назад", "taxes:income")],
        ]),
    )


async def save_income_tax_profile(
    telegram_id: int,
    subject: str,
    mode: str,
    rate: Decimal,
) -> tuple[str, bool]:
    """Atomically add one selectable income-tax profile."""
    from settings_editor import tax_profile_name
    name = tax_profile_name(subject, mode, rate)
    lock = _PROFILE_SAVE_LOCKS.setdefault(telegram_id, asyncio.Lock())
    async with lock:
        allocator = db.load_allocator(telegram_id)
        if allocator is None:
            raise ValueError("Сначала создайте финансовый профиль командой /start.")
        existing = next(
            (
                stored for stored in allocator.settings.income_tax_profiles
                if compact_income_tax_profile(stored) == name
            ),
            None,
        )
        if existing is not None:
            return compact_income_tax_profile(existing), False
        allocator.settings.income_tax_profiles[name] = {
            "subject": subject, "mode": mode, "rate": str(rate),
        }
        db.save_allocator(telegram_id, allocator)
    return name, True


@router.callback_query(F.data == "taxes:help")
async def taxes_help(callback: CallbackQuery):
    await callback.answer()
    today = moscow_today()
    current_year = today.year
    due_year = annual_tax_due_date(today).year
    tax_year = due_year - 1
    await callback.message.answer(
        "ℹ️ <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "<b>➤ Копим заранее</b>\n"
        f"Сейчас, в {current_year} году, мы копим на налоги за квартиру, машину "
        f"и землю за {tax_year} год.\n"
        "Для нового налога это отдельная срочная задача: до первой подтверждённой "
        "оплаты она не увеличивает Критический минимум и долгосрочные резервы.\n\n"
        "<b>➤ Сверяем с ФНС</b>\n"
        "<b>1 ноября</b> я напомню проверить налоговое уведомление. Нажмите\n"
        "Получено уведомление\n"
        "и введите сумму из него.\n\n"
        "<b>➤ Докапливаем</b>\n"
        "Аллокатор пересчитает налог по уведомлению и, если нужно, поможет "
        f"накопить недостающую сумму <b>до 1 декабря {due_year}.</b>\n\n"
        "<b>➤ Платим</b>\n"
        "• Налоги на имущество, транспорт и землю нужно оплатить "
        f"до 1 декабря {due_year}.\n"
        "• Налог на доход платите по графику своего налогового режима "
        "(самозанятость, ИП на УСН «Доходы» или ИП на ПСН). После оплаты "
        "выберите сохранённый доходный профиль, чтобы уменьшился именно его сектор.\n\n"
        "<b>➤ Повторяем</b>\n"
        "После оплаты нажмите\n"
        "Налог оплачен\n"
        "— и начнём копить на следующий платёж. Только после этой отметки "
        "последняя подтверждённая сумма делится на 12 и включается в Критический "
        "минимум, Подушку, Стабилизатор или Фонд Зарплаты по правилам вашего профиля.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data == "taxes:km_repair")
async def ask_critical_life_repair(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Старая ручная корректировка больше не требуется. "
        "Критический минимум не изменён: Аллокатор теперь пересчитывает "
        "налоговые обязательства автоматически.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data == "taxes:km_repair:confirm")
async def apply_critical_life_repair(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Старая ручная корректировка отключена. Критический минимум не изменён.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data == "menu:taxes")
async def taxes_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_taxes(callback.message, callback.from_user.id)


@router.callback_query(F.data.in_({"taxes:details", "taxes:summary"}))
async def taxes_details(callback: CallbackQuery):
    await callback.answer()
    await show_taxes(callback.message, callback.from_user.id, callback.data == "taxes:details")


def latest_annual_tax_items(telegram_id: int) -> list[dict]:
    # Первый налог ещё не входит в КМ, поэтому его нельзя искать через
    # settings.planned_taxes. Канонический источник — активный налоговый цикл.
    return [
        item
        for item in db.load_tax_obligations(telegram_id)
        if item["tax_type"] in ANNUAL_PROPERTY_TAXES
    ]


def tax_due_year(item: dict) -> int | None:
    try:
        return date.fromisoformat(str(item.get("due_date") or "")).year
    except ValueError:
        return None


def tax_report_year(item: dict) -> int | None:
    """The tax period shown in an FNS property-tax notice."""
    due_year = tax_due_year(item)
    if due_year is None:
        return None
    return due_year - 1 if item.get("tax_type") in ANNUAL_PROPERTY_TAXES else due_year


def _tax_item(telegram_id: int, obligation_id: int, *, active_only: bool = False) -> dict | None:
    return next(
        (
            item for item in db.load_tax_obligations(telegram_id, active_only=active_only)
            if int(item["id"]) == int(obligation_id)
        ),
        None,
    )


async def show_tax_payment_review(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    name = str(data.get("tax_payment_name") or "").strip()
    raw_amount = data.get("tax_payment_amount")
    if not name or raw_amount is None:
        await state.clear()
        await message.answer(
            "Данные оплаты устарели. Начните ещё раз.",
            reply_markup=keyboard([tax_navigation("menu:taxes")]),
        )
        return
    amount = Decimal(str(raw_amount))
    token = token_urlsafe(8)
    await state.update_data(tax_payment_flow_token=token)
    await state.set_state(TaxStates.payment_review)
    await message.answer(
        "<b>ПРОВЕРЬТЕ ОПЛАТУ</b>\n\n"
        f"{escape(name)}\n"
        "————————————\n"
        f"Оплачено — <b>{money(amount)}</b>",
        reply_markup=keyboard([
            [
                ("✖️ Отмена", f"taxpayment:cancel:{token}"),
                ("✔️ Сохранить", f"taxpayment:confirm:{token}"),
            ],
            [("✎ Сумма", f"taxpayment:edit_amount:{token}")],
            tax_navigation("taxes:payment"),
        ]),
    )


async def _valid_payment_review(callback: CallbackQuery, state: FSMContext) -> bool:
    data = await state.get_data()
    supplied = str(callback.data or "").rsplit(":", 1)[-1]
    current_state = await state.get_state()
    return (
        bool(data.get("tax_payment_flow_token"))
        and supplied == data.get("tax_payment_flow_token")
        and current_state == TaxStates.payment_review.state
    )


async def _answer_stale_payment_review(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer(
        "Этот экран оплаты уже неактуален. Откройте «Налог оплачен» ещё раз.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data == "taxes:notice")
async def tax_notice_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    items = latest_annual_tax_items(callback.from_user.id)
    if not items:
        await callback.message.answer(
            "Сначала добавьте квартиру, машину или землю, для которых вы платите налог.",
            reply_markup=keyboard([
                [("+ Добавить налог", "taxes:add")],
                tax_navigation("menu:taxes"),
            ]),
        )
        return
    rows = [
        [(
            f"{item['tax_type']} · {item['object_name']}",
            f"taxnotice:item:{item['id']}",
        )]
        for item in items
    ]
    rows.append(tax_navigation("menu:taxes"))
    await callback.message.answer(
        "<b>ПО КАКОМУ НАЛОГУ ПРИШЛО УВЕДОМЛЕНИЕ?</b>",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.regexp(r"^taxnotice:item:\d+$"))
async def tax_notice_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (
            row for row in db.load_tax_obligations(
                callback.from_user.id, active_only=False,
            )
            if row["id"] == obligation_id
        ),
        None,
    )
    if item is None or item["tax_type"] not in ANNUAL_PROPERTY_TAXES:
        await callback.message.answer(
            "Налог не найден.",
            reply_markup=keyboard([tax_navigation("taxes:notice")]),
        )
        return
    key = tax_obligation_key(item["tax_type"], item["object_name"])
    saved = virtual_tax_balance(callback.from_user.id, key, item["id"])
    await state.update_data(
        next_tax_type=item["tax_type"],
        next_tax_object=item["object_name"],
        tax_notice_source_id=item["id"],
    )
    await state.set_state(TaxStates.next_obligation_amount)
    await callback.message.answer(
        f"<b>{escape(item['tax_type'].upper())}</b>\n\n"
        f"{escape(item['object_name'])}\n"
        f"Аллокатор уже отнёс на этот налог — <b>{money(saved)}</b>\n\n"
        "Введите <b>полную сумму из уведомления ФНС</b>. "
        "Вычитать накопленные деньги самостоятельно не нужно.\n\n"
        "——————\n"
        "<b>→ Введите сумму.</b>",
        reply_markup=keyboard([tax_navigation("taxes:notice")]),
    )


@router.callback_query(F.data == "taxes:payment")
async def tax_payment_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    obligations = [
        item for item in db.load_tax_obligations(callback.from_user.id)
        if db.tax_obligation_paid_amount(callback.from_user.id, item["id"])
        < item["target_amount"]
    ]
    income_by_key: dict[str, dict[str, str | Decimal]] = {}
    buckets, _ = _tax_ledger(callback.from_user.id)
    for bucket in buckets.values():
        amount = max(ZERO, Decimal(str(bucket["amount"])))
        if bucket["group"] != "Налог на доход" or amount <= ZERO:
            continue
        item = income_by_key.setdefault(
            str(bucket["key"]),
            {"key": str(bucket["key"]), "label": str(bucket["detail"]), "amount": ZERO},
        )
        item["amount"] = Decimal(str(item["amount"])) + amount
    income_profiles = [
        {"key": item["key"], "label": item["label"], "amount": str(item["amount"])}
        for item in income_by_key.values()
    ]
    await state.update_data(tax_payment_income_profiles=income_profiles)
    rows = [
        [(f"{item['label']} · {money(Decimal(str(item['amount'])))}", f"taxpayment:income:{index}")]
        for index, item in enumerate(income_profiles)
    ]
    rows.extend([
        [(
            f"{item['tax_type']} · {item['object_name']}",
            f"taxpayment:obligation:{item['id']}",
        )]
        for item in obligations
    ])
    rows.extend([
        [("Другой налог", "taxpayment:other")],
        tax_navigation("menu:taxes"),
    ])
    await callback.message.answer(
        "<b>КАКОЙ НАЛОГ ВЫ ОПЛАТИЛИ?</b>",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.regexp(r"^taxpayment:income:\d+$"))
async def tax_payment_income_profile(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    profiles = data.get("tax_payment_income_profiles") or []
    try:
        item = profiles[int(callback.data.rsplit(":", 1)[1])]
        amount = Decimal(str(item["amount"]))
    except (IndexError, KeyError, TypeError, ValueError):
        await callback.message.answer(
            "Этот список уже неактуален. Выберите оплаченный налог ещё раз.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    if amount <= ZERO:
        await callback.message.answer(
            "По этому профилю уже нет накопленной суммы.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    await state.update_data(
        tax_payment_name=str(item["key"]),
        tax_payment_obligation_id=None,
        tax_payment_type="Налог на доход",
        tax_payment_object=str(item["label"]),
        tax_payment_amount=str(amount),
        tax_payment_adjust_target=False,
    )
    await show_tax_payment_review(callback.message, state)


@router.callback_query(F.data == "taxpayment:other")
async def tax_payment_other(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await state.set_state(TaxStates.payment_name)
    await callback.message.answer(
        "Введите название налога или объекта.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data.regexp(r"^taxpayment:obligation:\d+$"))
async def tax_payment_obligation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (row for row in db.load_tax_obligations(callback.from_user.id) if row["id"] == obligation_id),
        None,
    )
    if item is None:
        await callback.message.answer(
            "Налоговое обязательство не найдено.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    already_paid = db.tax_obligation_paid_amount(callback.from_user.id, obligation_id)
    remaining = max(ZERO, item["target_amount"] - already_paid)
    if remaining <= ZERO:
        await callback.message.answer(
            "Этот налог уже отмечен как оплаченный.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    await state.clear()
    await state.update_data(
        tax_payment_name=f"{item['tax_type']} · {item['object_name']}",
        tax_payment_obligation_id=obligation_id,
        tax_payment_type=item["tax_type"],
        tax_payment_object=item["object_name"],
        tax_payment_due_date=item.get("due_date"),
        tax_payment_amount=str(remaining),
        tax_payment_adjust_target=False,
    )
    await show_tax_payment_review(callback.message, state)


@router.message(TaxStates.payment_name, F.text & ~F.text.startswith("/"))
async def tax_payment_name(message: Message, state: FSMContext):
    name = parse_tax_object_name(message.text)
    if name is None:
        await message.answer(
            "Здесь нужно название налога или объекта, а не сумма.\n\n"
            "Например: <b>Дача</b>, <b>Автомобиль</b>, <b>Квартира</b> "
            "или <b>Патент № 1</b>.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    await state.update_data(
        tax_payment_name=name,
        tax_payment_obligation_id=None,
        tax_payment_adjust_target=False,
    )
    await state.set_state(TaxStates.payment_amount)
    await message.answer(
        f"<b>{escape(name.upper())}</b>\n\n"
        "Введите полную оплаченную сумму.",
        reply_markup=keyboard([tax_navigation("taxes:payment")]),
    )


@router.message(TaxStates.payment_amount, F.text & ~F.text.startswith("/"))
async def tax_payment_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        amount = ZERO
    if amount <= ZERO:
        await message.answer(
            "Введите положительную сумму.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    await state.update_data(tax_payment_amount=str(amount))
    await show_tax_payment_review(message, state)


@router.callback_query(F.data.startswith("taxpayment:edit_amount:"))
async def tax_payment_edit_amount(callback: CallbackQuery, state: FSMContext):
    if not await _valid_payment_review(callback, state):
        await _answer_stale_payment_review(callback, state)
        return
    await callback.answer()
    data = await state.get_data()
    if not data.get("tax_payment_name"):
        await callback.message.answer(
            "Данные оплаты устарели. Начните ещё раз.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    await state.set_state(TaxStates.payment_edit_amount)
    await callback.message.answer(
        "<b>ИСПРАВИТЬ СУММУ ОПЛАТЫ</b>\n\n"
        "Введите полную сумму, которую вы заплатили. Для налога из плана "
        "Аллокатор также исправит начисленную сумму.\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([tax_navigation("taxes:payment")]),
    )


@router.message(TaxStates.payment_edit_amount, F.text & ~F.text.startswith("/"))
async def tax_payment_save_edited_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None or amount <= ZERO:
        await message.answer(
            "Введите положительную сумму.",
            reply_markup=keyboard([tax_navigation("taxes:payment")]),
        )
        return
    data = await state.get_data()
    obligation_id = data.get("tax_payment_obligation_id")
    if obligation_id:
        paid_before = db.tax_obligation_paid_amount(message.from_user.id, int(obligation_id))
        replacement_id = data.get("tax_payment_replaces_id")
        if replacement_id:
            previous = db.load_tax_payment(message.from_user.id, int(replacement_id))
            if previous is not None:
                paid_before = max(ZERO, paid_before - previous["amount"])
        if amount <= ZERO:
            await message.answer(
                "Введите положительную сумму.",
                reply_markup=keyboard([tax_navigation("taxes:payment")]),
            )
            return
        await state.update_data(
            tax_payment_amount=str(amount),
            tax_payment_adjust_target=True,
            tax_payment_target=str(paid_before + amount),
        )
    else:
        await state.update_data(tax_payment_amount=str(amount))
    await show_tax_payment_review(message, state)


@router.callback_query(F.data.startswith("taxpayment:cancel:"))
async def tax_payment_cancel(callback: CallbackQuery, state: FSMContext):
    if not await _valid_payment_review(callback, state):
        await _answer_stale_payment_review(callback, state)
        return
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "Оплата не сохранена.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


def _apply_tax_payment(
    telegram_id: int,
    data: dict,
) -> tuple[int, dict | None, dict | None]:
    """Persist a reviewed full payment and its next cycle atomically."""
    amount = Decimal(str(data["tax_payment_amount"]))
    obligation_id = data.get("tax_payment_obligation_id")
    item = None
    next_cycle = None
    allocator = db.load_allocator(telegram_id)
    with db.transaction():
        if obligation_id:
            item = _tax_item(telegram_id, int(obligation_id), active_only=True)
            if item is None:
                raise ValueError("Налоговое обязательство больше не активно.")
            paid_before = db.tax_obligation_paid_amount(telegram_id, int(obligation_id))
            target = item["target_amount"]
            if data.get("tax_payment_adjust_target"):
                target = paid_before + amount
                annual_monthly = (
                    (target / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
                    if item["tax_type"] in ANNUAL_PROPERTY_TAXES else ZERO
                )
                db.update_tax_obligation_plan(
                    telegram_id, int(obligation_id), target_amount=target,
                    months=max(1, int(item.get("months", 1))), monthly_amount=ZERO,
                    annual_monthly_amount=annual_monthly,
                )
                item = dict(item)
                item["target_amount"] = target
                item["annual_monthly_amount"] = annual_monthly
            remaining = max(ZERO, target - paid_before)
            if amount != remaining:
                raise ValueError(
                    "Сумма налога изменилась. Откройте «Налог оплачен» и проверьте её ещё раз."
                )
            payment_id = db.save_tax_payment(
                telegram_id, data["tax_payment_name"], amount,
                obligation_id=int(obligation_id), tax_due_year=tax_due_year(item),
            )
            db.deactivate_tax_obligation(
                telegram_id, int(obligation_id), reason="paid",
            )
            if allocator is not None:
                key = tax_obligation_key(item["tax_type"], item["object_name"])
                if item["tax_type"] in ANNUAL_PROPERTY_TAXES:
                    next_cycle = start_next_annual_tax_cycle(
                        telegram_id, item, allocator, moscow_today(),
                    )
                    db.link_tax_payment_next_cycle(
                        telegram_id, payment_id, int(next_cycle["id"]),
                    )
                else:
                    set_tax_monthly_target(allocator, key, ZERO)
                    allocator.settings.tax_catchups.pop(key, None)
                reconcile_tax_obligation_balances(
                    telegram_id, allocator, moscow_today(),
                )
                db.save_allocator(telegram_id, allocator)
        else:
            payment_id = db.save_tax_payment(
                telegram_id, data["tax_payment_name"], amount,
            )
            if allocator is not None:
                reconcile_tax_obligation_balances(
                    telegram_id, allocator, moscow_today(),
                )
                db.save_allocator(telegram_id, allocator)
    return payment_id, item, next_cycle


@router.callback_query(F.data.startswith("taxpayment:confirm:"))
async def tax_payment_confirm(callback: CallbackQuery, state: FSMContext):
    lock = _PAYMENT_CONFIRM_LOCKS.setdefault(callback.from_user.id, asyncio.Lock())
    async with lock:
        if not await _valid_payment_review(callback, state):
            await _answer_stale_payment_review(callback, state)
            return
        await callback.answer()
        data = await state.get_data()
        if not data.get("tax_payment_name") or not data.get("tax_payment_amount"):
            await state.clear()
            await callback.message.answer(
                "Данные оплаты устарели. Начните ещё раз.",
                reply_markup=keyboard([tax_navigation("taxes:payment")]),
            )
            return
        try:
            with db.transaction():
                replacement_id = data.get("tax_payment_replaces_id")
                if replacement_id:
                    _undo_tax_payment(callback.from_user.id, int(replacement_id))
                payment_id, item, next_cycle = _apply_tax_payment(callback.from_user.id, data)
        except ValueError as error:
            await state.clear()
            await callback.message.answer(
                escape(str(error)), reply_markup=keyboard([tax_navigation("taxes:payment")]),
            )
            return
        amount = Decimal(str(data["tax_payment_amount"]))
        await state.clear()
    lines = [
        "<b>НАЛОГ ОТМЕЧЕН КАК ОПЛАЧЕННЫЙ</b>", "",
        escape(str(data["tax_payment_name"])),
        f"Оплачено — <b>{money(amount)}</b>",
    ]
    if item is not None and next_cycle is not None:
        due = next_cycle["due_date"]
        before = next_cycle["targets_before"]
        after = next_cycle["targets_after"]
        annual_monthly = next_cycle["annual_monthly_amount"]
        lines.extend([
            "", "————————————",
            "<b>ГОДОВАЯ НОРМА АКТИВИРОВАНА</b>",
            f"На следующий цикл — <b>{money(annual_monthly)}</b> в месяц.",
            "",
            f"➤ Критический минимум: {money(before['critical_life'])} → "
            f"<b>{money(after['critical_life'])}</b>",
            f"➤ Устойчивая жизнь: {money(before['household_life'])} → "
            f"<b>{money(after['household_life'])}</b>",
            f"➤ Бытовой резерв: <b>{money(after['household_reserve'])}</b> — не изменился.",
            f"🛡️ Подушка: {money(before['force_majeure'])} → "
            f"<b>{money(after['force_majeure'])}</b>",
        ])
        if after["minimum_pillow"] > ZERO:
            lines.append(
                f"🛡️ Минимальная подушка: {money(before['minimum_pillow'])} → "
                f"<b>{money(after['minimum_pillow'])}</b>"
            )
        if after["stabilizer"] > ZERO:
            lines.append(
                f"🛟 Стабилизатор: {money(before['stabilizer'])} → "
                f"<b>{money(after['stabilizer'])}</b>"
            )
        if after["salary_fund"] > ZERO:
            lines.append(
                f"🏦 Фонд Зарплаты: {money(before['salary_fund'])} → "
                f"<b>{money(after['salary_fund'])}</b>"
            )
        reward = "🏆" * int(after["mode"])
        lines.extend([
            "",
            f"Текущий уровень — <b>{reward}</b>",
            f"Текущий приоритет — <b>{escape(str(after['priority']))}</b>.",
            "", "————————————",
            f"Следующий платёж — до <b>{due.strftime('%d.%m.%Y')}</b>.",
            f"Он относится к налоговому периоду <b>{due.year - 1}</b> года.",
            "До нового уведомления Аллокатор использует последнюю известную сумму.",
        ])
    await callback.message.answer(
        "\n".join(lines),
        reply_markup=keyboard([
            [("✎ Исправить оплату", f"taxpayment:correct:{payment_id}")],
            [("🗑️ Удалить оплату", f"taxpayment:delete:{payment_id}")],
            tax_navigation("menu:taxes"),
        ]),
    )
    if item is not None and next_cycle is not None:
        updated = db.load_allocator(callback.from_user.id)
        if updated is not None:
            mode = updated.active_mode()
            image_path = mode_image_path(updated.profile_id, mode)
            if image_path is not None and image_path.exists():
                caption = (
                    f"<b>{'🏆' * mode}</b>\n"
                    f"{escape(updated.mode_display_name(mode))} · "
                    f"{escape(updated.mode_title(mode))}"
                )
                try:
                    await callback.message.answer_photo(
                        photo=FSInputFile(image_path), caption=caption,
                    )
                except TelegramBadRequest:
                    pass


def _tax_payment_reversal_problem(telegram_id: int, payment: dict) -> str | None:
    child_id = payment.get("next_obligation_id")
    if child_id is None and payment.get("obligation_id") is not None:
        child = next(
            (
                item for item in db.load_tax_obligations(telegram_id, active_only=False)
                if item.get("source_obligation_id") == payment["obligation_id"]
            ),
            None,
        )
    else:
        child = _tax_item(telegram_id, int(child_id)) if child_id is not None else None
    if child is None:
        return None
    if not child.get("active") or child.get("notice_received"):
        return (
            "Следующий налоговый цикл уже изменён. Эту оплату нельзя исправить "
            "автоматически без риска потерять новые данные."
        )
    if db.tax_obligation_paid_amount(telegram_id, int(child["id"])) > ZERO:
        return "По следующему циклу уже сохранена оплата. Сначала исправьте более позднюю запись."
    start = int(child.get("tracking_started_operation_id") or 0)
    key = tax_obligation_key(child["tax_type"], child["object_name"])
    for fallback_id, operation in enumerate(
        db.load_operations(telegram_id, limit=-1), start=1,
    ):
        if int(operation.get("id", fallback_id)) <= start:
            continue
        amount = Decimal(str(
            (operation.get("payload", {}).get("planned_tax_details") or {}).get(key, ZERO)
        ))
        if amount > ZERO:
            return (
                "После этой оплаты Аллокатор уже пополнял следующий налоговый цикл. "
                "Автоматическое исправление остановлено, чтобы не исказить баланс."
            )
    return None


def _undo_tax_payment(telegram_id: int, payment_id: int) -> dict:
    payment = db.load_tax_payment(telegram_id, payment_id)
    if payment is None:
        raise ValueError("Оплата уже удалена или не найдена.")
    problem = _tax_payment_reversal_problem(telegram_id, payment)
    if problem:
        raise ValueError(problem)
    child_id = payment.get("next_obligation_id")
    if child_id is None and payment.get("obligation_id") is not None:
        child = next(
            (
                item for item in db.load_tax_obligations(telegram_id, active_only=False)
                if item.get("source_obligation_id") == payment["obligation_id"]
            ),
            None,
        )
        child_id = child["id"] if child is not None else None
    with db.transaction():
        if child_id is not None:
            db.deactivate_tax_obligation(
                telegram_id, int(child_id), reason="payment_reversed",
            )
        if not db.reverse_tax_payment(telegram_id, payment_id):
            raise ValueError("Оплата уже удалена или не найдена.")
        if payment.get("obligation_id") is not None:
            db.reactivate_tax_obligation(telegram_id, int(payment["obligation_id"]))
        allocator = db.load_allocator(telegram_id)
        if allocator is not None:
            reconcile_tax_obligation_balances(
                telegram_id, allocator, moscow_today(),
            )
            db.save_allocator(telegram_id, allocator)
    return payment


@router.callback_query(F.data.regexp(r"^taxpayment:correct:\d+$"))
async def tax_payment_correct(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    payment_id = int(callback.data.rsplit(":", 1)[1])
    payment = db.load_tax_payment(callback.from_user.id, payment_id)
    if payment is None:
        await callback.message.answer("Оплата уже удалена или не найдена.")
        return
    problem = _tax_payment_reversal_problem(callback.from_user.id, payment)
    if problem:
        await callback.message.answer(
            escape(problem), reply_markup=keyboard([tax_navigation("menu:taxes")]),
        )
        return
    item = (
        _tax_item(callback.from_user.id, int(payment["obligation_id"]))
        if payment.get("obligation_id") is not None else None
    )
    await state.clear()
    await state.update_data(
        tax_payment_name=payment["tax_name"],
        tax_payment_obligation_id=payment.get("obligation_id"),
        tax_payment_type=item.get("tax_type") if item else None,
        tax_payment_object=item.get("object_name") if item else None,
        tax_payment_due_date=item.get("due_date") if item else None,
        tax_payment_replaces_id=payment_id,
        tax_payment_adjust_target=item is not None,
    )
    await state.set_state(TaxStates.payment_edit_amount)
    await callback.message.answer(
        "<b>ИСПРАВИТЬ ОПЛАТУ</b>\n\n"
        f"Сейчас сохранено — <b>{money(payment['amount'])}</b>\n\n"
        "——————\n<b>→ Введите правильную сумму.</b>",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data.regexp(r"^taxpayment:delete:\d+$"))
async def tax_payment_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    payment_id = int(callback.data.rsplit(":", 1)[1])
    payment = db.load_tax_payment(callback.from_user.id, payment_id)
    if payment is None:
        await callback.message.answer("Оплата уже удалена или не найдена.")
        return
    problem = _tax_payment_reversal_problem(callback.from_user.id, payment)
    if problem:
        await callback.message.answer(
            escape(problem), reply_markup=keyboard([tax_navigation("menu:taxes")]),
        )
        return
    await callback.message.answer(
        "<b>УДАЛИТЬ ОШИБОЧНУЮ ОПЛАТУ?</b>\n\n"
        f"{escape(payment['tax_name'])} — <b>{money(payment['amount'])}</b>\n\n"
        "Баланс налогового счёта и активный план будут восстановлены.",
        reply_markup=keyboard([
            [("🗑️ Удалить оплату", f"taxpayment:delete_confirm:{payment_id}")],
            tax_navigation("menu:taxes"),
        ]),
    )


@router.callback_query(F.data.regexp(r"^taxpayment:delete_confirm:\d+$"))
async def tax_payment_delete(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    payment_id = int(callback.data.rsplit(":", 1)[1])
    try:
        payment = _undo_tax_payment(callback.from_user.id, payment_id)
    except ValueError as error:
        await callback.message.answer(
            escape(str(error)), reply_markup=keyboard([tax_navigation("menu:taxes")]),
        )
        return
    await state.clear()
    await callback.message.answer(
        "Ошибочная оплата удалена. Баланс и налоговый план восстановлены.\n\n"
        f"{escape(payment['tax_name'])} — <b>{money(payment['amount'])}</b>",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.message(TaxStates.next_obligation_amount, F.text & ~F.text.startswith("/"))
async def save_next_tax_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None or amount <= ZERO:
        await message.answer(
            "Введите положительную сумму.",
            reply_markup=keyboard([tax_navigation("taxes:notice")]),
        )
        return
    data = await state.get_data()
    tax_type = data.get("next_tax_type")
    object_name = data.get("next_tax_object")
    if tax_type not in ANNUAL_PROPERTY_TAXES or not object_name:
        await state.clear()
        await message.answer("Не удалось определить налог. Откройте раздел «Налоги» ещё раз.")
        return
    key = tax_obligation_key(tax_type, object_name)
    source_id = data.get("tax_notice_source_id")
    saved = virtual_tax_balance(
        message.from_user.id, key, int(source_id) if source_id else None,
    )
    source_item = next(
        (
            item for item in db.load_tax_obligations(message.from_user.id)
            if item["id"] == source_id
        ),
        None,
    )
    already_paid = (
        db.tax_obligation_paid_amount(message.from_user.id, int(source_id))
        if source_item is not None else ZERO
    )
    funded_for_cycle = min(amount, saved + already_paid)
    try:
        due = date.fromisoformat(str(source_item.get("due_date") or "")) if source_item else annual_tax_due_date()
    except ValueError:
        due = annual_tax_due_date()
    if due < moscow_today():
        due = annual_tax_due_date()
    remaining, months, monthly, annual_monthly = calculate_notice_plan(
        amount, funded_for_cycle, due, moscow_today(),
    )
    # Updating the notification changes both the obligation and the hidden
    # annual/catch-up settings.  Keep both sides in one SQLite transaction.
    with db.transaction():
        if source_item is not None:
            db.update_tax_obligation_notice(
                message.from_user.id,
                int(source_id),
                target_amount=amount,
                saved_before=funded_for_cycle,
                months=months,
                monthly_amount=monthly,
                annual_monthly_amount=annual_monthly,
                due_date=due.isoformat(),
            )
            obligation_id = int(source_id)
        else:
            for item in db.load_tax_obligations(message.from_user.id):
                if tax_obligation_key(item["tax_type"], item["object_name"]) == key:
                    db.deactivate_tax_obligation(
                        message.from_user.id, item["id"], reason="replaced_by_notice",
                    )
            obligation_id = db.add_tax_obligation(
                message.from_user.id,
                tax_type,
                object_name,
                amount,
                funded_for_cycle,
                months,
                monthly,
                due.isoformat(),
                annual_monthly,
                notice_received=True,
                opening_amount=ZERO,
                monthly_period=moscow_today().strftime("%Y-%m"),
                applied_annual_monthly_amount=ZERO,
            )
        allocator = db.load_allocator(message.from_user.id)
        if allocator is not None:
            # Уведомление задаёт точную норму будущего цикла, но не меняет
            # КМ и долгосрочные резервы до подтверждённой оплаты.
            if monthly > ZERO:
                allocator.settings.tax_catchups[key] = monthly
            else:
                allocator.settings.tax_catchups.pop(key, None)
            if already_paid >= amount and source_item is not None:
                refreshed_item = next(
                    item for item in db.load_tax_obligations(message.from_user.id)
                    if item["id"] == obligation_id
                )
                db.deactivate_tax_obligation(
                    message.from_user.id, obligation_id, reason="paid_before_notice",
                )
                start_next_annual_tax_cycle(
                    message.from_user.id, refreshed_item, allocator, moscow_today(),
                )
            db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    paid_line = (
        f"Уже оплачено — <b>{money(already_paid)}</b>\n"
        if already_paid > ZERO else ""
    )
    await message.answer(
        f"<b>УВЕДОМЛЕНИЕ СОХРАНЕНО</b>\n\n"
        f"{escape(tax_type)} · {escape(str(object_name))}\n"
        f"Налоговый период — <b>{due.year - 1}</b> год\n"
        f"Сумма из уведомления — <b>{money(amount)}</b>\n"
        f"Уже отложено — <b>{money(saved)}</b>\n"
        + paid_line
        +
        f"Осталось накопить — <b>{money(remaining)}</b>\n\n"
        f"Оплатить до — <b>{due.strftime('%d.%m.%Y')}</b>\n"
        f"Временно направлять в «Налоги» — <b>{money(monthly)}</b> в месяц\n"
        f"Будущая годовая норма — <b>{money(annual_monthly)}</b> в месяц. "
        "Она включится в Критический минимум и долгосрочные резервы только после оплаты.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data == "taxes:add")
async def tax_obligation_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TaxStates.obligation_type)
    await callback.message.answer(
        "<b>ВЫБЕРИТЕ НАЛОГ</b>",
        reply_markup=keyboard([
            [("Налог на доход", "taxgoal:type:income")],
            [("Налог на имущество", "taxgoal:type:property")],
            [("Транспортный налог", "taxgoal:type:transport")],
            [("Земельный налог", "taxgoal:type:land")],
            [("Другой налог", "taxgoal:type:other")],
            [("← Главное меню", "taxes:back"), ("← К налогам", "menu:taxes")],
        ]),
    )


@router.callback_query(TaxStates.obligation_type, F.data.startswith("taxgoal:type:"))
async def tax_obligation_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[1]
    if code == "income":
        await show_income_tax_profile_menu(callback.message, callback.from_user.id)
        return
    labels = {
        "property": "Налог на имущество",
        "transport": "Транспортный налог",
        "land": "Земельный налог",
        "patent": "Патент",
        "other": "Другой налог",
    }
    if code not in labels:
        await callback.message.answer(
            "Эта кнопка устарела. Выберите вид налога ещё раз.",
            reply_markup=keyboard([tax_navigation("taxes:add")]),
        )
        return
    await state.update_data(tax_goal_type=labels[code])
    await state.set_state(TaxStates.obligation_name)
    if code == "patent":
        prompt = (
            "<b>ПАТЕНТ</b>\n"
            "——————\n"
            "<b>→ Введите название, по которому вы узнаете этот патент.</b>\n"
            "Например: <b>Ветеринарная клиника — 1-й платёж</b>."
        )
    else:
        examples = {
            "property": "Квартира, дом или гараж",
            "transport": "Автомобиль или мотоцикл",
            "land": "Дачный или другой земельный участок",
            "other": "Страховые взносы или другой понятный вам платёж",
        }
        prompt = (
            f"<b>{labels[code].upper()}</b>\n\n"
            "Введите название объекта или обязательства.\n"
            f"Например: <b>{examples[code]}</b>."
        )
    await callback.message.answer(
        prompt,
        reply_markup=keyboard([tax_navigation("taxes:add")]),
    )


@router.callback_query(F.data.startswith("taxincome:"))
async def income_tax_profile_choice(callback: CallbackQuery, state: FSMContext):
    """Route the compact Taxes-menu choice into the shared profile builder."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) == 2 and parts[1] == "self_employed":
        await show_self_employed_rate_menu(callback.message)
        return
    if len(parts) == 3 and parts[1] == "usn" and parts[2] == "6":
        try:
            profile_name, created = await save_income_tax_profile(
                callback.from_user.id, "ИП", "УСН «Доходы»", Decimal("6"),
            )
        except ValueError as error:
            await callback.message.answer(escape(str(error)))
            return
        await callback.message.answer(
            f"{'Добавлен профиль' if created else 'Такой профиль уже добавлен'} "
            f"<b>{escape(profile_name)}</b>."
        )
        await show_income_tax_profile_menu(callback.message, callback.from_user.id)
        return
    if len(parts) == 3 and parts[1] == "usn" and parts[2] == "custom":
        from settings_editor import EditSettingsStates, tax_profile_navigation
        await state.update_data(
            income_type_action="add_profile",
            income_profile_return="taxes:income",
            income_tax_profile_subject="ИП",
            income_tax_profile_mode="УСН «Доходы»",
        )
        await state.set_state(EditSettingsStates.income_type_rate)
        data = await state.get_data()
        await callback.message.answer(
            "<b>ИП НА УСН «ДОХОДЫ» · СВОЯ СТАВКА</b>\n\n"
            "Введите процент, который нужно откладывать с каждого поступления.",
            reply_markup=keyboard([tax_profile_navigation(data)]),
        )
        return
    if len(parts) == 4 and parts[1] == "npd":
        client = "Физики" if parts[2] == "physical" else "Юрики"
        rate = Decimal(parts[3])
        try:
            profile_name, created = await save_income_tax_profile(
                callback.from_user.id, "Самозанятость", client, rate,
            )
        except ValueError as error:
            await callback.message.answer(escape(str(error)))
            return
        await callback.message.answer(
            f"{'Добавлен профиль' if created else 'Такой профиль уже добавлен'} "
            f"<b>{escape(profile_name)}</b>."
        )
        await show_income_tax_profile_menu(callback.message, callback.from_user.id)
        return
    code = parts[-1]
    if code == "patent":
        await state.set_state(TaxStates.obligation_name)
        await state.update_data(tax_goal_type="Патент", tax_goal_return="taxes:income")
        await callback.message.answer(
            "<b>ИП НА ПСН (ПАТЕНТ)</b>\n\n"
            "Введите название, по которому вы узнаете этот патент.\n\n"
            "Например: <b>Консультации — 1-й платёж</b>.",
            reply_markup=keyboard([tax_navigation("taxes:income")]),
        )
        return
    if code == "ip":
        await show_ip_usn_rate_menu(callback.message)
        return
    if code not in {"self_employed"}:
        await show_income_tax_profile_menu(callback.message, callback.from_user.id)
        return
    from settings_editor import start_income_tax_profile
    await start_income_tax_profile(
        callback.message, state,
        return_to="taxes:income", subject_key=code,
    )


@router.callback_query(F.data == "taxes:income")
async def income_tax_menu_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await state.set_state(TaxStates.obligation_type)
    await show_income_tax_profile_menu(callback.message, callback.from_user.id)


@router.message(TaxStates.obligation_name, F.text & ~F.text.startswith("/"))
async def tax_obligation_name(message: Message, state: FSMContext):
    data = await state.get_data()
    name = parse_tax_object_name(message.text)
    if name is None:
        await message.answer(
            "Здесь нужно название объекта или обязательства, а не сумма.\n\n"
            "Например: <b>Дача</b>, <b>Автомобиль</b>, <b>Квартира</b> "
            "или <b>Патент № 1</b>.",
            reply_markup=keyboard([tax_goal_navigation(data)]),
        )
        return
    await state.update_data(tax_goal_name=name)
    if data.get("tax_goal_type") in ANNUAL_PROPERTY_TAXES:
        due = annual_tax_due_date()
        months = tax_months_remaining(data["tax_goal_type"], due, moscow_today())
        await state.update_data(
            tax_goal_due_date=due.isoformat(),
            tax_goal_months=months,
        )
        await state.set_state(TaxStates.obligation_amount)
        await message.answer(
            f"<b>{escape(name.upper())}</b>\n\n"
            f"Предварительная сумма должна быть готова к <b>{tax_funding_date(data['tax_goal_type'], due).strftime('%d.%m.%Y')}</b>.\n"
            f"Оплатить налог нужно до <b>{due.strftime('%d.%m.%Y')}</b>.\n\n"
            "<b>КАКУЮ ГОДОВУЮ СУММУ ЗАПЛАНИРОВАТЬ?</b>\n\n"
            "Введите полную сумму из последнего налогового уведомления. "
            "Если уведомления ещё не было, укажите осторожную оценку.\n\n"
            "——————\n<b>→ Введите сумму.</b>",
            reply_markup=keyboard([tax_navigation("taxes:add")]),
        )
        return
    await state.set_state(TaxStates.obligation_due_date)
    if data.get("tax_goal_type") == "Патент":
        due_prompt = (
            f"<b>{escape(name.upper())}</b>\n\n"
            "<b>КОГДА НУЖНО ВНЕСТИ ЭТОТ ПЛАТЁЖ?</b>\n\n"
            "Укажите ближайший срок оплаты, указанный в патенте.\n"
            "Если патент оплачивается двумя частями, добавьте в список налогов "
            "каждый платёж отдельно, например:\n"
            "• Кофейня — 1-й платёж\n"
            "• Кофейня — 2-й платёж\n"
            "——————\n"
            "<b>→ Введите дату в формате ДД.ММ.ГГГГ.</b>"
        )
    else:
        due_prompt = (
            f"<b>{escape(name.upper())}</b>\n\n"
            "<b>КОГДА НУЖНО ОПЛАТИТЬ НАЛОГ?</b>\n\n"
            "Введите дату в формате <code>ДД.ММ.ГГГГ</code>."
        )
    await message.answer(
        due_prompt,
        reply_markup=keyboard([tax_goal_navigation(data)]),
    )


def parse_amount(text: str | None) -> Decimal | None:
    value = (text or "").strip().replace("₽", "").replace(" ", "").replace(" ", "")
    if not value or "e" in value.lower():
        return None

    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        whole, fraction = value.rsplit(decimal_separator, 1)
        groups = whole.split(grouping_separator)
        if (
            not whole
            or not fraction.isdigit()
            or len(fraction) > 2
            or any(not group.isdigit() for group in groups)
            or (len(groups) > 1 and not 1 <= len(groups[0]) <= 3)
            or (len(groups) > 1 and any(len(group) != 3 for group in groups[1:]))
        ):
            return None
        value = "".join(groups) + "." + fraction
    elif "," in value:
        parts = value.split(",")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) <= 2:
            value = f"{parts[0]}.{parts[1]}"
        elif len(parts) > 1 and all(len(part) == 3 for part in parts[1:]) and len(parts[0]) <= 3:
            value = "".join(parts)
        else:
            return None

    elif "." in value:
        parts = value.split(".")
        if len(parts) == 2 and not parts[0].startswith("+"):
            if parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) <= 2:
                value = value
            else:
                return None
        elif len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            if not (1 <= len(parts[0]) <= 3 and all(part.isdigit() for part in parts)):
                return None
            value = "".join(parts)
        else:
            return None

    try:
        parsed = Decimal(value)
    except Exception:
        return None

    if not parsed.is_finite() or parsed <= 0 or abs(parsed) > MAX_MONEY_INPUT:
        return None
    if parsed != parsed.quantize(Decimal("0.01")):
        return None
    return parsed


@router.message(TaxStates.obligation_amount, F.text & ~F.text.startswith("/"))
async def tax_obligation_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = parse_amount(message.text)
    if amount is None or amount <= ZERO:
        await message.answer(
            "Введите положительную сумму.",
            reply_markup=keyboard([tax_goal_navigation(data)]),
        )
        return
    await state.update_data(tax_goal_amount=str(amount))
    await state.update_data(tax_goal_saved="0")
    await save_tax_obligation(
        message,
        state,
        message.from_user.id,
        int(data["tax_goal_months"]),
        str(data["tax_goal_due_date"]),
    )


@router.message(TaxStates.obligation_saved, F.text & ~F.text.startswith("/"))
async def tax_obligation_saved(message: Message, state: FSMContext):
    saved = parse_amount(message.text)
    data = await state.get_data()
    target = Decimal(data["tax_goal_amount"])
    if saved is None or saved < ZERO or saved >= target:
        await message.answer(
            f"Введите сумму от 0 до значения меньше {money(target)}. "
            "Если вся сумма уже собрана, создавать план не нужно.",
            reply_markup=keyboard([tax_navigation("taxes:add")]),
        )
        return
    await state.update_data(tax_goal_saved=str(saved))
    await state.set_state(TaxStates.obligation_due_date)
    await message.answer(
        "<b>КОГДА НУЖНО ОПЛАТИТЬ НАЛОГ?</b>\n\n"
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code>. "
        "Аллокатор сам пересчитает сумму накопления при каждом поступлении."
    )


@router.message(TaxStates.obligation_due_date, F.text & ~F.text.startswith("/"))
async def tax_obligation_due_date(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        due = date.fromisoformat("-".join(reversed((message.text or "").strip().split("."))))
    except ValueError:
        example = (moscow_today() + timedelta(days=30)).strftime("%d.%m.%Y")
        await message.answer(
            f"Введите дату в формате ДД.ММ.ГГГГ. Например: <code>{example}</code>",
            reply_markup=keyboard([tax_goal_navigation(data)]),
        )
        return
    if due <= moscow_today():
        await message.answer(
            "Дата должна быть позже сегодняшнего дня.",
            reply_markup=keyboard([tax_goal_navigation(data)]),
        )
        return
    months = tax_months_remaining(data["tax_goal_type"], due, moscow_today())
    await state.update_data(
        tax_goal_due_date=due.isoformat(),
        tax_goal_months=months,
    )
    await state.set_state(TaxStates.obligation_amount)
    await message.answer(
        "<b>СКОЛЬКО НУЖНО ОПЛАТИТЬ?</b>\n\n"
        "Введите полную сумму этого платежа.\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([tax_goal_navigation(data)]),
    )


@router.callback_query(TaxStates.obligation_months, F.data.startswith("taxgoal:months:"))
async def tax_obligation_months_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.rsplit(":", 1)[1]
    if value == "custom":
        await callback.message.answer("Введите количество месяцев целым числом.")
        return
    try:
        months = int(value)
    except ValueError:
        await callback.message.answer(
            "Эта кнопка устарела. Укажите срок ещё раз.",
            reply_markup=keyboard([tax_navigation("taxes:add")]),
        )
        return
    if not 1 <= months <= 120:
        await callback.message.answer(
            "Срок должен быть от 1 до 120 месяцев.",
            reply_markup=keyboard([tax_navigation("taxes:add")]),
        )
        return
    await save_tax_obligation(callback.message, state, callback.from_user.id, months)


@router.message(TaxStates.obligation_months, F.text & ~F.text.startswith("/"))
async def tax_obligation_custom_months(message: Message, state: FSMContext):
    try:
        months = int((message.text or "").strip())
    except ValueError:
        months = 0
    if months <= 0 or months > 120:
        await message.answer(
            "Введите целое количество месяцев от 1 до 120.",
            reply_markup=keyboard([tax_navigation("taxes:add")]),
        )
        return
    await save_tax_obligation(message, state, message.from_user.id, months)


async def save_tax_obligation(
    message: Message, state: FSMContext, telegram_id: int, months: int,
    due_date: str | None = None,
):
    data = await state.get_data()
    target = Decimal(data["tax_goal_amount"])
    declared_saved = Decimal(data["tax_goal_saved"])
    tax_type = data["tax_goal_type"]
    object_name = data["tax_goal_name"]
    key = tax_obligation_key(tax_type, object_name)
    duplicate = next(
        (
            item for item in db.load_tax_obligations(telegram_id)
            if tax_obligation_key(item["tax_type"], item["object_name"]).casefold()
            == key.casefold()
        ),
        None,
    )
    if duplicate is not None:
        await state.clear()
        if tax_type == "Патент":
            duplicate_text = (
                f"Платёж <b>{escape(tax_type)} · {escape(object_name)}</b> уже есть. "
                "Измените существующий платёж или задайте новому патенту другое понятное название."
            )
            duplicate_rows = [
                [("✎ Изменить налоги", "taxes:edit")],
                tax_navigation("taxes:income"),
            ]
        else:
            duplicate_text = (
                f"Налог <b>{escape(tax_type)} · {escape(object_name)}</b> уже есть. "
                "Измените существующий налог или внесите сумму из уведомления ФНС — "
                "второй одинаковый план создавать не нужно."
            )
            duplicate_rows = [
                [("✎ Изменить налоги", "taxes:edit")],
                [("Получено уведомление ФНС", "taxes:notice")],
                tax_navigation("menu:taxes"),
            ]
        await message.answer(
            duplicate_text,
            reply_markup=keyboard(duplicate_rows),
        )
        return
    saved, opening_amount = new_tax_plan_funding(
        telegram_id, key, target, declared_saved,
    )
    monthly = ((target - saved) / Decimal(months)).quantize(
        Decimal("0.01"), rounding=ROUND_CEILING,
    )
    # До первого уведомления это только предварительный текущий план. Точную
    # будущую годовую норму и её влияние на КМ определим по уведомлению/оплате.
    annual_monthly = ZERO
    with db.transaction():
        obligation_id = db.add_tax_obligation(
            telegram_id, tax_type, object_name, target, saved, months, monthly, due_date,
            annual_monthly, opening_amount=opening_amount,
            monthly_period=moscow_today().strftime("%Y-%m"),
            applied_annual_monthly_amount=ZERO,
        )
        allocator = db.load_allocator(telegram_id)
        if allocator is not None:
            set_tax_monthly_target(allocator, key, ZERO)
            allocator.settings.tax_catchups[key] = monthly
            db.save_allocator(telegram_id, allocator)
        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
    await state.clear()
    await message.answer(
        tax_obligation_card_text(
            item, saved, allocator, show_changes=True,
        ),
        reply_markup=keyboard([
            tax_completion_navigation(
                "taxes:income" if data.get("tax_goal_return") == "taxes:income" else "menu:taxes"
            )
        ]),
    )


async def show_tax_obligations_edit(message: Message, telegram_id: int, notice: str = "") -> None:
    obligations = db.load_tax_obligations(telegram_id)
    allocator = db.load_allocator(telegram_id)
    settings = getattr(allocator, "settings", None)
    catalog = getattr(settings, "income_tax_profiles", {})
    profile_names = []
    if isinstance(catalog, dict):
        profile_names = list(dict.fromkeys(
            compact_income_tax_profile(name) for name in catalog
        ))
    rows = [
        [(f"С дохода: {name}", f"taxprofile:view:{index}")]
        for index, name in enumerate(profile_names)
    ]
    rows.extend([
        [(f"{item['tax_type']}: {item['object_name']}", f"taxgoal:view:{item['id']}")]
        for item in obligations
    ])
    rows.append([("＋ Добавить налог", "taxes:add")])
    rows.append([
        ("← Главное меню", "taxes:back"),
        ("← Назад", "menu:taxes"),
    ])
    body = "<b>ИЗМЕНИТЬ НАЛОГИ</b>"
    if notice:
        body = f"{notice}\n\n{body}"
    if profile_names:
        body += "\n\n<b>Налоги с доходов</b>\n" + "\n".join(
            f"• {escape(name)}" for name in profile_names
        )
    if obligations:
        body += "\n\n<b>Плановые налоги</b>\n" + "\n".join(
            f"• {escape(item['tax_type'])} · {escape(item['object_name'])}"
            for item in obligations
        )
    if not profile_names and not obligations:
        body += "\n\nДобавленных налогов пока нет."
    await message.answer(body, reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("taxprofile:view:"))
async def income_tax_profile_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    catalog = getattr(allocator.settings, "income_tax_profiles", {})
    names = list(dict.fromkeys(compact_income_tax_profile(name) for name in catalog))
    try:
        name = names[int(callback.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError):
        await show_tax_obligations_edit(callback.message, callback.from_user.id)
        return
    bound_sources = [
        source for source, profile in allocator.settings.income_type_tax_profiles.items()
        if compact_income_tax_profile(profile) == name
    ]
    await state.update_data(tax_profile_delete_name=name)
    binding = (
        "\n\nИспользуется по умолчанию: " + ", ".join(escape(source) for source in bound_sources)
        if bound_sources else ""
    )
    await callback.message.answer(
        f"<b>{escape(name)}</b>{binding}\n\n"
        "Удаление уберёт правило из настроек будущих поступлений. Уже накопленные "
        "суммы и история доходов сохранятся.",
        reply_markup=keyboard([
            [("🗑️ Удалить", "taxprofile:delete")],
            tax_navigation("taxes:edit"),
        ]),
    )


@router.callback_query(F.data == "taxprofile:delete")
async def income_tax_profile_delete(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    target = str(data.get("tax_profile_delete_name") or "")
    allocator = db.load_allocator(callback.from_user.id)
    if not target or allocator is None:
        await show_tax_obligations_edit(callback.message, callback.from_user.id)
        return
    stored_names = [
        name for name in allocator.settings.income_tax_profiles
        if compact_income_tax_profile(name) == target
    ]
    for name in stored_names:
        allocator.settings.income_tax_profiles.pop(name, None)
        # Remove only legacy pseudo-sources created by the old implementation.
        if name in allocator.settings.income_type_tax_rates:
            allocator.settings.income_type_tax_rates.pop(name, None)
            allocator.settings.income_type_ids.pop(name, None)
    for source, profile in list(allocator.settings.income_type_tax_profiles.items()):
        if compact_income_tax_profile(profile) != target:
            continue
        allocator.settings.income_type_tax_profiles.pop(source, None)
        allocator.settings.income_type_tax_rates[source] = ZERO
    allocator.settings.taxable_income_types = [
        name for name, rate in allocator.settings.income_type_tax_rates.items()
        if rate > ZERO
    ]
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_tax_obligations_edit(callback.message, callback.from_user.id, "Налоговое правило удалено.")


@router.callback_query(F.data == "taxes:edit")
async def tax_obligations_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_tax_obligations_edit(callback.message, callback.from_user.id)


async def show_tax_obligation(
    message: Message,
    telegram_id: int,
    obligation_id: int,
    *,
    completed: bool = False,
) -> None:
    item = next(
        (item for item in db.load_tax_obligations(telegram_id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await show_tax_obligations_edit(message, telegram_id, "Налог не найден.")
        return
    key = tax_obligation_key(item["tax_type"], item["object_name"])
    virtually_saved = virtual_tax_balance(telegram_id, key, obligation_id)
    allocator = db.load_allocator(telegram_id)
    await message.answer(
        tax_obligation_card_text(item, virtually_saved, allocator),
        reply_markup=keyboard([
            [
                ("✎ Название", f"taxgoal:edit_name:{obligation_id}"),
                ("✎ Сумма", f"taxgoal:edit_amount:{obligation_id}"),
            ],
            [("🗑️ Удалить из плана", f"taxgoal:delete:{obligation_id}")],
            tax_completion_navigation("taxes:edit") if completed else tax_navigation("taxes:edit"),
        ]),
    )


@router.callback_query(F.data.regexp(r"^taxgoal:view:\d+$"))
async def tax_obligation_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    await show_tax_obligation(callback.message, callback.from_user.id, obligation_id)


@router.callback_query(F.data.regexp(r"^taxgoal:edit_name:\d+$"))
async def tax_obligation_edit_name(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await show_tax_obligations_edit(callback.message, callback.from_user.id, "Налог не найден.")
        return
    await state.update_data(tax_edit_obligation_id=obligation_id)
    await state.set_state(TaxStates.edit_obligation_name)
    await callback.message.answer(
        f"<b>ИЗМЕНИТЬ НАЗВАНИЕ</b>\n\n"
        f"Сейчас — <b>{escape(item['object_name'])}</b>\n\n"
        "——————\n"
        "<b>→ Введите новое название.</b>",
        reply_markup=keyboard([tax_navigation(f"taxgoal:view:{obligation_id}")]),
    )


@router.message(TaxStates.edit_obligation_name, F.text & ~F.text.startswith("/"))
async def tax_obligation_save_name(message: Message, state: FSMContext):
    new_name = parse_tax_object_name(message.text)
    if new_name is None:
        await message.answer(
            "Здесь нужно название объекта или обязательства, а не сумма.\n\n"
            "Например: <b>Дача</b>, <b>Автомобиль</b>, <b>Квартира</b> "
            "или <b>Патент № 1</b>.",
            reply_markup=keyboard([tax_navigation("taxes:edit")]),
        )
        return
    data = await state.get_data()
    obligation_id = int(data.get("tax_edit_obligation_id", 0))
    item = next(
        (item for item in db.load_tax_obligations(message.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await state.clear()
        await show_tax_obligations_edit(message, message.from_user.id, "Налог не найден.")
        return
    duplicate = next(
        (
            row for row in db.load_tax_obligations(message.from_user.id)
            if row["id"] != obligation_id
            and row["tax_type"] == item["tax_type"]
            and row["object_name"].casefold() == new_name.casefold()
        ),
        None,
    )
    if duplicate is not None:
        await message.answer(
            "Налог с таким названием уже есть. Введите другое название.",
            reply_markup=keyboard([tax_navigation("taxes:edit")]),
        )
        return
    old_key = tax_obligation_key(item["tax_type"], item["object_name"])
    new_key = tax_obligation_key(item["tax_type"], new_name)
    with db.transaction():
        db.rename_tax_obligation(
            message.from_user.id, item["tax_type"], item["object_name"], new_name,
            obligation_id=obligation_id,
        )
        allocator = db.load_allocator(message.from_user.id)
        if allocator is not None:
            annual_monthly = applied_annual_tax_monthly_norm(item)
            catchup = allocator.settings.tax_catchups.pop(old_key, item["monthly_amount"])
            set_tax_monthly_target(allocator, old_key, ZERO)
            set_tax_monthly_target(allocator, new_key, annual_monthly)
            if catchup > ZERO:
                allocator.settings.tax_catchups[new_key] = catchup
            db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_tax_obligation(message, message.from_user.id, obligation_id, completed=True)


@router.callback_query(F.data.regexp(r"^taxgoal:edit_amount:\d+$"))
async def tax_obligation_edit_amount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await show_tax_obligations_edit(callback.message, callback.from_user.id, "Налог не найден.")
        return
    await state.update_data(tax_edit_obligation_id=obligation_id)
    await state.set_state(TaxStates.edit_obligation_amount)
    await callback.message.answer(
        f"<b>ИЗМЕНИТЬ СУММУ НАЛОГА</b>\n\n"
        f"Сейчас запланировано — <b>{money(item['target_amount'])}</b>\n\n"
        "Введите полную годовую сумму. Аллокатор сам учтёт, сколько уже отложено, "
        "и пересчитает дальнейшие пополнения.\n\n"
        "——————\n"
        "<b>→ Введите сумму.</b>",
        reply_markup=keyboard([tax_navigation(f"taxgoal:view:{obligation_id}")]),
    )


@router.message(TaxStates.edit_obligation_amount, F.text & ~F.text.startswith("/"))
async def tax_obligation_save_amount(message: Message, state: FSMContext):
    target = parse_amount(message.text)
    if target is None or target <= ZERO:
        await message.answer(
            "Введите положительную сумму.",
            reply_markup=keyboard([tax_navigation("taxes:edit")]),
        )
        return
    data = await state.get_data()
    obligation_id = int(data.get("tax_edit_obligation_id", 0))
    item = next(
        (item for item in db.load_tax_obligations(message.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await state.clear()
        await show_tax_obligations_edit(message, message.from_user.id, "Налог не найден.")
        return
    paid = db.tax_obligation_paid_amount(message.from_user.id, obligation_id)
    if target < paid:
        await message.answer(
            f"Уже отмечено как оплаченное — <b>{money(paid)}</b>. "
            "Новая сумма не может быть меньше.",
            reply_markup=keyboard([tax_navigation(f"taxgoal:view:{obligation_id}")]),
        )
        return
    today = moscow_today()
    due = date.fromisoformat(item["due_date"]) if item.get("due_date") else None
    if due is not None:
        months = (
            tax_notice_months_remaining(due, today)
            if item.get("notice_received")
            else tax_months_remaining(item["tax_type"], due, today)
        )
    else:
        months = max(1, int(item.get("months", 1)))
    remaining = max(ZERO, target - item["saved_before"])
    monthly = (
        (remaining / Decimal(months)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        if remaining > ZERO else ZERO
    )
    annual_monthly = (
        (target / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        if item["tax_type"] in ANNUAL_PROPERTY_TAXES else ZERO
    )
    with db.transaction():
        db.update_tax_obligation_plan(
            message.from_user.id,
            obligation_id,
            target_amount=target,
            months=months,
            monthly_amount=monthly,
            annual_monthly_amount=annual_monthly,
        )
        allocator = db.load_allocator(message.from_user.id)
        if allocator is not None:
            key = tax_obligation_key(item["tax_type"], item["object_name"])
            # Исправление текущего уведомления не активирует новую годовую
            # норму раньше фактической оплаты. load_allocator уже сохранил
            # последнюю подтверждённую норму, если она была.
            if monthly > ZERO:
                allocator.settings.tax_catchups[key] = monthly
            else:
                allocator.settings.tax_catchups.pop(key, None)
            db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_tax_obligation(message, message.from_user.id, obligation_id, completed=True)


@router.callback_query(F.data.regexp(r"^taxgoal:delete:\d+$"))
async def tax_obligation_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await show_tax_obligations_edit(
            callback.message, callback.from_user.id, "Налог уже удалён.",
        )
        return
    await callback.message.answer(
        "<b>УДАЛИТЬ НАЛОГ ИЗ ПЛАНА?</b>\n\n"
        f"{escape(item['tax_type'])} · {escape(item['object_name'])}\n\n"
        "Аллокатор перестанет копить на него и исключит его годовую норму "
        "из Критического минимума. "
        "История уже внесённых пополнений и оплат сохранится.",
        reply_markup=keyboard([
            [("🗑️ Удалить налог", f"taxgoal:delete_confirm:{obligation_id}")],
            tax_navigation(f"taxgoal:view:{obligation_id}"),
        ]),
    )


@router.callback_query(F.data.regexp(r"^taxgoal:delete_confirm:\d+$"))
async def tax_obligation_delete(callback: CallbackQuery):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await show_tax_obligations_edit(
            callback.message, callback.from_user.id, "Налог уже удалён.",
        )
        return
    with db.transaction():
        db.deactivate_tax_obligation(
            callback.from_user.id, obligation_id, reason="deleted",
        )
        allocator = db.load_allocator(callback.from_user.id)
        if allocator is not None:
            key = tax_obligation_key(item['tax_type'], item['object_name'])
            remaining_same_tax = next(
                (
                    row for row in db.load_tax_obligations(callback.from_user.id)
                    if tax_obligation_key(row["tax_type"], row["object_name"]) == key
                ),
                None,
            )
            if remaining_same_tax is None:
                set_tax_monthly_target(allocator, key, ZERO)
                allocator.settings.tax_catchups.pop(key, None)
            else:
                set_tax_monthly_target(
                    allocator, key,
                    applied_annual_tax_monthly_norm(remaining_same_tax),
                )
                if remaining_same_tax["monthly_amount"] > ZERO:
                    allocator.settings.tax_catchups[key] = remaining_same_tax["monthly_amount"]
            db.save_allocator(callback.from_user.id, allocator)
    await show_tax_obligations_edit(
        callback.message,
        callback.from_user.id,
        "Налог удалён из плана. Ранее сохранённая статистика не изменилась.",
    )


@router.callback_query(F.data == "taxes:back")
async def taxes_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "Что хотите сделать?",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data.startswith("taxreminder:not_paid"))
async def tax_reminder_not_paid(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Хорошо. Налог остаётся активным. Если оплата не будет отмечена, "
        "я напомню снова через неделю.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )


@router.callback_query(F.data.startswith("taxreminder:snooze"))
async def tax_reminder_snooze(callback: CallbackQuery):
    await callback.answer()
    try:
        obligation_id = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.message.answer(
            "Это старое напоминание. Выберите налог, который нужно отложить.",
            reply_markup=keyboard([
                [("Налоги", "menu:taxes")],
                [("← Главное меню", "taxes:back")],
            ]),
        )
        return
    if not db.snooze_tax_payment_reminder(
        callback.from_user.id, obligation_id, days=3,
    ):
        await callback.message.answer("Налог уже закрыт или удалён.")
        return
    await callback.message.answer(
        "Напомню об оплате налога через 3 дня.",
        reply_markup=keyboard([tax_navigation("menu:taxes")]),
    )
