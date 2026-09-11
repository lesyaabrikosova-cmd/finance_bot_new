from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_CEILING
from html import escape
from io import BytesIO

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
try:
    from PIL import Image, ImageDraw
except ImportError:  # Диаграмма не должна мешать работе налогового учёта.
    Image = None
    ImageDraw = None

from financial_engine import fmt_money
from storage import db
from ui import keyboard, main_menu_keyboard
from charts import make_chart, send_chart_report


router = Router()
ZERO = Decimal("0")


def tax_obligation_key(tax_type: str, object_name: str) -> str:
    return f"{tax_type} · {object_name}"


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
    stored = Decimal(str(item.get("annual_monthly_amount", ZERO)))
    if stored > ZERO:
        return stored
    if item["tax_type"] in ANNUAL_PROPERTY_TAXES:
        return (item["target_amount"] / Decimal("12")).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING,
        )
    return Decimal(str(item["monthly_amount"]))

TAX_GROUPS = (
    "Налог на доход",
    "Налог на имущество",
    "Транспортный налог",
    "Земельный налог",
)

TAX_COLORS = {
    "Налог на доход": "#7656D8",
    "Налог на имущество": "#E2B93B",
    "Транспортный налог": "#7A7F87",
    "Земельный налог": "#8B5A2B",
}


class TaxStates(StatesGroup):
    payment_name = State()
    payment_amount = State()
    obligation_type = State()
    obligation_name = State()
    obligation_amount = State()
    obligation_saved = State()
    obligation_months = State()
    obligation_due_date = State()
    next_obligation_amount = State()
    edit_obligation_name = State()
    edit_obligation_amount = State()


ANNUAL_PROPERTY_TAXES = {
    "Налог на имущество",
    "Транспортный налог",
    "Земельный налог",
}


def annual_tax_due_date(today: date | None = None) -> date:
    """Ближайший российский срок уплаты трёх имущественных налогов."""
    today = today or date.today()
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


def virtual_tax_balance(telegram_id: int, key: str) -> Decimal:
    """Money virtually assigned to one tax inside the shared bank envelope."""
    balance = sum(
        (
            item["opening_amount"]
            for item in db.load_tax_obligations(telegram_id, active_only=False)
            if tax_obligation_key(item["tax_type"], item["object_name"]) == key
        ),
        ZERO,
    )
    for operation in db.load_operations(telegram_id, limit=100000):
        payload = operation.get("payload", {})
        if payload.get("type") != "income_distribution":
            continue
        details = payload.get("planned_tax_details", {})
        balance += Decimal(str(details.get(key, ZERO)))
    balance -= sum(
        (
            item["amount"]
            for item in db.load_tax_payments(telegram_id)
            if item["tax_name"] == key
        ),
        ZERO,
    )
    return max(ZERO, balance)


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
    key = tax_obligation_key(item["tax_type"], item["object_name"])
    try:
        previous_due = date.fromisoformat(item.get("due_date") or "")
        next_due = date(previous_due.year + 1, 12, 1)
    except ValueError:
        next_due = date(today.year + 1, 12, 1)
    if next_due <= today:
        next_due = date(today.year + 1, 12, 1)

    target = Decimal(str(item["target_amount"]))
    virtually_saved = virtual_tax_balance(telegram_id, key)
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
    )
    set_tax_monthly_target(allocator, key, annual_monthly)
    if monthly > ZERO:
        allocator.settings.tax_catchups[key] = monthly
    else:
        allocator.settings.tax_catchups.pop(key, None)
    return {
        "id": obligation_id,
        "due_date": next_due,
        "monthly_amount": monthly,
        "saved_before": saved_for_next_cycle,
    }


def money(value: Decimal) -> str:
    return f"{fmt_money(value)} ₽"


def tax_group(name: str) -> str:
    lowered = name.lower()
    if "имуще" in lowered or "имущество" in lowered:
        return "Налог на имущество"
    if "транспорт" in lowered:
        return "Транспортный налог"
    if "земел" in lowered:
        return "Земельный налог"
    return "Налог на доход"


def collect_tax_statistics(telegram_id: int, year: int) -> tuple[dict, Decimal, Decimal]:
    """Build current virtual balances inside the shared bank tax envelope."""
    groups: dict[str, dict] = {
        name: {"total": ZERO, "details": defaultdict(lambda: ZERO)}
        for name in TAX_GROUPS
    }
    total_all_time = ZERO
    allocator = db.load_allocator(telegram_id)
    planned = allocator.settings.planned_taxes if allocator else {}
    target_total = sum(planned.values(), ZERO)
    for item in db.load_tax_obligations(telegram_id, active_only=False):
        opening = item["opening_amount"]
        if opening <= ZERO:
            continue
        group = tax_group(item["tax_type"])
        groups[group]["total"] += opening
        groups[group]["details"][item["object_name"]] += opening
        total_all_time += opening

    for operation in db.load_operations(telegram_id, limit=10000):
        payload = operation.get("payload", {})
        if payload.get("type") != "income_distribution":
            continue
        income_tax = Decimal(str(payload.get("tax", "0")))
        planned_tax = Decimal(str(payload.get("allocations", {}).get("КЖ:Налоги", "0")))
        total_all_time += income_tax + planned_tax

        if income_tax > ZERO:
            source = payload.get("income_type") or "Другой доход"
            groups["Налог на доход"]["total"] += income_tax
            groups["Налог на доход"]["details"][source] += income_tax

        if planned_tax > ZERO:
            saved_details = payload.get("planned_tax_details", {})
            if saved_details:
                for name, raw_value in saved_details.items():
                    share = Decimal(str(raw_value))
                    group = tax_group(name)
                    detail = name.split(" · ", 1)[-1]
                    groups[group]["total"] += share
                    groups[group]["details"][detail] += share
            elif target_total > ZERO:
                distributed = ZERO
                entries = list(planned.items())
                for index, (name, target) in enumerate(entries):
                    share = (
                        planned_tax - distributed
                        if index == len(entries) - 1
                        else (planned_tax * target / target_total).quantize(Decimal("0.01"))
                    )
                    distributed += share
                    group = tax_group(name)
                    detail = name.split(" · ", 1)[-1]
                    groups[group]["total"] += share
                    groups[group]["details"][detail] += share
            else:
                groups["Налог на имущество"]["total"] += planned_tax
                groups["Налог на имущество"]["details"]["Налоги Критического минимума"] += planned_tax

    for payment in db.load_tax_payments(telegram_id):
        amount = payment["amount"]
        group = tax_group(payment["tax_name"])
        groups[group]["total"] = max(ZERO, groups[group]["total"] - amount)
        detail = payment["tax_name"].split(" · ", 1)[-1]
        if detail in groups[group]["details"]:
            groups[group]["details"][detail] = max(
                ZERO, groups[group]["details"][detail] - amount,
            )

    current_total = sum((item["total"] for item in groups.values()), ZERO)
    return groups, current_total, total_all_time


def apply_planned_tax_allocation(telegram_id: int, allocator, amount: Decimal) -> None:
    """Зачисляет фактическое пополнение КЖ в активные налоговые цели."""
    amount = Decimal(str(amount))
    obligations = db.load_tax_obligations(telegram_id)
    target_total = sum((item["monthly_amount"] for item in obligations), ZERO)
    if amount <= ZERO or target_total <= ZERO:
        return

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
            need = max(ZERO, item["target_amount"] - item["saved_before"])
            credited = min(share, need)
            item["saved_before"] += credited
            overflow += share - credited
        remaining_amount = overflow
        active = [item for item in active if item["saved_before"] < item["target_amount"]]
        if overflow == ZERO:
            break

    for item in obligations:
        completed = item["saved_before"] >= item["target_amount"]
        db.update_tax_obligation_saved(
            telegram_id,
            item["id"],
            item["saved_before"],
            True,
        )
        if not completed:
            continue

        key = tax_obligation_key(item['tax_type'], item['object_name'])
        # После оплаты имущественный налог остаётся ежегодной статьёй КМ.
        # Обычный разовый налог, напротив, больше не влияет на стоимость жизни.
        if item["tax_type"] not in ANNUAL_PROPERTY_TAXES:
            set_tax_monthly_target(allocator, key, ZERO)
        allocator.settings.tax_catchups.pop(key, None)
        db.update_tax_obligation_monthly(telegram_id, item["id"], ZERO)


def refresh_planned_tax_targets(telegram_id: int, allocator, today: date | None = None, *, persist: bool = True) -> None:
    """Separates annual KМ norm from temporary catch-up before due date."""
    today = today or date.today()
    obligations = db.load_tax_obligations(telegram_id)
    allocator.settings.tax_catchups = {}
    for item in obligations:
        annual_monthly = annual_tax_monthly_norm(item)
        if persist and annual_monthly != item.get("annual_monthly_amount", ZERO):
            db.update_tax_obligation_annual_monthly(
                telegram_id, item["id"], annual_monthly,
            )
        set_tax_monthly_target(
            allocator,
            tax_obligation_key(item['tax_type'], item['object_name']),
            annual_monthly,
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
        monthly = (remaining / Decimal(months)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        if persist and monthly != item["monthly_amount"]:
            db.update_tax_obligation_monthly(telegram_id, item["id"], monthly)
        if monthly > ZERO:
            allocator.settings.tax_catchups[
                tax_obligation_key(item['tax_type'], item['object_name'])
            ] = monthly


def make_pie_chart(groups: dict) -> bytes | None:
    if Image is None:
        return None
    return make_chart({name: data["total"] for name, data in groups.items()},
                      "НАЛОГИ", "Фактически отложено за год", TAX_COLORS)


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


async def show_taxes(message: Message, telegram_id: int, detailed: bool = False) -> None:
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль командой /start.")
        return
    year = date.today().year
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
    rows = [[("+ Добавить налог", "taxes:add"), ("✎ Изменить налоги", "taxes:edit")]]
    rows.append([("Получено уведомление ФНС", "taxes:notice")])
    rows.append([("Налог оплачен", "taxes:payment")])
    rows.append([
        ("← Главное меню", "taxes:back"),
        ("ℹ️ Как это работает", "taxes:help"),
    ])

    tax_values = {name: data["total"] for name, data in groups.items() if data["total"] > ZERO}
    await send_chart_report(
        message, tax_values,
        "НАЛОГИ", text, reply_markup=keyboard(rows),
        subtitle="Сейчас отложено на налоговом счёте", colors=TAX_COLORS,
        center_amount=annual_total,
        center_label="Отложено",
        center_suffix="₽ на налоги",
    )


@router.callback_query(F.data == "taxes:help")
async def taxes_help(callback: CallbackQuery):
    await callback.answer()
    current_year = date.today().year
    tax_year = current_year - 1
    await callback.message.answer(
        "ℹ️ <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "<b>➤ Копим заранее</b>\n"
        f"Сейчас, в {current_year} году, мы копим на налоги за квартиру, машину "
        f"и землю за {tax_year} год.\n"
        "<b>С января по октябрь</b> понемногу откладываем примерную сумму. "
        "За ориентир берём налог прошлого года.\n\n"
        "<b>➤ Сверяем с ФНС</b>\n"
        "<b>1 ноября</b> я напомню проверить налоговое уведомление. Нажмите\n"
        "Получено уведомление\n"
        "и введите сумму из него.\n\n"
        "<b>➤ Докапливаем</b>\n"
        "Аллокатор пересчитает налог по уведомлению и, если нужно, поможет "
        f"накопить недостающую сумму <b>до 1 декабря {current_year}.</b>\n\n"
        "<b>➤ Платим</b>\n"
        "• Налоги на имущество, транспорт и землю нужно оплатить "
        f"до 1 декабря {current_year}.\n"
        "• Налог на доход платите по графику своего налогового режима "
        "(Самозанятый, ИП-УСН, Патент и т.п.).\n\n"
        "<b>➤ Повторяем</b>\n"
        "После оплаты нажмите\n"
        "Налог оплачен\n"
        "— и начнём копить на следующий платёж.",
        reply_markup=keyboard([[('← К налогам', 'menu:taxes')]]),
    )


@router.callback_query(F.data == "taxes:km_repair")
async def ask_critical_life_repair(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or not getattr(
        allocator.settings, "_base_critical_life_inferred", False,
    ):
        await show_taxes(callback.message, callback.from_user.id)
        return
    cancelled = [
        item for item in db.load_tax_obligations(callback.from_user.id, active_only=False)
        if not item["active"]
        and item["saved_before"] < item["target_amount"]
        and item["monthly_amount"] > ZERO
    ]
    correction = sum((item["monthly_amount"] for item in cancelled), ZERO)
    if correction <= ZERO:
        await show_taxes(callback.message, callback.from_user.id)
        return
    await callback.message.answer(
        "<b>ВОССТАНОВИТЬ КРИТИЧЕСКИЙ МИНИМУМ?</b>\n\n"
        f"Найдены отменённые налоговые планы на <b>{money(correction)}</b> в месяц. "
        "Их взносы могли остаться внутри Критического минимума из старой версии расчёта.\n\n"
        f"Критический минимум уменьшится на <b>{money(correction)}</b>.",
        reply_markup=keyboard([
            [("✔️ Восстановить критический минимум", "taxes:km_repair:confirm")],
            [("✖️ Отмена", "menu:taxes")],
        ]),
    )


@router.callback_query(F.data == "taxes:km_repair:confirm")
async def apply_critical_life_repair(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or not getattr(
        allocator.settings, "_base_critical_life_inferred", False,
    ):
        await show_taxes(callback.message, callback.from_user.id)
        return
    cancelled = [
        item for item in db.load_tax_obligations(callback.from_user.id, active_only=False)
        if not item["active"]
        and item["saved_before"] < item["target_amount"]
        and item["monthly_amount"] > ZERO
    ]
    correction = sum((item["monthly_amount"] for item in cancelled), ZERO)
    if correction <= ZERO:
        await show_taxes(callback.message, callback.from_user.id)
        return
    allocator.settings.base_critical_life = max(
        ZERO, allocator.settings.base_critical_life - correction,
    )
    allocator.settings.recalculate_critical_life()
    for item in cancelled:
        db.update_tax_obligation_monthly(callback.from_user.id, item["id"], ZERO)
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        f"Критический минимум уменьшен на <b>{money(correction)}</b>.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
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
    allocator = db.load_allocator(telegram_id)
    configured = set(allocator.settings.planned_taxes) if allocator is not None else set()
    latest: dict[str, dict] = {}
    for item in db.load_tax_obligations(telegram_id, active_only=False):
        if item["tax_type"] not in ANNUAL_PROPERTY_TAXES:
            continue
        key = tax_obligation_key(item["tax_type"], item["object_name"])
        # Оплаченный ежегодный налог остаётся в плане на следующий год;
        # удалённый пользователем налог больше не предлагаем обновлять.
        if key in configured:
            latest[key] = item
    return list(latest.values())


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
                [("← Назад", "menu:taxes")],
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
    rows.append([("← Назад", "menu:taxes")])
    await callback.message.answer(
        "<b>ПО КАКОМУ НАЛОГУ ПРИШЛО УВЕДОМЛЕНИЕ?</b>",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.startswith("taxnotice:item:"))
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
        await callback.message.answer("Налог не найден.")
        return
    key = tax_obligation_key(item["tax_type"], item["object_name"])
    saved = virtual_tax_balance(callback.from_user.id, key)
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
        reply_markup=keyboard([[('← Назад', 'taxes:notice')]]),
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
    if obligations:
        rows = [
            [(
                f"{item['tax_type']} · {item['object_name']}",
                f"taxpayment:obligation:{item['id']}",
            )]
            for item in obligations
        ]
        rows.extend([
            [("Другой налог", "taxpayment:other")],
            [("← Назад", "menu:taxes")],
        ])
        await callback.message.answer(
            "<b>КАКОЙ НАЛОГ ВЫ ОПЛАТИЛИ?</b>",
            reply_markup=keyboard(rows),
        )
        return
    await state.set_state(TaxStates.payment_name)
    await callback.message.answer(
        "<b>КАКОЙ НАЛОГ ВЫ ОПЛАТИЛИ?</b>\n\nВведите название налога или объекта.",
        reply_markup=keyboard([[("← Назад", "menu:taxes")]]),
    )


@router.callback_query(F.data == "taxpayment:other")
async def tax_payment_other(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TaxStates.payment_name)
    await callback.message.answer(
        "Введите название налога или объекта.",
        reply_markup=keyboard([[("← Назад", "menu:taxes")]]),
    )


@router.callback_query(F.data.startswith("taxpayment:obligation:"))
async def tax_payment_obligation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (row for row in db.load_tax_obligations(callback.from_user.id) if row["id"] == obligation_id),
        None,
    )
    if item is None:
        await callback.message.answer("Налоговое обязательство не найдено.")
        return
    already_paid = db.tax_obligation_paid_amount(callback.from_user.id, obligation_id)
    remaining = max(ZERO, item["target_amount"] - already_paid)
    if remaining <= ZERO:
        await callback.message.answer(
            "Этот налог уже отмечен как оплаченный.",
            reply_markup=keyboard([[("← К налогам", "menu:taxes")]]),
        )
        return
    await state.update_data(
        tax_payment_name=f"{item['tax_type']} · {item['object_name']}",
        tax_payment_obligation_id=obligation_id,
        tax_payment_type=item["tax_type"],
        tax_payment_object=item["object_name"],
        tax_payment_due_date=item.get("due_date"),
        tax_payment_suggested=str(remaining),
    )
    await state.set_state(TaxStates.payment_amount)
    await callback.message.answer(
        f"<b>{escape(item['tax_type'].upper())}</b>\n\n"
        f"{escape(item['object_name'])}\n"
        f"Начислено — <b>{money(item['target_amount'])}</b>\n"
        f"Уже оплачено — <b>{money(already_paid)}</b>\n"
        f"Осталось оплатить — <b>{money(remaining)}</b>\n\n"
        "Введите фактически оплаченную сумму.",
        reply_markup=keyboard([[("← Назад", "taxes:payment")]]),
    )


@router.message(TaxStates.payment_name)
async def tax_payment_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Введите понятное название налога.")
        return
    await state.update_data(tax_payment_name=name)
    await state.set_state(TaxStates.payment_amount)
    await message.answer(
        f"<b>{escape(name.upper())}</b>\n\nВведите оплаченную сумму.",
        reply_markup=keyboard([[("← Назад", "taxes:payment")]]),
    )


@router.message(TaxStates.payment_amount)
async def tax_payment_amount(message: Message, state: FSMContext):
    try:
        amount = Decimal((message.text or "").replace(" ", "").replace(",", "."))
    except Exception:
        amount = ZERO
    if amount <= ZERO:
        await message.answer("Введите положительную сумму.")
        return
    data = await state.get_data()
    obligation_id = data.get("tax_payment_obligation_id")
    item = None
    paid_before = ZERO
    remaining_after = ZERO
    fully_paid = False
    if obligation_id:
        item = next(
            (row for row in db.load_tax_obligations(message.from_user.id) if row["id"] == obligation_id),
            None,
        )
        if item is None:
            await state.clear()
            await message.answer(
                "Налоговое обязательство больше не активно.",
                reply_markup=keyboard([[("← К налогам", "menu:taxes")]]),
            )
            return
        paid_before = db.tax_obligation_paid_amount(message.from_user.id, int(obligation_id))
        remaining_before = max(ZERO, item["target_amount"] - paid_before)
        if amount > remaining_before:
            await message.answer(
                f"Осталось оплатить <b>{money(remaining_before)}</b>. "
                "Введите сумму текущей оплаты, не превышающую остаток."
            )
            return
        due_year = None
        if item.get("due_date"):
            try:
                due_year = date.fromisoformat(item["due_date"]).year
            except ValueError:
                pass
        db.save_tax_payment(
            message.from_user.id,
            data["tax_payment_name"],
            amount,
            obligation_id=int(obligation_id),
            tax_due_year=due_year,
        )
        paid_after, remaining_after, fully_paid = calculate_payment_progress(
            item["target_amount"], paid_before, amount,
        )
        if fully_paid:
            allocator = db.load_allocator(message.from_user.id)
            if allocator is not None:
                key = tax_obligation_key(item['tax_type'], item['object_name'])
                if item["tax_type"] in ANNUAL_PROPERTY_TAXES:
                    db.deactivate_tax_obligation(message.from_user.id, int(obligation_id))
                    start_next_annual_tax_cycle(
                        message.from_user.id, item, allocator, date.today(),
                    )
                else:
                    set_tax_monthly_target(allocator, key, ZERO)
                    allocator.settings.tax_catchups.pop(key, None)
                    db.deactivate_tax_obligation(message.from_user.id, int(obligation_id))
                db.save_allocator(message.from_user.id, allocator)
            else:
                db.deactivate_tax_obligation(message.from_user.id, int(obligation_id))
    else:
        db.save_tax_payment(message.from_user.id, data["tax_payment_name"], amount)

    standard_annual = data.get("tax_payment_type") in ANNUAL_PROPERTY_TAXES
    await state.clear()
    if obligation_id and not fully_paid:
        await message.answer(
            f"Частичная оплата <b>{escape(data['tax_payment_name'])}</b> "
            f"на сумму <b>{money(amount)}</b> сохранена.\n\n"
            f"Всего оплачено — <b>{money(paid_before + amount)}</b>\n"
            f"Осталось оплатить — <b>{money(remaining_after)}</b>.\n\n"
            "Налог остаётся активным. Аллокатор не начнёт новый годовой цикл, "
            "пока вы не отметите оплату оставшейся суммы.",
            reply_markup=keyboard([[('← К налогам', 'menu:taxes')]]),
        )
        return
    if standard_annual:
        await message.answer(
            f"Оплата <b>{escape(data['tax_payment_name'])}</b> на сумму <b>{money(amount)}</b> сохранена.\n\n"
            "Аллокатор продолжит копить по последней известной годовой сумме. "
            "Когда придёт новое уведомление ФНС, откройте «Налоги» и нажмите "
            "«Получено уведомление ФНС».",
            reply_markup=keyboard([[("← К налогам", "menu:taxes")]]),
        )
        return
    await message.answer(
        f"Оплата <b>{escape(data['tax_payment_name'])}</b> на сумму <b>{money(amount)}</b> сохранена.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.message(TaxStates.next_obligation_amount)
async def save_next_tax_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None or amount <= ZERO:
        await message.answer("Введите положительную сумму.")
        return
    data = await state.get_data()
    tax_type = data.get("next_tax_type")
    object_name = data.get("next_tax_object")
    if tax_type not in ANNUAL_PROPERTY_TAXES or not object_name:
        await state.clear()
        await message.answer("Не удалось определить налог. Откройте раздел «Налоги» ещё раз.")
        return
    key = tax_obligation_key(tax_type, object_name)
    saved = virtual_tax_balance(message.from_user.id, key)
    source_id = data.get("tax_notice_source_id")
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
    due = annual_tax_due_date()
    remaining, months, monthly, annual_monthly = calculate_notice_plan(
        amount, funded_for_cycle, due, date.today(),
    )
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
                db.deactivate_tax_obligation(message.from_user.id, item["id"])
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
        )
    allocator = db.load_allocator(message.from_user.id)
    if allocator is not None:
        set_tax_monthly_target(
            allocator,
            key,
            annual_monthly,
        )
        if monthly > ZERO:
            allocator.settings.tax_catchups[key] = monthly
        else:
            allocator.settings.tax_catchups.pop(key, None)
        if already_paid >= amount and source_item is not None:
            refreshed_item = next(
                item for item in db.load_tax_obligations(message.from_user.id)
                if item["id"] == obligation_id
            )
            db.deactivate_tax_obligation(message.from_user.id, obligation_id)
            start_next_annual_tax_cycle(
                message.from_user.id, refreshed_item, allocator, date.today(),
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
        f"Сумма из уведомления — <b>{money(amount)}</b>\n"
        f"Уже отложено — <b>{money(saved)}</b>\n"
        + paid_line
        +
        f"Осталось накопить — <b>{money(remaining)}</b>\n\n"
        f"Оплатить до — <b>{due.strftime('%d.%m.%Y')}</b>\n"
        f"Временно направлять в «Налоги» — <b>{money(monthly)}</b> в месяц\n"
        f"Годовая норма для Критического минимума — <b>{money(annual_monthly)}</b> в месяц.",
        reply_markup=keyboard([[("← К налогам", "menu:taxes")]]),
    )


@router.callback_query(F.data == "taxes:add")
async def tax_obligation_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TaxStates.obligation_type)
    await callback.message.answer(
        "<b>КАКОЙ НАЛОГ НУЖНО НАКОПИТЬ?</b>\n\n"
        "Все виды налогов учитываются внутри одного общего конверта «Налоги». "
        "Здесь вы добавляете отдельное обязательство для расчёта суммы и срока.",
        reply_markup=keyboard([
            [("Налог на имущество", "taxgoal:type:property")],
            [("Транспортный налог", "taxgoal:type:transport")],
            [("Земельный налог", "taxgoal:type:land")],
            [("Патент", "taxgoal:type:patent")],
            [("Другой налог", "taxgoal:type:other")],
            [("← Назад", "menu:taxes")],
        ]),
    )


@router.callback_query(TaxStates.obligation_type, F.data.startswith("taxgoal:type:"))
async def tax_obligation_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[1]
    labels = {
        "property": "Налог на имущество",
        "transport": "Транспортный налог",
        "land": "Земельный налог",
        "patent": "Патент",
        "other": "Другой налог",
    }
    if code not in labels:
        return
    await state.update_data(tax_goal_type=labels[code])
    await state.set_state(TaxStates.obligation_name)
    await callback.message.answer(
        f"<b>{labels[code].upper()}</b>\n\nВведите название объекта или обязательства.\n"
        "Например: Двушка, Автомобиль, Дача или Патент — первый платёж.",
        reply_markup=keyboard([[('← Назад', 'taxes:add')]]),
    )


@router.message(TaxStates.obligation_name)
async def tax_obligation_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Введите понятное название.")
        return
    await state.update_data(tax_goal_name=name)
    data = await state.get_data()
    if data.get("tax_goal_type") in ANNUAL_PROPERTY_TAXES:
        due = annual_tax_due_date()
        months = tax_months_remaining(data["tax_goal_type"], due, date.today())
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
            reply_markup=keyboard([[('← Назад', 'taxes:add')]]),
        )
        return
    await state.set_state(TaxStates.obligation_due_date)
    await message.answer(
        f"<b>{escape(name.upper())}</b>\n\n"
        "<b>КОГДА НУЖНО ОПЛАТИТЬ НАЛОГ?</b>\n\n"
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code>.",
        reply_markup=keyboard([[('← Назад', 'taxes:add')]]),
    )


def parse_amount(text: str | None) -> Decimal | None:
    try:
        return Decimal((text or "").replace(" ", "").replace(",", "."))
    except Exception:
        return None


@router.message(TaxStates.obligation_amount)
async def tax_obligation_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None or amount <= ZERO:
        await message.answer("Введите положительную сумму.")
        return
    await state.update_data(tax_goal_amount=str(amount))
    data = await state.get_data()
    await state.update_data(tax_goal_saved="0")
    await save_tax_obligation(
        message,
        state,
        message.from_user.id,
        int(data["tax_goal_months"]),
        str(data["tax_goal_due_date"]),
    )


@router.message(TaxStates.obligation_saved)
async def tax_obligation_saved(message: Message, state: FSMContext):
    saved = parse_amount(message.text)
    data = await state.get_data()
    target = Decimal(data["tax_goal_amount"])
    if saved is None or saved < ZERO or saved >= target:
        await message.answer(
            f"Введите сумму от 0 до значения меньше {money(target)}. "
            "Если вся сумма уже собрана, создавать план не нужно."
        )
        return
    await state.update_data(tax_goal_saved=str(saved))
    await state.set_state(TaxStates.obligation_due_date)
    await message.answer(
        "<b>КОГДА НУЖНО ОПЛАТИТЬ НАЛОГ?</b>\n\n"
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code>. "
        "Аллокатор сам пересчитает сумму накопления при каждом поступлении."
    )


@router.message(TaxStates.obligation_due_date)
async def tax_obligation_due_date(message: Message, state: FSMContext):
    try:
        due = date.fromisoformat("-".join(reversed((message.text or "").strip().split("."))))
    except ValueError:
        await message.answer("Введите дату в формате ДД.ММ.ГГГГ. Например: <code>01.12.2026</code>")
        return
    if due <= date.today():
        await message.answer("Дата должна быть позже сегодняшнего дня.")
        return
    data = await state.get_data()
    months = tax_months_remaining(data["tax_goal_type"], due, date.today())
    await state.update_data(
        tax_goal_due_date=due.isoformat(),
        tax_goal_months=months,
    )
    await state.set_state(TaxStates.obligation_amount)
    await message.answer(
        "<b>СКОЛЬКО ОСТАЛОСЬ НАКОПИТЬ К ДАТЕ ПЛАТЕЖА?</b>\n\n"
        "Укажите не полную сумму начисления, а остаток, которого сейчас не хватает в конверте «Налоги».\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[('← Назад', 'taxes:add')]]),
    )


@router.callback_query(TaxStates.obligation_months, F.data.startswith("taxgoal:months:"))
async def tax_obligation_months_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.rsplit(":", 1)[1]
    if value == "custom":
        await callback.message.answer("Введите количество месяцев целым числом.")
        return
    await save_tax_obligation(callback.message, state, callback.from_user.id, int(value))


@router.message(TaxStates.obligation_months)
async def tax_obligation_custom_months(message: Message, state: FSMContext):
    try:
        months = int((message.text or "").strip())
    except ValueError:
        months = 0
    if months <= 0 or months > 120:
        await message.answer("Введите целое количество месяцев от 1 до 120.")
        return
    await save_tax_obligation(message, state, message.from_user.id, months)


async def save_tax_obligation(
    message: Message, state: FSMContext, telegram_id: int, months: int,
    due_date: str | None = None,
):
    data = await state.get_data()
    target = Decimal(data["tax_goal_amount"])
    saved = Decimal(data["tax_goal_saved"])
    monthly = ((target - saved) / Decimal(months)).quantize(
        Decimal("0.01"), rounding=ROUND_CEILING,
    )
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
        await message.answer(
            f"Налог <b>{escape(tax_type)} · {escape(object_name)}</b> уже есть. "
            "Измените существующий налог или внесите сумму из уведомления ФНС — "
            "второй одинаковый план создавать не нужно.",
            reply_markup=keyboard([
                [("✎ Изменить налоги", "taxes:edit")],
                [("Получено уведомление ФНС", "taxes:notice")],
                [("← К налогам", "menu:taxes")],
            ]),
        )
        return
    annual_monthly = (
        (target / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        if tax_type in ANNUAL_PROPERTY_TAXES else monthly
    )
    due_line = (
        f"Оплатить до — <b>{date.fromisoformat(due_date).strftime('%d.%m.%Y')}</b>\n"
        if due_date else ""
    )
    ready_line = ""
    if due_date:
        ready = tax_funding_date(tax_type, date.fromisoformat(due_date))
        if ready != date.fromisoformat(due_date):
            ready_line = f"Предварительная сумма должна быть готова — <b>{ready.strftime('%d.%m.%Y')}</b>\n"
    db.add_tax_obligation(
        telegram_id, tax_type, object_name, target, saved, months, monthly, due_date,
        annual_monthly,
    )

    allocator = db.load_allocator(telegram_id)
    reserve_effect = ""
    if allocator is not None:
        pillow_before = allocator.settings.force_majeure_limit
        stabilizer_before = allocator.settings.stabilizer_full_limit
        set_tax_monthly_target(allocator, key, annual_monthly)
        allocator.settings.tax_catchups[key] = monthly
        pillow_delta = allocator.settings.force_majeure_limit - pillow_before
        stabilizer_delta = allocator.settings.stabilizer_full_limit - stabilizer_before
        effects = [f"➤ Критический минимум — +{money(annual_monthly)}"]
        if pillow_delta > ZERO:
            effects.append(f"➤ Цель Подушки — +{money(pillow_delta)}")
        if allocator.settings.needs_stabilizer and stabilizer_delta > ZERO:
            effects.append(f"➤ Цель Стабилизатора — +{money(stabilizer_delta)}")
        reserve_effect = (
            "\n\n————————————\n"
            "<b>ИЗМЕНЕНИЕ ФИНАНСОВЫХ ЦЕЛЕЙ</b>\n\n"
            + "\n".join(effects)
            + "\n\nВ Критический минимум и резервы входит годовая норма налога. "
            "Срочное накопление до ближайшей даты влияет только на пополнение конверта «Налоги»."
        )
        db.save_allocator(telegram_id, allocator)

    await state.clear()
    await message.answer(
        f"<b>{escape(tax_type.upper())}: {escape(object_name.upper())}</b>\n\n"
        f"Нужно накопить — <b>{money(target)}</b>\n"
        f"Уже накоплено — <b>{money(saved)}</b>\n"
        f"Срок — <b>{months} мес.</b>\n"
        f"{ready_line}"
        f"{due_line}"
        f"Срочно направлять в «Налоги» — <b>{money(monthly)}</b> в месяц\n"
        f"Годовая норма для Критического минимума — <b>{money(annual_monthly)}</b> в месяц\n\n"
        "Годовая норма включена в Критический минимум. До 1 ноября Аллокатор собирает "
        "предварительную сумму. Когда придёт уведомление ФНС, нажмите "
        "«Получено уведомление ФНС» "
        "и введите полную сумму из него."
        f"{reserve_effect}",
        reply_markup=main_menu_keyboard(telegram_id),
    )


async def show_tax_obligations_edit(message: Message, telegram_id: int, notice: str = "") -> None:
    obligations = db.load_tax_obligations(telegram_id)
    rows = [
        [(f"{item['tax_type']}: {item['object_name']}", f"taxgoal:view:{item['id']}")]
        for item in obligations
    ]
    rows.append([
        ("← Главное меню", "taxes:back"),
        ("← Назад", "menu:taxes"),
    ])
    body = "<b>ПЛАНОВЫЕ НАЛОГИ</b>"
    if notice:
        body = f"{notice}\n\n{body}"
    if not obligations:
        body += "\n\nПлановых налогов пока нет."
    await message.answer(body, reply_markup=keyboard(rows))


@router.callback_query(F.data == "taxes:edit")
async def tax_obligations_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_tax_obligations_edit(callback.message, callback.from_user.id)


async def show_tax_obligation(message: Message, telegram_id: int, obligation_id: int) -> None:
    item = next(
        (item for item in db.load_tax_obligations(telegram_id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await show_tax_obligations_edit(message, telegram_id, "Налог не найден.")
        return
    due_line = (
        f"Оплатить до — <b>{date.fromisoformat(item['due_date']).strftime('%d.%m.%Y')}</b>\n"
        if item.get("due_date") else ""
    )
    ready_line = ""
    if item.get("due_date") and not item.get("notice_received"):
        due = date.fromisoformat(item["due_date"])
        ready = tax_funding_date(item["tax_type"], due)
        if ready != due:
            ready_line = f"Предварительная сумма должна быть готова — <b>{ready.strftime('%d.%m.%Y')}</b>\n"
    key = tax_obligation_key(item["tax_type"], item["object_name"])
    virtually_saved = virtual_tax_balance(telegram_id, key)
    status_line = (
        "Статус — <b>готово к оплате</b>\n"
        if virtually_saved >= item["target_amount"]
        else ""
    )
    await message.answer(
        f"<b>{escape(item['tax_type'].upper())}</b>\n\n"
        f"{escape(item['object_name'])}\n"
        f"Нужно — <b>{money(item['target_amount'])}</b>\n"
        f"Аллокатор отнёс на этот налог — <b>{money(virtually_saved)}</b>\n"
        f"{status_line}"
        f"{ready_line}"
        f"{due_line}"
        f"Срочно направлять в «Налоги» — <b>{money(item['monthly_amount'])}</b> в месяц\n"
        f"Годовая норма для Критического минимума — "
        f"<b>{money(annual_tax_monthly_norm(item))}</b> в месяц",
        reply_markup=keyboard([
            [
                ("✎ Название", f"taxgoal:edit_name:{obligation_id}"),
                ("✎ Сумма", f"taxgoal:edit_amount:{obligation_id}"),
            ],
            [("Удалить из плана", f"taxgoal:delete:{obligation_id}")],
            [("← Назад", "taxes:edit")],
        ]),
    )


@router.callback_query(F.data.startswith("taxgoal:view:"))
async def tax_obligation_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    await show_tax_obligation(callback.message, callback.from_user.id, obligation_id)


@router.callback_query(F.data.startswith("taxgoal:edit_name:"))
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
        reply_markup=keyboard([[('← Назад', f'taxgoal:view:{obligation_id}')]]),
    )


@router.message(TaxStates.edit_obligation_name)
async def tax_obligation_save_name(message: Message, state: FSMContext):
    new_name = (message.text or "").strip()
    if len(new_name) < 2 or len(new_name) > 60:
        await message.answer("Введите название длиной от 2 до 60 символов.")
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
        await message.answer("Налог с таким названием уже есть. Введите другое название.")
        return
    old_key = tax_obligation_key(item["tax_type"], item["object_name"])
    new_key = tax_obligation_key(item["tax_type"], new_name)
    db.rename_tax_obligation(
        message.from_user.id, item["tax_type"], item["object_name"], new_name,
    )
    allocator = db.load_allocator(message.from_user.id)
    if allocator is not None:
        annual_monthly = annual_tax_monthly_norm(item)
        catchup = allocator.settings.tax_catchups.pop(old_key, item["monthly_amount"])
        set_tax_monthly_target(allocator, old_key, ZERO)
        set_tax_monthly_target(allocator, new_key, annual_monthly)
        if catchup > ZERO:
            allocator.settings.tax_catchups[new_key] = catchup
        db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_tax_obligation(message, message.from_user.id, obligation_id)


@router.callback_query(F.data.startswith("taxgoal:edit_amount:"))
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
        reply_markup=keyboard([[('← Назад', f'taxgoal:view:{obligation_id}')]]),
    )


@router.message(TaxStates.edit_obligation_amount)
async def tax_obligation_save_amount(message: Message, state: FSMContext):
    target = parse_amount(message.text)
    if target is None or target <= ZERO:
        await message.answer("Введите положительную сумму.")
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
            "Новая сумма не может быть меньше."
        )
        return
    today = date.today()
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
        if item["tax_type"] in ANNUAL_PROPERTY_TAXES else monthly
    )
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
        set_tax_monthly_target(allocator, key, annual_monthly)
        if monthly > ZERO:
            allocator.settings.tax_catchups[key] = monthly
        else:
            allocator.settings.tax_catchups.pop(key, None)
        db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_tax_obligation(message, message.from_user.id, obligation_id)


@router.callback_query(F.data.startswith("taxgoal:delete:"))
async def tax_obligation_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await callback.message.answer("Налог уже удалён.")
        return
    await callback.message.answer(
        "<b>УДАЛИТЬ НАЛОГ ИЗ ПЛАНА?</b>\n\n"
        f"{escape(item['tax_type'])} · {escape(item['object_name'])}\n\n"
        "Аллокатор перестанет копить на него и исключит его годовую норму "
        "из Критического минимума. "
        "История уже внесённых пополнений и оплат сохранится.",
        reply_markup=keyboard([
            [("Удалить налог", f"taxgoal:delete_confirm:{obligation_id}")],
            [("✖️ Отмена", f"taxgoal:view:{obligation_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("taxgoal:delete_confirm:"))
async def tax_obligation_delete(callback: CallbackQuery):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        return
    db.deactivate_tax_obligation(callback.from_user.id, obligation_id)
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
                allocator, key, annual_tax_monthly_norm(remaining_same_tax),
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


@router.callback_query(F.data == "taxreminder:not_paid")
async def tax_reminder_not_paid(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Хорошо. Налог остаётся активным. Если оплата не будет отмечена, "
        "я напомню снова через неделю.",
        reply_markup=keyboard([[('← К налогам', 'menu:taxes')]]),
    )


@router.callback_query(F.data == "taxreminder:snooze")
async def tax_reminder_snooze(callback: CallbackQuery):
    await callback.answer()
    db.snooze_tax_payment_reminders(callback.from_user.id, days=3)
    await callback.message.answer(
        "Напомню об оплате налога через 3 дня.",
        reply_markup=keyboard([[('← К налогам', 'menu:taxes')]]),
    )
