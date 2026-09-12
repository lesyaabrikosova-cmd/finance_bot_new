from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from financial_engine import goal_display_name, is_system_envelope_name
from storage import db
from time_utils import moscow_now
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
    income_tax_profile_subject = State()
    income_tax_profile_mode = State()
    life_categories = State()
    life_category_rename = State()
    life_category_amount = State()
    goal_percentages = State()
    c_split = State()
    full_reset_period_start = State()


TAX_PROFILE_SUBJECTS = {
    "ip": "ИП",
    "self_employed": "Самозанятость",
}
# Only regimes whose liability can be estimated from gross receipts belong in
# a personal-finance allocator. Patent is handled as a dated fixed payment.
IP_TAX_REGIMES = ("УСН «Доходы»",)
SELF_EMPLOYED_CLIENTS = ("Физики", "Юрики")


def tax_profile_name(subject: str, mode: str, rate: Decimal) -> str:
    """One readable identity for selection, history and the tax-chart legend."""
    return f"{subject} · {mode} · {fmt_money(rate)}%"


def tax_profile_navigation(data: dict) -> list[tuple[str, str]]:
    """Keep the back row identical when the builder was opened from Taxes."""
    if data.get("income_profile_return") == "taxes:income":
        return [("← Главное меню", "taxes:back"), ("← Назад", "taxes:income")]
    return [("Отмена", "incomesettings:cancel")]


def start_reset_period(allocator, start: date, today: date) -> tuple[date, date]:
    """Start an empty accounting period from a user-selected past or current day.

    A full reset deliberately permits a historical start: it lets a person
    enter the income that has already arrived in their current period.  The
    original date remains the beginning of the accounting history, while the
    displayed end is extended to the next current anchor if the date is older
    than one financial month.
    """
    return allocator.state.activate_budget_period(start, today=today)

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
    return fmt_money(value) + " ₽"

def fmt_money(value: Decimal) -> str:
    formatted = f"{Decimal(value):,.2f}"
    formatted = formatted.replace(",", " ").replace(".", ",")
    return formatted[:-3] if formatted.endswith(",00") else formatted


def set_user_critical_life(settings, value: Decimal) -> Decimal:
    """Save the user's ordinary monthly costs without baking in automatics.

    Planned taxes and other dated obligations are kept separately and are
    added by ``recalculate_critical_life``.  Treating the number entered by the
    user as the already-increased total used to make the permanent part drift
    down after an automatic obligation was later removed.
    """
    settings.base_critical_life = Decimal(value)
    return settings.recalculate_critical_life()

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
        "🛠 Выключить уровень разработчика"
        if s.developer_mode
        else "🛠 Включить уровень разработчика"
    )

    rhythm_labels = {"monthly": "Стабильный", "irregular": "Сдельный", "cyclic": "Циклический"}
    active_debts = [credit for credit in s.credits if credit.active]
    reward = "🏆" * allocator.active_mode() + "➖" * (allocator.profile_mode_total - allocator.active_mode())
    lines = ["<b>НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ</b>", "",
             f"Профиль: {rhythm_labels.get(s.income_rhythm, s.income_rhythm)}",
             f"Уровень: {reward}", ""]
    if not active_debts:
        lines.extend(["Долгов нет.", ""])
    if s.income_rhythm == "cyclic":
        phase = "перерыв" if st.intercontract_break_active else "рабочая часть"
        phase_line = (f"До конца перерыва — {st.intercontract_months_remaining} мес."
                      if st.intercontract_break_active else
                      f"До следующего перерыва — {st.current_phase_months_remaining} мес.")
        lines.extend([f"Цикл — {s.income_work_months} мес. работы / {s.income_gap_months} мес. перерыва",
                      f"Текущая фаза — {phase}", phase_line, "",
                      f"Средний доход за цикл — {fmt_money(s.cycle_regular_income_limit)}"])
    else:
        label = "Обычный доход" if s.income_rhythm == "monthly" else "Средний доход"
        lines.append(f"{label} — {fmt_money(s.average_income)}" + (" в месяц" if s.income_rhythm == "monthly" else ""))
    monthly = " в месяц" if s.income_rhythm == "cyclic" else ""
    lines.extend(["————————————",
                  f"➤ <b>Критический минимум</b> — {fmt_money(s.critical_life)}{monthly}",
                  f"➤ <b>Устойчивая жизнь</b> — {fmt_money(s.household_life)}{monthly}"])
    if active_debts:
        debt_total = sum((credit.principal_balance for credit in active_debts), Decimal("0"))
        payment_total = sum((credit.minimum_payment for credit in active_debts), Decimal("0"))
        lines.extend(["————————————", "<b>ДОЛГИ</b>", "",
                      f"Общий остаток — {fmt_money(debt_total)}",
                      f"Минимальные платежи — {fmt_money(payment_total)} в мес"])
    lines.append("————————————")
    if s.income_rhythm == "cyclic":
        lines.extend([f"🏦 <b>Фонд Зарплаты</b> • {s.income_gap_months} мес •",
                      f"{fmt_money(st.intercontract_reserve)} / {fmt_money(allocator.intercontract_current_limit)}", ""])
    lines.extend([f"🛡️ <b>Подушка</b> • {s.force_majeure_months} мес •",
                  f"{fmt_money(st.pillow_balance)} / {fmt_money(s.force_majeure_limit)}"])
    if s.needs_stabilizer:
        lines.extend([
            "",
            f"🛟 <b>Стабилизатор</b> • {s.stabilizer_target_months} мес •",
            f"{fmt_money(st.stabilizer_balance)} / {fmt_money(s.stabilizer_full_limit)}",
        ])
    lines.extend(["————————————", "<b>КАТЕГОРИИ ЖИЗНИ</b>"])
    for name, amount in s.life_categories.items():
        lines.append(f"❤️ <b>{escape(name)}</b> — {fmt_money(amount)}")
    lines.extend(["", f"💚 <b>Бытовой резерв</b> — {fmt_money(s.household_reserve)}"])
    if s.goals:
        lines.extend(["", "<b>ЦЕЛИ И СУНДУКИ</b>"])
        for goal in s.goals:
            icon = "🧳" if goal.is_chest else "⭐️"
            name = goal_display_name(goal.name, goal.is_chest)
            lines.append(f"{icon} <b>{escape(name)}</b> — {goal.percentage}%")
    lines.extend(["————————————", "<b>НАЛОГИ С ДОХОДА</b>", ""])
    for income_name, rate in s.income_type_tax_rates.items():
        lines.append(f"{escape(income_name)} — {'без налога' if rate == 0 else f'{rate}%'}")
    lines.extend([
        "————————————",
        f"<b>Бракеты</b>: {s.bracket_a}% / {s.bracket_b}% / {s.bracket_c}% / {s.bracket_d}%",
        "",
        f"<b>🛠 Уровень разработчика:</b> {dev_status}",
    ])

    await message.answer(
        "\n".join(lines),
        reply_markup=keyboard([
            [(f"Профиль: { {'stable': 'Стабильный', 'piecework': 'Сдельный', 'cyclic': 'Циклический'}.get(allocator.profile_id, allocator.profile_id)}", "settings:rhythm")],
            [("Средний доход", "settings:income"), ("Доходы и налоги", "settings:income_types")],
            [("Настройки Подушки", "settings:force_months")],
            [("Баланс Подушки", "settings:pillow")],
            *([
                [("Настройки Стабилизатора", "settings:stabilizer_months")],
                [("Баланс Стабилизатора", "settings:stabilizer_balance")],
            ] if s.needs_stabilizer else []),
            *([[("Баланс Фонда Зарплаты", "settings:intercontract_balance")]] if allocator.profile_id == "cyclic" else []),
            [("Изменить КМ", "settings:critical"), ("Категории КМ", "settings:life_categories")],
            [("Изменить Бытовой резерв", "settings:household")],
            [("Цели и Сундуки", "goals:manage")],
            [("Бракеты", "brackets:open")],
            [("Расходы к дате", "settings:planned")],
            *([
                [("Изменить рабочую жизнь", "phaselife:fill:work")],
                [("Изменить жизнь в перерыве", "phaselife:fill:break")],
            ] if allocator.profile_id == "cyclic" else []),
            [(dev_button, "settings:developer")],
            [("🗑 Полный сброс учёта", "settings:full_reset")],
            *([[("🗑️ Удалить профиль и всю историю", "settings:erase_all")]] if s.developer_mode else []),
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
        "🛟 <b>ТЕКУЩИЙ БАЛАНС СТАБИЛИЗАТОРА</b>\n\n"
        f"Сейчас в Аллокаторе: <b>{rub(allocator.state.stabilizer_balance)}</b>\n"
        f"Запланированный размер: <b>{rub(allocator.settings.stabilizer_full_limit)}</b>\n\n"
        "Теперь сверим данные с реальностью.\n\n"
        "<b>Сколько денег сейчас фактически отложено на Стабилизатор в вашем банке?</b>\n"
        "——————\n"
        "<b>→ Введите сумму.</b>\n"
        "Например: <b>175000</b>"
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
        "🏦 <b>ТЕКУЩИЙ БАЛАНС ФОНДА ЗАРПЛАТЫ</b>\n\n"
        f"Сейчас в Аллокаторе: <b>{rub(allocator.state.intercontract_reserve)}</b>\n"
        f"Запланированный размер: <b>{rub(allocator.settings.intercontract_full_limit)}</b>\n\n"
        "Теперь сверим данные с реальностью.\n\n"
        "<b>Сколько денег сейчас фактически отложено на Фонд Зарплаты в вашем банке?</b>\n"
        "——————\n"
        "<b>→ Введите сумму.</b>\n"
        "Например: <b>175000</b>"
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
    allocator.settings.remove_automatic_life_obligation(
        f"payment:{payment_id}"
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
        f"✅ Уровень разработчика {status}."
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
        "💲 Доход текущего периода\n"
        "🏛️ Налог текущего периода\n"
        "📜 История распределений и оплаты налогов\n"
        "🏛️ Накопления на налоги и плановые платежи\n\n"
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
    st.household_reserve_progress = Decimal("0")
    st.accumulated_minimum_payments = Decimal("0")

    # Подушка
    st.pillow_minimum = Decimal("0")
    st.pillow_force_majeure = Decimal("0")
    st.pillow_stabilizer = Decimal("0")
    st.intercontract_reserve = Decimal("0")
    st.contract_obligations_reserve = Decimal("0")

    st.fund_salary_currencies = {}
    st.fund_salary_start_reserves = {}
    st.fund_salary_period_rates = {}
    st.cycle_income = Decimal("0")
    for goal in allocator.settings.goals:
        goal.balance = Decimal("0")

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

    # Дату начала человек выбирает после успешного сброса. До этого момента
    # период намеренно не активен: можно безопасно начать его сегодняшним
    # числом или восстановить уже начавшуюся часть месяца.
    st.period_status = "not_started"
    st.period_started_at = None
    st.period_ends_at = None
    st.period_anchor_day = 0
    st.period_activation_date = None
    st.period_review_sent_for = None

    try:
        # The visible zero balances and the SQL accounting ledger are one
        # reset.  If either write fails, SQLite restores both instead of
        # leaving a half-reset profile.
        with db.transaction():
            db.save_allocator(
                callback.from_user.id,
                allocator,
            )
            db.clear_accounting_history(callback.from_user.id)
    except Exception:
        await callback.message.answer(
            "Не удалось полностью обнулить учёт. Все данные сохранены без изменений.",
            reply_markup=keyboard([
                [("← Главное меню", "menu:back")],
                [("← Назад", "settings:developer")],
            ]),
        )
        return

    await state.set_state(EditSettingsStates.full_reset_period_start)
    await callback.message.answer(
        "✅ <b>УЧЁТ ПОЛНОСТЬЮ ОБНУЛЁН</b>\n\n"
        "Настройки профиля сохранены.\n\n"
        "<b>КОГДА НАЧАТЬ РАСЧЁТНЫЙ ПЕРИОД?</b>\n\n"
        "Можно указать любую прошедшую дату или сегодня. Будущую дату выбрать нельзя. "
        "После этого можно будет внести поступления, начиная с выбранного дня.\n\n"
        "——————\n<b>→ Введите дату в формате ДД.ММ.ГГГГ.</b>",
        reply_markup=keyboard([
            [("Начать сегодня", "settings:full_reset_start_today")],
        ]),
    )


async def finish_full_reset_period_start(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    selected: date,
) -> None:
    """Activate the newly reset period only while its accounting is empty."""
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await state.clear()
        await message.answer("Финансовый профиль не найден.")
        return
    if allocator.state.period_status != "not_started" or db.operation_count(telegram_id) > 0:
        await state.clear()
        await message.answer(
            "Расчётный период уже начат. Изменять его дату после первых операций нельзя.",
            reply_markup=main_menu_keyboard(telegram_id),
        )
        return

    period_start, period_end = start_reset_period(
        allocator, selected, moscow_now().date(),
    )
    db.save_allocator(telegram_id, allocator)
    await state.clear()
    await message.answer(
        "✅ <b>РАСЧЁТНЫЙ ПЕРИОД НАЧАТ</b>\n\n"
        f"Начало периода: <b>{period_start.strftime('%d.%m.%Y')}</b>.\n"
        f"Контрольная точка: <b>{period_end.strftime('%d.%m.%Y')}</b>.\n\n"
        "В контрольную дату Аллокатор только напомнит проверить период. "
        "Он продолжится, пока вы сами не начнёте новый.\n\n"
        "Теперь можно добавлять поступления с выбранной даты.",
        reply_markup=main_menu_keyboard(telegram_id),
    )


@router.callback_query(
    EditSettingsStates.full_reset_period_start,
    F.data == "settings:full_reset_start_today",
)
async def start_full_reset_period_today(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()
    await finish_full_reset_period_start(
        callback.message, state, callback.from_user.id, moscow_now().date(),
    )


@router.message(
    EditSettingsStates.full_reset_period_start,
    F.text & ~F.text.startswith("/"),
)
async def save_full_reset_period_start(
    message: Message,
    state: FSMContext,
):
    try:
        selected = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Не удалось распознать дату. Введите её в формате <code>ДД.ММ.ГГГГ</code>.",
        )
        return
    if selected > moscow_now().date():
        await message.answer("Дата начала расчётного периода не может быть в будущем.")
        return
    await finish_full_reset_period_start(
        message, state, message.from_user.id, selected,
    )


@router.callback_query(F.data == "settings:pillow")
async def edit_pillow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    has_active_debt = any(credit.active for credit in allocator.settings.credits)
    target = (
        allocator.settings.minimum_reserve_limit
        if has_active_debt
        else allocator.settings.force_majeure_limit
    )
    title = "МИНИМАЛЬНОЙ ПОДУШКИ" if has_active_debt else "ПОДУШКИ"
    await state.set_state(EditSettingsStates.pillow)
    await callback.message.answer(
        f"🛡️ <b>ТЕКУЩИЙ БАЛАНС {title}</b>\n\n"
        f"Сейчас в Аллокаторе: <b>{rub(allocator.state.pillow_balance)}</b>\n"
        f"Запланированный размер: <b>{rub(target)}</b>\n\n"
        "Теперь сверим данные с реальностью.\n\n"
        "<b>Сколько денег сейчас фактически отложено на Подушку в вашем банке?</b>\n"
        "——————\n"
        "<b>→ Введите сумму.</b>\n"
        "Например: <b>175000</b>"
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
        f"Сейчас: <b>{rub(allocator.settings.base_critical_life)}</b>\n\n"
        "Введите новую месячную сумму обязательных расходов.\n"
        "Налоги, плановые платежи и минимальные платежи по долгам сюда "
        "не добавляйте — Аллокатор учитывает их отдельно."
    )

@router.message(EditSettingsStates.critical_life)
async def save_critical(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите сумму больше 0.")
        return
    allocator = db.load_allocator(message.from_user.id)
    explicit = sum(
        (
            amount
            for name, amount in allocator.settings.life_categories.items()
            if name.strip().casefold() not in {"налог", "налоги"}
        ),
        Decimal("0"),
    )
    if explicit > value:
        await message.answer(
            "Новая КЖ меньше суммы ваших отдельных категорий КЖ.\n\n"
            f"Категории сейчас составляют {rub(explicit)}.\n"
            "Сначала уменьшите категории либо введите КЖ не меньше этой суммы."
        )
        return
    actual = set_user_critical_life(allocator.settings, value)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(
        f"✅ Критический минимум обновлён: <b>{rub(actual)}</b>",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )

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
    rows.append([("+ Налоговый профиль", "incomesettings:add")])
    rows.append([("+ Свой тип дохода", "incomesettings:add_custom")])
    rows.append([("← Назад", "settings:open")])
    await message.answer(
        "<b>НАЛОГОВЫЕ ПРОФИЛИ И ТИПЫ ДОХОДОВ</b>\n\n"
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
    await start_income_tax_profile(callback.message, state)


async def start_income_tax_profile(
    message: Message,
    state: FSMContext,
    *,
    return_to: str | None = None,
    subject_key: str | None = None,
) -> None:
    """Open the shared profile builder from Settings or the Taxes menu."""
    await state.update_data(income_type_action="add_profile")
    if return_to:
        await state.update_data(income_profile_return=return_to)
    if subject_key:
        await choose_income_tax_profile_subject(message, state, subject_key)
        return
    await state.set_state(EditSettingsStates.income_tax_profile_subject)
    await message.answer(
        "<b>НОВЫЙ НАЛОГОВЫЙ ПРОФИЛЬ</b>\n\n"
        "1 из 3 · Кто получает доход?\n\n"
        "Профиль будет отдельной строкой при добавлении дохода и отдельным "
        "фиолетовым сектором в диаграмме налогов.",
        reply_markup=keyboard([
            [("ИП", "incomesettings:subject:ip"), ("Самозанятость", "incomesettings:subject:self_employed")],
            [("Отмена", "incomesettings:cancel")],
        ]),
    )


@router.callback_query(F.data == "incomesettings:add_custom")
async def income_type_add_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(income_type_action="add")
    await state.set_state(EditSettingsStates.income_type_name)
    await callback.message.answer(
        "<b>СВОЙ ТИП ДОХОДА</b>\n\n—————\nВведите короткое название.",
        reply_markup=keyboard([[("Отмена", "incomesettings:cancel")]]),
    )


@router.callback_query(EditSettingsStates.income_tax_profile_subject, F.data.startswith("incomesettings:subject:"))
async def income_tax_profile_subject(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subject_key = callback.data.rsplit(":", 1)[1]
    await choose_income_tax_profile_subject(callback.message, state, subject_key)


async def choose_income_tax_profile_subject(message: Message, state: FSMContext, subject_key: str) -> None:
    subject = TAX_PROFILE_SUBJECTS.get(subject_key)
    if subject is None:
        await message.answer("Выберите налоговый профиль ещё раз.")
        return
    await state.update_data(income_tax_profile_subject=subject)
    await state.set_state(EditSettingsStates.income_tax_profile_mode)
    choices = SELF_EMPLOYED_CLIENTS if subject_key == "self_employed" else IP_TAX_REGIMES
    rows = [[(choice, f"incomesettings:profile_mode:{index}")] for index, choice in enumerate(choices)]
    rows.append(tax_profile_navigation(await state.get_data()))
    word = "для кого заказ" if subject_key == "self_employed" else "налоговый режим"
    await message.answer(
        f"<b>{escape(subject.upper())}</b>\n\n2 из 3 · Выберите {word}.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(EditSettingsStates.income_tax_profile_mode, F.data.startswith("incomesettings:profile_mode:"))
async def income_tax_profile_mode(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    subject = data.get("income_tax_profile_subject")
    choices = SELF_EMPLOYED_CLIENTS if subject == "Самозанятость" else IP_TAX_REGIMES
    try:
        mode = choices[int(callback.data.rsplit(":", 1)[1])]
    except (ValueError, IndexError):
        await show_income_types_settings(callback.message, callback.from_user.id)
        return
    await state.update_data(income_tax_profile_mode=mode)
    await state.set_state(EditSettingsStates.income_type_rate)
    hint = (
        "Для самозанятости стандартные ставки — 4% с доходов от физиков и "
        "6% от юриков; при налоговом бонусе — 3% и 4%."
        if subject == "Самозанятость"
        else "Введите процент, который нужно откладывать с каждого поступления."
    )
    await callback.message.answer(
        f"<b>{escape(subject)} · {escape(mode)}</b>\n\n"
        f"3 из 3 · Введите ставку в процентах.\n\n{hint}",
        reply_markup=keyboard([tax_profile_navigation(data)]),
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
    data = await state.get_data()
    rate = parse_decimal(message.text)
    if rate is None or rate <= 0 or rate > 100:
        await message.answer(
            "Введите ставку больше 0 и не больше 100.",
            reply_markup=keyboard([tax_profile_navigation(data)]),
        )
        return
    if data.get("income_type_action") == "add_profile":
        name = tax_profile_name(
            data["income_tax_profile_subject"], data["income_tax_profile_mode"], rate,
        )
        if data.get("income_profile_return") == "taxes:income":
            from taxes import save_income_tax_profile, show_income_tax_profile_menu
            try:
                name, created = await save_income_tax_profile(
                    message.from_user.id,
                    data["income_tax_profile_subject"],
                    data["income_tax_profile_mode"],
                    rate,
                )
            except ValueError as error:
                await message.answer(escape(str(error)))
                return
            await state.clear()
            await message.answer(
                f"{'Добавлен профиль' if created else 'Такой профиль уже добавлен'} "
                f"<b>{escape(name)}</b>."
            )
            await show_income_tax_profile_menu(message, message.from_user.id)
            return
        allocator = db.load_allocator(message.from_user.id)
        if name in allocator.settings.income_type_tax_rates:
            await message.answer("Такой налоговый профиль уже есть. Выберите его в списке.")
            return
        await state.update_data(income_type_draft_name=name)
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
        identifier = allocator.settings.ensure_income_type_id(original)
        rates = {name if item == original else item: item_rate for item, item_rate in rates.items()}
        allocator.settings.income_type_tax_rates = rates
        allocator.settings.income_type_ids.pop(original, None)
        allocator.settings.income_type_ids[name] = identifier
        allocator.settings.income_type_labels[identifier] = name
        profile = allocator.settings.income_tax_profiles.pop(original, None)
        if profile is not None:
            allocator.settings.income_tax_profiles[name] = {
                **profile, "rate": str(rate),
            }
    else:
        rates[name] = rate
        allocator.settings.ensure_income_type_id(name)
        if action == "add_profile":
            allocator.settings.income_tax_profiles[name] = {
                "subject": str(data["income_tax_profile_subject"]),
                "mode": str(data["income_tax_profile_mode"]),
                "rate": str(rate),
            }
    allocator.settings.taxable_income_types = [
        item for item, item_rate in allocator.settings.income_type_tax_rates.items() if item_rate > 0
    ]
    if action == "rename":
        # The label bridge and the settings row are one logical migration.
        # Keeping them in one transaction prevents a half-renamed type if the
        # second write ever fails.
        with db.transaction():
            db.record_income_type_rename(
                callback.from_user.id,
                original,
                name,
                identifier,
            )
            db.save_allocator(callback.from_user.id, allocator)
    else:
        db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    if data.get("income_profile_return") == "taxes:income":
        from taxes import show_income_tax_profile_menu
        await show_income_tax_profile_menu(callback.message, callback.from_user.id)
    else:
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
    is_profile = name in allocator.settings.income_tax_profiles
    await callback.message.answer(
        f"<b>{escape(name.upper())}</b>\n\n" + (f"Налог — <b>{rate}%</b>" if rate > 0 else "Без налога"),
        reply_markup=keyboard([
            *([] if is_profile else [[("Изменить название", "incomesettings:rename")]]),
            *([] if is_profile else [[("Изменить налог", "incomesettings:rerate")]]),
            [("🗑️ Удалить", "incomesettings:delete")],
            [("← Назад", "settings:income_types")],
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
    profile = allocator.settings.income_tax_profiles.get(name)
    if profile:
        new_name = tax_profile_name(profile["subject"], profile["mode"], rate)
        if new_name != name and new_name in allocator.settings.income_type_tax_rates:
            await message.answer("Профиль с такой ставкой уже существует.")
            return
        await state.update_data(
            income_type_action="rename",
            income_type_draft_name=new_name,
            income_type_draft_rate=str(rate),
        )
        await show_income_type_confirmation(message, state)
        return
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
            [("🗑️ Удалить", "incomesettings:delete:confirm"), ("Отмена", "incomesettings:cancel")],
        ]),
    )


@router.callback_query(F.data == "incomesettings:delete:confirm")
async def income_type_delete_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    original = data["income_type_edit_original"]
    allocator.settings.income_type_tax_rates.pop(original, None)
    allocator.settings.income_type_ids.pop(original, None)
    allocator.settings.income_tax_profiles.pop(original, None)
    allocator.settings.taxable_income_types = [name for name, rate in allocator.settings.income_type_tax_rates.items() if rate > 0]
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_income_types_settings(callback.message, callback.from_user.id)


@router.callback_query(F.data == "incomesettings:cancel")
async def income_type_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    if data.get("income_profile_return") == "taxes:income":
        from taxes import show_income_tax_profile_menu
        await show_income_tax_profile_menu(callback.message, callback.from_user.id)
    else:
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
    await state.clear()
    current = "\n".join(
        f"• {escape(name)} = {rub(amount)}"
        for name, amount in allocator.settings.life_categories.items()
    ) or "Отдельных категорий сейчас нет."
    rows = [
        [(name, f"settings:life_open:{allocator.settings.ensure_life_category_id(name)}")]
        for name in allocator.settings.life_categories
    ]
    current_names = set(allocator.settings.life_categories) | {"Зарплата"}
    legacy_names = [
        name for name, amount in allocator.state.period_life_topups.items()
        if name not in current_names and Decimal(str(amount)) > 0
    ]
    if legacy_names:
        rows.append([("Связать прежние суммы", "settings:life_repair")])
    await callback.message.answer(
        "<b>ОТДЕЛЬНЫЕ КОНВЕРТЫ КРИТИЧЕСКОГО МИНИМУМА</b>\n\n"
        f"{current}\n\n"
        "Выберите категорию, чтобы изменить её название или сумму.\n\n"
        "Не распределённая между категориями часть Критического минимума остаётся в конверте «Зарплата».",
        reply_markup=keyboard(rows + [[("← Назад", "settings:open")]])
    )


@router.callback_query(F.data == "settings:life_repair")
async def choose_legacy_life_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    current_names = set(allocator.settings.life_categories) | {"Зарплата"}
    legacy = [
        (name, Decimal(str(amount)))
        for name, amount in allocator.state.period_life_topups.items()
        if name not in current_names and Decimal(str(amount)) > 0
    ]
    if not legacy:
        await edit_life_categories(callback, state)
        return
    rows = [[(f"{name} — {rub(amount)}", f"settings:life_repair_old:{name}")]
            for name, amount in legacy]
    await callback.message.answer(
        "<b>ПРЕЖНИЕ КАТЕГОРИИ</b>\n\n"
        "Выберите старое название. Затем укажите его новое название — "
        "Аллокатор объединит всю сумму за период и историю доходов.",
        reply_markup=keyboard(rows + [[("← Назад", "settings:life_categories")]]),
    )


@router.callback_query(F.data.startswith("settings:life_repair_old:"))
async def choose_current_life_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    old = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    await state.update_data(legacy_life_name=old)
    rows = [[(name, f"settings:life_repair_apply:{name}")]
            for name in allocator.settings.life_categories]
    if "Зарплата" not in allocator.settings.life_categories:
        rows.append([("Зарплата", "settings:life_repair_apply:Зарплата")])
    await callback.message.answer(
        f"Старое название: <b>{escape(old)}</b>\n\n"
        "Выберите текущую категорию, к которой относится эта сумма.",
        reply_markup=keyboard(rows + [[("← Назад", "settings:life_repair")]]),
    )


@router.callback_query(F.data.startswith("settings:life_repair_apply:"))
async def apply_legacy_life_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    old = data.get("legacy_life_name")
    new = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    if not old or new not in set(allocator.settings.life_categories) | {"Зарплата"}:
        await callback.message.answer("Не удалось связать категории. Откройте список заново.")
        return
    amount = Decimal(str(allocator.state.period_life_topups.pop(old, Decimal("0"))))
    allocator.state.period_life_topups[new] = (
        Decimal(str(allocator.state.period_life_topups.get(new, 0))) + amount
    )
    entity_id = allocator.settings.life_category_ids.get(new, "")
    db.record_envelope_rename(callback.from_user.id, allocator, "КЖ:", old, new, entity_id)
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"Сумма категории «{escape(old)}» добавлена в «{escape(new)}».",
        reply_markup=keyboard([[("К категориям", "settings:life_categories")]]),
    )

@router.callback_query(F.data.startswith("settings:life_open:"))
async def open_life_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    token = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    name = next(
        (name for name, uid in allocator.settings.life_category_ids.items() if uid == token),
        token,  # backward compatibility with buttons sent before the migration
    )
    amount = allocator.settings.life_categories.get(name)
    if amount is None:
        await callback.message.answer(
            "Эта категория уже изменена. Откройте список заново.",
            reply_markup=keyboard([
                [("← Назад", "settings:life_categories")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
    uid = allocator.settings.ensure_life_category_id(name)
    await state.update_data(life_category_old=name)
    await callback.message.answer(
        f"<b>{escape(name)}</b>\n\nСумма в Критическом минимуме — <b>{rub(amount)}</b>.",
        reply_markup=keyboard([
            [("Переименовать", f"settings:life_rename:{uid}"), ("Изменить сумму", f"settings:life_amount:{uid}")],
            [("🗑️ Удалить категорию", f"settings:life_delete_ask:{uid}")],
            [("← Назад к категориям", "settings:life_categories")],
        ]),
    )

@router.callback_query(F.data.startswith("settings:life_rename:"))
async def rename_life_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    token = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    old = next(
        (name for name, uid in allocator.settings.life_category_ids.items() if uid == token),
        token,
    )
    if old not in allocator.settings.life_categories:
        await callback.message.answer(
            "Эта категория уже изменена.",
            reply_markup=keyboard([[("← Назад", "settings:life_categories")]]),
        )
        return
    await state.update_data(life_category_old=old)
    await state.set_state(EditSettingsStates.life_category_rename)
    await callback.message.answer(f"Введите новое название для категории «{escape(old)}».")

@router.callback_query(F.data.startswith("settings:life_amount:"))
async def change_life_category_amount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    token = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    name = next(
        (name for name, uid in allocator.settings.life_category_ids.items() if uid == token),
        token,
    )
    if name not in allocator.settings.life_categories:
        await callback.message.answer(
            "Эта категория уже изменена.",
            reply_markup=keyboard([[("← Назад", "settings:life_categories")]]),
        )
        return
    await state.update_data(life_category_old=name)
    await state.set_state(EditSettingsStates.life_category_amount)
    await callback.message.answer(f"Введите новую месячную сумму для категории «{escape(name)}».")

@router.callback_query(F.data.startswith("settings:life_delete_ask:"))
async def ask_delete_life_category(callback: CallbackQuery):
    await callback.answer()
    token = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    name = next(
        (name for name, uid in allocator.settings.life_category_ids.items() if uid == token),
        None,
    )
    if name is None:
        await callback.message.answer(
            "Эта категория уже изменена.",
            reply_markup=keyboard([[("← Назад", "settings:life_categories")]]),
        )
        return
    moved = Decimal(str(allocator.state.period_life_topups.get(name, 0)))
    await callback.message.answer(
        f"Удалить категорию <b>{escape(name)}</b>?\n\n"
        f"Распределено за текущий период — <b>{rub(moved)}</b>. "
        "Эта сумма останется в Балансе жизни и будет показана в конверте «Зарплата».",
        reply_markup=keyboard([
            [("🗑️ Удалить категорию", f"settings:life_delete:{token}")],
            [("← Назад", f"settings:life_open:{token}"), ("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data.startswith("settings:life_delete:"))
async def delete_life_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    token = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    name = next(
        (name for name, uid in allocator.settings.life_category_ids.items() if uid == token),
        token,
    )
    if name in allocator.settings.life_categories:
        uid = allocator.settings.life_category_ids.get(name, "")
        allocator.settings.life_categories.pop(name)
        allocator.settings.life_category_ids.pop(name, None)
        moved = Decimal(str(allocator.state.period_life_topups.pop(name, Decimal("0"))))
        if moved:
            allocator.state.period_life_topups["Зарплата"] = (
                Decimal(str(allocator.state.period_life_topups.get("Зарплата", 0))) + moved
            )
            source_key = f"КЖ:{name}"
            allocator.state.period_allocations["КЖ:Зарплата"] = (
                Decimal(str(allocator.state.period_allocations.get("КЖ:Зарплата", 0)))
                + Decimal(str(allocator.state.period_allocations.pop(source_key, 0)))
            )
        with db.transaction():
            if moved:
                db.save_operation(
                    callback.from_user.id,
                    "envelope_transfer",
                    {
                        "source": f"КЖ:{name}",
                        "source_id": f"life:{uid}" if uid else "",
                        "destination": "КЖ:Зарплата",
                        "source_kind": "life",
                        "destination_kind": "life",
                        "amount": str(moved),
                        "reason": "life_category_deleted",
                    },
                )
            db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"Категория «{escape(name)}» удалена. Её сумма осталась в Балансе жизни и перенесена в конверт «Зарплата».",
        reply_markup=keyboard([
            [("← К категориям", "settings:life_categories")],
            [("← Главное меню", "menu:back")],
        ]),
    )

@router.message(EditSettingsStates.life_categories)
@router.message(EditSettingsStates.life_category_rename)
@router.message(EditSettingsStates.life_category_amount)
async def save_life_categories(message: Message, state: FSMContext):
    text = message.text.strip()
    allocator = db.load_allocator(message.from_user.id)

    current_state = await state.get_state()
    if current_state == EditSettingsStates.life_category_amount.state:
        data = await state.get_data()
        old, value = data.get("life_category_old", ""), parse_decimal(text)
        if old not in allocator.settings.life_categories or value is None or value <= 0:
            await message.answer("Введите сумму больше нуля.")
            return
        other_total = sum((amount for name, amount in allocator.settings.life_categories.items() if name != old), Decimal("0"))
        if other_total + value > allocator.settings.critical_life:
            await message.answer("Сумма категорий не может быть больше Критического минимума.")
            return
        allocator.settings.life_categories[old] = value
        db.save_allocator(message.from_user.id, allocator)
        await state.clear()
        await message.answer(f"Сумма категории «{escape(old)}» обновлена: <b>{rub(value)}</b>.", reply_markup=main_menu_keyboard(message.from_user.id))
        return

    if current_state == EditSettingsStates.life_category_rename.state:
        data = await state.get_data()
        old, new = data.get("life_category_old", ""), text.strip()
        if not new or new in allocator.settings.life_categories or is_system_envelope_name(new):
            await message.answer("Такое название недоступно. Введите другое название.")
            return
        allocator.settings.life_categories[new] = allocator.settings.life_categories.pop(old)
        allocator.settings.life_category_ids[new] = allocator.settings.life_category_ids.pop(old)
        if old in allocator.state.period_life_topups:
            allocator.state.period_life_topups[new] = (
                allocator.state.period_life_topups.get(new, Decimal("0"))
                + allocator.state.period_life_topups.pop(old)
            )
        db.record_envelope_rename(
            message.from_user.id, allocator, 'КЖ:', old, new,
            allocator.settings.life_category_ids[new],
        )
        db.save_allocator(message.from_user.id, allocator)
        await state.clear()
        await message.answer(f"Категория переименована: <b>{escape(new)}</b>.", reply_markup=main_menu_keyboard(message.from_user.id))
        return

    if text.lower().startswith("переименовать:"):
        try:
            old, new = text.split(":", 1)[1].split("=", 1)
            old, new = old.strip(), new.strip()
        except ValueError:
            old = new = ""
        if (not old or not new or is_system_envelope_name(old) or is_system_envelope_name(new)
                or old not in allocator.settings.life_categories
                or new in allocator.settings.life_categories):
            await message.answer("Не удалось переименовать категорию. Проверьте старое и новое название.")
            return
        allocator.settings.life_categories[new] = allocator.settings.life_categories.pop(old)
        allocator.settings.life_category_ids[new] = allocator.settings.life_category_ids.pop(old)
        if old in allocator.state.period_life_topups:
            allocator.state.period_life_topups[new] = (
                allocator.state.period_life_topups.get(new, Decimal("0"))
                + allocator.state.period_life_topups.pop(old)
            )
        db.record_envelope_rename(
            message.from_user.id, allocator, 'КЖ:', old, new,
            allocator.settings.life_category_ids[new],
        )
        db.save_allocator(message.from_user.id, allocator)
        await state.clear()
        await message.answer(f"Категория переименована: <b>{escape(old)}</b> → <b>{escape(new)}</b>.", reply_markup=main_menu_keyboard(message.from_user.id))
        return

    previous_category_ids = dict(allocator.settings.life_category_ids)
    if text.lower() in {"нет", "none", "0"}:
        allocator.settings.life_categories = {}
        allocator.settings.life_category_ids = {}
    else:
        new_categories = {}
        try:
            for raw_item in text.split(","):
                name, raw_value = raw_item.split("=", 1)
                name = name.strip()
                value = parse_decimal(raw_value)
                if not name or is_system_envelope_name(name) or value is None or value <= 0:
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
        allocator.settings.life_category_ids = {
            name: previous_category_ids.get(name) or allocator.settings.ensure_life_category_id(name)
            for name in new_categories
        }

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
        f"• {'🧳' if goal.is_chest else '⭐️'} "
        f"{escape(goal_display_name(goal.name, goal.is_chest))} = "
        f"{format(goal.percentage.normalize(), 'f')}%"
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
    await state.clear()
    await message.answer(
        "Проценты остатка больше не настраиваются отдельно: они определяются правилами уровня. "
        "Там, где остаток делится между двумя направлениями, он делится поровну.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "settings:erase_all")
async def ask_erase_all(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or not allocator.settings.developer_mode:
        return
    await state.clear()
    await state.update_data(erase_all_pending=True)
    await callback.message.answer(
        "<b>УДАЛИТЬ ПРОФИЛЬ И ВСЮ ИСТОРИЮ?</b>\n\n"
        "Будут удалены настройки, балансы, Цели и Сундуки, кредиты, "
        "все распределения, налоги, оплаты налогов и плановые платежи.\n"
        "Это касается только вашего аккаунта. Отменить удаление нельзя.\n"
        "После удаления отправьте /start, чтобы пройти настройку с нуля.\n"
        "Старые сообщения в Telegram останутся, но данные в боте будут удалены.",
        reply_markup=keyboard([
            [("🗑️ Удалить всё и начать с нуля", "settings:erase_all_confirm")],
            [("Отмена", "settings:full_reset_cancel")],
        ]),
    )


@router.callback_query(F.data == "settings:erase_all_confirm")
async def confirm_erase_all(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    pending = (await state.get_data()).get("erase_all_pending")
    allocator = db.load_allocator(callback.from_user.id)
    if not pending or allocator is None or not allocator.settings.developer_mode:
        await callback.message.answer("Откройте удаление заново в настройках режима разработчика.")
        return
    db.delete_user(callback.from_user.id)
    await state.clear()
    await callback.message.answer(
        "Профиль и вся история удалены. Налоги, оплаты и обязательства тоже очищены.\n\n"
        "Отправьте /start — начнём с чистого профиля."
    )
