from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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
async def pay_intercontract_salary(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    try:
        amount = allocator.pay_intercontract_salary()
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)
    transfer_text = (
        f"Из Фонда Зарплаты в Баланс жизни переведено <b>{amount} ₽</b>."
        if amount > 0
        else "Перевод из Фонда Зарплаты не потребовался."
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
async def ask_new_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала нужно создать финансовый профиль через /start.")
        return

    period_note = ""
    if allocator.state.period_status == "active" and allocator.state.period_ends_at:
        end = date.fromisoformat(allocator.state.period_ends_at)
        period_note = (
            f"\n\nТекущий период рассчитан до <b>{end.strftime('%d.%m.%Y')}</b>. "
            "Если начать новый период сейчас, прежний будет закрыт досрочно."
        )
    await callback.message.answer(
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
        f"{period_note}",
        reply_markup=keyboard([
            [("✅ Начать новый период", "period:confirm")],
            [("Отмена", "period:cancel")],
        ]),
    )

@router.callback_query(F.data == "period:cancel")
async def cancel_new_period(callback: CallbackQuery):
    await callback.answer("Отменено")
    await callback.message.answer(
        "Расчётный период не изменён.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

@router.callback_query(F.data == "period:confirm")
async def confirm_new_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Финансовый профиль не найден.")
        return

    allocator.reset_period()
    work_months_left = allocator.advance_work_month()
    period_start, period_end = allocator.state.activate_budget_period(date.today())
    db.save_allocator(callback.from_user.id, allocator)
    db.save_operation(
        callback.from_user.id,
        "period_reset",
        {"started_at": allocator.state.period_started_at, "message": "Начат новый расчётный период"},
    )

    phase_text = ""
    if allocator.settings.income_rhythm == "cyclic" and allocator.state.current_cycle_phase == "work":
        phase_text = (
            f"\n\nВ рабочей части осталось: <b>{work_months_left} мес.</b>"
            + (
                "\nПлановая рабочая часть завершена. Когда работа фактически закончится, нажмите «Начать перерыв»."
                if work_months_left <= 0 else ""
            )
        )
    await callback.message.answer(
        "✅ <b>НОВЫЙ РАСЧЁТНЫЙ ПЕРИОД НАЧАТ</b>\n\n"
        f"Период: <b>{period_start.strftime('%d.%m.%Y')} — {period_end.strftime('%d.%m.%Y')}</b>.\n\n"
        "Баланс жизни и месячные категории начаты заново.\n"
        "Подушка, цели, инвестиции, кредиты и история сохранены. Для Цикличного (контрактного) "
        "профиля Фонд Зарплаты и счётчик полного финансового цикла тоже не сбрасываются."
        f"{phase_text}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
