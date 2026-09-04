from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from storage import db
from ui import keyboard, main_menu_keyboard

router = Router()

class EditSettingsStates(StatesGroup):
    pillow = State()
    critical_life = State()
    household_reserve = State()
    average_income = State()
    income_gap_months = State()
    income_work_months = State()
    force_majeure_months = State()
    stabilizer_months = State()
    stabilizer_balance = State()
    intercontract_balance = State()
    planned_amount = State()
    planned_due_date = State()
    tax_rate = State()
    income_type_name = State()
    income_type_rate = State()
    income_type_edit_name = State()
    income_type_edit_rate = State()
    income_type_confirm = State()
    life_categories = State()
    goal_percentages = State()
    c_split = State()

def parse_decimal(text: str) -> Decimal | None:
    if not text:
        return None
    value = text.strip().replace("₽", "").replace("%", "").replace("\u00a0", "").replace(" ", "")
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    elif "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None

def rub(value: Decimal) -> str:
    formatted = f"{Decimal(value):,.2f}"
    return formatted.replace(",", " ").replace(".", ",") + " ₽"

def distribute_existing_pillow(allocator, total: Decimal) -> None:
    s = allocator.settings
    st = allocator.state

    st.pillow_minimum = Decimal("0")
    st.pillow_force_majeure = Decimal("0")

    remaining = total

    if any(credit.active for credit in s.credits):
        part = min(remaining, s.minimum_reserve_limit)
        st.pillow_minimum = part
        remaining -= part

    st.pillow_force_majeure = remaining

async def show_settings_menu(message: Message, telegram_id: int):
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала настройте профиль через /start.")
        return

    s = allocator.settings
    st = allocator.state
    
    dev_status = (
        "включён"
        if s.developer_mode
        else "выключен"
    )

    dev_button = (
        "🛠 Выключить режим разработчика"
        if s.developer_mode
        else "🛠 Включить режим разработчика"
    )

    goals = ", ".join(
        f"{'💼' if g.is_chest else '⭐️'} {g.name} {g.percentage}%"
        for g in s.goals
    ) if s.goals else "без отдельных категорий"
    categories = ", ".join(f"{name} {rub(amount)}" for name, amount in s.life_categories.items()) if s.life_categories else "нет отдельных категорий"
    rhythm_labels = {"monthly": "Стабильный", "irregular": "Сдельный", "cyclic": "Цикличный (контрактный)"}

    await message.answer(
        "⚙️ <b>РЕДАКТИРОВАНИЕ НАСТРОЕК</b>\n\n"
        f"🔴 Обязательная жизнь: <b>{rub(s.critical_life)}</b>\n"
        f"💚 Бытовой резерв: <b>{rub(s.household_reserve)}</b>\n"
        f"💰 Средний доход: <b>{rub(s.average_income)}</b>\n"
        f"Ритм дохода: <b>{rhythm_labels.get(s.income_rhythm, s.income_rhythm)}</b>\n"
        + (f"Финансовый цикл: <b>{s.income_work_months} / {s.income_gap_months}</b>\n" if s.income_rhythm == "cyclic" else "")
        + (
            "Текущая фаза: <b>"
            + (
                f"Перерыв · осталось {st.intercontract_months_remaining} мес."
                if st.intercontract_break_active
                else "Рабочая часть"
            )
            + "</b>\n"
            if s.income_rhythm == "cyclic"
            else ""
        )
        + (f"Доход текущего цикла: <b>{rub(st.cycle_income)}</b> / {rub(s.cycle_regular_income_limit)}\n" if s.income_rhythm == "cyclic" else "")
        + (f"Стабилизатор: <b>{s.stabilizer_target_months} мес.</b>\n" if s.needs_stabilizer else "")
        + (f"Обязательства на время контракта: <b>{rub(s.contract_obligations_total)}</b>\n" if s.income_rhythm == "cyclic" else "")
        + (f"Уже зарезервировано на рабочую часть: <b>{rub(st.contract_obligations_reserve)}</b>\n" if s.income_rhythm == "cyclic" else "")
        +
        f"Типов доходов: <b>{len(s.income_type_tax_rates)}</b>\n"
        f"🛡️ Подушка сейчас: <b>{rub(st.pillow_balance)}</b>\n"
        + (f"🛟 Стабилизатор дохода: <b>{rub(st.stabilizer_balance)}</b> / {rub(s.stabilizer_full_limit)}\n" if s.needs_stabilizer else "")
        + (f"Фонд Зарплаты: <b>{rub(st.intercontract_reserve)}</b> / {rub(allocator.intercontract_current_limit)}\n" if s.income_rhythm == "cyclic" else "")
        +
        f"🛠 Режим разработчика: <b>{dev_status}</b>\n\n"
        f"❤️ Категории КЖ: {escape(categories)}\n"
        f"Цели и Сундуки: {escape(goals)}\n\n"
        f"Распределение этапа C: ⭐️ цели {s.goals_share_c}% / 🛡️ подушка {s.pillow_share_c}%",
        reply_markup=keyboard([
            [("🛡️ Изменить Подушку", "settings:pillow")],
            [("ФМ-подушка", "settings:force_months"), ("Стабилизатор", "settings:stabilizer_months")],
            [("Баланс Стабилизатора", "settings:stabilizer_balance")],
            [("Фонд Зарплаты", "settings:intercontract_balance")],
            [("🔴 Изменить КЖ", "settings:critical"), ("💚 Изменить Быт. резерв", "settings:household")],
            *(
                [
                    [("Изменить рабочую жизнь", "phaselife:fill:work")],
                    [("Изменить жизнь в перерыве", "phaselife:fill:break")],
                ]
                if s.income_rhythm == "cyclic"
                else []
            ),
            [("💰 Средний доход", "settings:income")],
            [("Ритм поступлений", "settings:rhythm")],
            [("Типы доходов", "settings:income_types")],
            [("Плановые платежи", "settings:planned")],
            [("❤️ Категории КЖ", "settings:life_categories")],
            [("Цели и Сундуки", "goals:manage")],
            [(dev_button, "settings:developer")],
            [("🗑 Полный сброс учёта", "settings:full_reset")],
            [("🔄 Пройти настройку заново", "setup:restart")],
            [("⬅️ Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "settings:force_months")
async def edit_force_months(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    s = allocator.settings
    minimum = Decimal("4") if s.income_rhythm == "irregular" else Decimal("3")
    if s.income_rhythm == "cyclic" and s.income_gap_months > 1:
        minimum = Decimal("6")
    await state.set_state(EditSettingsStates.force_majeure_months)
    await state.update_data(force_minimum=str(minimum))
    await callback.message.answer(
        f"<b>ФОРС-МАЖОРНАЯ ПОДУШКА</b>\n\nВведите количество месяцев от {minimum} до 12."
    )


@router.message(EditSettingsStates.force_majeure_months)
async def save_force_months_setting(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    data = await state.get_data()
    minimum = Decimal(data.get("force_minimum", "3"))
    if value is None or value < minimum or value > 12:
        await message.answer(f"Введите количество месяцев от {minimum} до 12.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.force_majeure_months = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_settings_menu(message, message.from_user.id)


@router.callback_query(F.data == "settings:stabilizer_months")
async def edit_stabilizer_months(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if not allocator.settings.needs_stabilizer:
        await callback.message.answer("Для Стабильного профиля Стабилизатор не используется.")
        return
    await state.set_state(EditSettingsStates.stabilizer_months)
    await callback.message.answer("<b>СТАБИЛИЗАТОР ДОХОДА</b>\n\nВведите количество месяцев от 1 до 12.")


@router.message(EditSettingsStates.stabilizer_months)
async def save_stabilizer_months_setting(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 12:
        await message.answer("Введите количество месяцев от 1 до 12.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.stabilizer_target_months = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_settings_menu(message, message.from_user.id)


@router.callback_query(F.data == "settings:stabilizer_balance")
async def edit_stabilizer_balance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if not allocator.settings.needs_stabilizer:
        await callback.message.answer("Для Стабильного профиля Стабилизатор не используется.")
        return
    await state.set_state(EditSettingsStates.stabilizer_balance)
    await callback.message.answer(
        "<b>ТЕКУЩИЙ БАЛАНС СТАБИЛИЗАТОРА ДОХОДА</b>\n\n"
        f"Сейчас: <b>{rub(allocator.state.stabilizer_balance)}</b>\n\n"
        "Введите фактическую сумму в Стабилизаторе дохода."
    )


@router.message(EditSettingsStates.stabilizer_balance)
async def save_stabilizer_balance(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.state.pillow_stabilizer = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_settings_menu(message, message.from_user.id)


@router.callback_query(F.data == "settings:intercontract_balance")
async def edit_intercontract_balance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator.settings.income_rhythm != "cyclic":
        await callback.message.answer("Фонд Зарплаты используется только в Цикличном (контрактном) профиле.")
        return
    await state.set_state(EditSettingsStates.intercontract_balance)
    await callback.message.answer(
        "<b>БАЛАНС ФОНДА ЗАРПЛАТЫ</b>\n\nВведите сумму, которая уже отложена на плановый перерыв."
    )


@router.message(EditSettingsStates.intercontract_balance)
async def save_intercontract_balance(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    if value > allocator.settings.intercontract_full_limit:
        await message.answer(f"Текущая цель Фонда Зарплаты — {rub(allocator.settings.intercontract_full_limit)}.")
        return
    allocator.state.intercontract_reserve = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_settings_menu(message, message.from_user.id)


async def show_planned_payments(message: Message, telegram_id: int):
    items = db.load_planned_payments(telegram_id)
    taxes = db.load_tax_obligations(telegram_id)
    lines = []
    rows = []
    for item in items:
        remaining = max(Decimal("0"), item["target_amount"] - item["saved_amount"])
        due = date.fromisoformat(item["due_date"]).strftime("%d.%m.%Y")
        lines.append(
            f"• <b>{escape(item['payment_name'])}</b>\n"
            f"  осталось {rub(remaining)}, до {due}, сейчас {rub(item['monthly_amount'])}/мес"
        )
        rows.append([(f"{item['payment_name']}", f"planned:view:{item['id']}")])
    for item in taxes:
        remaining = max(Decimal("0"), item["target_amount"] - item["saved_before"])
        due = f", до {date.fromisoformat(item['due_date']).strftime('%d.%m.%Y')}" if item.get("due_date") else ""
        lines.append(
            f"• <b>{escape(item['tax_type'])} · {escape(item['object_name'])}</b>\n"
            f"  осталось {rub(remaining)}{due}"
        )
    rows.append([("← Настройки", "settings:open")])
    await message.answer(
        "<b>ПЛАНОВЫЕ ПЛАТЕЖИ</b>\n\n"
        + ("\n\n".join(lines) if lines else "Активных плановых платежей нет.")
        + "\n\nСумма ежемесячного накопления пересчитывается по остатку и сроку.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data == "settings:planned")
async def open_planned_payments(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_planned_payments(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("planned:view:"))
async def view_planned_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    payment_id = int(callback.data.rsplit(":", 1)[1])
    item = next((x for x in db.load_planned_payments(callback.from_user.id) if x["id"] == payment_id), None)
    if item is None:
        await show_planned_payments(callback.message, callback.from_user.id)
        return
    await callback.message.answer(
        f"<b>{escape(item['payment_name'])}</b>\n\n"
        f"Нужно накопить — {rub(item['target_amount'])}\n"
        f"Уже учтено — {rub(item['saved_amount'])}\n"
        f"Срок — {date.fromisoformat(item['due_date']).strftime('%d.%m.%Y')}",
        reply_markup=keyboard([
            [("Изменить сумму", f"planned:amount:{payment_id}"), ("Изменить дату", f"planned:date:{payment_id}")],
            [("Отметить оплату", f"planned:close:{payment_id}")],
            [("Отменить обязательство", f"planned:cancel:{payment_id}")],
            [("← Назад", "settings:planned")],
        ]),
    )


@router.callback_query(F.data.startswith("planned:amount:"))
async def edit_planned_amount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(planned_payment_id=int(callback.data.rsplit(":", 1)[1]))
    await state.set_state(EditSettingsStates.planned_amount)
    await callback.message.answer("Введите новую полную сумму планового платежа.")


@router.message(EditSettingsStates.planned_amount)
async def save_planned_amount(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    data = await state.get_data()
    payment_id = data.get("planned_payment_id")
    item = next((x for x in db.load_planned_payments(message.from_user.id) if x["id"] == payment_id), None)
    if item is None:
        await state.clear()
        await show_planned_payments(message, message.from_user.id)
        return
    if value is None or value <= item["saved_amount"]:
        await message.answer(
            f"Новая сумма должна быть больше уже накопленных {rub(item['saved_amount'])}."
        )
        return
    allocator = db.load_allocator(message.from_user.id)
    db.update_planned_payment_details(message.from_user.id, payment_id, target_amount=value)
    from planned_payments import refresh_planned_payment_targets
    refresh_planned_payment_targets(message.from_user.id, allocator)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_planned_payments(message, message.from_user.id)


@router.callback_query(F.data.startswith("planned:date:"))
async def edit_planned_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(planned_payment_id=int(callback.data.rsplit(":", 1)[1]))
    await state.set_state(EditSettingsStates.planned_due_date)
    await callback.message.answer("Введите новую дату в формате <code>ДД.ММ.ГГГГ</code>.")


@router.message(EditSettingsStates.planned_due_date)
async def save_planned_date(message: Message, state: FSMContext):
    try:
        due = date.fromisoformat("-".join(reversed((message.text or "").strip().split("."))))
    except ValueError:
        await message.answer("Введите дату в формате ДД.ММ.ГГГГ.")
        return
    if due <= date.today():
        await message.answer("Дата должна быть позже сегодняшнего дня.")
        return
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    db.update_planned_payment_details(
        message.from_user.id, data["planned_payment_id"], due_date=due.isoformat()
    )
    from planned_payments import refresh_planned_payment_targets
    refresh_planned_payment_targets(message.from_user.id, allocator)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_planned_payments(message, message.from_user.id)


async def close_planned_payment(message: Message, telegram_id: int, payment_id: int, paid: bool):
    allocator = db.load_allocator(telegram_id)
    item = next((x for x in db.load_planned_payments(telegram_id) if x["id"] == payment_id), None)
    if allocator is None or item is None:
        await show_planned_payments(message, telegram_id)
        return
    envelope = item["envelope_name"]
    monthly = item["monthly_amount"]
    current = allocator.settings.life_categories.get(envelope, Decimal("0"))
    updated = max(Decimal("0"), current - monthly)
    if updated:
        allocator.settings.life_categories[envelope] = updated
    else:
        allocator.settings.life_categories.pop(envelope, None)
    allocator.settings.critical_life = max(
        sum(allocator.settings.life_categories.values(), Decimal("0")),
        allocator.settings.critical_life - monthly,
    )
    db.deactivate_planned_payment(telegram_id, payment_id)
    db.save_allocator(telegram_id, allocator)
    await message.answer("Платёж отмечен оплаченным." if paid else "Плановое обязательство отменено.")
    await show_planned_payments(message, telegram_id)


@router.callback_query(F.data.startswith("planned:close:"))
async def mark_planned_paid(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await close_planned_payment(callback.message, callback.from_user.id, int(callback.data.rsplit(":", 1)[1]), True)


@router.callback_query(F.data.startswith("planned:cancel:"))
async def cancel_planned(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await close_planned_payment(callback.message, callback.from_user.id, int(callback.data.rsplit(":", 1)[1]), False)


@router.callback_query(F.data == "settings:rhythm")
async def edit_income_rhythm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "<b>РИТМ ПОСТУПЛЕНИЙ</b>\n\n"
        "Профиль определяется совокупным денежным потоком, а не профессией или источником денег.",
        reply_markup=keyboard([
            [("Стабильный", "settingsrhythm:monthly"), ("Сдельный", "settingsrhythm:irregular")],
            [("Цикличный (контрактный)", "settingsrhythm:cyclic")],
            [("Отмена", "settings:open")],
        ]),
    )


@router.callback_query(F.data.startswith("settingsrhythm:"))
async def save_income_rhythm_setting(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    rhythm = callback.data.split(":", 1)[1]
    allocator = db.load_allocator(callback.from_user.id)
    if rhythm == "cyclic":
        await state.set_state(EditSettingsStates.income_gap_months)
        await state.update_data(settings_income_rhythm=rhythm)
        await callback.message.answer(
            "Сколько полных месяцев может не быть надёжного дохода?\n"
            "Введите целое число от 1 до 24."
        )
        return
    allocator.settings.income_rhythm = rhythm
    allocator.settings.profile_type = "stable" if rhythm == "monthly" else "piecework"
    allocator.settings.employment_type = "Наёмный" if rhythm == "monthly" else "Фрилансер"
    allocator.settings.income_gap_months = Decimal("1")
    allocator.settings.income_work_months = Decimal("1")
    allocator.settings.reliable_gap_income = Decimal("0")
    allocator.settings.stabilizer_target_months = Decimal("1")
    db.save_allocator(callback.from_user.id, allocator)
    await show_settings_menu(callback.message, callback.from_user.id)


@router.message(EditSettingsStates.income_gap_months)
async def save_income_gap_setting(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 24 or value != value.to_integral_value():
        await message.answer("Введите целое число от 1 до 24.")
        return
    await state.update_data(settings_gap_months=str(value))
    await state.set_state(EditSettingsStates.income_work_months)
    await message.answer("Сколько месяцев обычно длится рабочая часть цикла? Введите число от 1 до 24.")


@router.message(EditSettingsStates.income_work_months)
async def save_income_work_setting(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 24 or value != value.to_integral_value():
        await message.answer("Введите целое количество месяцев от 1 до 24.")
        return
    await state.update_data(settings_work_months=str(value))
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.income_rhythm = "cyclic"
    allocator.settings.profile_type = "cyclic"
    allocator.settings.employment_type = "Фрилансер"
    allocator.settings.income_gap_months = Decimal(data["settings_gap_months"])
    allocator.settings.income_work_months = Decimal(data["settings_work_months"])
    allocator.settings.reliable_gap_income = Decimal("0")
    allocator.settings.stabilizer_target_months = max(Decimal("2"), allocator.settings.stabilizer_target_months)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_settings_menu(message, message.from_user.id)

@router.callback_query(
    F.data.in_(
        {
            "settings:open",
            "menu:settings",
        }
    )
)
async def open_settings(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await show_settings_menu(
        callback.message,
        callback.from_user.id,
    )

@router.callback_query(
    F.data == "settings:developer"
)
async def toggle_developer(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    allocator = db.load_allocator(
        callback.from_user.id
    )

    if allocator is None:
        return

    allocator.settings.developer_mode = (
        not allocator.settings.developer_mode
    )

    db.save_allocator(
        callback.from_user.id,
        allocator,
    )

    status = (
        "включён"
        if allocator.settings.developer_mode
        else "выключен"
    )

    await callback.message.answer(
        f"✅ Режим разработчика {status}."
    )

    await show_settings_menu(
        callback.message,
        callback.from_user.id,
    )


@router.callback_query(
    F.data == "settings:full_reset"
)
async def ask_full_reset(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()
    await state.clear()

    await callback.message.answer(
        "⚠️ <b>ПОЛНЫЙ СБРОС УЧЁТА</b>\n\n"
        "Будут обнулены:\n"
        "🔄 Баланс жизни\n"
        "🛡️ Подушка\n"
        "📈 Инвестиции\n"
        "💳 Счётчик досрочного погашения\n"
        "⭐️ Накопления по целям\n"
        "❤️ Категории КЖ текущего периода\n"
        "💚 Бытовой резерв текущего периода\n"
        "👛 Доход текущего периода\n"
        "🏛️ Налог текущего периода\n"
        "📜 История распределений\n\n"
        "<b>Настройки профиля сохранятся.</b>\n"
        "КЖ, Бытовой резерв, категории, проценты, налог, "
        "тип занятости и данные кредитов останутся без изменений.",
        reply_markup=keyboard([
            [("Да, обнулить учёт", "settings:full_reset_confirm")],
            [("Отмена", "settings:full_reset_cancel")],
        ]),
    )


@router.callback_query(
    F.data == "settings:full_reset_cancel"
)
async def cancel_full_reset(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer("Сброс отменён")
    await state.clear()

    await show_settings_menu(
        callback.message,
        callback.from_user.id,
    )


@router.callback_query(
    F.data == "settings:full_reset_confirm"
)
async def confirm_full_reset(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()
    await state.clear()

    allocator = db.load_allocator(
        callback.from_user.id
    )

    if allocator is None:
        await callback.message.answer(
            "Финансовый профиль не найден."
        )
        return

    st = allocator.state

    # Баланс жизни и резерв минимальных платежей
    st.life_balance = Decimal("0")
    st.accumulated_minimum_payments = Decimal("0")

    # Подушка
    st.pillow_minimum = Decimal("0")
    st.pillow_force_majeure = Decimal("0")
    st.pillow_stabilizer = Decimal("0")
    st.intercontract_reserve = Decimal("0")
    st.contract_obligations_reserve = Decimal("0")

    # Накопительные финансовые показатели
    st.investments = Decimal("0")
    st.early_repayment = Decimal("0")

    # Цели
    st.goal_balances = {
        goal.name: Decimal("0")
        for goal in allocator.settings.goals
    }

    # Категории КЖ текущего периода
    st.period_life_topups = {
        name: Decimal("0")
        for name in allocator.settings.life_categories
    }
    st.period_life_topups["Зарплата"] = Decimal("0")

    # Периодические счётчики
    st.period_income = Decimal("0")
    st.period_tax = Decimal("0")

    if hasattr(st, "period_allocations"):
        st.period_allocations = {}

    # История
    st.operation_log = []
    st.distribution_history = []

    # Новый отсчёт начинается сейчас
    st.period_started_at = datetime.now().isoformat()

    db.save_allocator(
        callback.from_user.id,
        allocator,
    )

    await callback.message.answer(
        "✅ <b>УЧЁТ ПОЛНОСТЬЮ ОБНУЛЁН</b>\n\n"
        "Все финансовые счётчики начаты с нуля.\n"
        "Настройки профиля сохранены.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "settings:pillow")
async def edit_pillow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.pillow)
    await callback.message.answer(
        "🛡️ <b>ТЕКУЩИЙ БАЛАНС ПОДУШКИ</b>\n\n"
        f"Сейчас в Аллокаторе: <b>{rub(allocator.state.pillow_balance)}</b>\n\n"
        "Введите фактическую сумму, которая сейчас находится в вашей Подушке.\n"
        "Например: <code>175000</code>"
    )

@router.message(EditSettingsStates.pillow)
async def save_pillow(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    distribute_existing_pillow(allocator, value)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Подушка обновлена: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard(message.from_user.id))

@router.callback_query(F.data == "settings:critical")
async def edit_critical(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.critical_life)
    await callback.message.answer(
        "🔴 <b>ОБЯЗАТЕЛЬНАЯ ЖИЗНЬ</b>\n\n"
        f"Сейчас: <b>{rub(allocator.settings.critical_life)}</b>\n\n"
        "Введите новую месячную сумму обязательных расходов.\n"
        "Кредитные минимальные платежи сюда не добавляйте — они учитываются отдельно."
    )

@router.message(EditSettingsStates.critical_life)
async def save_critical(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите сумму больше 0.")
        return
    allocator = db.load_allocator(message.from_user.id)
    explicit = sum(allocator.settings.life_categories.values(), Decimal("0"))
    if explicit > value:
        await message.answer(
            "Новая КЖ меньше суммы ваших отдельных категорий КЖ.\n\n"
            f"Категории сейчас составляют {rub(explicit)}.\n"
            "Сначала уменьшите категории либо введите КЖ не меньше этой суммы."
        )
        return
    allocator.settings.critical_life = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Обязательная жизнь обновлена: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard(message.from_user.id))

@router.callback_query(F.data == "settings:household")
async def edit_household(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.household_reserve)
    await callback.message.answer(
        "💚 <b>БЫТОВОЙ РЕЗЕРВ</b>\n\n"
        f"Сейчас: <b>{rub(allocator.settings.household_reserve)}</b>\n\n"
        "Введите новую месячную сумму нерегулярных бытовых расходов."
    )

@router.message(EditSettingsStates.household_reserve)
async def save_household(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.household_reserve = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Бытовой резерв обновлён: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard(message.from_user.id))

@router.callback_query(F.data == "settings:income")
async def edit_average_income(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.average_income)
    await callback.message.answer(
        "💰 <b>СРЕДНЕМЕСЯЧНЫЙ ДОХОД</b>\n\n"
        f"Сейчас: <b>{rub(allocator.settings.average_income)}</b>\n\n"
        "Введите новую среднюю сумму."
    )

@router.message(EditSettingsStates.average_income)
async def save_average_income(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.average_income = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Средний доход обновлён: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard(message.from_user.id))

async def show_income_types_settings(message: Message, telegram_id: int):
    allocator = db.load_allocator(telegram_id)
    rates = allocator.settings.income_type_tax_rates
    lines = [
        f"• {escape(name)} — " + (f"налог {rate}%" if rate > 0 else "без налога")
        for name, rate in rates.items()
    ]
    rows = [[(name, f"incomesettings:view:{index}")] for index, name in enumerate(rates)]
    rows.append([("Добавить доход", "incomesettings:add")])
    rows.append([("Назад", "settings:open")])
    await message.answer(
        "<b>ТИПЫ ДОХОДОВ</b>\n\n"
        + ("\n".join(lines) if lines else "Пока ничего не добавлено."),
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data == "settings:income_types")
async def income_types_settings(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_income_types_settings(callback.message, callback.from_user.id)


@router.callback_query(F.data == "incomesettings:add")
async def income_type_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(income_type_action="add")
    await state.set_state(EditSettingsStates.income_type_name)
    await callback.message.answer(
        "<b>НОВЫЙ ТИП ДОХОДА</b>\n\n—————\n"
        "<b>→ Введите короткое название.</b>",
        reply_markup=keyboard([[("Отмена", "incomesettings:cancel")]]),
    )


@router.message(EditSettingsStates.income_type_name)
async def income_type_add_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    allocator = db.load_allocator(message.from_user.id)
    if len(name) < 2 or len(name) > 40:
        await message.answer("Введите название длиной от 2 до 40 символов.")
        return
    if name.casefold() in {item.casefold() for item in allocator.settings.income_type_tax_rates}:
        await message.answer("Такой тип дохода уже существует.")
        return
    await state.update_data(income_type_draft_name=name)
    await message.answer(
        f"<b>{escape(name.upper())}</b>\n\nНужно самостоятельно откладывать налог с этого дохода?",
        reply_markup=keyboard([
            [("Есть налог", "incomesettings:tax:yes"), ("Без налога", "incomesettings:tax:no")],
            [("Отмена", "incomesettings:cancel")],
        ]),
    )


@router.callback_query(F.data.startswith("incomesettings:tax:"))
async def income_type_add_tax(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data.endswith(":no"):
        await state.update_data(income_type_draft_rate="0")
        await show_income_type_confirmation(callback.message, state)
        return
    await state.set_state(EditSettingsStates.income_type_rate)
    await callback.message.answer("<b>СТАВКА НАЛОГА</b>\n\n—————\n<b>→ Введите число без знака %.</b>")


@router.message(EditSettingsStates.income_type_rate)
async def income_type_add_rate(message: Message, state: FSMContext):
    rate = parse_decimal(message.text)
    if rate is None or rate <= 0 or rate > 100:
        await message.answer("Введите ставку больше 0 и не больше 100.")
        return
    await state.update_data(income_type_draft_rate=str(rate))
    await show_income_type_confirmation(message, state)


async def show_income_type_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()
    name = data["income_type_draft_name"]
    rate = Decimal(data["income_type_draft_rate"])
    fix_callback = {
        "rename": "incomesettings:rename",
        "rerate": "incomesettings:rerate",
    }.get(data.get("income_type_action"), "incomesettings:add")
    await state.set_state(EditSettingsStates.income_type_confirm)
    await message.answer(
        "<b>ПРОВЕРЬТЕ ТИП ДОХОДА</b>\n\n"
        f"Название — <b>{escape(name)}</b>\n"
        + (f"Налог — <b>{rate}%</b>" if rate > 0 else "Налог — <b>не резервируется</b>"),
        reply_markup=keyboard([
            [("Исправить", fix_callback), ("✔️ Сохранить", "incomesettings:save")],
            [("Отмена", "incomesettings:cancel")],
        ]),
    )


@router.callback_query(EditSettingsStates.income_type_confirm, F.data == "incomesettings:save")
async def income_type_save(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    rates = allocator.settings.income_type_tax_rates
    action = data.get("income_type_action", "add")
    name = data["income_type_draft_name"]
    rate = Decimal(data["income_type_draft_rate"])
    if action == "rename":
        original = data["income_type_edit_original"]
        rates = {name if item == original else item: item_rate for item, item_rate in rates.items()}
        allocator.settings.income_type_tax_rates = rates
    else:
        rates[name] = rate
    allocator.settings.taxable_income_types = [
        item for item, item_rate in allocator.settings.income_type_tax_rates.items() if item_rate > 0
    ]
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_income_types_settings(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("incomesettings:view:"))
async def income_type_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    names = list(allocator.settings.income_type_tax_rates)
    if not 0 <= index < len(names):
        await show_income_types_settings(callback.message, callback.from_user.id)
        return
    name = names[index]
    rate = allocator.settings.income_type_tax_rates[name]
    await state.update_data(income_type_edit_original=name)
    await callback.message.answer(
        f"<b>{escape(name.upper())}</b>\n\n" + (f"Налог — <b>{rate}%</b>" if rate > 0 else "Без налога"),
        reply_markup=keyboard([
            [("Изменить название", "incomesettings:rename")],
            [("Изменить налог", "incomesettings:rerate")],
            [("Удалить", "incomesettings:delete")],
            [("Назад", "settings:income_types")],
        ]),
    )


@router.callback_query(F.data == "incomesettings:rename")
async def income_type_rename_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(EditSettingsStates.income_type_edit_name)
    await callback.message.answer("Введите новое название.", reply_markup=keyboard([[("Отмена", "incomesettings:cancel")]]))


@router.message(EditSettingsStates.income_type_edit_name)
async def income_type_rename_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    original = data["income_type_edit_original"]
    if len(name) < 2 or len(name) > 40:
        await message.answer("Введите название длиной от 2 до 40 символов.")
        return
    if name.casefold() != original.casefold() and name.casefold() in {item.casefold() for item in allocator.settings.income_type_tax_rates}:
        await message.answer("Такой тип дохода уже существует.")
        return
    await state.update_data(
        income_type_action="rename",
        income_type_draft_name=name,
        income_type_draft_rate=str(allocator.settings.income_type_tax_rates[original]),
    )
    await show_income_type_confirmation(message, state)


@router.callback_query(F.data == "incomesettings:rerate")
async def income_type_rate_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(EditSettingsStates.income_type_edit_rate)
    await callback.message.answer(
        "Введите новую ставку от 0 до 100. Ноль означает, что налог автоматически не резервируется.",
        reply_markup=keyboard([[("Отмена", "incomesettings:cancel")]]),
    )


@router.message(EditSettingsStates.income_type_edit_rate)
async def income_type_rate_save(message: Message, state: FSMContext):
    rate = parse_decimal(message.text)
    if rate is None or rate < 0 or rate > 100:
        await message.answer("Введите ставку от 0 до 100.")
        return
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    name = data["income_type_edit_original"]
    await state.update_data(
        income_type_action="rerate",
        income_type_draft_name=name,
        income_type_draft_rate=str(rate),
    )
    await show_income_type_confirmation(message, state)


@router.callback_query(F.data == "incomesettings:delete")
async def income_type_delete(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    name = data["income_type_edit_original"]
    await callback.message.answer(
        f"Удалить тип дохода <b>{escape(name)}</b>? История поступлений сохранится.",
        reply_markup=keyboard([
            [("Удалить", "incomesettings:delete:confirm"), ("Отмена", "incomesettings:cancel")],
        ]),
    )


@router.callback_query(F.data == "incomesettings:delete:confirm")
async def income_type_delete_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    allocator.settings.income_type_tax_rates.pop(data["income_type_edit_original"], None)
    allocator.settings.taxable_income_types = [name for name, rate in allocator.settings.income_type_tax_rates.items() if rate > 0]
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_income_types_settings(callback.message, callback.from_user.id)


@router.callback_query(F.data == "incomesettings:cancel")
async def income_type_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_income_types_settings(callback.message, callback.from_user.id)


@router.callback_query(F.data == "settings:tax")
async def edit_tax(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_income_types_settings(callback.message, callback.from_user.id)

@router.message(EditSettingsStates.tax_rate)
async def save_tax(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0 or value > 100:
        await message.answer("Введите число от 0 до 100.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.tax_rate = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(
        f"✅ Ставка налога обновлена: <b>{value}%</b>\n\n"
        "Список типов дохода, с которых удерживается налог, остаётся прежним.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )

@router.callback_query(F.data == "settings:life_categories")
async def edit_life_categories(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.life_categories)
    current = "\n".join(
        f"• {escape(name)} = {rub(amount)}"
        for name, amount in allocator.settings.life_categories.items()
    ) or "Отдельных категорий сейчас нет."
    await callback.message.answer(
        "<b>ОТДЕЛЬНЫЕ КОНВЕРТЫ КРИТИЧЕСКОГО МИНИМУМА</b>\n\n"
        f"{current}\n\n"
        "Отправьте весь новый список одним сообщением в формате:\n"
        "<code>Квартира=43000, Транспорт=5000, Питомец=4000</code>\n\n"
        "Всё, что не вынесено в отдельный конверт, бот автоматически оставит в «Зарплате».\n"
        "Чтобы удалить все отдельные категории, отправьте: <code>нет</code>"
    )

@router.message(EditSettingsStates.life_categories)
async def save_life_categories(message: Message, state: FSMContext):
    text = message.text.strip()
    allocator = db.load_allocator(message.from_user.id)

    if text.lower() in {"нет", "none", "0"}:
        allocator.settings.life_categories = {}
    else:
        new_categories = {}
        try:
            for raw_item in text.split(","):
                name, raw_value = raw_item.split("=", 1)
                name = name.strip()
                value = parse_decimal(raw_value)
                if not name or value is None or value <= 0:
                    raise ValueError
                new_categories[name] = value
        except ValueError:
            await message.answer(
                "Не удалось разобрать список.\n\n"
                "Используйте формат:\n"
                "<code>Квартира=43000, Транспорт=5000</code>"
            )
            return

        total = sum(new_categories.values(), Decimal("0"))
        if total > allocator.settings.critical_life:
            await message.answer(
                f"Отдельные конверты дают {rub(total)}, а ваш Критический минимум — {rub(allocator.settings.critical_life)}.\n"
                "Сумма отдельных конвертов не может быть больше Критического минимума."
            )
            return

        allocator.settings.life_categories = new_categories

    valid = set(allocator.settings.life_categories) | {"Зарплата"}
    allocator.state.period_life_topups = {
        name: amount
        for name, amount in allocator.state.period_life_topups.items()
        if name in valid
    }
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer("Отдельные конверты Критического минимума обновлены.", reply_markup=main_menu_keyboard(message.from_user.id))

@router.callback_query(F.data == "settings:goals")
async def edit_goal_percentages(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if not allocator.settings.goals:
        await callback.message.answer(
            "У вас пока нет отдельных категорий целей. Чтобы создать их, проще пройти настройку заново.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        return

    current = "\n".join(
        f"• ⭐️ {escape(goal.name)} = {format(goal.percentage.normalize(), 'f')}%"
        for goal in allocator.settings.goals
    )
    await state.set_state(EditSettingsStates.goal_percentages)
    await callback.message.answer(
        "⭐️ <b>ПРОЦЕНТЫ ЦЕЛЕЙ</b>\n\n"
        f"{current}\n\n"
        "Отправьте новый список процентов для всех существующих целей:\n"
        "<code>Отпуск=50, Техника=30, Подарки=20</code>\n\n"
        "Сумма должна быть ровно 100%."
    )

@router.message(EditSettingsStates.goal_percentages)
async def save_goal_percentages(message: Message, state: FSMContext):
    allocator = db.load_allocator(message.from_user.id)
    try:
        entered = {}
        for raw_item in message.text.split(","):
            name, raw_value = raw_item.split("=", 1)
            name = name.strip()
            value = parse_decimal(raw_value)
            if not name or value is None or value <= 0:
                raise ValueError
            entered[name.lower()] = value
    except ValueError:
        await message.answer(
            "Не удалось разобрать проценты.\n"
            "Пример: <code>Отпуск=50, Техника=30, Подарки=20</code>"
        )
        return

    existing_names = {goal.name.lower() for goal in allocator.settings.goals}
    if set(entered) != existing_names:
        await message.answer(
            "Нужно указать все существующие цели и не добавлять новые названия."
        )
        return

    total = sum(entered.values(), Decimal("0"))
    if abs(total - Decimal("100")) > Decimal("0.0001"):
        await message.answer(f"Сейчас сумма процентов = {total}%. Нужно ровно 100%.")
        return

    for goal in allocator.settings.goals:
        goal.percentage = entered[goal.name.lower()]

    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer("✅ Проценты целей обновлены.", reply_markup=main_menu_keyboard(message.from_user.id))

@router.callback_query(F.data == "settings:c_split")
async def edit_c_split(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Теперь этот выбор появляется непосредственно при каждом распределении дохода. "
        "Постоянная стратегия больше не нужна.",
        reply_markup=keyboard([[("← Назад", "settings:open")]]),
    )


@router.callback_query(F.data.startswith("settings:c_strategy:"))
async def save_c_strategy(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Постоянный вариант больше не сохраняется. Бот предложит выбор при следующем "
        "распределении дохода, если после текущей жизни останется свободная часть.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

@router.message(EditSettingsStates.c_split)
async def save_c_split(message: Message, state: FSMContext):
    parts = [part.strip() for part in message.text.split(",")]
    if len(parts) != 2:
        await message.answer("Введите два процента через запятую, например: 60,40")
        return

    first = parse_decimal(parts[0])
    second = parse_decimal(parts[1])

    if (
        first is None or second is None
        or first < 0 or second < 0
        or abs(first + second - Decimal("100")) > Decimal("0.0001")
    ):
        await message.answer("Проценты должны быть неотрицательными и в сумме давать 100%.")
        return

    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.goals_share_c = first
    allocator.settings.pillow_share_c = second
    db.save_allocator(message.from_user.id, allocator)

    await state.clear()
    await message.answer(f"✅ Этап C обновлён: ⭐️ {first}% / 🛡️ {second}%", reply_markup=main_menu_keyboard(message.from_user.id))
