from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from financial_engine import Credit, fmt_money
from storage import db
from ui import keyboard, main_menu_keyboard


router = Router()


class DebtStates(StatesGroup):
    name = State()
    balance = State()
    rate = State()
    minimum_payment = State()
    full_repayment = State()
    payment_type = State()
    early_action = State()
    edit_value = State()
    payment = State()
    balance_update = State()


def parse_amount(text: str | None) -> Decimal | None:
    try:
        value = Decimal((text or "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return value if value >= 0 else None


@router.callback_query(F.data == "debt:add")
async def add_debt_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(DebtStates.name)
    await callback.message.answer(
        "<b>ДОБАВИТЬ ДОЛГ</b>\n\nВведите понятное название.\n"
        "Например: Кредитная карта или Долг Анне.",
        reply_markup=keyboard([[("✖️ Отмена", "debt:cancel")]]),
    )


@router.message(DebtStates.name)
async def add_debt_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Введите понятное название словами.")
        return
    await state.update_data(new_debt_name=name)
    await state.set_state(DebtStates.balance)
    await message.answer("Введите текущий остаток долга.")


@router.message(DebtStates.balance)
async def add_debt_balance(message: Message, state: FSMContext):
    value = parse_amount(message.text)
    if value is None or value <= 0:
        await message.answer("Введите сумму больше нуля.")
        return
    await state.update_data(new_debt_balance=str(value))
    await state.set_state(DebtStates.rate)
    await message.answer("Введите годовую процентную ставку. Если процентов нет — отправьте 0.")


@router.message(DebtStates.rate)
async def add_debt_rate(message: Message, state: FSMContext):
    value = parse_amount(message.text)
    if value is None:
        await message.answer("Введите ставку числом. Например: 24 или 0.")
        return
    await state.update_data(new_debt_rate=str(value))
    await state.set_state(DebtStates.minimum_payment)
    await message.answer("Введите обязательный минимальный платёж в месяц. Если его нет — отправьте 0.")


@router.message(DebtStates.minimum_payment)
async def add_debt_minimum(message: Message, state: FSMContext):
    value = parse_amount(message.text)
    if value is None:
        await message.answer("Введите сумму числом.")
        return
    await state.update_data(new_debt_minimum=str(value))
    await state.set_state(DebtStates.full_repayment)
    await message.answer(
        "Введите сумму полного погашения на сегодня. Если банк её не показывает — отправьте 0."
    )


@router.message(DebtStates.full_repayment)
async def add_debt_full_repayment(message: Message, state: FSMContext):
    value = parse_amount(message.text)
    if value is None:
        await message.answer("Введите сумму от нуля и выше.")
        return
    await state.update_data(
        new_debt_full_repayment=None if value == 0 else str(value),
    )
    await state.set_state(DebtStates.payment_type)
    await message.answer(
        "Какой тип платежа указан по кредиту?",
        reply_markup=keyboard([
            [("Аннуитетный", "debtadd:payment_type:annuity")],
            [("Дифференцированный", "debtadd:payment_type:differentiated")],
            [("✖️ Отмена", "debt:cancel")],
        ]),
    )


@router.callback_query(DebtStates.payment_type, F.data.startswith("debtadd:payment_type:"))
async def add_debt_payment_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    payment_type = (
        "Аннуитетный"
        if callback.data.endswith(":annuity")
        else "Дифференцированный"
    )
    await state.update_data(new_debt_payment_type=payment_type)
    await state.set_state(DebtStates.early_action)
    await callback.message.answer(
        "Что обычно выбирать при досрочном погашении?",
        reply_markup=keyboard([
            [("Уменьшать срок", "debtadd:early:term")],
            [("Уменьшать платёж", "debtadd:early:payment")],
            [("✖️ Отмена", "debt:cancel")],
        ]),
    )


@router.callback_query(DebtStates.early_action, F.data.startswith("debtadd:early:"))
async def add_debt_early_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await state.clear()
        return
    allocator.settings.credits.append(Credit(
        name=data["new_debt_name"],
        principal_balance=Decimal(data["new_debt_balance"]),
        full_repayment_amount=(
            Decimal(data["new_debt_full_repayment"])
            if data.get("new_debt_full_repayment") else None
        ),
        annual_rate=Decimal(data["new_debt_rate"]),
        minimum_payment=Decimal(data["new_debt_minimum"]),
        payment_type=data["new_debt_payment_type"],
        early_repayment_action=(
            "Уменьшать срок"
            if callback.data.endswith(":term") else "Уменьшать платёж"
        ),
    ))
    allocator.settings.has_debts = True
    if allocator.settings.minimum_reserve_months <= 0:
        allocator.settings.minimum_reserve_months = Decimal(
            "1" if allocator.profile_id == "stable" else "2"
        )
    db.save_allocator(callback.from_user.id, allocator)
    mode = allocator.active_mode()
    minimum = allocator.settings.minimum_reserve_limit
    pillow = allocator.pillow_total_balance
    if pillow >= minimum:
        advice = (
            "Минимальная Подушка уже сохранена. Аллокатор переключил приоритет "
            "на закрытие долгов. Если решите погасить долг из Подушки, оставьте "
            f"на ней не меньше <b>{fmt_money(minimum)} ₽</b>."
        )
    else:
        advice = (
            "Сначала Аллокатор восстановит Минимальную Подушку до "
            f"<b>{fmt_money(minimum)} ₽</b>, затем переключится на закрытие долгов."
        )
    await state.clear()
    await callback.message.answer(
        f"✔️ Долг добавлен.\n\nТекущий уровень: <b>{mode}</b>.\n\n{advice}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "debt:cancel")
async def debt_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "Добавление долга отменено.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


def _credit(allocator, index: int):
    if index < 0 or index >= len(allocator.settings.credits):
        return None
    return allocator.settings.credits[index]


def _sync_debt_flag(allocator) -> None:
    allocator.settings.has_debts = any(item.active for item in allocator.settings.credits)
    required = sum(
        (item.minimum_payment for item in allocator.settings.credits if item.active),
        Decimal("0"),
    )
    allocator.state.accumulated_minimum_payments = min(
        allocator.state.accumulated_minimum_payments,
        required,
    )


async def show_debt_card(message: Message, telegram_id: int, index: int) -> None:
    allocator = db.load_allocator(telegram_id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None:
        await message.answer("Долг не найден.")
        return
    minimum = allocator.settings.minimum_reserve_limit
    safe_from_pillow = max(Decimal("0"), allocator.pillow_total_balance - minimum)
    suggested = min(credit.principal_balance, safe_from_pillow)
    advice = (
        f"\n\nМожно направить из Подушки до <b>{fmt_money(suggested)} ₽</b>, "
        f"сохранив Минимальную Подушку <b>{fmt_money(minimum)} ₽</b>."
        if suggested > 0 else
        f"\n\nПодушку пока не трогаем: на ней нужно сохранить минимум <b>{fmt_money(minimum)} ₽</b>."
    )
    rows = []
    if credit.active:
        rows.extend([
            [("Внести платёж", f"debt:pay:{index}")],
            [("Уточнить остаток", f"debt:balance:{index}")],
        ])
        if suggested > 0:
            rows.append([("Погасить из Подушки", f"debt:pillow:{index}")])
        rows.append([("Закрыть полностью", f"debt:closeask:{index}")])
    rows.append([("✎ Изменить данные кредита", f"debt:edit:{index}")])
    if allocator.settings.debt_strategy == "Ручной выбор" and len(allocator.settings.credits) > 1:
        order_row = []
        if index > 0:
            order_row.append(("↑ Выше в очереди", f"debt:move:up:{index}"))
        if index < len(allocator.settings.credits) - 1:
            order_row.append(("↓ Ниже в очереди", f"debt:move:down:{index}"))
        if order_row:
            rows.append(order_row)
    rows.extend([
        [("🗑️ Удалить запись", f"debt:deleteask:{index}")],
        [("← К долгам", "menu:credits")],
    ])
    full_repayment = (
        f"{fmt_money(credit.full_repayment_amount)} ₽"
        if credit.full_repayment_amount is not None else "не указана"
    )
    await message.answer(
        f"<b>{escape(credit.name.upper())}</b>\n\n"
        f"Остаток — <b>{fmt_money(credit.principal_balance)} ₽</b>\n"
        f"Полное погашение — <b>{full_repayment}</b>\n"
        f"Ставка — <b>{credit.annual_rate}%</b>\n"
        f"Минимальный платёж — <b>{fmt_money(credit.minimum_payment)} ₽</b>\n"
        f"Тип платежа — <b>{escape(credit.payment_type)}</b>\n"
        f"Досрочное погашение — <b>{escape(credit.early_repayment_action)}</b>\n"
        f"Статус — <b>{credit.status}</b>{advice}",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.startswith("debt:view:"))
async def debt_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    index = int(callback.data.rsplit(":", 1)[1])
    await show_debt_card(callback.message, callback.from_user.id, index)


@router.callback_query(F.data.startswith("debt:edit:"))
async def debt_edit_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None:
        await callback.message.answer("Долг не найден.")
        return
    await callback.message.answer(
        f"<b>ИЗМЕНИТЬ: {escape(credit.name.upper())}</b>",
        reply_markup=keyboard([
            [("Название", f"debt:editfield:name:{index}"),
             ("Ставка", f"debt:editfield:rate:{index}")],
            [("Текущий остаток", f"debt:balance:{index}")],
            [("Минимальный платёж", f"debt:editfield:minimum:{index}")],
            [("Сумма полного погашения", f"debt:editfield:full:{index}")],
            [("Тип платежа", f"debt:editfield:payment_type:{index}")],
            [("Досрочное погашение", f"debt:editfield:early_action:{index}")],
            [("← Назад", f"debt:view:{index}")],
        ]),
    )


@router.callback_query(F.data.startswith("debt:editfield:"))
async def debt_edit_field(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, field, raw_index = callback.data.split(":", 3)
    index = int(raw_index)
    allocator = db.load_allocator(callback.from_user.id)
    if _credit(allocator, index) is None:
        await callback.message.answer("Долг не найден.")
        return
    await state.update_data(debt_edit_index=index, debt_edit_field=field)
    if field == "payment_type":
        await callback.message.answer(
            "Выберите новый тип платежа.",
            reply_markup=keyboard([
                [("Аннуитетный", f"debt:editchoice:payment_type:annuity:{index}")],
                [("Дифференцированный", f"debt:editchoice:payment_type:differentiated:{index}")],
                [("← Назад", f"debt:edit:{index}")],
            ]),
        )
        return
    if field == "early_action":
        await callback.message.answer(
            "Выберите действие при досрочном погашении.",
            reply_markup=keyboard([
                [("Уменьшать срок", f"debt:editchoice:early_action:term:{index}")],
                [("Уменьшать платёж", f"debt:editchoice:early_action:payment:{index}")],
                [("← Назад", f"debt:edit:{index}")],
            ]),
        )
        return
    prompts = {
        "name": "Введите новое название кредита.",
        "rate": "Введите новую годовую ставку от 0 до 200.",
        "minimum": "Введите новый минимальный платёж.",
        "full": "Введите сумму полного погашения или 0, если она неизвестна.",
    }
    await state.set_state(DebtStates.edit_value)
    await callback.message.answer(
        prompts[field],
        reply_markup=keyboard([[("← Назад", f"debt:edit:{index}")]]),
    )


@router.message(DebtStates.edit_value)
async def debt_edit_value_save(message: Message, state: FSMContext):
    data = await state.get_data()
    index = int(data.get("debt_edit_index", -1))
    field = str(data.get("debt_edit_field", ""))
    allocator = db.load_allocator(message.from_user.id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None:
        await state.clear()
        await message.answer("Долг не найден.")
        return
    if field == "name":
        value = (message.text or "").strip()
        if len(value) < 2 or any(
            item is not credit and item.name.casefold() == value.casefold()
            for item in allocator.settings.credits
        ):
            await message.answer("Введите уникальное понятное название.")
            return
        credit.name = value
    else:
        value = parse_amount(message.text)
        if value is None or (field == "rate" and value > 200):
            await message.answer("Введите корректное неотрицательное число.")
            return
        if field == "rate":
            credit.annual_rate = value
        elif field == "minimum":
            credit.minimum_payment = value
        elif field == "full":
            credit.full_repayment_amount = None if value == 0 else value
        else:
            await message.answer("Неизвестное поле кредита.")
            return
    _sync_debt_flag(allocator)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_debt_card(message, message.from_user.id, index)


@router.callback_query(F.data.startswith("debt:editchoice:"))
async def debt_edit_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, field, choice, raw_index = callback.data.split(":", 4)
    index = int(raw_index)
    allocator = db.load_allocator(callback.from_user.id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None:
        await callback.message.answer("Долг не найден.")
        return
    if field == "payment_type":
        credit.payment_type = (
            "Аннуитетный" if choice == "annuity" else "Дифференцированный"
        )
    elif field == "early_action":
        credit.early_repayment_action = (
            "Уменьшать срок" if choice == "term" else "Уменьшать платёж"
        )
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_debt_card(callback.message, callback.from_user.id, index)


@router.callback_query(F.data.startswith("debt:move:"))
async def debt_move(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, direction, raw_index = callback.data.split(":", 3)
    index = int(raw_index)
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or allocator.settings.debt_strategy != "Ручной выбор":
        return
    target = index - 1 if direction == "up" else index + 1
    if not 0 <= index < len(allocator.settings.credits) or not 0 <= target < len(allocator.settings.credits):
        return
    allocator.settings.credits[index], allocator.settings.credits[target] = (
        allocator.settings.credits[target], allocator.settings.credits[index]
    )
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_debt_card(callback.message, callback.from_user.id, target)


@router.callback_query(F.data.startswith("debt:pay:"))
async def debt_payment_ask(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(debt_index=index)
    await state.set_state(DebtStates.payment)
    await callback.message.answer(
        "Введите фактически внесённую сумму. Она уменьшит остаток долга.\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[('← Назад', f'debt:view:{index}')]]),
    )


@router.message(DebtStates.payment)
async def debt_payment_save(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    credit = _credit(allocator, int(data.get("debt_index", -1))) if allocator else None
    if amount is None or amount <= 0 or credit is None:
        await message.answer("Введите сумму больше нуля.")
        return
    applied = min(amount, credit.principal_balance)
    credit.principal_balance -= applied
    allocator.state.early_repayment += applied
    if credit.principal_balance <= 0:
        credit.principal_balance = Decimal("0")
        credit.status = "Погашен"
    _sync_debt_flag(allocator)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(
        f"Платёж учтён — <b>{fmt_money(applied)} ₽</b>.\n"
        f"Остаток — <b>{fmt_money(credit.principal_balance)} ₽</b>.\n"
        f"Текущий уровень — <b>{allocator.active_mode()}</b>.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data.startswith("debt:balance:"))
async def debt_balance_ask(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(debt_index=index)
    await state.set_state(DebtStates.balance_update)
    await callback.message.answer(
        "Введите актуальный остаток долга из приложения банка. Если долг погашен — отправьте 0.",
        reply_markup=keyboard([[('← Назад', f'debt:view:{index}')]]),
    )


@router.message(DebtStates.balance_update)
async def debt_balance_save(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    credit = _credit(allocator, int(data.get("debt_index", -1))) if allocator else None
    if amount is None or credit is None:
        await message.answer("Введите сумму от нуля и выше.")
        return
    credit.principal_balance = amount
    credit.status = "Активный" if amount > 0 else "Погашен"
    _sync_debt_flag(allocator)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(
        f"Остаток обновлён: <b>{fmt_money(amount)} ₽</b>.\n"
        f"Текущий уровень — <b>{allocator.active_mode()}</b>.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(F.data.startswith("debt:pillow:"))
async def debt_pay_from_pillow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None or not credit.active:
        return
    minimum = allocator.settings.minimum_reserve_limit
    amount = min(credit.principal_balance, max(Decimal("0"), allocator.pillow_total_balance - minimum))
    if amount <= 0:
        await callback.message.answer("Свободной части Подушки сейчас нет.")
        return
    # Сначала расходуется слой ФМ; Минимальная Подушка остаётся неприкосновенной.
    from_force = min(amount, allocator.state.pillow_force_majeure)
    allocator.state.pillow_force_majeure -= from_force
    remaining = amount - from_force
    if remaining > 0:
        allocator.state.pillow_minimum -= remaining
    credit.principal_balance -= amount
    allocator.state.early_repayment += amount
    if credit.principal_balance <= 0:
        credit.principal_balance = Decimal("0")
        credit.status = "Погашен"
    _sync_debt_flag(allocator)
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        f"Из Подушки направлено <b>{fmt_money(amount)} ₽</b>. "
        f"Минимальная Подушка <b>{fmt_money(minimum)} ₽</b> сохранена.\n"
        f"Остаток долга — <b>{fmt_money(credit.principal_balance)} ₽</b>.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data.startswith("debt:closeask:"))
async def debt_close_ask(callback: CallbackQuery):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    await callback.message.answer(
        "Отмечайте долг закрытым только после фактического погашения.",
        reply_markup=keyboard([[('✔️ Долг погашен', f'debt:close:{index}')], [('← Назад', f'debt:view:{index}')]]),
    )


@router.callback_query(F.data.startswith("debt:close:"))
async def debt_close(callback: CallbackQuery):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None:
        return
    credit.principal_balance = Decimal("0")
    credit.status = "Погашен"
    _sync_debt_flag(allocator)
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer("Долг отмечен погашенным.", reply_markup=main_menu_keyboard(callback.from_user.id))


@router.callback_query(F.data.startswith("debt:deleteask:"))
async def debt_delete_ask(callback: CallbackQuery):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    await callback.message.answer(
        "Удалить запись о долге? Историю этой записи восстановить автоматически не получится.",
        reply_markup=keyboard([[('🗑️ Удалить запись', f'debt:delete:{index}')], [('← Назад', f'debt:view:{index}')]]),
    )


@router.callback_query(F.data.startswith("debt:delete:"))
async def debt_delete(callback: CallbackQuery):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or _credit(allocator, index) is None:
        return
    removed = allocator.settings.credits.pop(index)
    _sync_debt_flag(allocator)
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        f"Запись «{removed.name}» удалена.", reply_markup=main_menu_keyboard(callback.from_user.id)
    )
