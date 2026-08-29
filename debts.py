from decimal import Decimal, InvalidOperation

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
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    if allocator is None:
        await state.clear()
        return
    allocator.settings.credits.append(Credit(
        name=data["new_debt_name"],
        principal_balance=Decimal(data["new_debt_balance"]),
        full_repayment_amount=None,
        annual_rate=Decimal(data["new_debt_rate"]),
        minimum_payment=value,
    ))
    allocator.settings.has_debts = True
    if allocator.settings.minimum_reserve_months <= 0:
        allocator.settings.minimum_reserve_months = Decimal(
            "1" if allocator.profile_id == "stable" else "2"
        )
    db.save_allocator(message.from_user.id, allocator)
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
    await message.answer(
        f"✔️ Долг добавлен.\n\nТекущий режим: <b>{mode}</b>.\n\n{advice}",
        reply_markup=main_menu_keyboard(message.from_user.id),
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


@router.callback_query(F.data.startswith("debt:view:"))
async def debt_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    credit = _credit(allocator, index) if allocator else None
    if credit is None:
        await callback.message.answer("Долг не найден.")
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
    rows.extend([[("Удалить запись", f"debt:deleteask:{index}")], [("← К долгам", "menu:credits")]])
    await callback.message.answer(
        f"<b>{credit.name.upper()}</b>\n\n"
        f"Остаток — <b>{fmt_money(credit.principal_balance)} ₽</b>\n"
        f"Ставка — <b>{credit.annual_rate}%</b>\n"
        f"Минимальный платёж — <b>{fmt_money(credit.minimum_payment)} ₽</b>\n"
        f"Статус — <b>{credit.status}</b>{advice}",
        reply_markup=keyboard(rows),
    )


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
        f"Текущий режим — <b>{allocator.active_mode()}</b>.",
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
        f"Текущий режим — <b>{allocator.active_mode()}</b>.",
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
        reply_markup=keyboard([[('Удалить запись', f'debt:delete:{index}')], [('← Назад', f'debt:view:{index}')]]),
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
