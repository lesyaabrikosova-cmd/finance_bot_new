from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from financial_engine import MODE_NAMES, MODE_TITLES, fmt_money
from storage import db
from ui import main_menu_keyboard

router = Router()

EMPLOYEE_MODES = {
    1: ("🏆➖➖➖", "В срочном порядке формируй минимальную Подушку на 1–2 месяца закрытия обязательств (включая платежи по кредитам). Ни о каком досрочном погашении не может быть и речи. Про инвестиции и цели вообще забудь. Мы сейчас спасаем твою жопу."),
    2: ("🏆🏆➖➖", "Твоя жопа в минимальной безопасности, а значит можно все средства бросить на досрочное погашение кредитов. Если кредитов несколько, советую гасить по методу «Лавина», и уменьшать срок, а не платёж — так ты сэкономишь больше денег. Цели и инвестиции не доступны."),
    3: ("🏆🏆🏆➖", "Продолжай формировать подушку безопасности на случай форс-мажора, который, поверь, может случиться! Рекомендую установить размер подушки от 3 до 6 месяцев обязательств. Инвестиции и цели недоступны, но скоро это изменится."),
    6: ("🏆🏆🏆🏆", "Ты прошёл непростой путь, чтобы обрести финансовую безопасность. Теперь работай над тем, чтоб обрести финансовую свободу. Твои цели будут быстро копиться, и потребительский кредит тебе станет не нужен. Инвестиции рекомендую направить на пенсию, так как государство не сможет позаботиться о тебе в старости. С ростом дохода старайся не увеличивать потребление, иначе ты перестанешь богатеть, как 90% людей."),
}

FREELANCER_MODES = {
    1: ("🏆➖➖➖➖➖", EMPLOYEE_MODES[1][1]),
    2: ("🏆🏆➖➖➖➖", EMPLOYEE_MODES[2][1]),
    3: ("🏆🏆🏆➖➖➖", "Продолжай формировать подушку безопасности на случай форс-мажора, который, поверь, может случиться! Рекомендую установить размер подушки от 6 до 12 месяцев обязательств. Инвестиции и цели недоступны, но скоро это изменится."),
    4: ("🏆🏆🏆🏆➖➖", "Ты фрилансер, а это само по себе рисково. Поэтому твоя подушка не обычная, а двухуровневая. Первый уровень — форс-мажорный — у тебя уже накоплен, постарайся к нему прикасаться только тогда, когда реально случилась катастрофа: авария, операция, смерть родственника, потеря жилья, потеря дохода, война, вынужденный переезд, пандемия… В нашей реальности это всё может произойти в течение одного года.\n\nВторой уровень — Стабилизатор Дохода. Это дополнительная сумма на Подушке, которая равна твоей Устойчивой Жизни. Стабилизатор нужен на случай сезонной просадки заказов, больничного или отпуска, которые тебе никто не оплачивает. Это не форс-мажор, не путай, это вполне цикличные события, которые выбивают из колеи 90% фрилансеров. В «тощие» месяцы можешь спокойно взять из подушки недостающую сумму (в рамках среднего дохода), в «жирные» месяцы — придётся его восполнить. Инвестиции и цели пока не доступны."),
    5: ("🏆🏆🏆🏆🏆➖", "Твоего стабилизатора уже хватит на то, чтобы закрыть месячные обязательства без заимствования из форс-мажорной подушки. Осталось чуть-чуть. Но чтоб копилось не так грустно — ты уже можешь установить Цели. Они будут копиться в пол силы. Рекомендую в цели установить Подарки близким от 3 до 7%, Отпуск, Амортизацию техники (заранее копить на новый телефон, ноут)."),
    6: ("🏆🏆🏆🏆🏆🏆", "Ты прошёл длинный путь, чтобы обрести финансовую безопасность. Теперь работай над тем, чтоб обрести финансовую свободу. Твои цели теперь копятся быстрее, а потребительский кредит больше не нужен. Инвестиции рекомендую направить на пенсию, так как государство не сможет позаботиться о тебе в старости. С ростом дохода старайся не увеличивать потребление, иначе ты перестанешь богатеть, как 90% людей."),
}

ABOUT_TEXT = (
    "✨ <b>ПОЧЕМУ ЭТО РАБОТАЕТ</b>\n\n"
    "Большинство финансовых систем начинают с прошлого: сколько ты потратил, где перерасходовал и в какой категории опять всё пошло не по плану.\n\n"
    "<b>Аллокатор работает с будущим.</b> Деньги получают задачу в момент поступления — ещё до того, как успевают раствориться в повседневности.\n\n"
    "Я собрала эту систему из принципов примерно двух десятков финансовых подходов и адаптировала их в одну последовательную механику. Здесь одновременно учитываются обязательная жизнь, бытовой резерв, долги, финансовая подушка, нестабильность фриланса, цели и инвестиции.\n\n"
    "Главное отличие — <b>правила меняются вместе с твоим финансовым состоянием</b>. Человеку без минимальной защиты система не предлагает изображать инвестора. Человеку с дорогими долгами — не предлагает копить на хотелки в ущерб процентам. А когда безопасность уже построена, деньги автоматически начинают работать на цели и капитал.\n\n"
    "Это не наказание за траты и не попытка жить по таблице. Это способ снять с себя десятки мелких решений: сколько можно потратить, пора ли инвестировать, можно ли сейчас досрочно гасить кредит, не слишком ли мало в подушке.\n\n"
    "Аллокатор превращает эти вопросы в систему правил. Ты вводишь реальные поступления, а система показывает, какую работу должен выполнить каждый рубль."
)

HELP_TEXT = (
    "❓ <b>КАК ПОЛЬЗОВАТЬСЯ БОТОМ</b>\n\n"
    "💰 <b>Добавить доход</b> — внести поступление и получить распределение.\n\n"
    "📊 <b>Балансы</b> — текущее положение дел и аналитика расчётного периода.\n\n"
    "🧭 <b>Мой режим</b> — уровень, описание и остаток до следующего режима.\n\n"
    "📅 <b>Новый расчётный период</b> — начать новый период вручную.\n\n"
    "⚙️ <b>Настройки</b> — изменить Подушку, расходы, категории и проценты.\n\n"
    "✨ <b>Почему это работает</b> — идея авторского метода."
)


def rub(value) -> str:
    return f"{fmt_money(Decimal(str(value)))} ₽"


def pct(amount: Decimal, income: Decimal) -> str:
    if income <= 0:
        return "0,00%"
    return f"{(amount / income * Decimal('100')):.2f}%".replace(".", ",")


def current_period_allocations(allocator) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    started_at = allocator.state.period_started_at
    start_date = None
    if started_at:
        try:
            start_date = datetime.fromisoformat(started_at).date()
        except ValueError:
            start_date = None

    for operation in allocator.state.distribution_history:
        if operation.get("type") != "income_distribution":
            continue
        if start_date:
            try:
                op_date = date.fromisoformat(str(operation.get("date", ""))[:10])
                if op_date < start_date:
                    continue
            except ValueError:
                pass
        for key, value in operation.get("allocations", {}).items():
            result[key] = result.get(key, Decimal("0")) + Decimal(str(value))
    return result


async def show_menu(message: Message, state: FSMContext):
    await state.clear()
    allocator = db.load_allocator(message.from_user.id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль командой /start.")
        return
    await message.answer("🧪 <b>ФИНАНСОВЫЙ АЛЛОКАТОР</b>\n\nВыберите действие.", reply_markup=main_menu_keyboard())


@router.message(Command("menu"))
async def command_menu(message: Message, state: FSMContext):
    await show_menu(message, state)


@router.callback_query(F.data == "menu:back")
async def menu_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("🧪 <b>ФИНАНСОВЫЙ АЛЛОКАТОР</b>\n\nВыберите действие.", reply_markup=main_menu_keyboard())


async def send_mode(message: Message, telegram_id: int):
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль через /start.")
        return
    s = allocator.settings
    mode = allocator.active_mode()
    mapping = FREELANCER_MODES if s.employment_type == "Фрилансер" else EMPLOYEE_MODES
    reward, description = mapping[mode]
    debt_profile = "с долгами" if any(c.active for c in s.credits) else "без долгов"
    next_info = allocator.next_mode_info()
    next_text = (
        f"\n\n🎯 До следующего режима осталось: <b>{rub(next_info['remaining'])}</b>"
        if next_info else "\n\n🏁 <b>Это максимальный режим вашей текущей траектории.</b>"
    )
    await message.answer(
        "🧭 <b>МОЙ РЕЖИМ</b>\n\n"
        f"👤 Профиль: <b>{escape(s.employment_type)}, {debt_profile}</b>\n\n"
        f"Уровень: <b>{MODE_NAMES[mode]}</b>\n"
        f"Награда: <b>{reward}</b>\n"
        f"Название: <b>{escape(MODE_TITLES[mode])}</b>\n\n"
        f"{escape(description)}{next_text}",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("state"))
async def command_state(message: Message, state: FSMContext):
    await state.clear()
    await send_mode(message, message.from_user.id)


@router.callback_query(F.data == "menu:state")
async def menu_state(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await send_mode(callback.message, callback.from_user.id)


async def send_balances(message: Message, telegram_id: int):
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль через /start.")
        return
    s, st = allocator.settings, allocator.state
    income = Decimal(st.period_income)
    alloc = current_period_allocations(allocator)
    pillow_period = alloc.get("Подушка", Decimal("0"))
    invest_period = alloc.get("Инвестиции", Decimal("0"))
    early_period = alloc.get("Досрочное", Decimal("0"))
    household_period = alloc.get("Бытовой резерв", Decimal("0"))
    minimum_period = alloc.get("Мин. платеж", Decimal("0"))
    life_items = sorted((k.split(":", 1)[1], v) for k, v in alloc.items() if k.startswith("КЖ:"))
    goal_items = sorted((k.split(":", 1)[1], v) for k, v in alloc.items() if k.startswith("Цели:"))
    until_kzh = max(Decimal("0"), s.critical_life - st.life_balance)
    until_uzh = max(Decimal("0"), s.household_life - st.life_balance)
    next_info = allocator.next_mode_info()

    lines = [
        "📊 <b>БАЛАНСЫ — ТЕКУЩИЙ РАСЧЁТНЫЙ ПЕРИОД</b>", "",
        f"👛 Доход итого: <b>{rub(income)}</b>",
        f"🏛 Налог: <b>{rub(st.period_tax)}</b> ({pct(Decimal(st.period_tax), income)})", "",
        f"🛟 Подушка за период: <b>{rub(pillow_period)}</b> ({pct(pillow_period, income)})",
        f"🛟 Подушка итого: <b>{rub(st.pillow_balance)}</b>", "",
        f"💰 Инвестиции за период: <b>{rub(invest_period)}</b> ({pct(invest_period, income)})",
        f"💰 Всего направлено в инвестиции: <b>{rub(st.investments)}</b>", "",
        f"🔄 Баланс жизни сейчас: <b>{rub(st.life_balance)}</b>",
    ]
    for name, amount in life_items:
        lines.append(f"❤️ {escape(name)}: <b>{rub(amount)}</b> ({pct(amount, income)})")
    lines.append(f"💚 Бытовой резерв: <b>{rub(household_period)}</b> ({pct(household_period, income)})")
    for name, amount in goal_items:
        lines.append(f"⭐️ {escape(name)}: <b>{rub(amount)}</b> ({pct(amount, income)})")

    if s.credits:
        debt_total = sum((c.principal_balance for c in s.credits if c.active), Decimal("0"))
        lines += ["", f"💳 Минимальные платежи: <b>{rub(minimum_period)}</b> ({pct(minimum_period, income)})", f"💳 Досрочно за период: <b>{rub(early_period)}</b> ({pct(early_period, income)})", f"💳 Досрочно погашено всего: <b>{rub(st.early_repayment)}</b>", f"💳 Остаток активных долгов: <b>{rub(debt_total)}</b>"]

    lines += ["", "🎯 <b>АКТУАЛЬНЫЕ ПОРОГИ</b>", f"До 🔴 КЖ: <b>{rub(until_kzh)}</b>", f"До 🟢 УЖ: <b>{rub(until_uzh)}</b>"]
    if next_info:
        lines.append(f"До следующего режима {next_info['next_name']}: <b>{rub(next_info['remaining'])}</b>")
    else:
        lines.append("До следующего режима: <b>максимальный режим достигнут</b>")

    if s.developer_mode:
        lines += ["", "🛠 <b>СЛОИ ПОДУШКИ</b>", f"МП: {rub(st.pillow_minimum)} / {rub(s.minimum_reserve_limit)}", f"ФМ: {rub(st.pillow_force_majeure)} / {rub(s.force_majeure_limit)}", f"СтабД: {rub(st.pillow_stabilizer)} / {rub(s.stabilizer_full_limit)}"]

    await message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "menu:analytics")
async def menu_balances(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await send_balances(callback.message, callback.from_user.id)


@router.message(Command("help"))
async def command_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "menu:about")
async def menu_about(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(ABOUT_TEXT, reply_markup=main_menu_keyboard())
