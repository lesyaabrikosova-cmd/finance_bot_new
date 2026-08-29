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


router = Router()
ZERO = Decimal("0")

TAX_GROUPS = (
    "Налог на доход",
    "Налог на имущество",
    "Транспортный налог",
    "Земельный налог",
)

TAX_COLORS = {
    "Налог на доход": "#7656D8",
    "Налог на имущество": "#4F9DD9",
    "Транспортный налог": "#D89B3C",
    "Земельный налог": "#58A66B",
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


ANNUAL_PROPERTY_TAXES = {
    "Налог на имущество",
    "Транспортный налог",
    "Земельный налог",
}


def annual_tax_due_date(today: date | None = None) -> date:
    """Ближайший российский срок уплаты трёх имущественных налогов."""
    today = today or date.today()
    candidate = date(today.year, 12, 1)
    return candidate if today < candidate else date(today.year + 1, 12, 1)


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
    groups: dict[str, dict] = {
        name: {"total": ZERO, "details": defaultdict(lambda: ZERO)}
        for name in TAX_GROUPS
    }
    total_all_time = ZERO
    allocator = db.load_allocator(telegram_id)
    planned = allocator.settings.planned_taxes if allocator else {}
    target_total = sum(planned.values(), ZERO)
    total_all_time += sum(
        (item["opening_amount"] for item in db.load_tax_obligations(telegram_id, active_only=False)),
        ZERO,
    )

    for operation in db.load_operations(telegram_id, limit=10000):
        payload = operation.get("payload", {})
        if payload.get("type") != "income_distribution":
            continue
        operation_date = payload.get("date") or operation.get("created_at", "")[:10]
        try:
            operation_year = date.fromisoformat(operation_date).year
        except (TypeError, ValueError):
            continue

        income_tax = Decimal(str(payload.get("tax", "0")))
        planned_tax = Decimal(str(payload.get("allocations", {}).get("КЖ:Налоги", "0")))
        total_all_time += income_tax + planned_tax

        if operation_year != year:
            continue

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

    annual_total = sum((item["total"] for item in groups.values()), ZERO)
    return groups, annual_total, total_all_time


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

        key = f"{item['tax_type']} · {item['object_name']}"
        allocator.settings.planned_taxes.pop(key, None)
        current = allocator.settings.life_categories.get("Налоги", ZERO)
        next_target = max(ZERO, current - item["monthly_amount"])
        if next_target > ZERO:
            allocator.settings.life_categories["Налоги"] = next_target
        else:
            allocator.settings.life_categories.pop("Налоги", None)
        allocator.settings.critical_life = max(
            sum(allocator.settings.life_categories.values(), ZERO),
            allocator.settings.critical_life - item["monthly_amount"],
        )
        db.update_tax_obligation_monthly(telegram_id, item["id"], ZERO)


def refresh_planned_tax_targets(telegram_id: int, allocator, today: date | None = None) -> None:
    """Пересчитывает налоговый взнос по остатку до конкретной даты."""
    today = today or date.today()
    obligations = db.load_tax_obligations(telegram_id)
    delta_total = ZERO
    for item in obligations:
        if not item.get("due_date"):
            continue
        due = date.fromisoformat(item["due_date"])
        months = tax_months_remaining(item["tax_type"], due, today)
        remaining = max(ZERO, item["target_amount"] - item["saved_before"])
        monthly = (remaining / Decimal(months)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        delta = monthly - item["monthly_amount"]
        if delta == ZERO:
            continue
        delta_total += delta
        db.update_tax_obligation_monthly(telegram_id, item["id"], monthly)
        key = f"{item['tax_type']} · {item['object_name']}"
        allocator.settings.planned_taxes[key] = monthly
    if delta_total != ZERO:
        allocator.settings.life_categories["Налоги"] = max(
            ZERO, allocator.settings.life_categories.get("Налоги", ZERO) + delta_total
        )
        allocator.settings.critical_life = max(
            sum(allocator.settings.life_categories.values(), ZERO),
            allocator.settings.critical_life + delta_total,
        )


def make_pie_chart(groups: dict) -> bytes | None:
    if Image is None or ImageDraw is None:
        return None
    values = [(name, data["total"]) for name, data in groups.items() if data["total"] > ZERO]
    total = sum((value for _, value in values), ZERO)
    if total <= ZERO:
        return None

    image = Image.new("RGB", (960, 540), "#17131f")
    draw = ImageDraw.Draw(image)
    box = (210, 40, 710, 540)
    start = -90.0
    for name, value in values:
        angle = float(value / total * Decimal("360"))
        draw.pieslice(box, start=start, end=start + angle, fill=TAX_COLORS[name], outline="#17131f", width=4)
        start += angle
    draw.ellipse((355, 185, 565, 395), fill="#17131f")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def report_text(
    groups: dict,
    annual_total: Decimal,
    year: int,
    annual_payments: Decimal,
    calculated_balance: Decimal | None,
    detailed: bool,
) -> str:
    lines = [
        "<b>НАЛОГИ</b>",
        "",
        f"Отложено ботом за {year} год — <b>{money(annual_total)}</b>",
    ]
    if calculated_balance is not None:
        lines.append(f"Расчётный остаток в конверте — <b>{money(calculated_balance)}</b>")
        lines.append(f"Отмечено оплаченным за {year} год — <b>{money(annual_payments)}</b>")
    lines.append("")

    for name in TAX_GROUPS:
        amount = groups[name]["total"]
        percent = ZERO if annual_total <= ZERO else amount * Decimal("100") / annual_total
        lines.append(f"<b>{name} — {money(amount)} ({percent.quantize(Decimal('0.1'))}%)</b>")
        details = groups[name]["details"]
        if detailed and details:
            for detail, value in details.items():
                lines.append(f"• {escape(str(detail))} — {money(value)}")
        elif detailed:
            lines.append("• Не настроен")
        lines.append("")
    return "\n".join(lines).rstrip()


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
    rows = [[("Подробнее" if not detailed else "Кратко", "taxes:details" if not detailed else "taxes:summary")]]
    rows.append([("Добавить налог", "taxes:add"), ("Изменить налоги", "taxes:edit")])
    rows.append([("Отметить оплату", "taxes:payment")])
    if allocator.settings.track_tax_payments:
        rows.append([("Не учитывать оплаты", "taxes:tracking:off")])
    else:
        rows.append([("Учитывать оплаты", "taxes:tracking:on")])
    rows.append([("Назад", "taxes:back")])

    chart = make_pie_chart(groups)
    if chart is not None and len(text) <= 1024:
        await message.answer_photo(
            BufferedInputFile(chart, filename=f"taxes-{year}.png"),
            caption=text,
            reply_markup=keyboard(rows),
        )
    else:
        await message.answer(text, reply_markup=keyboard(rows))


@router.callback_query(F.data == "menu:taxes")
async def taxes_menu(callback: CallbackQuery):
    await callback.answer()
    await show_taxes(callback.message, callback.from_user.id)


@router.callback_query(F.data.in_({"taxes:details", "taxes:summary"}))
async def taxes_details(callback: CallbackQuery):
    await callback.answer()
    await show_taxes(callback.message, callback.from_user.id, callback.data == "taxes:details")


@router.callback_query(F.data.startswith("taxes:tracking:"))
async def taxes_tracking(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    allocator.settings.track_tax_payments = callback.data.endswith(":on")
    db.save_allocator(callback.from_user.id, allocator)
    await show_taxes(callback.message, callback.from_user.id)


@router.callback_query(F.data == "taxes:payment")
async def tax_payment_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    obligations = db.load_tax_obligations(callback.from_user.id)
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
        "<b>КАКОЙ НАЛОГ ВЫ ОПЛАТИЛИ?</b>\n\nВведите название налога или объекта."
    )


@router.callback_query(F.data == "taxpayment:other")
async def tax_payment_other(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TaxStates.payment_name)
    await callback.message.answer("Введите название налога или объекта.")


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
    await state.update_data(
        tax_payment_name=f"{item['tax_type']} · {item['object_name']}",
        tax_payment_obligation_id=obligation_id,
        tax_payment_type=item["tax_type"],
        tax_payment_object=item["object_name"],
        tax_payment_due_date=item.get("due_date"),
        tax_payment_suggested=str(item["target_amount"]),
    )
    await state.set_state(TaxStates.payment_amount)
    await callback.message.answer(
        f"<b>{escape(item['tax_type'].upper())}</b>\n\n"
        f"{escape(item['object_name'])}\n"
        f"Плановая сумма — <b>{money(item['target_amount'])}</b>.\n\n"
        "Введите фактически оплаченную сумму."
    )


@router.message(TaxStates.payment_name)
async def tax_payment_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Введите понятное название налога.")
        return
    await state.update_data(tax_payment_name=name)
    await state.set_state(TaxStates.payment_amount)
    await message.answer(f"<b>{escape(name.upper())}</b>\n\nВведите оплаченную сумму.")


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
    db.save_tax_payment(message.from_user.id, data["tax_payment_name"], amount)
    obligation_id = data.get("tax_payment_obligation_id")
    if obligation_id:
        item = next(
            (row for row in db.load_tax_obligations(message.from_user.id) if row["id"] == obligation_id),
            None,
        )
        if item is not None:
            allocator = db.load_allocator(message.from_user.id)
            if allocator is not None and item["monthly_amount"] > ZERO:
                key = f"{item['tax_type']} · {item['object_name']}"
                allocator.settings.planned_taxes.pop(key, None)
                current = allocator.settings.life_categories.get("Налоги", ZERO)
                remaining = max(ZERO, current - item["monthly_amount"])
                if remaining > ZERO:
                    allocator.settings.life_categories["Налоги"] = remaining
                else:
                    allocator.settings.life_categories.pop("Налоги", None)
                allocator.settings.critical_life = max(
                    sum(allocator.settings.life_categories.values(), ZERO),
                    allocator.settings.critical_life - item["monthly_amount"],
                )
                db.save_allocator(message.from_user.id, allocator)
            db.deactivate_tax_obligation(message.from_user.id, int(obligation_id))

    standard_annual = data.get("tax_payment_type") in ANNUAL_PROPERTY_TAXES
    await state.clear()
    if standard_annual:
        await state.update_data(
            next_tax_type=data["tax_payment_type"],
            next_tax_object=data["tax_payment_object"],
        )
        await message.answer(
            f"Оплата <b>{escape(data['tax_payment_name'])}</b> на сумму <b>{money(amount)}</b> сохранена.\n\n"
            "Когда новое начисление появится в ФНС или на Госуслугах, укажите его сумму. "
            "Аллокатор подготовит её к 1 ноября следующего налогового цикла.",
            reply_markup=keyboard([
                [("Ввести новое начисление", "taxpayment:next")],
                [("Сделать позже", "menu:taxes")],
            ]),
        )
        return
    await message.answer(
        f"Оплата <b>{escape(data['tax_payment_name'])}</b> на сумму <b>{money(amount)}</b> сохранена.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "taxpayment:next")
async def ask_next_tax_amount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    if not data.get("next_tax_type"):
        await callback.message.answer("Сначала выберите оплаченный налог.")
        return
    await state.set_state(TaxStates.next_obligation_amount)
    await callback.message.answer(
        f"<b>{escape(str(data['next_tax_type']).upper())}</b>\n\n"
        f"{escape(str(data['next_tax_object']))}\n\n"
        "Введите сумму нового начисления."
    )


@router.message(TaxStates.next_obligation_amount)
async def save_next_tax_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None or amount <= ZERO:
        await message.answer("Введите положительную сумму.")
        return
    data = await state.get_data()
    due = annual_tax_due_date()
    ready = tax_funding_date(data["next_tax_type"], due)
    months = tax_months_remaining(data["next_tax_type"], due, date.today())
    monthly = (amount / Decimal(months)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    db.add_tax_obligation(
        message.from_user.id,
        data["next_tax_type"],
        data["next_tax_object"],
        amount,
        ZERO,
        months,
        monthly,
        due.isoformat(),
    )
    allocator = db.load_allocator(message.from_user.id)
    if allocator is not None:
        key = f"{data['next_tax_type']} · {data['next_tax_object']}"
        allocator.settings.planned_taxes[key] = monthly
        allocator.settings.life_categories["Налоги"] = (
            allocator.settings.life_categories.get("Налоги", ZERO) + monthly
        )
        allocator.settings.critical_life += monthly
        db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(
        f"Новое начисление — <b>{money(amount)}</b>.\n"
        f"Сумма должна быть готова к <b>{ready.strftime('%d.%m.%Y')}</b>.\n"
        f"Оплатить нужно до <b>{due.strftime('%d.%m.%Y')}</b>.\n"
        f"Плановое пополнение — <b>{money(monthly)}</b> за расчётный период.",
        reply_markup=main_menu_keyboard(message.from_user.id),
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
            [("Отмена", "taxes:back")],
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
        "Например: Двушка, Автомобиль, Дача или Патент — первый платёж."
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
            f"Сумма должна быть готова к <b>{tax_funding_date(data['tax_goal_type'], due).strftime('%d.%m.%Y')}</b>.\n"
            f"Оплатить налог нужно до <b>{due.strftime('%d.%m.%Y')}</b>.\n\n"
            "<b>СКОЛЬКО ОСТАЛОСЬ НАКОПИТЬ?</b>\n\n"
            "Укажите не полную сумму начисления, а сумму, которой сейчас не хватает в конверте «Налоги».\n\n"
            "——————\n<b>→ Введите сумму.</b>"
        )
        return
    await state.set_state(TaxStates.obligation_due_date)
    await message.answer(
        f"<b>{escape(name.upper())}</b>\n\n"
        "<b>КОГДА НУЖНО ОПЛАТИТЬ НАЛОГ?</b>\n\n"
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code>."
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
        "——————\n<b>→ Введите сумму.</b>"
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
    monthly = ((target - saved) / Decimal(months)).quantize(Decimal("0.01"))
    tax_type = data["tax_goal_type"]
    object_name = data["tax_goal_name"]
    due_line = (
        f"Оплатить до — <b>{date.fromisoformat(due_date).strftime('%d.%m.%Y')}</b>\n"
        if due_date else ""
    )
    ready_line = ""
    if due_date:
        ready = tax_funding_date(tax_type, date.fromisoformat(due_date))
        if ready != date.fromisoformat(due_date):
            ready_line = f"Сумма должна быть готова — <b>{ready.strftime('%d.%m.%Y')}</b>\n"
    db.add_tax_obligation(
        telegram_id, tax_type, object_name, target, saved, months, monthly, due_date
    )

    allocator = db.load_allocator(telegram_id)
    if allocator is not None:
        key = f"{tax_type} · {object_name}"
        allocator.settings.planned_taxes[key] = monthly
        allocator.settings.life_categories["Налоги"] = (
            allocator.settings.life_categories.get("Налоги", ZERO) + monthly
        )
        allocator.settings.critical_life += monthly
        db.save_allocator(telegram_id, allocator)

    await state.clear()
    await message.answer(
        f"<b>{escape(tax_type.upper())}: {escape(object_name.upper())}</b>\n\n"
        f"Нужно накопить — <b>{money(target)}</b>\n"
        f"Уже накоплено — <b>{money(saved)}</b>\n"
        f"Срок — <b>{months} мес.</b>\n"
        f"{ready_line}"
        f"{due_line}"
        f"Пополнение в месяц — <b>{money(monthly)}</b>\n\n"
        "Сумма включена в Критический минимум и будет направляться в общий конверт «Налоги».",
        reply_markup=main_menu_keyboard(telegram_id),
    )


@router.callback_query(F.data == "taxes:edit")
async def tax_obligations_edit(callback: CallbackQuery):
    await callback.answer()
    obligations = db.load_tax_obligations(callback.from_user.id)
    if not obligations:
        await callback.message.answer("Плановых налогов пока нет.")
        return
    rows = [
        [(f"{item['tax_type']}: {item['object_name']}", f"taxgoal:view:{item['id']}")]
        for item in obligations
    ]
    rows.append([("Назад", "menu:taxes")])
    await callback.message.answer("<b>ПЛАНОВЫЕ НАЛОГИ</b>", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("taxgoal:view:"))
async def tax_obligation_view(callback: CallbackQuery):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        await callback.message.answer("Налоговое обязательство не найдено.")
        return
    due_line = (
        f"Срок — <b>{date.fromisoformat(item['due_date']).strftime('%d.%m.%Y')}</b>\n"
        if item.get("due_date") else ""
    )
    ready_line = ""
    if item.get("due_date"):
        due = date.fromisoformat(item["due_date"])
        ready = tax_funding_date(item["tax_type"], due)
        if ready != due:
            ready_line = f"Сумма должна быть готова — <b>{ready.strftime('%d.%m.%Y')}</b>\n"
    status_line = (
        "Статус — <b>готово к оплате</b>\n"
        if item["saved_before"] >= item["target_amount"]
        else ""
    )
    await callback.message.answer(
        f"<b>{escape(item['tax_type'].upper())}</b>\n\n"
        f"{escape(item['object_name'])}\n"
        f"Нужно — <b>{money(item['target_amount'])}</b>\n"
        f"Уже было накоплено — <b>{money(item['saved_before'])}</b>\n"
        f"{status_line}"
        f"{ready_line}"
        f"{due_line}"
        f"Пополнение в месяц — <b>{money(item['monthly_amount'])}</b>",
        reply_markup=keyboard([
            [("Удалить из плана", f"taxgoal:delete:{obligation_id}")],
            [("Назад", "taxes:edit")],
        ]),
    )


@router.callback_query(F.data.startswith("taxgoal:delete:"))
async def tax_obligation_delete(callback: CallbackQuery):
    await callback.answer()
    obligation_id = int(callback.data.rsplit(":", 1)[1])
    item = next(
        (item for item in db.load_tax_obligations(callback.from_user.id) if item["id"] == obligation_id),
        None,
    )
    if item is None:
        return
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is not None:
        key = f"{item['tax_type']} · {item['object_name']}"
        allocator.settings.planned_taxes.pop(key, None)
        current = allocator.settings.life_categories.get("Налоги", ZERO)
        remaining = max(ZERO, current - item["monthly_amount"])
        if remaining > ZERO:
            allocator.settings.life_categories["Налоги"] = remaining
        else:
            allocator.settings.life_categories.pop("Налоги", None)
        allocator.settings.critical_life = max(
            sum(allocator.settings.life_categories.values(), ZERO),
            allocator.settings.critical_life - item["monthly_amount"],
        )
        db.save_allocator(callback.from_user.id, allocator)
    db.deactivate_tax_obligation(callback.from_user.id, obligation_id)
    await callback.message.answer(
        "Налог удалён из плана. Ранее сохранённая статистика не изменилась.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "taxes:back")
async def taxes_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Что хотите сделать?",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
