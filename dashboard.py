from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from financial_engine import (
    MODE_NAMES,
    MODE_TITLES,
    fmt_money,
)
from storage import db
from ui import main_menu_keyboard


router = Router()


# ============================================================
# ОПИСАНИЯ РЕЖИМОВ
# ============================================================

EMPLOYEE_MODES = {
    1: (
        "🏆➖➖➖",
        (
            "Сейчас главная задача — перестать жить без страховки. "
            "Сначала собери минимальную Подушку на 1–2 месяца "
            "Критического минимума. Пока она не готова, досрочное "
            "погашение долгов, инвестиции и цели ставим на паузу. "
            "Сначала защищаем твою жопу от ситуации, когда любая "
            "поломка, болезнь или внезапный расход снова отправляет "
            "тебя в кредит."
        ),
    ),
    2: (
        "🏆🏆➖➖",
        (
            "Минимальная страховка уже есть — теперь пора избавляться "
            "от долгов. Свободные деньги Аллокатор направляет на "
            "досрочное погашение. Если кредитов несколько, стратегия "
            "«Лавина» обычно выгоднее: сначала гасим самый дорогой "
            "по ставке. Если банк предлагает выбор, уменьшение срока "
            "чаще экономит больше процентов. Инвестиции и цели пока "
            "подождут: сначала освобождаем будущий доход от старых долгов."
        ),
    ),
    3: (
        "🏆🏆🏆➖",
        (
            "Долгов больше нет. Теперь строим полноценную "
            "форс-мажорную Подушку — деньги на действительно серьёзные "
            "события: потерю работы, болезнь, аварийный переезд и другие "
            "ситуации, которые невозможно нормально запланировать. "
            "Для наёмного ориентир — несколько месяцев Критического "
            "минимума; точный размер ты выбрал в настройках. Пока "
            "Подушка не заполнена, она остаётся главным финансовым "
            "приоритетом."
        ),
    ),
    6: (
        "🏆🏆🏆🏆",
        (
            "Финансовый фундамент готов: долгов нет, форс-мажорная "
            "Подушка собрана. Теперь деньги могут полноценно работать "
            "на будущее — инвестиции, крупные цели, отпуск, техника, "
            "образование и другие планы. Твоя задача на этом уровне — "
            "не просто больше зарабатывать, а не позволять расходам "
            "расти вслед за каждым повышением дохода. Именно разница "
            "между тем, что ты можешь потратить, и тем, что действительно "
            "тратишь, постепенно превращается в капитал."
        ),
    ),
}


FREELANCER_MODES = {
    1: (
        "🏆➖➖➖➖➖",
        (
            "Доход фрилансера может исчезнуть быстрее, чем зарплата "
            "наёмного сотрудника, поэтому начинать без минимальной "
            "страховки особенно опасно. Сначала собери Подушку на "
            "1–2 месяца Критического минимума. До этого момента не "
            "распыляем деньги на досрочные погашения, инвестиции и цели. "
            "Первая задача — сделать так, чтобы один плохой месяц не "
            "превращался в новый долг."
        ),
    ),
    2: (
        "🏆🏆➖➖➖➖",
        (
            "Минимальная Подушка уже защищает от мелких финансовых "
            "ударов. Теперь освобождаемся от долгов. Аллокатор направляет "
            "свободные деньги на досрочное погашение, чтобы как можно "
            "быстрее убрать обязательные платежи из будущих месяцев. "
            "Если кредитов несколько, «Лавина» обычно позволяет меньше "
            "переплатить. Цели и инвестиции пока не открываем: сначала "
            "возвращаем себе финансовую манёвренность."
        ),
    ),
    3: (
        "🏆🏆🏆➖➖➖",
        (
            "Долги закрыты. Следующая задача — форс-мажорная Подушка. "
            "Для фрилансера она особенно важна, поэтому её запас обычно "
            "больше, чем у человека с регулярной зарплатой. Эти деньги "
            "предназначены не для сезонной просадки заказов и не для "
            "отпуска, а для настоящего форс-мажора: серьёзной болезни, "
            "аварии, потери жилья, резкой потери источника дохода или "
            "вынужденного переезда. Когда этот слой заполнится, начнём "
            "защищаться уже от обычной нестабильности фриланса."
        ),
    ),
    4: (
        "🏆🏆🏆🏆➖➖",
        (
            "Форс-мажорная Подушка готова. Но для фрилансера этого мало: "
            "не каждый плохой месяц — катастрофа. Бывают сезонные просадки, "
            "больничные, отпуск, паузы между проектами. Поэтому теперь мы "
            "строим второй защитный слой — Стабилизатор дохода.\n\n"
            "Первая цель Стабилизатора — накопить сумму, равную твоему "
            "Критическому минимуму. Тогда один слабый месяц можно будет "
            "пережить без кредитов и без вторжения в форс-мажорную Подушку. "
            "Инвестиции и цели пока не торопим: сначала создаём эту базовую "
            "устойчивость."
        ),
    ),
    5: (
        "🏆🏆🏆🏆🏆➖",
        (
            "Стабилизатор уже способен закрыть твой Критический минимум — "
            "это серьёзный уровень защиты. Теперь доводим его до полного "
            "размера, равного Устойчивой жизни: Критический минимум плюс "
            "Бытовой резерв.\n\n"
            "На этом этапе система уже может понемногу направлять деньги "
            "в цели и инвестиции, поэтому жизнь перестаёт быть бесконечным "
            "«сначала ещё чуть-чуть безопасности». Но часть денег всё ещё "
            "идёт в Подушку, пока Стабилизатор не станет полноценным. "
            "Ещё один шаг — и финансовая защита будет собрана полностью."
        ),
    ),
    6: (
        "🏆🏆🏆🏆🏆🏆",
        (
            "Теперь у тебя есть не просто Подушка, а полноценная система "
            "защиты: форс-мажорный резерв на большие неприятности и "
            "Стабилизатор дохода, способный покрыть Устойчивую жизнь во "
            "время обычной просадки заработка.\n\n"
            "Это значит, что деньги больше не нужно постоянно бросать "
            "на строительство защиты. Аллокатор может активнее направлять "
            "их в инвестиции и цели. В хорошие месяцы ты создаёшь капитал "
            "и финансируешь будущее; в слабые — у тебя уже есть запас, "
            "который позволяет не паниковать и не залезать в кредиты. "
            "Именно здесь нестабильный доход перестаёт означать "
            "нестабильную жизнь."
        ),
    ),
}


# ============================================================
# ТЕКСТ ОТ РАЗРАБОТЧИКА
# ============================================================

ABOUT_TEXT = (
    "<b>ОТ РАЗРАБОТЧИКА</b>\n\n"
    "Большинство финансовых систем начинают с прошлого: "
    "сколько ты потратил, где перерасходовал и в какой "
    "категории опять всё пошло не по плану.\n\n"
    "<b>Аллокатор работает с будущим.</b> Деньги получают "
    "задачу в момент поступления — ещё до того, как успевают "
    "раствориться в повседневности.\n\n"
    "Я собрала эту систему из принципов множества финансовых "
    "подходов и адаптировала их в одну последовательную "
    "механику. Здесь одновременно учитываются обязательная "
    "жизнь, бытовой резерв, долги, финансовая подушка, "
    "нестабильность фриланса, цели и инвестиции.\n\n"
    "<b>Главное отличие — правила меняются вместе с твоим "
    "финансовым состоянием.</b> Человеку без минимальной "
    "защиты система не предлагает изображать инвестора. "
    "Человеку с дорогими долгами — не предлагает копить на "
    "хотелки в ущерб процентам. А когда безопасность уже "
    "построена, деньги начинают работать на цели и капитал.\n\n"
    "Ты вводишь реальные поступления, а система показывает, "
    "какую работу должен выполнить каждый рубль."
)


HELP_TEXT = (
    "<b>ПОМОЩЬ</b>\n\n"
    "Новый доход — внести поступление и получить распределение.\n\n"
    "Балансы — посмотреть текущее положение дел и аналитику "
    "расчётного периода.\n\n"
    "Режим — посмотреть текущий финансовый уровень, описание "
    "и остаток до следующего режима.\n\n"
    "Кредиты — посмотреть кредиты и остатки.\n\n"
    "Цели — посмотреть цели и накопления.\n\n"
    "Новый расчетный период — вручную начать новый период.\n\n"
    "Настройки — изменить Подушку, Критический минимум, "
    "категории, налог и проценты.\n\n"
    "От разработчика — идея авторского метода."
)


# ============================================================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# ============================================================

def D(value) -> Decimal:
    return Decimal(str(value))


def rub(value) -> str:
    return f"{fmt_money(D(value))} ₽"


def pct(
    amount: Decimal,
    income: Decimal,
) -> str:

    if income <= 0:
        return "0,00%"

    result = (
        amount
        / income
        * Decimal("100")
    )

    return (
        f"{result:.2f}%"
        .replace(".", ",")
    )


def operation_is_in_current_period(
    operation: dict,
    period_started_at: str | None,
) -> bool:

    if not period_started_at:
        return True

    try:
        start_date = datetime.fromisoformat(
            period_started_at
        ).date()
    except ValueError:
        return True

    raw_date = operation.get("date")

    if not raw_date:
        return True

    try:
        operation_date = date.fromisoformat(
            str(raw_date)[:10]
        )
    except ValueError:
        return True

    return operation_date >= start_date


def get_period_allocations(
    allocator,
    telegram_id: int,
) -> dict[str, Decimal]:
    """
    Возвращает распределения текущего расчётного периода.

    1. Если period_allocations уже сохранён в state — используем его.
    2. Если текущие данные ещё не сохранены в этом поле —
       восстанавливаем их из постоянного SQLite operation_log
       после последнего period_reset.
    """

    stored = getattr(
        allocator.state,
        "period_allocations",
        None,
    )

    if stored:
        return {
            key: D(value)
            for key, value in stored.items()
        }

    result: dict[str, Decimal] = {}

    operations = db.load_operations(
        telegram_id,
        limit=1000,
    )

    # Операции идут от новых к старым.
    # Всё после последнего period_reset относится
    # к текущему расчётному периоду.
    for operation in operations:

        operation_type = operation.get(
            "type"
        )

        if operation_type == "period_reset":
            break

        if operation_type != "income_distribution":
            continue

        payload = (
            operation.get("payload")
            or {}
        )

        allocations = (
            payload.get("allocations")
            or {}
        )

        for key, value in allocations.items():
            result[key] = (
                result.get(
                    key,
                    Decimal("0"),
                )
                + D(value)
            )

    return result


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

async def show_menu(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    allocator = db.load_allocator(
        message.from_user.id
    )

    if allocator is None:

        await message.answer(
            "Сначала создайте финансовый профиль "
            "командой /start."
        )

        return

    await message.answer(
        "<b>ГЛАВНОЕ МЕНЮ</b>",
        reply_markup=main_menu_keyboard(),
    )


@router.message(
    Command("menu")
)
async def command_menu(
    message: Message,
    state: FSMContext,
):

    await show_menu(
        message,
        state,
    )


@router.callback_query(
    F.data == "menu:back"
)
async def menu_back(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        "<b>ГЛАВНОЕ МЕНЮ</b>",
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# РЕЖИМ
# ============================================================

async def send_mode(
    message: Message,
    telegram_id: int,
):

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:

        await message.answer(
            "Сначала создайте финансовый профиль "
            "через /start."
        )

        return

    settings = allocator.settings

    mode = allocator.active_mode()

    mapping = (
        FREELANCER_MODES
        if settings.employment_type == "Фрилансер"
        else EMPLOYEE_MODES
    )

    reward, description = mapping[
        mode
    ]

    debt_profile = (
        "с долгами"
        if any(
            credit.active
            for credit in settings.credits
        )
        else "без долгов"
    )

    next_info = (
        allocator.next_mode_info()
    )

    if next_info:

        next_text = (
            "\n\nДо следующего режима осталось: "
            f"<b>{rub(next_info['remaining'])}</b>"
        )

    else:

        next_text = (
            "\n\n<b>Максимальный режим достигнут.</b>"
        )

    await message.answer(
        "<b>ТЕКУЩИЙ РЕЖИМ</b>\n\n"
        f"Профиль: "
        f"<b>{escape(settings.employment_type)}, "
        f"{debt_profile}</b>\n\n"
        f"Уровень: <b>{MODE_NAMES[mode]}</b>\n"
        f"Награда: <b>{reward}</b>\n"
        f"Название: "
        f"<b>{escape(MODE_TITLES[mode])}</b>\n\n"
        f"{escape(description)}"
        f"{next_text}",
        reply_markup=main_menu_keyboard(),
    )


@router.message(
    Command("state")
)
async def command_state(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await send_mode(
        message,
        message.from_user.id,
    )


@router.callback_query(
    F.data == "menu:state"
)
async def menu_state(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await send_mode(
        callback.message,
        callback.from_user.id,
    )


# ============================================================
# БАЛАНСЫ
# ============================================================

async def send_balances(
    message: Message,
    telegram_id: int,
):

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:

        await message.answer(
            "Сначала создайте финансовый профиль "
            "через /start."
        )

        return

    settings = allocator.settings
    state = allocator.state

    income = D(
        state.period_income
    )

    tax = D(
        state.period_tax
    )

    allocations = (
        get_period_allocations(
            allocator,
            telegram_id,
        )
    )

    # --------------------------------------------------------
    # Основные суммы периода
    # --------------------------------------------------------

    pillow_period = allocations.get(
        "Подушка",
        Decimal("0"),
    )

    investment_period = allocations.get(
        "Инвестиции",
        Decimal("0"),
    )

    household_period = allocations.get(
        "Бытовой резерв",
        Decimal("0"),
    )

    minimum_period = allocations.get(
        "Мин. платеж",
        Decimal("0"),
    )

    early_period = allocations.get(
        "Досрочное",
        Decimal("0"),
    )

    # --------------------------------------------------------
    # Заголовок периода
    # --------------------------------------------------------

    period_label = (
        "текущий расчётный период"
    )

    period_started_at = getattr(
        state,
        "period_started_at",
        None,
    )

    if period_started_at:

        try:

            started = datetime.fromisoformat(
                period_started_at
            )

            period_label = (
                f"с {started.strftime('%d.%m.%Y')}"
            )

        except ValueError:
            pass

    lines = [
        f"<b>БАЛАНСЫ — {period_label.upper()}</b>",
        "",
        f"👛 Доход итого: "
        f"<b>{rub(income)}</b>",
        f"🏛️ Налог: "
        f"<b>{rub(tax)}</b> "
        f"({pct(tax, income)})",
        "",
        f"🛟 Подушка за период: "
        f"<b>{rub(pillow_period)}</b> "
        f"({pct(pillow_period, income)})",
        f"🛟 Подушка итого: "
        f"<b>{rub(state.pillow_balance)}</b>",
        "",
        f"📈 Инвестиции за период: "
        f"<b>{rub(investment_period)}</b> "
        f"({pct(investment_period, income)})",
        f"📈 Всего направлено в инвестиции: "
        f"<b>{rub(state.investments)}</b>",
        "",
        f"🔄 Баланс жизни: "
        f"<b>{rub(state.life_balance)}</b>",
        "",
        "<b>КАТЕГОРИИ КРИТИЧЕСКОГО МИНИМУМА</b>",
    ]

    # --------------------------------------------------------
    # Каждая категория КЖ
    #
    # period_life_topups — самый надёжный источник именно
    # для КЖ текущего расчётного периода.
    # Показываем ВСЕ категории настроек, даже если там 0 ₽.
    # --------------------------------------------------------

    category_names = list(
        settings.life_categories.keys()
    )

    if "Зарплата" not in category_names:

        category_names.append(
            "Зарплата"
        )

    for name in category_names:

        amount = D(
            state.period_life_topups.get(
                name,
                Decimal("0"),
            )
        )

        lines.append(
            f"❤️ {escape(name)}: "
            f"<b>{rub(amount)}</b> "
            f"({pct(amount, income)})"
        )

    # --------------------------------------------------------
    # Бытовой резерв
    # --------------------------------------------------------

    lines.extend([
        "",
        f"💚 Бытовой резерв: "
        f"<b>{rub(household_period)}</b> "
        f"({pct(household_period, income)})",
        "",
        "<b>ЦЕЛИ</b>",
    ])

    # --------------------------------------------------------
    # Каждая цель
    #
    # Показываем ВСЕ цели из настроек, даже если туда
    # в текущем периоде пока ничего не распределено.
    # --------------------------------------------------------

    if settings.goals:

        for goal in settings.goals:

            amount = allocations.get(
                f"Цели:{goal.name}",
                Decimal("0"),
            )

            lines.append(
                f"⭐️ {escape(goal.name)}: "
                f"<b>{rub(amount)}</b> "
                f"({pct(amount, income)})"
            )

    else:

        amount = allocations.get(
            "Цели:ЦЕЛИ (всего)",
            Decimal("0"),
        )

        lines.append(
            f"⭐️ Цели (всего): "
            f"<b>{rub(amount)}</b> "
            f"({pct(amount, income)})"
        )

    # --------------------------------------------------------
    # Кредиты
    # --------------------------------------------------------

    if settings.credits:

        active_debt = sum(
            (
                credit.principal_balance
                for credit
                in settings.credits
                if credit.active
            ),
            Decimal("0"),
        )

        lines.extend([
            "",
            "<b>КРЕДИТЫ</b>",
            f"💳 Минимальные платежи за период: "
            f"<b>{rub(minimum_period)}</b> "
            f"({pct(minimum_period, income)})",
            f"💳 Досрочно за период: "
            f"<b>{rub(early_period)}</b> "
            f"({pct(early_period, income)})",
            f"💳 Досрочно погашено всего: "
            f"<b>{rub(state.early_repayment)}</b>",
            f"💳 Остаток активных долгов: "
            f"<b>{rub(active_debt)}</b>",
        ])

    # --------------------------------------------------------
    # Пороги
    # --------------------------------------------------------

    critical_minimum = D(
        settings.critical_life
    )

    sustainable_life = (
        D(settings.critical_life)
        + D(settings.household_reserve)
    )

    life_balance = D(
        state.life_balance
    )

    until_kzh = max(
        Decimal("0"),
        critical_minimum
        - life_balance,
    )

    until_uzh = max(
        Decimal("0"),
        sustainable_life
        - life_balance,
    )

    next_info = (
        allocator.next_mode_info()
    )

    lines.extend([
        "",
        "<b>ПОРОГИ</b>",
        f"🎯 До Критического минимума осталось: "
        f"<b>{rub(until_kzh)}</b>",
        f"🎯 До Устойчивой жизни осталось: "
        f"<b>{rub(until_uzh)}</b>",
    ])

    if next_info:

        lines.append(
            f"🎯 До следующего режима "
            f"{next_info['next_name']}: "
            f"<b>{rub(next_info['remaining'])}</b>"
        )

    else:

        lines.append(
            "🎯 До следующего режима: "
            "<b>максимальный режим достигнут</b>"
        )

    # --------------------------------------------------------
    # Только режим разработчика
    # --------------------------------------------------------

    if settings.developer_mode:

        lines.extend([
            "",
            "<b>СЛОИ ПОДУШКИ — "
            "РЕЖИМ РАЗРАБОТЧИКА</b>",
            f"МП: "
            f"{rub(state.pillow_minimum)} / "
            f"{rub(settings.minimum_reserve_limit)}",
            f"ФМ: "
            f"{rub(state.pillow_force_majeure)} / "
            f"{rub(settings.force_majeure_limit)}",
            f"СтабД: "
            f"{rub(state.pillow_stabilizer)} / "
            f"{rub(settings.stabilizer_full_limit)}",
        ])

    await message.answer(
        "\n".join(lines),
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(
    F.data == "menu:analytics"
)
async def menu_balances(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await send_balances(
        callback.message,
        callback.from_user.id,
    )


# ============================================================
# ПОМОЩЬ
# ============================================================

@router.message(
    Command("help")
)
async def command_help(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(
    F.data == "menu:help"
)
async def menu_help(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# ОТ РАЗРАБОТЧИКА
# ============================================================

@router.callback_query(
    F.data == "menu:about"
)
async def menu_about(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        ABOUT_TEXT,
        reply_markup=main_menu_keyboard(),
    )

    
# ============================================================
# АНАЛИЗ ДОХОДОВ
# ============================================================

async def send_income_analysis(
    message: Message,
    telegram_id: int,
):
    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:
        await message.answer(
            "Сначала создайте финансовый профиль "
            "через /start."
        )
        return

    # Берём операции прямо из SQLite.
    # Они уже отсортированы:
    # сначала самые новые.
    operations = db.load_operations(
        telegram_id,
        limit=1000,
    )

    totals: dict[str, Decimal] = {}
    total_income = Decimal("0")

    # ========================================================
    # ВАЖНО
    #
    # Идём от самых новых операций назад.
    # Как только встретили последний period_reset —
    # останавливаемся.
    #
    # Значит учитываются ТОЛЬКО доходы,
    # сделанные после последнего сброса периода.
    # ========================================================

    for operation in operations:

        operation_type = operation.get(
            "type"
        )

        # Дошли до начала текущего периода.
        if operation_type == "period_reset":
            break

        # Остальные типы операций нам не нужны.
        if operation_type != "income_distribution":
            continue

        payload = (
            operation.get("payload")
            or {}
        )

        income_type = str(
            payload.get(
                "income_type",
                "Без типа",
            )
        )

        amount = D(
            payload.get(
                "income",
                Decimal("0"),
            )
        )

        if amount <= 0:
            continue

        totals[income_type] = (
            totals.get(
                income_type,
                Decimal("0"),
            )
            + amount
        )

        total_income += amount

    # ========================================================
    # НЕТ ПОСТУПЛЕНИЙ
    # ========================================================

    if total_income <= 0:

        await message.answer(
            "<b>АНАЛИЗ ДОХОДОВ</b>\n\n"
            "В текущем расчётном периоде "
            "пока нет поступлений.",
            reply_markup=main_menu_keyboard(),
        )

        return

    # ========================================================
    # СОРТИРУЕМ ПО СУММЕ
    # ========================================================

    ordered = sorted(
        totals.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    lines = [
        "<b>АНАЛИЗ ДОХОДОВ</b>",
        "",
        f"👛 Доход итого: "
        f"<b>{rub(total_income)}</b>",
        "",
    ]

    for income_type, amount in ordered:

        lines.append(
            f"{escape(income_type)} — "
            f"<b>{rub(amount)}</b> "
            f"({pct(amount, total_income)})"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(
    F.data == "menu:income_analysis"
)
async def menu_income_analysis(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await send_income_analysis(
        callback.message,
        callback.from_user.id,
    )
