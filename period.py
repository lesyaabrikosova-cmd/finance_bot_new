from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from currency_rates import CurrencyRateService, CurrencyRateUnavailable, currency_symbol
from storage import db
from ui import keyboard, main_menu_keyboard

router = Router()


class FundCurrencyStates(StatesGroup):
    amount = State()
    manual_rate = State()
    forecast_amount = State()


class SalaryPaymentStates(StatesGroup):
    reserve = State()
    amount = State()
    received_rub = State()
    confirmation = State()


class PeriodClosingStates(StatesGroup):
    salary_remainder = State()


def _decimal(text: str | None) -> Decimal | None:
    try:
        return Decimal(str(text or "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def _fund_currency_text(allocator) -> str:
    state = allocator.state
    lines = []
    for code, amount in state.fund_salary_currencies.items():
        rate = state.fund_salary_period_rates.get(code, Decimal("1"))
        lines.append(
            f"• <b>{amount} {currency_symbol(code)}</b> · курс {rate} ₽ · "
            f"эквивалент {(amount * rate).quantize(Decimal('0.01'))} ₽"
        )
    if not lines:
        lines.append("• Валютные остатки пока не указаны")
    return (
        "<b>ВАЛЮТЫ ФОНДА ЗАРПЛАТЫ</b>\n\n"
        + "\n".join(lines)
        + f"\n\nПлановый эквивалент Фонда — <b>{state.intercontract_reserve} ₽</b>.\n\n"
        "Курс фиксируется для расчётного периода. Ежедневные колебания не меняют режим. "
        "После реального обмена укажите новые фактические остатки: это внутреннее перемещение, "
        "оно не считается доходом."
    )


@router.callback_query(F.data == "fundcurrency:menu")
async def fund_currency_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or allocator.settings.income_rhythm != "cyclic":
        return
    await callback.message.answer(
        _fund_currency_text(allocator),
        reply_markup=keyboard([
            [("₽ RUB", "fundcurrency:add:RUB"), ("$ USD", "fundcurrency:add:USD")],
            [("€ EUR", "fundcurrency:add:EUR"), ("₹ INR", "fundcurrency:add:INR")],
            [("د.إ AED", "fundcurrency:add:AED"), ("¥ CNY", "fundcurrency:add:CNY")],
            [("Проверить обмен", "fundcurrency:forecast")],
            [("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data.startswith("fundcurrency:add:"))
async def ask_fund_currency_amount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[-1]
    await state.update_data(fund_currency_code=code)
    await state.set_state(FundCurrencyStates.amount)
    await callback.message.answer(
        f"<b>СКОЛЬКО {code} ФАКТИЧЕСКИ ЛЕЖИТ В ФОНДЕ ЗАРПЛАТЫ?</b>\n\n"
        "Введите текущий остаток. Отправьте 0, чтобы удалить валюту из списка.",
        reply_markup=keyboard([[("← Назад", "fundcurrency:menu")]]),
    )


@router.message(FundCurrencyStates.amount)
async def save_fund_currency_amount(message: Message, state: FSMContext):
    amount = _decimal(message.text)
    if amount is None or amount < 0:
        await message.answer("Введите сумму от 0 и выше.")
        return
    data = await state.get_data()
    code = data["fund_currency_code"]
    await state.update_data(fund_currency_amount=str(amount))
    if code == "RUB" or amount == 0:
        allocator = db.load_allocator(message.from_user.id)
        allocator.state.set_fund_salary_currency(code, amount, Decimal("1"))
        db.save_allocator(message.from_user.id, allocator)
        await state.clear()
        await message.answer(_fund_currency_text(allocator), reply_markup=main_menu_keyboard(message.from_user.id))
        return
    try:
        quote = await CurrencyRateService(db).get_rate_async(code)
    except CurrencyRateUnavailable:
        await state.set_state(FundCurrencyStates.manual_rate)
        await message.answer("Автоматический курс сейчас недоступен. Введите, сколько рублей считать за 1 единицу валюты.")
        return
    await state.update_data(fund_currency_cbr_rate=str(quote.rub_per_unit))
    await message.answer(
        f"Ориентир Банка России на {quote.rate_date.strftime('%d.%m.%Y')}: "
        f"<b>1 {code} = {quote.rub_per_unit.quantize(Decimal('0.0001'))} ₽</b>.\n\n"
        "Реальный обмен обычно менее выгоден из-за спреда и комиссии. Можно зафиксировать "
        "ориентир ЦБ или указать собственный плановый курс.",
        reply_markup=keyboard([
            [("Использовать курс ЦБ", "fundcurrency:rate:cbr")],
            [("Ввести курс вручную", "fundcurrency:rate:manual")],
            [("✖️ Отмена", "fundcurrency:menu")],
        ]),
    )


@router.callback_query(F.data == "fundcurrency:rate:cbr")
async def apply_fund_currency_cbr(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    allocator.state.set_fund_salary_currency(
        data["fund_currency_code"], Decimal(data["fund_currency_amount"]),
        Decimal(data["fund_currency_cbr_rate"]), locked_at=datetime.now().isoformat(),
    )
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(_fund_currency_text(allocator), reply_markup=main_menu_keyboard(callback.from_user.id))


@router.callback_query(F.data == "fundcurrency:rate:manual")
async def ask_manual_fund_rate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(FundCurrencyStates.manual_rate)
    await callback.message.answer("Введите, сколько рублей учитывать за 1 единицу валюты.")


@router.message(FundCurrencyStates.manual_rate)
async def apply_manual_fund_rate(message: Message, state: FSMContext):
    rate = _decimal(message.text)
    if rate is None or rate <= 0:
        await message.answer("Введите курс больше нуля.")
        return
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    allocator.state.set_fund_salary_currency(
        data["fund_currency_code"], Decimal(data["fund_currency_amount"]), rate,
        locked_at=datetime.now().isoformat(),
    )
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(_fund_currency_text(allocator), reply_markup=main_menu_keyboard(message.from_user.id))


@router.callback_query(F.data == "fundcurrency:forecast")
async def fund_currency_forecast_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    rows = [[(f"{currency_symbol(code)} {code} — {amount}", f"fundcurrency:forecast:{code}")]
            for code, amount in allocator.state.fund_salary_currencies.items()
            if code != "RUB" and amount > 0]
    rows.append([("← Назад", "fundcurrency:menu")])
    await callback.message.answer(
        "<b>ПРОГНОЗ ОБМЕНА</b>\n\nВыберите валюту. Прогноз ничего не спишет и не изменит режим.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.startswith("fundcurrency:forecast:"))
async def fund_currency_forecast_ask(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[1]
    await state.update_data(fund_forecast_code=code)
    await state.set_state(FundCurrencyStates.forecast_amount)
    await callback.message.answer(
        f"Введите, сколько {code} хотите оценочно обменять. Это расчёт без изменения балансов.",
        reply_markup=keyboard([[('← Назад', 'fundcurrency:forecast')]]),
    )


@router.message(FundCurrencyStates.forecast_amount)
async def fund_currency_forecast_show(message: Message, state: FSMContext):
    amount = _decimal(message.text)
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    code = data.get("fund_forecast_code", "")
    available = allocator.state.fund_salary_currencies.get(code, Decimal("0")) if allocator else Decimal("0")
    if amount is None or amount <= 0 or amount > available:
        await message.answer(f"Введите сумму от 0 до {available} {code}.")
        return
    rate = allocator.state.fund_salary_period_rates.get(code, Decimal("0"))
    rub_value = (amount * rate).quantize(Decimal("0.01"))
    preview = deepcopy(allocator)
    preview.state.convert_fund_salary_currency(code, "RUB", amount, rub_value, Decimal("1"))
    before = preview.state.life_balance
    try:
        distributed = preview.pay_intercontract_salary(rub_value)
        life_delta = preview.state.life_balance - before
        detail = (
            f"Если затем выплатить эти деньги себе, Аллокатор сможет распределить "
            f"<b>{distributed} ₽</b>; на текущую жизнь поступит <b>{life_delta} ₽</b>."
        )
    except ValueError:
        detail = "Сумму можно оценить сейчас, а фактическое распределение выполнить после обмена."
    await state.clear()
    await message.answer(
        "<b>ПРОГНОЗ ОБМЕНА</b>\n\n"
        f"{amount} {code} × {rate} ₽ ≈ <b>{rub_value} ₽</b>.\n\n{detail}\n\n"
        "Биржевой ориентир не гарантирует сумму в обменнике: банк или обменный пункт может "
        "учесть спред и комиссию. Балансы не изменены.",
        reply_markup=keyboard([[('← К валютам', 'fundcurrency:menu')], [('Заплатить себе', 'intercontract:salary')]]),
    )


@router.callback_query(F.data == "phaselife:menu")
async def show_phase_life_menu(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or allocator.settings.income_rhythm != "cyclic":
        return
    rows = []
    for phase, label in (("work", "Рабочая жизнь"), ("break", "Жизнь в перерыве")):
        budget = allocator.settings.phase_life(phase)
        status = "✓" if budget and budget.completed else "⚠️"
        action = "Изменить" if budget and budget.completed else "Заполнить"
        rows.append([(f"{status} {action}: {label}", f"phaselife:fill:{phase}")])
    rows.extend([
        [("ℹ️ Как считать две жизни", "phaselife:help")],
        [("← Главное меню", "menu:back")],
    ])
    await callback.message.answer(
        "<b>ЖИЗНЬ В РАЗНЫХ ЧАСТЯХ ЦИКЛА</b>\n\n"
        "Расходы на работе и в перерыве могут отличаться. Здесь можно заполнить или изменить "
        "каждую часть отдельно.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data == "phaselife:help")
async def show_phase_life_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "<b>КАК СЧИТАТЬ ДВЕ ЖИЗНИ</b>\n\n"
        "Аллокатор отдельно запоминает расходы во время работы и во время перерыва. "
        "Не усредняйте их между собой.\n\n"
        "• Если транспорт, здоровье или связь оплачиваются только дома — добавляйте их только "
        "в жизнь в перерыве.\n"
        "• Если расход возникает только на работе — добавляйте его только в рабочую жизнь.\n"
        "• Домашние обязательства, которые продолжаются во время отъезда, отметьте при отдельной "
        "проверке: Аллокатор зарезервирует их заранее.\n"
        "• Расходы, которые работодатель оплачивает напрямую, не добавляйте.\n\n"
        "Смотрите банковскую аналитику только за сопоставимые месяцы нужной части цикла. Например, "
        "при графике 5 / 7 расходы в России считайте по российским месяцам, а не делите на весь год. "
        "При графике месяц через месяц берите несколько домашних или несколько рабочих месяцев.\n\n"
        "Для зарубежной работы можно выбрать местную валюту. Аллокатор сохранит исходные суммы и "
        "покажет рублёвый эквивалент по выбранному курсу.",
        reply_markup=keyboard([
            [("← Назад", "phaselife:menu")],
        ]),
    )


@router.callback_query(F.data == "intercontract:start")
async def start_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or allocator.settings.income_rhythm != "cyclic":
        await callback.message.answer("Межконтрактный период недоступен для этого профиля.")
        return
    break_life = allocator.settings.phase_life("break")
    if break_life is None or not break_life.completed:
        await callback.message.answer(
            "<b>СНАЧАЛА ЗАПОЛНИТЕ ЖИЗНЬ В ПЕРЕРЫВЕ</b>\n\n"
            "Без этих расходов Аллокатор не сможет правильно рассчитать Фонд Зарплаты.",
            reply_markup=keyboard([
                [("Заполнить жизнь в перерыве", "phaselife:fill:break")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
    result = allocator.start_intercontract_break()
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        "<b>ПЕРЕРЫВ МЕЖДУ КОНТРАКТАМИ НАЧАТ</b>\n\n"
        f"Месяцев: <b>{result['months_remaining']}</b>\n"
        f"Плановая зарплата себе: <b>{result['monthly_salary']} ₽</b>.\n\n"
        "Счётчик дохода продолжает учитывать полный цикл: рабочую часть и перерыв.\n\n"
        "<b>РЕЗЕРВ НА СЛЕДУЮЩУЮ РАБОЧУЮ ЧАСТЬ</b>\n"
        f"Нужно подготовить: <b>{result['next_work_obligations']} ₽</b>.\n"
        "Сейчас начинается накопление этого резерва. Он получает приоритет раньше Фонда Зарплаты, "
        "Стабилизатора и Подушки. Если денег пока недостаточно, Аллокатор покажет дефицит и будет "
        "закрывать его из следующих поступлений.\n\n"
        "В начале каждого личного расчётного периода добавьте внешние "
        "поступления, если они уже пришли. Затем нажмите «Заплатить себе из Фонда Зарплаты».",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "intercontract:salary")
async def pay_intercontract_salary(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    currencies = allocator.state.fund_salary_currencies or {"RUB": allocator.state.intercontract_reserve}
    rows = [
        [(f"{currency_symbol(code)} {code} — {amount}", f"salarypay:source:{code}")]
        for code, amount in currencies.items()
        if amount > 0
    ]
    rows.append([("✖️ Отмена", "menu:back")])
    await callback.message.answer(
        "<b>ЗАПЛАТИТЬ СЕБЕ ИЗ ФОНДА ЗАРПЛАТЫ</b>\n\n"
        "Выберите, из какой части Фонда сделать выплату. Если деньги хранятся "
        "в валюте, сначала покажу прогноз, а распределение сделаю по фактически "
        "полученной сумме в рублях.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.startswith("salarypay:source:"))
async def salary_payment_source(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[1]
    allocator = db.load_allocator(callback.from_user.id)
    currencies = allocator.state.fund_salary_currencies or {"RUB": allocator.state.intercontract_reserve}
    balance = currencies.get(code, Decimal("0"))
    saved_reserve = allocator.state.fund_salary_start_reserves.get(code, Decimal("0"))
    await state.update_data(salary_source=code, salary_source_balance=str(balance))
    await state.set_state(SalaryPaymentStates.reserve)
    await callback.message.answer(
        "<b>СКОЛЬКО ОСТАВИТЬ НА НАЧАЛО РАБОТЫ?</b>\n\n"
        "Это деньги на дорогу и первые дни до следующей выплаты. Если отдельный "
        f"запас не нужен — отправьте 0. Сохранённый ориентир: <b>{saved_reserve} {code}</b>.\n\n"
        f"→ Введите сумму в {code}.",
        reply_markup=keyboard([[("← Назад", "intercontract:salary")]]),
    )


@router.message(SalaryPaymentStates.reserve)
async def salary_payment_reserve(message: Message, state: FSMContext):
    reserve = _decimal(message.text)
    data = await state.get_data()
    balance = Decimal(data["salary_source_balance"])
    if reserve is None or reserve < 0 or reserve > balance:
        await message.answer("Введите сумму от 0 до текущего остатка Фонда.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.state.fund_salary_start_reserves[data["salary_source"]] = reserve
    db.save_allocator(message.from_user.id, allocator)
    months = max(Decimal("1"), allocator.state.intercontract_months_remaining)
    suggested = (balance - reserve) / months
    await state.update_data(salary_reserve=str(reserve), salary_suggested=str(suggested))
    await state.set_state(SalaryPaymentStates.amount)
    code = data["salary_source"]
    rate = allocator.state.fund_salary_period_rates.get(code, Decimal("1"))
    forecast = suggested * rate
    await message.answer(
        "<b>СУММА ВЫПЛАТЫ</b>\n\n"
        f"После запаса доступно — <b>{balance - reserve} {code}</b>.\n"
        f"Осталось периодов — <b>{months}</b>.\n"
        f"Ориентир на период — <b>{suggested.quantize(Decimal('0.01'))} {code}</b>"
        + (f" ≈ <b>{forecast.quantize(Decimal('0.01'))} ₽</b>." if code != "RUB" else ".")
        + f"\n\n→ Введите, сколько {code} хотите взять сейчас.",
        reply_markup=keyboard([[("← Назад", f"salarypay:source:{code}")]]),
    )


@router.message(SalaryPaymentStates.amount)
async def salary_payment_amount(message: Message, state: FSMContext):
    amount = _decimal(message.text)
    data = await state.get_data()
    balance = Decimal(data["salary_source_balance"])
    reserve = Decimal(data["salary_reserve"])
    if amount is None or amount < 0 or amount > balance - reserve:
        await message.answer("Сумма превышает доступную часть Фонда после сохранённого запаса.")
        return
    code = data["salary_source"]
    await state.update_data(salary_source_amount=str(amount))
    if code != "RUB":
        await state.set_state(SalaryPaymentStates.received_rub)
        await message.answer(
            f"Обменяйте выбранные <b>{amount} {code}</b> и введите, сколько рублей "
            "фактически получили. Спред и комиссия будут учтены по реальному результату."
        )
        return
    await finish_salary_payment(message, state, amount)


@router.message(SalaryPaymentStates.received_rub)
async def salary_payment_received(message: Message, state: FSMContext):
    received = _decimal(message.text)
    if received is None or received < 0:
        await message.answer("Введите фактически полученную сумму в рублях.")
        return
    await state.update_data(salary_received_rub=str(received))
    await finish_salary_payment(message, state, received)


async def finish_salary_payment(message: Message, state: FSMContext, requested_rub: Decimal):
    allocator = db.load_allocator(message.from_user.id)
    data = await state.get_data()
    preview = deepcopy(allocator)
    try:
        if data.get("salary_source") != "RUB":
            preview.state.convert_fund_salary_currency(
                data["salary_source"], "RUB", Decimal(data["salary_source_amount"]),
                requested_rub, Decimal("1"),
            )
        before = {
            "Жизнь текущего периода": preview.state.life_balance,
            "Обязательства на время работы": preview.state.contract_obligations_reserve,
            "Подушка": preview.pillow_total_balance,
            "Стабилизатор дохода": preview.state.pillow_stabilizer,
            "Фонд Зарплаты": preview.state.intercontract_reserve,
        }
        amount = preview.pay_intercontract_salary(requested_rub)
    except ValueError as error:
        await message.answer(str(error))
        return
    after = {
        "Жизнь текущего периода": preview.state.life_balance,
        "Обязательства на время работы": preview.state.contract_obligations_reserve,
        "Подушка": preview.pillow_total_balance,
        "Стабилизатор дохода": preview.state.pillow_stabilizer,
        "Фонд Зарплаты": preview.state.intercontract_reserve,
    }
    lines = []
    for name, final in after.items():
        delta = final - before[name]
        if delta:
            sign = "+" if delta > 0 else "−"
            lines.append(f"• <b>{name}</b>: {sign}{abs(delta).quantize(Decimal('0.01'))} ₽")
    await state.update_data(salary_requested_rub=str(requested_rub))
    await state.set_state(SalaryPaymentStates.confirmation)
    await message.answer(
        "<b>ПРОВЕРЬТЕ ВЫПЛАТУ ИЗ ФОНДА ЗАРПЛАТЫ</b>\n\n"
        f"Будет распределено — <b>{amount.quantize(Decimal('0.01'))} ₽</b>.\n\n"
        + ("\n".join(lines) or "• Перемещения не требуются")
        + "\n\nЭто только прогноз. Балансы пока не изменены.",
        reply_markup=keyboard([
            [("← Изменить сумму", f"salarypay:source:{data['salary_source']}")],
            [("✔️ Распределить", "salarypay:confirm")],
            [("✖️ Отмена", "menu:back")],
        ]),
    )


@router.callback_query(SalaryPaymentStates.confirmation, F.data == "salarypay:confirm")
async def confirm_salary_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    requested_rub = Decimal(data["salary_requested_rub"])
    try:
        if data.get("salary_source") != "RUB":
            allocator.state.convert_fund_salary_currency(
                data["salary_source"], "RUB", Decimal(data["salary_source_amount"]),
                requested_rub, Decimal("1"),
            )
        amount = allocator.pay_intercontract_salary(requested_rub)
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    transfer_text = (
        f"Из Фонда Зарплаты распределено <b>{amount} ₽</b>."
        if amount > 0 else "Перевод из Фонда Зарплаты не потребовался."
    )
    cycle_text = (
        "\n\nВсе запланированные месяцы перерыва проведены. Когда перерыв действительно закончится, "
        "нажмите «Начать рабочую часть»."
        if allocator.state.intercontract_months_remaining <= 0
        else ""
    )
    await callback.message.answer(
        f"{transfer_text}\n\n"
        "Это внутренний перевод: налог и повторное распределение не рассчитываются.\n"
        f"Осталось месяцев: <b>{allocator.state.intercontract_months_remaining}</b>."
        f"{cycle_text}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "intercontract:finish")
async def finish_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    if allocator.state.intercontract_months_remaining > 0:
        await callback.message.answer(
            "<b>НАЧАТЬ РАБОЧУЮ ЧАСТЬ РАНЬШЕ?</b>\n\n"
            f"По прежнему плану до работы оставалось <b>{allocator.state.intercontract_months_remaining} мес.</b> "
            "Фактическое начало работы заменит этот прогноз.",
            reply_markup=keyboard([
                [("← Назад", "menu:back"), ("✔️ Начать", "intercontract:finish:confirm")],
            ]),
        )
        return
    await validate_work_phase_start(callback, allocator)


@router.callback_query(F.data == "intercontract:extend")
async def ask_extend_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or allocator.settings.income_rhythm != "cyclic":
        return
    if not allocator.state.intercontract_break_active:
        await callback.message.answer("Сначала начните перерыв между рабочими частями.")
        return

    required = allocator.settings.household_life
    available = allocator.state.intercontract_reserve
    shortfall = max(Decimal("0"), required - available)
    funding = (
        f"Фонда Зарплаты хватает на дополнительный период. После подтверждения останется "
        f"выбрать фактическую выплату."
        if shortfall <= 0
        else
        f"В Фонде Зарплаты доступно <b>{available} ₽</b>. Для полной обычной жизни "
        f"не хватает <b>{shortfall} ₽</b>."
    )
    await callback.message.answer(
        "<b>ПЕРЕРЫВ ПРОДЛЕВАЕТСЯ?</b>\n\n"
        "Точную дату контракта угадывать не нужно. Добавим ещё один полный расчётный период "
        "к текущему прогнозу. Если работа начнётся раньше, нажмите «Начать рабочую часть» — "
        "неиспользованные деньги останутся в своих конвертах.\n\n"
        f"На один период нужно — <b>{required} ₽</b>.\n"
        f"{funding}\n\n"
        "При дефиците Аллокатор ничего не забирает автоматически. После продления можно "
        "использовать доступную часть Фонда Зарплаты, добавить фактическое поступление или "
        "уменьшить план жизни. Если этого недостаточно, решение об использовании "
        "Стабилизатора, Бытового резерва, Подушки, целей или инвестиций остаётся за вами.",
        reply_markup=keyboard([
            [("← Назад", "menu:back"), ("✔️ Продлить на период", "intercontract:extend:confirm")],
        ]),
    )


@router.callback_query(F.data == "intercontract:extend:confirm")
async def confirm_extend_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    try:
        result = allocator.extend_intercontract_break()
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)

    rows = []
    if result["available_in_salary_fund"] > 0:
        rows.append([("Заплатить себе из Фонда Зарплаты", "intercontract:salary")])
    if result["shortfall"] > 0:
        rows.extend([
            [("Добавить поступление", "menu:income")],
            [("Изменить жизнь в перерыве", "phaselife:fill:break")],
        ])
    rows.append([("← Главное меню", "menu:back")])

    shortfall_text = (
        "Фонда Зарплаты достаточно для плановой выплаты."
        if result["shortfall"] <= 0
        else
        f"До полной плановой суммы не хватает <b>{result['shortfall']} ₽</b>. "
        "Это предупреждение, а не автоматическое списание из других конвертов."
    )
    await callback.message.answer(
        "<b>ПЕРЕРЫВ ПРОДЛЁН</b>\n\n"
        f"Осталось расчётных периодов по текущему прогнозу — "
        f"<b>{result['periods_remaining']}</b>.\n"
        f"{shortfall_text}\n\n"
        "Когда появится точная информация о работе, просто начните рабочую часть. "
        "Фактическое событие заменит этот прогноз.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data == "intercontract:finish:confirm")
async def confirm_early_work_phase(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    await validate_work_phase_start(callback, allocator)


async def validate_work_phase_start(callback: CallbackQuery, allocator):
    work_life = allocator.settings.phase_life("work")
    if work_life is None or not work_life.completed:
        await callback.message.answer(
            "<b>СНАЧАЛА ЗАПОЛНИТЕ РАБОЧУЮ ЖИЗНЬ</b>\n\n"
            "Так Аллокатор будет знать ваши личные расходы во время работы.",
            reply_markup=keyboard([
                [("Заполнить рабочую жизнь", "phaselife:fill:work")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
    await complete_work_phase_start(callback, allocator, allow_early=True)


@router.callback_query(F.data == "intercontract:finish:force")
async def force_finish_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    await complete_work_phase_start(callback, allocator, allow_early=True)


async def complete_work_phase_start(callback: CallbackQuery, allocator, allow_early: bool = False):
    try:
        allocator.start_new_work_phase(allow_early=allow_early)
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        "<b>РАБОЧАЯ ЧАСТЬ НАЧАЛАСЬ</b>\n\n"
        "Проверьте, что все обязательства уплачены. Оставшиеся деньги на счёте «Зарплата» "
        "<b>после оплаты счетов</b> переведите в <b>Фонд Зарплаты</b>. Они пригодятся в следующем перерыве.\n\n"
        "Бытовой резерв и остальные финансовые конверты не трогайте.",
        reply_markup=keyboard([[("✔️ Хорошо", "menu:back")]]),
    )


@router.callback_query(F.data == "fundsalary:help")
async def show_fund_salary_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "<b>КАК РАБОТАЕТ ФОНД ЗАРПЛАТЫ</b>\n\n"
        "🏦 Фонд Зарплаты оплачивает обычную жизнь во время планового перерыва между контрактами.\n\n"
        "Каждый месяц:\n"
        "1. Начните новый расчётный период.\n"
        "2. Добавьте уже полученные внешние доходы, если они были.\n"
        "3. Нажмите «Заплатить себе из Фонда Зарплаты».\n"
        "4. Переведите предложенную сумму на карту для повседневных расходов.\n\n"
        "Контракт, подработку, подарок и другие внешние поступления добавляйте через «Новый доход» "
        "под их обычными названиями. Аллокатор не делит деньги по происхождению: все поступления "
        "учитываются в общем доходе текущего финансового цикла.\n\n"
        "Выплата из Фонда Зарплаты — внутренний перевод ваших денег, а не новый доход. Поэтому налог "
        "и повторное распределение не рассчитываются. Если деньги хранятся в валюте, обменяйте только "
        "необходимую для выплаты сумму.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

@router.callback_query(F.data == "period:new")
async def ask_new_period(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала нужно создать финансовый профиль через /start.")
        return

    await state.update_data(period_salary_remainder="0", period_salary_target="")
    await callback.message.answer(
        "<b>ЗАВЕРШИМ ТЕКУЩИЙ ПЕРИОД</b>\n\n"
        "После оплаты всех обязательств проверьте остаток на счёте «Зарплата».\n\n"
        "Если деньги остались, выберите, куда их перевести. Это ваш результат "
        "экономии за прошедший период.",
        reply_markup=keyboard([
            [("Осталось 0 ₽", "period:remainder:zero")],
            [("Указать остаток", "period:remainder:amount")],
            [("✖️ Отмена", "period:cancel")],
        ]),
    )


async def show_new_period_confirmation(
    message: Message, user_id: int, target_label: str = "", remainder: Decimal = Decimal("0")
):
    allocator = db.load_allocator(user_id)
    period_note = ""
    if allocator.state.period_status == "active" and allocator.state.period_ends_at:
        end = date.fromisoformat(allocator.state.period_ends_at)
        period_note = (
            f"\n\nТекущий период рассчитан до <b>{end.strftime('%d.%m.%Y')}</b>. "
            "Если начать новый период сейчас, прежний будет закрыт досрочно."
        )
    await message.answer(
        "📅 <b>НАЧАТЬ НОВЫЙ РАСЧЁТНЫЙ ПЕРИОД?</b>\n\n"
        "Будут обнулены только показатели текущего периода:\n"
        "🔄 Баланс жизни\n"
        "❤️ категории обязательной жизни\n"
        "💚 Бытовой резерв текущего периода\n"
        "💳 деньги, зарезервированные на минимальные платежи этого периода\n"
        "👛 доход и 🏛 налог текущего периода\n\n"
        "<b>Не сбрасываются:</b>\n"
        "🛡️ Подушка\n"
        "💰 общий объём направленных инвестиций\n"
        "⭐️ накопления по целям\n"
        "💳 остатки кредитов и общий объём досрочного погашения\n"
        "⚙️ настройки\n"
        "📜 история операций\n\n"
        "Для Цикличного (контрактного) профиля также сохраняются Фонд Зарплаты и общий доход "
        "текущего финансового цикла.\n\n"
        "Дата начала нового периода будет сохранена автоматически."
        + (
            f"\n\nОстаток «Зарплаты»: <b>{remainder} ₽</b>.\n"
            f"Направление: <b>{escape(target_label)}</b>."
            if target_label and remainder > 0 else ""
        )
        + period_note,
        reply_markup=keyboard([
            [("✅ Начать новый период", "period:confirm")],
            [("Отмена", "period:cancel")],
        ]),
    )


@router.callback_query(F.data == "period:remainder:zero")
async def period_remainder_zero(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(period_salary_remainder="0", period_salary_target="")
    await show_new_period_confirmation(callback.message, callback.from_user.id)


@router.callback_query(F.data == "period:remainder:amount")
async def period_remainder_amount_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PeriodClosingStates.salary_remainder)
    await callback.message.answer("Введите остаток на счёте «Зарплата».")


@router.message(PeriodClosingStates.salary_remainder)
async def period_remainder_amount_save(message: Message, state: FSMContext):
    amount = _decimal(message.text)
    if amount is None or amount <= 0:
        await message.answer("Введите сумму больше нуля.")
        return
    await state.update_data(period_salary_remainder=str(amount))
    allocator = db.load_allocator(message.from_user.id)
    rows = [[("Текущий финансовый приоритет", "period:target:priority")]]
    if allocator.settings.income_rhythm == "cyclic":
        rows.append([("Фонд Зарплаты", "period:target:salary_fund")])
    if allocator.settings.household_reserve > 0:
        rows.append([("Бытовой резерв", "period:target:household")])
    rows.append([("Подушка", "period:target:pillow")])
    if allocator.settings.needs_stabilizer:
        rows.append([("Стабилизатор дохода", "period:target:stabilizer")])

    active_mode = allocator.active_mode()
    if allocator.settings.goals and active_mode >= 3:
        rows.append([("Распределить по всем целям", "period:target:goals")])
        for index, goal in enumerate(allocator.settings.goals):
            rows.append([(f"Цель: {goal.name}", f"period:target:goal:{index}")])

    investment_mode = {
        "stable": 4,
        "piecework": 5,
        "cyclic": 7,
    }[allocator.profile_id]
    if active_mode >= investment_mode:
        rows.append([("Инвестиции", "period:target:investments")])
    rows.append([("← Назад", "period:new")])
    await message.answer(
        "<b>КУДА ПЕРЕВЕСТИ ОСТАТОК?</b>\n\n"
        "Выберите один конверт, одну конкретную цель или распределение по всем целям. "
        "Это внутренний перевод: он не считается новым доходом.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.startswith("period:target:"))
async def period_remainder_target(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":")
    target = ":".join(parts[2:])
    allocator = db.load_allocator(callback.from_user.id)
    labels = {
        "priority": "текущий финансовый приоритет",
        "salary_fund": "Фонд Зарплаты",
        "household": "Бытовой резерв",
        "pillow": "Подушка",
        "stabilizer": "Стабилизатор дохода",
        "goals": "все цели в выбранных пропорциях",
        "investments": "Инвестиции",
    }
    if target.startswith("goal:"):
        try:
            goal = allocator.settings.goals[int(target.split(":", 1)[1])]
        except (ValueError, IndexError):
            await callback.message.answer("Цель больше не найдена. Выберите направление заново.")
            return
        label = f"цель «{goal.name}»"
    else:
        label = labels.get(target, "текущий финансовый приоритет")
    data = await state.get_data()
    remainder = Decimal(str(data.get("period_salary_remainder", "0")))
    await state.update_data(period_salary_target=target, period_salary_target_label=label)
    await show_new_period_confirmation(
        callback.message, callback.from_user.id, label, remainder
    )

@router.callback_query(F.data == "period:cancel")
async def cancel_new_period(callback: CallbackQuery):
    await callback.answer("Отменено")
    await callback.message.answer(
        "Расчётный период не изменён.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

@router.callback_query(F.data == "period:confirm")
async def confirm_new_period(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Финансовый профиль не найден.")
        return

    allocator.reset_period()
    data = await state.get_data()
    remainder = Decimal(str(data.get("period_salary_remainder", "0")))
    try:
        target_name = allocator.transfer_salary_remainder(
            remainder,
            data.get("period_salary_target", "priority"),
        )
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    work_months_left = allocator.advance_work_month()
    period_start, period_end = allocator.state.activate_budget_period(date.today())
    db.save_allocator(callback.from_user.id, allocator)
    db.save_operation(
        callback.from_user.id,
        "period_reset",
        {
            "started_at": allocator.state.period_started_at,
            "message": "Начат новый расчётный период",
            "salary_remainder": str(remainder),
            "salary_remainder_target": target_name,
            "internal_transfer": True,
        },
    )
    await state.clear()

    phase_text = ""
    if allocator.settings.income_rhythm == "cyclic" and allocator.state.current_cycle_phase == "work":
        phase_text = (
            f"\n\nВ рабочей части осталось: <b>{work_months_left} мес.</b>"
            + (
                "\nПлановая рабочая часть завершена. Когда работа фактически закончится, нажмите «Начать перерыв»."
                if work_months_left <= 0 else ""
            )
        )
    confirmation_text = (
        "✅ <b>НОВЫЙ РАСЧЁТНЫЙ ПЕРИОД НАЧАТ</b>\n\n"
        f"Период: <b>{period_start.strftime('%d.%m.%Y')} — {period_end.strftime('%d.%m.%Y')}</b>.\n\n"
        "Баланс жизни и месячные категории начаты заново.\n"
        "Подушка, цели, инвестиции, кредиты и история сохранены. Для Цикличного (контрактного) "
        "профиля Фонд Зарплаты и счётчик полного финансового цикла тоже не сбрасываются."
        + (
            f"\n\nОстаток «Зарплаты» <b>{remainder} ₽</b> перенесён в <b>{escape(target_name)}</b>."
            if remainder > 0 and target_name else ""
        )
        + phase_text
    )
    await callback.message.answer(
        confirmation_text,
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
