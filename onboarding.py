from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from financial_engine import (
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    UserSettings,
)

from storage import db
from ui import main_menu_keyboard


router = Router()


BASE_DIR = Path(__file__).resolve().parent
INTRO_IMAGES_DIR = BASE_DIR / "images"

INTRO_IMAGE_1 = INTRO_IMAGES_DIR / "intro_1.png"
INTRO_IMAGE_2 = INTRO_IMAGES_DIR / "intro_2.png"
INTRO_IMAGE_3 = INTRO_IMAGES_DIR / "intro_3.png"
INTRO_IMAGE_4 = INTRO_IMAGES_DIR / "intro_4.png"


# ============================================================
# СОСТОЯНИЯ МАСТЕРА НАСТРОЙКИ
# ============================================================


class SetupStates(StatesGroup):

    # Профиль
    has_debts = State()
    employment = State()

    # Основные расходы
    critical_life = State()
    household_reserve = State()
    average_income = State()

    # Налог
    tax_rate = State()
    taxable_types = State()

    # Подушка
    minimum_reserve_months = State()
    force_majeure_months = State()

    # Категории обязательной жизни
    life_categories_menu = State()
    life_category_name = State()
    life_category_amount = State()

    # Цели
    goals_menu = State()
    goal_name = State()
    goal_percentage = State()

    # Кредиты
    debt_strategy = State()

    credit_name = State()
    credit_principal = State()
    credit_full_repayment = State()
    credit_rate = State()
    credit_minimum_payment = State()
    credit_payment_type = State()
    credit_early_action = State()
    credit_more = State()

    # Текущее состояние
    current_pillow = State()
    current_life_balance = State()
    current_minimum_payments = State()

    # Дополнительно
    interest_savings = State()
    developer_mode = State()

    # Подтверждение
    confirmation = State()


# ============================================================
# КНОПКИ
# ============================================================


def keyboard(
    rows: list[list[tuple[str, str]]]
) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=data,
                )
                for text, data in row
            ]
            for row in rows
        ]
    )


def yes_no_keyboard(
    prefix: str
) -> InlineKeyboardMarkup:

    return keyboard([
        [
            ("✅ Да", f"{prefix}:yes"),
            ("❌ Нет", f"{prefix}:no"),
        ]
    ])


def continue_keyboard(
    callback_data: str,
    text: str = "Продолжить →",
) -> InlineKeyboardMarkup:

    return keyboard([
        [
            (
                text,
                callback_data,
            )
        ]
    ])


# ============================================================
# ЧИСЛА
# ============================================================


def parse_decimal(
    text: str
) -> Decimal | None:
    """
    Позволяет пользователю писать:

        85000
        85 000
        85 000,50
        85000.50
        85.000,50

    Основной российский формат поддерживается.
    """

    if not text:
        return None

    value = (
        text.strip()
        .replace("₽", "")
        .replace("%", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )

    # Самый типичный вариант:
    # 85000,50
    if "," in value and "." not in value:
        value = value.replace(",", ".")

    # 85.000,50
    elif "," in value and "." in value:
        value = (
            value
            .replace(".", "")
            .replace(",", ".")
        )

    try:
        result = Decimal(value)

    except (
        InvalidOperation,
        ValueError,
    ):
        return None

    return result


def rub(value) -> str:

    value = Decimal(str(value))

    formatted = f"{value:,.2f}"

    return (
        formatted
        .replace(",", " ")
        .replace(".", ",")
        + " ₽"
    )


# ============================================================
# /START
# ============================================================


@router.message(Command("start"))
async def start(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    telegram_id = message.from_user.id

    existing = db.load_settings(
        telegram_id
    )

    if existing is not None:

        await message.answer(
            "👋 <b>С возвращением.</b>\n\n"
            "Ваш финансовый профиль уже настроен.\n\n"
            "Настройки можно будет изменить через "
            "раздел ⚙️ <b>Настройки</b>.\n\n"
            "Если хотите полностью пройти настройку "
            "заново, нажмите кнопку ниже.",
            reply_markup=keyboard([
                [
                    (
                        "⚙️ Настроить заново",
                        "setup:restart",
                    )
                ]
            ]),
        )

        return

    await show_intro(
        message,
        state,
    )


@router.callback_query(
    F.data == "setup:restart"
)
async def restart_setup(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await show_intro(
        callback.message,
        state,
    )


async def send_intro_photo(
    message: Message,
    image_path: Path,
    caption: str,
    callback_data: str,
    button_text: str,
):

    if not image_path.exists():

        await message.answer(
            "⚠️ Не найдена приветственная картинка.\n"
            f"<code>{escape(str(image_path))}</code>"
        )

        return

    await message.answer_photo(
        photo=FSInputFile(
            image_path
        ),
        caption=caption,
        reply_markup=continue_keyboard(
            callback_data,
            button_text,
        ),
    )


async def remove_old_intro_button(
    callback: CallbackQuery,
):

    try:

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:

        # Если Telegram уже убрал клавиатуру или сообщение
        # нельзя отредактировать, переход всё равно продолжаем.
        pass


async def show_intro(
    message: Message,
    state: FSMContext,
):

    caption = (
        "🧪 <b>Богатый Алхимик — это финансовый аллокатор.</b>\n\n"

        "Я не буду заставлять вас записывать каждую чашку кофе "
        "и разбирать, куда вчера исчезли деньги. Анализ прошлых "
        "расходов иногда полезен для дисциплины, но сам по себе "
        "он не решает главную задачу: <b>что делать с деньгами, "
        "когда они только пришли?</b>"
    )

    await send_intro_photo(
        message=message,
        image_path=INTRO_IMAGE_1,
        caption=caption,
        callback_data="intro:2",
        button_text="Что же делать?",
    )


@router.callback_query(
    F.data == "intro:2"
)
async def intro_step_2(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await remove_old_intro_button(
        callback
    )

    caption = (
        "<b>Богатый Алхимик работает с будущим.</b>\n\n"

        "Его задача — <b>заранее распределять каждый доход</b> "
        "по финансовым «конвертам». О некоторых вы наверняка "
        "уже догадываетесь. Но будут и неочевидные — именно они "
        "помогают постепенно выстраивать финансовую устойчивость."
    )

    await send_intro_photo(
        message=callback.message,
        image_path=INTRO_IMAGE_2,
        caption=caption,
        callback_data="intro:3",
        button_text="Продолжить",
    )


@router.callback_query(
    F.data == "intro:3"
)
async def intro_step_3(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await remove_old_intro_button(
        callback
    )

    caption = (
        "<b>Секрет философского камня прост: чтобы начать "
        "богатеть, сначала нужно научиться управлять тем, "
        "что уже зарабатываешь.</b>\n\n"

        "Если человек зарабатывает миллион и тратит его в тот "
        "же месяц, его трудно назвать богатым. И это совсем не "
        "значит, что для богатства ему нужно начать зарабатывать "
        "два миллиона. Часто проблема не в размере дохода, а в "
        "том, <b>как мозг принимает решения о деньгах.</b>\n\n"

        "Управление бюджетом — это работа с вероятностями. "
        "Мы не знаем, что произойдёт завтра, но можем заранее "
        "подготовить деньги на дорогие неприятности."
    )

    await send_intro_photo(
        message=callback.message,
        image_path=INTRO_IMAGE_3,
        caption=caption,
        callback_data="intro:4",
        button_text="Дальше",
    )


@router.callback_query(
    F.data == "intro:4"
)
async def intro_step_4(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await remove_old_intro_button(
        callback
    )

    caption = (
        "<b>Волшебная таблетка существует! Но её нужно приготовить.</b>\n\n"

        "Чтобы алгоритм работал именно под вашу жизнь, сначала "
        "создадим <b>финансовый профиль</b>.\n\n"

        "Я буду задавать вопросы по одному и объяснять, "
        "<b>что означает каждая цифра и где её взять.</b>\n\n"

        "Настройка состоит из нескольких небольших разделов. "
        "Не торопитесь: лучше один раз вдумчиво настроить систему, "
        "чем потом месяцами исправлять неверные цифры.\n\n"

        "Освободите вечер, заварите ароматный чай и приготовьтесь "
        "немного поколдовать над своими финансами."
    )

    await send_intro_photo(
        message=callback.message,
        image_path=INTRO_IMAGE_4,
        caption=caption,
        callback_data="setup:start",
        button_text="Начать настройку",
    )


# ============================================================
# ДОЛГИ
# ============================================================


@router.callback_query(
    F.data == "setup:start"
)
async def setup_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await remove_old_intro_button(
        callback
    )

    await state.set_state(
        SetupStates.has_debts
    )

    await callback.message.answer(
        "💳 <b>ШАГ 1. КРЕДИТЫ И ДОЛГИ</b>\n\n"

        "<b>У вас сейчас есть кредиты или другие "
        "долги с обязательным ежемесячным платежом?</b>\n\n"

        "Сюда относятся, например:\n"
        "• потребительский кредит;\n"
        "• ипотека;\n"
        "• автокредит;\n"
        "• кредитная карта, если по ней есть долг;\n"
        "• другой банковский долг с минимальным "
        "платежом.\n\n"

        "Если кредитная карта полностью погашена "
        "и задолженности нет — выбирайте «Нет».",
        reply_markup=yes_no_keyboard(
            "debts"
        ),
    )


@router.callback_query(
    SetupStates.has_debts,
    F.data.startswith("debts:")
)
async def save_debts(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    has_debts = (
        callback.data == "debts:yes"
    )

    await state.update_data(
        has_debts=has_debts,
        credits=[],
    )

    await state.set_state(
        SetupStates.employment
    )

    await callback.message.answer(
        "👤 <b>Как вы получаете основной доход?</b>\n\n"

        "Выберите <b>Наёмный</b>, если ваш доход "
        "в основном представляет собой регулярную "
        "зарплату от работодателя.\n\n"

        "Выберите <b>Фрилансер</b>, если доход "
        "нерегулярный: проекты, заказы, самозанятость, "
        "собственный небольшой бизнес и т. п.\n\n"

        "Это важно: для нерегулярного дохода алгоритм "
        "создаёт дополнительный стабилизатор, который "
        "помогает переживать слабые месяцы.",
        reply_markup=keyboard([
            [
                (
                    "👔 Наёмный",
                    "employment:employee",
                )
            ],
            [
                (
                    "🧑‍💻 Фрилансер",
                    "employment:freelancer",
                )
            ],
        ]),
    )


# ============================================================
# ФОРМА ЗАНЯТОСТИ
# ============================================================


@router.callback_query(
    SetupStates.employment,
    F.data.startswith("employment:")
)
async def save_employment(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    employment = (
        "Наёмный"
        if callback.data
        == "employment:employee"
        else "Фрилансер"
    )

    await state.update_data(
        employment_type=employment
    )

    await state.set_state(
        SetupStates.critical_life
    )

    data = await state.get_data()

    debt_note = ""

    if data.get("has_debts"):

        debt_note = (
            "\n\n⚠️ <b>Платежи по кредитам сейчас "
            "сюда не добавляйте.</b> Мы введём каждый "
            "кредит отдельно, и бот сам добавит "
            "минимальные платежи к обязательствам. "
            "Так мы не посчитаем их дважды."
        )

    await callback.message.answer(
        "🔴 <b>ШАГ 2. ОБЯЗАТЕЛЬНЫЕ РАСХОДЫ</b>\n\n"

        "Сколько денег в среднем вам нужно "
        "<b>каждый месяц на обязательную жизнь</b>?\n\n"

        "Это расходы, которые нельзя спокойно "
        "отложить на потом:\n"
        "• жильё и коммунальные услуги;\n"
        "• продукты;\n"
        "• связь и интернет;\n"
        "• обязательный транспорт;\n"
        "• лекарства;\n"
        "• содержание питомца;\n"
        "• другие необходимые платежи.\n\n"

        "💡 <b>Лучше не оценивать эту сумму на глаз.</b>\n"
        "Откройте банковскую аналитику за последние "
        "3–6 месяцев, сложите такие расходы и "
        "разделите на число месяцев.\n\n"

        "Пример:\n"
        "за 6 месяцев обязательные расходы составили "
        "480 000 ₽ → 480 000 ÷ 6 = <b>80 000 ₽</b>."
        + debt_note
        + "\n\n"
        "Отправьте сумму одним сообщением.\n"
        "Например: <code>80000</code>",
    )


# ============================================================
# КРИТИЧЕСКАЯ ЖИЗНЬ
# ============================================================


@router.message(
    SetupStates.critical_life
)
async def save_critical_life(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value <= 0:

        await message.answer(
            "Не получилось распознать сумму.\n\n"
            "Отправьте положительное число, например:\n"
            "<code>80000</code>"
        )

        return

    await state.update_data(
        critical_life=str(value)
    )

    await state.set_state(
        SetupStates.household_reserve
    )

    await message.answer(
        "🟢 <b>Теперь — нерегулярные бытовые расходы.</b>\n\n"

        "Они не происходят строго каждый месяц, "
        "но регулярно появляются в жизни:\n\n"

        "• одежда и обувь;\n"
        "• стрижка и уход;\n"
        "• кафе и развлечения;\n"
        "• такси;\n"
        "• подарки;\n"
        "• бытовая химия;\n"
        "• мелкий ремонт;\n"
        "• необязательная аптека;\n"
        "• другие бытовые покупки.\n\n"

        "Аллокатор создаёт для этого отдельный "
        "<b>Бытовой резерв</b>, чтобы такие траты "
        "не приходилось оплачивать кредиткой "
        "или забирать из финансовой подушки.\n\n"

        "Посчитайте среднюю сумму за месяц так же "
        "по банковской аналитике.\n\n"

        "Например: <code>25000</code>",
    )


# ============================================================
# БЫТОВОЙ РЕЗЕРВ
# ============================================================


@router.message(
    SetupStates.household_reserve
)
async def save_household_reserve(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value < 0:

        await message.answer(
            "Введите сумму от 0 ₽ и выше.\n\n"
            "Например: <code>25000</code>"
        )

        return

    await state.update_data(
        household_reserve=str(value)
    )

    await state.set_state(
        SetupStates.average_income
    )

    await message.answer(
        "💰 <b>Среднемесячный доход</b>\n\n"

        "Теперь укажите, сколько денег вы "
        "<b>в среднем получаете за месяц</b>.\n\n"

        "Если доход меняется, особенно у фрилансера, "
        "лучше взять поступления за 6–12 месяцев "
        "и разделить на количество месяцев.\n\n"

        "Это не означает, что бот будет каждый месяц "
        "ждать именно такую сумму. Реальные поступления "
        "вы будете добавлять по мере их получения.\n\n"

        "Средний доход нужен для понимания вашей "
        "финансовой картины.\n\n"

        "Например: <code>180000</code>",
    )


# ============================================================
# СРЕДНИЙ ДОХОД
# ============================================================


@router.message(
    SetupStates.average_income
)
async def save_average_income(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value < 0:

        await message.answer(
            "Введите корректную сумму дохода.\n\n"
            "Например: <code>180000</code>"
        )

        return

    await state.update_data(
        average_income=str(value)
    )

    await state.set_state(
        SetupStates.tax_rate
    )

    await message.answer(
        "🏛️ <b>ШАГ 3. НАЛОГ</b>\n\n"

        "Если с некоторых поступлений вам нужно "
        "самостоятельно откладывать деньги на налог, "
        "бот может делать это автоматически "
        "при каждом распределении.\n\n"

        "Например, самозанятый или предприниматель "
        "может указать свою ставку.\n\n"

        "Если вам не нужно самостоятельно "
        "резервировать налог — отправьте <code>0</code>.\n\n"

        "Если ставка, например, 6% — отправьте:\n"
        "<code>6</code>",
    )


# ============================================================
# НАЛОГ
# ============================================================


@router.message(
    SetupStates.tax_rate
)
async def save_tax_rate(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if (
        value is None
        or value < 0
        or value > 100
    ):

        await message.answer(
            "Введите ставку от 0 до 100.\n\n"
            "Например:\n"
            "<code>6</code>"
        )

        return

    await state.update_data(
        tax_rate=str(value)
    )

    if value == 0:

        await state.update_data(
            taxable_income_types=[]
        )

        await go_to_pillow_question(
            message,
            state,
        )

        return

    await state.set_state(
        SetupStates.taxable_types
    )

    await message.answer(
        "🏷 <b>С каких поступлений удерживать налог?</b>\n\n"

        "У одного человека разные виды дохода могут "
        "облагаться по-разному.\n\n"

        "Например, налог нужно резервировать с "
        "«Работы» и «Заказов», но не нужно "
        "с подарков или кэшбэка.\n\n"

        "Напишите названия через запятую.\n\n"

        "Пример:\n"
        "<code>Зарплата, Заказы, Фриланс</code>\n\n"

        "Позже при добавлении дохода бот сверит "
        "его тип с этим списком.",
    )


@router.message(
    SetupStates.taxable_types
)
async def save_taxable_types(
    message: Message,
    state: FSMContext,
):

    types = [
        item.strip()
        for item in message.text.split(",")
        if item.strip()
    ]

    if not types:

        await message.answer(
            "Укажите хотя бы один тип дохода.\n\n"
            "Например:\n"
            "<code>Зарплата, Фриланс</code>"
        )

        return

    await state.update_data(
        taxable_income_types=types
    )

    await go_to_pillow_question(
        message,
        state,
    )


# ============================================================
# ПОДУШКА
# ============================================================


async def go_to_pillow_question(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    if data["has_debts"]:

        await state.set_state(
            SetupStates.minimum_reserve_months
        )

        await message.answer(
            "🛟 <b>ШАГ 4. МИНИМАЛЬНАЯ ПОДУШКА</b>\n\n"

            "Пока есть долги, система сначала создаёт "
            "небольшой аварийный запас.\n\n"

            "Он нужен, чтобы при внезапной проблеме "
            "не брать новый кредит и не пропускать "
            "обязательные платежи.\n\n"

            "Выберите, на сколько месяцев обязательной "
            "жизни сформировать такую подушку.\n\n"

            "<b>1 месяц</b> — быстрее перейти "
            "к досрочному погашению.\n\n"

            "<b>2 месяца</b> — больше безопасности "
            "до начала агрессивного погашения долгов.",
            reply_markup=keyboard([
                [
                    (
                        "1 месяц",
                        "minmonths:1",
                    ),
                    (
                        "2 месяца",
                        "minmonths:2",
                    ),
                ]
            ]),
        )

    else:

        await state.update_data(
            minimum_reserve_months="0"
        )

        await state.set_state(
            SetupStates.force_majeure_months
        )

        await show_force_majeure_question(
            message,
            state,
        )


@router.callback_query(
    SetupStates.minimum_reserve_months,
    F.data.startswith("minmonths:")
)
async def save_minimum_months(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    months = (
        callback.data.split(":")[1]
    )

    await state.update_data(
        minimum_reserve_months=months
    )

    # Форс-мажорная подушка потребуется
    # после закрытия долгов.
    await state.set_state(
        SetupStates.force_majeure_months
    )

    await show_force_majeure_question(
        callback.message,
        state,
    )


async def show_force_majeure_question(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    employment = data[
        "employment_type"
    ]

    if employment == "Фрилансер":

        recommendation = (
            "Для нерегулярного дохода в вашей системе "
            "предусмотрен более осторожный подход. "
            "Обычно разумно рассматривать диапазон "
            "<b>6–12 месяцев</b>."
        )

        buttons = [
            [
                ("6 мес.", "fmmonths:6"),
                ("9 мес.", "fmmonths:9"),
                ("12 мес.", "fmmonths:12"),
            ],
            [
                (
                    "Ввести своё число",
                    "fmmonths:custom",
                )
            ],
        ]

    else:

        recommendation = (
            "Для регулярного дохода ориентир "
            "системы — примерно <b>3–6 месяцев</b> "
            "обязательных расходов."
        )

        buttons = [
            [
                ("3 мес.", "fmmonths:3"),
                ("4 мес.", "fmmonths:4"),
                ("6 мес.", "fmmonths:6"),
            ],
            [
                (
                    "Ввести своё число",
                    "fmmonths:custom",
                )
            ],
        ]

    await message.answer(
        "🛡 <b>ФОРС-МАЖОРНАЯ ПОДУШКА</b>\n\n"

        "Это деньги не на отпуск и не на покупки.\n\n"

        "Это резерв на действительно серьёзные "
        "ситуации: потеря дохода, болезнь, авария, "
        "вынужденный переезд и другие события, "
        "которые резко нарушают обычную жизнь.\n\n"

        + recommendation
        + "\n\n"

        "Выберите количество месяцев.",
        reply_markup=keyboard(
            buttons
        ),
    )


@router.callback_query(
    SetupStates.force_majeure_months,
    F.data.startswith("fmmonths:")
)
async def save_force_months_callback(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    value = (
        callback.data.split(":")[1]
    )

    if value == "custom":

        await callback.message.answer(
            "Введите количество месяцев числом.\n\n"
            "Например: <code>5</code>"
        )

        return

    await state.update_data(
        force_majeure_months=value
    )

    await start_life_categories(
        callback.message,
        state,
    )


@router.message(
    SetupStates.force_majeure_months
)
async def save_force_months_text(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value <= 0:

        await message.answer(
            "Количество месяцев должно быть "
            "больше нуля.\n\n"
            "Например: <code>6</code>"
        )

        return

    await state.update_data(
        force_majeure_months=str(value)
    )

    await start_life_categories(
        message,
        state,
    )


# ============================================================
# КАТЕГОРИИ КРИТИЧЕСКОЙ ЖИЗНИ
# ============================================================


async def start_life_categories(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        life_categories={}
    )

    await state.set_state(
        SetupStates.life_categories_menu
    )

    await message.answer(
        "❤️ <b>ШАГ 5. КРУПНЫЕ ОБЯЗАТЕЛЬНЫЕ КАТЕГОРИИ</b>\n\n"

        "Теперь можно разделить обязательные расходы "
        "на несколько отдельных «конвертов».\n\n"

        "Например:\n"
        "• Квартира — 43 000 ₽;\n"
        "• Транспорт — 5 000 ₽;\n"
        "• Питомец — 4 000 ₽.\n\n"

        "Алгоритм будет наполнять эти категории "
        "<b>одновременно и пропорционально</b>.\n\n"

        "Не нужно создавать десятки категорий. "
        "Лучше выделить до <b>4 самых крупных "
        "и важных</b>.\n\n"

        "Продукты, телефон, небольшие подписки "
        "и другую мелочь можно не перечислять: "
        "остаток бот автоматически оставит "
        "в категории ❤️ <b>Зарплата</b>.\n\n"

        "Категории необязательны.",
        reply_markup=keyboard([
            [
                (
                    "➕ Добавить категорию",
                    "lifecat:add",
                )
            ],
            [
                (
                    "Пропустить →",
                    "lifecat:done",
                )
            ],
        ]),
    )


@router.callback_query(
    SetupStates.life_categories_menu,
    F.data == "lifecat:add"
)
async def add_life_category(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    categories = data.get(
        "life_categories",
        {},
    )

    if len(categories) >= 4:

        await callback.message.answer(
            "Уже добавлено 4 категории.\n\n"
            "Этого достаточно для системы. "
            "Остальные расходы останутся "
            "в автоматической категории «Зарплата».",
            reply_markup=continue_keyboard(
                "lifecat:done"
            ),
        )

        return

    await state.set_state(
        SetupStates.life_category_name
    )

    await callback.message.answer(
        "Введите название категории.\n\n"
        "Например:\n"
        "<code>Квартира</code>\n"
        "или\n"
        "<code>Транспорт</code>"
    )


@router.message(
    SetupStates.life_category_name
)
async def life_category_name(
    message: Message,
    state: FSMContext,
):

    name = message.text.strip()

    if len(name) < 2:

        await message.answer(
            "Название слишком короткое."
        )

        return

    if name.lower() in {
        "зарплата",
        "мин. платеж",
        "мин платеж",
        "минимальный платеж",
    }:

        await message.answer(
            "Это название зарезервировано системой.\n\n"
            "Введите другое название категории."
        )

        return

    data = await state.get_data()

    categories = data.get(
        "life_categories",
        {},
    )

    if any(
        existing.lower() == name.lower()
        for existing in categories
    ):

        await message.answer(
            "Такая категория уже существует."
        )

        return

    await state.update_data(
        pending_life_category=name
    )

    await state.set_state(
        SetupStates.life_category_amount
    )

    await message.answer(
        f"Сколько в среднем нужно в месяц "
        f"на ❤️ <b>{escape(name)}</b>?\n\n"
        f"Например: <code>43000</code>"
    )


@router.message(
    SetupStates.life_category_amount
)
async def life_category_amount(
    message: Message,
    state: FSMContext,
):

    amount = parse_decimal(
        message.text
    )

    if amount is None or amount <= 0:

        await message.answer(
            "Введите положительную сумму."
        )

        return

    data = await state.get_data()

    critical_life = Decimal(
        data["critical_life"]
    )

    categories = dict(
        data.get(
            "life_categories",
            {},
        )
    )

    current_total = sum(
        (
            Decimal(str(value))
            for value in categories.values()
        ),
        Decimal("0"),
    )

    if current_total + amount > critical_life:

        remaining = (
            critical_life
            - current_total
        )

        await message.answer(
            "Эта сумма не помещается внутри "
            "ваших обязательных расходов.\n\n"

            f"Общая обязательная жизнь: "
            f"<b>{rub(critical_life)}</b>\n"

            f"Уже распределено по категориям: "
            f"<b>{rub(current_total)}</b>\n"

            f"Можно добавить максимум: "
            f"<b>{rub(remaining)}</b>\n\n"

            "Введите меньшую сумму."
        )

        return

    name = data[
        "pending_life_category"
    ]

    categories[name] = str(
        amount
    )

    await state.update_data(
        life_categories=categories
    )

    await state.set_state(
        SetupStates.life_categories_menu
    )

    lines = []

    for category, value in categories.items():

        lines.append(
            f"❤️ {escape(category)} — "
            f"{rub(Decimal(value))}"
        )

    salary = (
        critical_life
        - sum(
            Decimal(v)
            for v in categories.values()
        )
    )

    lines.append(
        f"❤️ Зарплата — "
        f"{rub(salary)} "
        f"<i>(автоматически)</i>"
    )

    text = "\n".join(lines)

    buttons = []

    if len(categories) < 4:

        buttons.append([
            (
                "➕ Ещё категория",
                "lifecat:add",
            )
        ])

    buttons.append([
        (
            "Готово →",
            "lifecat:done",
        )
    ])

    await message.answer(
        "Категория добавлена.\n\n"
        f"{text}",
        reply_markup=keyboard(
            buttons
        ),
    )


@router.callback_query(
    F.data == "lifecat:done"
)
async def finish_life_categories(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await start_goals(
        callback.message,
        state,
    )


# ============================================================
# ЦЕЛИ — НАЧАЛО
# ============================================================


async def start_goals(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        goals=[]
    )

    await state.set_state(
        SetupStates.goals_menu
    )

    await message.answer(
        "⭐️ <b>ШАГ 6. ФИНАНСОВЫЕ ЦЕЛИ</b>\n\n"

        "Цели — это деньги, которые вы хотите "
        "откладывать на конкретные будущие расходы.\n\n"

        "Например:\n"
        "• отпуск;\n"
        "• подарки;\n"
        "• новая техника;\n"
        "• образование;\n"
        "• ремонт;\n"
        "• продвижение проекта.\n\n"

        "Когда ваш финансовый режим разрешит "
        "накопление целей, бот будет автоматически "
        "делить предназначенную для них сумму "
        "по указанным вами долям.\n\n"

        "Например:\n"
        "Отпуск — 50%\n"
        "Техника — 30%\n"
        "Подарки — 20%\n\n"

        "В сумме должно получиться <b>100%</b>.\n\n"

        "Если пока не хотите создавать категории, "
        "можно пропустить этот шаг. Тогда деньги "
        "будут показываться как ⭐️ «Цели (всего)».",
        reply_markup=keyboard([
            [
                (
                    "➕ Добавить цель",
                    "goal:add",
                )
            ],
            [
                (
                    "Пока без категорий →",
                    "goal:skip",
                )
            ],
        ]),
    )
    # ============================================================
# ЦЕЛИ — ДОБАВЛЕНИЕ
# ============================================================


@router.callback_query(
    SetupStates.goals_menu,
    F.data == "goal:add"
)
async def add_goal(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    goals = data.get(
        "goals",
        []
    )

    if len(goals) >= 6:
        await callback.message.answer(
            "Уже добавлено 6 целей.\n\n"
            "Этого достаточно для удобного распределения.",
            reply_markup=continue_keyboard(
                "goal:done"
            ),
        )
        return

    await state.set_state(
        SetupStates.goal_name
    )

    await callback.message.answer(
        "Введите название цели.\n\n"
        "Например:\n"
        "<code>Отпуск</code>\n"
        "или\n"
        "<code>Новый ноутбук</code>"
    )


@router.message(
    SetupStates.goal_name
)
async def save_goal_name(
    message: Message,
    state: FSMContext,
):

    name = message.text.strip()

    if len(name) < 2:
        await message.answer(
            "Название слишком короткое."
        )
        return

    data = await state.get_data()

    goals = data.get(
        "goals",
        []
    )

    if any(
        item["name"].lower() == name.lower()
        for item in goals
    ):
        await message.answer(
            "Такая цель уже есть."
        )
        return

    await state.update_data(
        pending_goal_name=name
    )

    await state.set_state(
        SetupStates.goal_percentage
    )

    used = sum(
        Decimal(item["percentage"])
        for item in goals
    )

    remaining = Decimal("100") - used

    await message.answer(
        f"Какую долю от всех денег, "
        f"предназначенных для целей, "
        f"направлять на ⭐️ <b>{escape(name)}</b>?\n\n"
        f"Уже распределено: <b>{used}%</b>\n"
        f"Осталось: <b>{remaining}%</b>\n\n"
        f"Введите число от 0 до {remaining}.\n"
        f"Например: <code>20</code>"
    )


@router.message(
    SetupStates.goal_percentage
)
async def save_goal_percentage(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value <= 0:
        await message.answer(
            "Введите процент больше нуля."
        )
        return

    data = await state.get_data()

    goals = list(
        data.get(
            "goals",
            []
        )
    )

    used = sum(
        Decimal(item["percentage"])
        for item in goals
    )

    remaining = Decimal("100") - used

    if value > remaining:
        await message.answer(
            f"Можно указать максимум {remaining}%."
        )
        return

    goals.append({
        "name": data["pending_goal_name"],
        "percentage": str(value),
    })

    await state.update_data(
        goals=goals
    )

    used = sum(
        Decimal(item["percentage"])
        for item in goals
    )

    remaining = Decimal("100") - used

    lines = [
        f"⭐️ {escape(item['name'])} — "
        f"{item['percentage']}%"
        for item in goals
    ]

    if remaining == 0:

        await state.set_state(
            SetupStates.goals_menu
        )

        await message.answer(
            "Цели распределены полностью.\n\n"
            + "\n".join(lines),
            reply_markup=continue_keyboard(
                "goal:done",
                "Продолжить →",
            ),
        )

        return

    await state.set_state(
        SetupStates.goals_menu
    )

    await message.answer(
        "Цель добавлена.\n\n"
        + "\n".join(lines)
        + f"\n\nОсталось распределить: "
          f"<b>{remaining}%</b>",
        reply_markup=keyboard([
            [
                (
                    "➕ Добавить ещё",
                    "goal:add",
                )
            ],
            [
                (
                    "Закончить автоматически",
                    "goal:auto",
                )
            ],
        ]),
    )


@router.callback_query(
    SetupStates.goals_menu,
    F.data == "goal:auto"
)
async def finish_goals_automatically(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    goals = list(
        data.get(
            "goals",
            []
        )
    )

    used = sum(
        Decimal(item["percentage"])
        for item in goals
    )

    remaining = Decimal("100") - used

    if remaining > 0:
        goals.append({
            "name": "Остальные цели",
            "percentage": str(remaining),
        })

    await state.update_data(
        goals=goals
    )

    await after_goals(
        callback.message,
        state,
    )


@router.callback_query(
    F.data == "goal:skip"
)
async def skip_goals(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.update_data(
        goals=[]
    )

    await after_goals(
        callback.message,
        state,
    )


@router.callback_query(
    F.data == "goal:done"
)
async def finish_goals(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    goals = data.get(
        "goals",
        []
    )

    if goals:
        total = sum(
            Decimal(item["percentage"])
            for item in goals
        )

        if total != Decimal("100"):
            await callback.message.answer(
                "Сумма целей пока не равна 100%.\n\n"
                "Добавьте ещё цель или используйте "
                "«Закончить автоматически»."
            )
            return

    await after_goals(
        callback.message,
        state,
    )


# ============================================================
# ПЕРЕХОД К КРЕДИТАМ ИЛИ СОСТОЯНИЮ
# ============================================================


async def after_goals(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    if data["has_debts"]:

        await state.set_state(
            SetupStates.debt_strategy
        )

        await message.answer(
            "💳 <b>ШАГ 7. СТРАТЕГИЯ ПОГАШЕНИЯ ДОЛГОВ</b>\n\n"

            "Если после обязательных расходов появляются "
            "деньги на досрочное погашение, бот должен "
            "понимать, какой кредит гасить первым.\n\n"

            "<b>Лавина</b>\n"
            "Сначала кредит с самой высокой ставкой. "
            "Обычно это уменьшает общую переплату.\n\n"

            "<b>Снежный ком</b>\n"
            "Сначала кредит с самым маленьким остатком. "
            "Так быстрее исчезают отдельные долги.\n\n"

            "<b>Ручной выбор</b>\n"
            "Приоритет задаётся вашим порядком кредитов.\n\n"

            "Если не уверены — выбирайте «Лавина».",
            reply_markup=keyboard([
                [
                    (
                        "🏔 Лавина",
                        "strategy:avalanche",
                    )
                ],
                [
                    (
                        "❄️ Снежный ком",
                        "strategy:snowball",
                    )
                ],
                [
                    (
                        "✋ Ручной выбор",
                        "strategy:manual",
                    )
                ],
            ]),
        )

    else:

        await state.update_data(
            debt_strategy="Лавина",
            credits=[],
        )

        await start_current_state(
            message,
            state,
        )


# ============================================================
# СТРАТЕГИЯ ДОЛГОВ
# ============================================================


@router.callback_query(
    SetupStates.debt_strategy,
    F.data.startswith("strategy:")
)
async def save_debt_strategy(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    mapping = {
        "strategy:avalanche": "Лавина",
        "strategy:snowball": "Снежный ком",
        "strategy:manual": "Ручной выбор",
    }

    await state.update_data(
        debt_strategy=mapping[
            callback.data
        ],
        credits=[],
    )

    await state.set_state(
        SetupStates.credit_name
    )

    await callback.message.answer(
        "💳 <b>Добавим первый кредит.</b>\n\n"

        "Введите короткое понятное название.\n\n"

        "Например:\n"
        "<code>Ипотека</code>\n"
        "<code>Кредитка Т-Банк</code>\n"
        "<code>Автокредит</code>"
    )


# ============================================================
# КРЕДИТ — НАЗВАНИЕ
# ============================================================


@router.message(
    SetupStates.credit_name
)
async def save_credit_name(
    message: Message,
    state: FSMContext,
):

    name = message.text.strip()

    if len(name) < 2:
        await message.answer(
            "Введите более понятное название."
        )
        return

    data = await state.get_data()

    credits = data.get(
        "credits",
        []
    )

    if any(
        item["name"].lower() == name.lower()
        for item in credits
    ):
        await message.answer(
            "Кредит с таким названием уже есть."
        )
        return

    await state.update_data(
        pending_credit={
            "name": name
        }
    )

    await state.set_state(
        SetupStates.credit_principal
    )

    await message.answer(
        f"💳 <b>{escape(name)}</b>\n\n"

        "Какой сейчас <b>остаток основного долга</b>?\n\n"

        "Это тело кредита без будущих процентов.\n"
        "Посмотрите его в приложении банка.\n\n"

        "Например: <code>350000</code>"
    )


# ============================================================
# КРЕДИТ — ОСТАТОК
# ============================================================


@router.message(
    SetupStates.credit_principal
)
async def save_credit_principal(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value <= 0:
        await message.answer(
            "Введите положительную сумму."
        )
        return

    data = await state.get_data()

    credit = dict(
        data["pending_credit"]
    )

    credit["principal_balance"] = str(
        value
    )

    await state.update_data(
        pending_credit=credit
    )

    await state.set_state(
        SetupStates.credit_full_repayment
    )

    await message.answer(
        "Теперь укажите <b>сумму полного погашения "
        "на сегодня</b>, если банк её показывает.\n\n"

        "Она может немного отличаться от остатка тела "
        "из-за начисленных процентов.\n\n"

        "Если не знаете — отправьте <code>0</code>.\n\n"

        "⚠️ В этом случае бот не будет сам утверждать, "
        "что кредит полностью закрыт только по этой цифре."
    )


# ============================================================
# КРЕДИТ — ПОЛНОЕ ПОГАШЕНИЕ
# ============================================================


@router.message(
    SetupStates.credit_full_repayment
)
async def save_credit_full_repayment(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value < 0:
        await message.answer(
            "Введите сумму или 0."
        )
        return

    data = await state.get_data()

    credit = dict(
        data["pending_credit"]
    )

    credit["full_repayment_amount"] = (
        None
        if value == 0
        else str(value)
    )

    await state.update_data(
        pending_credit=credit
    )

    await state.set_state(
        SetupStates.credit_rate
    )

    await message.answer(
        "Какая <b>годовая процентная ставка</b> "
        "по кредиту?\n\n"

        "Например, если в договоре указано 29,9%, "
        "отправьте:\n"
        "<code>29,9</code>"
    )


# ============================================================
# КРЕДИТ — СТАВКА
# ============================================================


@router.message(
    SetupStates.credit_rate
)
async def save_credit_rate(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if (
        value is None
        or value < 0
        or value > 200
    ):
        await message.answer(
            "Введите корректную годовую ставку."
        )
        return

    data = await state.get_data()

    credit = dict(
        data["pending_credit"]
    )

    credit["annual_rate"] = str(
        value
    )

    await state.update_data(
        pending_credit=credit
    )

    await state.set_state(
        SetupStates.credit_minimum_payment
    )

    await message.answer(
        "Какой сейчас <b>минимальный ежемесячный "
        "платёж</b> по этому кредиту?\n\n"

        "Введите сумму из приложения банка.\n\n"
        "Например: <code>12500</code>"
    )


# ============================================================
# КРЕДИТ — МИНИМАЛЬНЫЙ ПЛАТЁЖ
# ============================================================


@router.message(
    SetupStates.credit_minimum_payment
)
async def save_credit_minimum_payment(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value <= 0:
        await message.answer(
            "Введите положительную сумму."
        )
        return

    data = await state.get_data()

    credit = dict(
        data["pending_credit"]
    )

    credit["minimum_payment"] = str(
        value
    )

    await state.update_data(
        pending_credit=credit
    )

    await state.set_state(
        SetupStates.credit_payment_type
    )

    await message.answer(
        "Какой тип платежа указан по кредиту?\n\n"

        "<b>Аннуитетный</b> — обычно платёж одинаковый "
        "каждый месяц.\n\n"

        "<b>Дифференцированный</b> — платёж постепенно "
        "уменьшается.",
        reply_markup=keyboard([
            [
                (
                    "Аннуитетный",
                    "paymenttype:annuity",
                )
            ],
            [
                (
                    "Дифференцированный",
                    "paymenttype:differentiated",
                )
            ],
        ]),
    )


# ============================================================
# КРЕДИТ — ТИП ПЛАТЕЖА
# ============================================================


@router.callback_query(
    SetupStates.credit_payment_type,
    F.data.startswith("paymenttype:")
)
async def save_credit_payment_type(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    value = (
        "Аннуитетный"
        if callback.data
        == "paymenttype:annuity"
        else "Дифференцированный"
    )

    data = await state.get_data()

    credit = dict(
        data["pending_credit"]
    )

    credit["payment_type"] = value

    await state.update_data(
        pending_credit=credit
    )

    await state.set_state(
        SetupStates.credit_early_action
    )

    await callback.message.answer(
        "Если вы делаете досрочный платёж, "
        "что обычно выбираете в банке?\n\n"

        "<b>Уменьшать срок</b> — чаще позволяет сильнее "
        "снизить общую переплату.\n\n"

        "<b>Уменьшать платёж</b> — снижает ежемесячную "
        "нагрузку.",
        reply_markup=keyboard([
            [
                (
                    "⏳ Уменьшать срок",
                    "early:term",
                )
            ],
            [
                (
                    "💸 Уменьшать платёж",
                    "early:payment",
                )
            ],
        ]),
    )


# ============================================================
# КРЕДИТ — ДОСРОЧНОЕ
# ============================================================


@router.callback_query(
    SetupStates.credit_early_action,
    F.data.startswith("early:")
)
async def save_credit_early_action(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    action = (
        "Уменьшать срок"
        if callback.data == "early:term"
        else "Уменьшать платёж"
    )

    data = await state.get_data()

    credit = dict(
        data["pending_credit"]
    )

    credit["early_repayment_action"] = action
    credit["status"] = "Активный"

    credits = list(
        data.get(
            "credits",
            []
        )
    )

    credits.append(
        credit
    )

    await state.update_data(
        credits=credits,
        pending_credit=None,
    )

    await state.set_state(
        SetupStates.credit_more
    )

    await callback.message.answer(
        f"✅ Кредит <b>{escape(credit['name'])}</b> добавлен.\n\n"
        f"Остаток: <b>{rub(Decimal(credit['principal_balance']))}</b>\n"
        f"Минимальный платёж: "
        f"<b>{rub(Decimal(credit['minimum_payment']))}</b>\n"
        f"Ставка: <b>{credit['annual_rate']}%</b>\n\n"
        "Есть ещё кредиты?",
        reply_markup=yes_no_keyboard(
            "creditmore"
        ),
    )


@router.callback_query(
    SetupStates.credit_more,
    F.data.startswith("creditmore:")
)
async def credit_more(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    if callback.data == "creditmore:yes":

        await state.set_state(
            SetupStates.credit_name
        )

        await callback.message.answer(
            "Введите название следующего кредита."
        )

        return

    await start_current_state(
        callback.message,
        state,
    )


# ============================================================
# ТЕКУЩЕЕ СОСТОЯНИЕ
# ============================================================


async def start_current_state(
    message: Message,
    state: FSMContext,
):

    await state.set_state(
        SetupStates.current_pillow
    )

    await message.answer(
        "🧭 <b>ШАГ 8. ГДЕ ВЫ НАХОДИТЕСЬ СЕЙЧАС</b>\n\n"

        "Мы настроили правила. Теперь нужно определить "
        "вашу стартовую точку.\n\n"

        "Сколько денег <b>уже сейчас лежит в вашей "
        "финансовой подушке</b>?\n\n"

        "Если подушки пока нет — отправьте <code>0</code>.\n\n"

        "Если деньги лежат на нескольких резервных "
        "счетах, сложите их.\n\n"

        "Например: <code>150000</code>"
    )


@router.message(
    SetupStates.current_pillow
)
async def save_current_pillow(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value < 0:
        await message.answer(
            "Введите сумму от 0 ₽ и выше."
        )
        return

    await state.update_data(
        current_pillow=str(value)
    )

    await state.set_state(
        SetupStates.current_life_balance
    )

    await message.answer(
        "Сколько денег уже отложено "
        "<b>на расходы текущего расчётного периода</b>?\n\n"

        "То есть сколько сейчас находится в вашем "
        "«балансе жизни» — деньги на обязательные "
        "и бытовые расходы текущего месяца.\n\n"

        "Если начинаете работу с ботом с нового месяца "
        "или ничего ещё не распределяли — отправьте "
        "<code>0</code>."
    )


@router.message(
    SetupStates.current_life_balance
)
async def save_current_life_balance(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value < 0:
        await message.answer(
            "Введите сумму от 0 ₽ и выше."
        )
        return

    data = await state.get_data()

    critical = Decimal(
        data["critical_life"]
    )

    reserve = Decimal(
        data["household_reserve"]
    )

    max_life = (
        critical
        + reserve
    )

    if value > max_life:
        await message.answer(
            "Баланс жизни не должен превышать "
            "сумму обязательных расходов и "
            "бытового резерва.\n\n"
            f"Максимум по вашим настройкам: "
            f"<b>{rub(max_life)}</b>"
        )
        return

    await state.update_data(
        current_life_balance=str(value)
    )

    data = await state.get_data()

    if data["has_debts"]:

        await state.set_state(
            SetupStates.current_minimum_payments
        )

        total_minimum = sum(
            Decimal(item["minimum_payment"])
            for item in data["credits"]
        )

        await message.answer(
            "💳 <b>Минимальные платежи текущего месяца</b>\n\n"

            "Сколько денег уже зарезервировано "
            "на обязательные платежи по кредитам "
            "в текущем расчётном периоде?\n\n"

            f"Полная месячная сумма по вашим кредитам: "
            f"<b>{rub(total_minimum)}</b>\n\n"

            "Если пока ничего не отложено — отправьте "
            "<code>0</code>."
        )

    else:

        await state.update_data(
            current_minimum_payments="0"
        )

        await ask_interest_savings(
            message,
            state,
        )


@router.message(
    SetupStates.current_minimum_payments
)
async def save_current_minimum_payments(
    message: Message,
    state: FSMContext,
):

    value = parse_decimal(
        message.text
    )

    if value is None or value < 0:
        await message.answer(
            "Введите сумму от 0 ₽ и выше."
        )
        return

    data = await state.get_data()

    total_minimum = sum(
        Decimal(item["minimum_payment"])
        for item in data["credits"]
    )

    if value > total_minimum:
        await message.answer(
            "Зарезервированная сумма не может быть "
            "больше общей суммы минимальных платежей "
            "текущего месяца.\n\n"
            f"Максимум: <b>{rub(total_minimum)}</b>"
        )
        return

    await state.update_data(
        current_minimum_payments=str(value)
    )

    await ask_interest_savings(
        message,
        state,
    )


# ============================================================
# РАСЧЁТ ЭКОНОМИИ
# ============================================================


async def ask_interest_savings(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    if not data["has_debts"]:

        await state.update_data(
            calculate_interest_savings=False
        )

        await ask_developer_mode(
            message,
            state,
        )

        return

    await state.set_state(
        SetupStates.interest_savings
    )

    await message.answer(
        "🧮 <b>Расчёт экономии на процентах</b>\n\n"

        "Бот может приблизительно оценивать, "
        "сколько процентов и месяцев вы экономите "
        "за счёт досрочных платежей.\n\n"

        "Для аннуитетных кредитов это будет "
        "математическая оценка, а не официальный "
        "расчёт банка.\n\n"

        "Включить?",
        reply_markup=yes_no_keyboard(
            "interestsaving"
        ),
    )


@router.callback_query(
    SetupStates.interest_savings,
    F.data.startswith("interestsaving:")
)
async def save_interest_savings(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    enabled = (
        callback.data
        == "interestsaving:yes"
    )

    await state.update_data(
        calculate_interest_savings=enabled
    )

    await ask_developer_mode(
        callback.message,
        state,
    )


# ============================================================
# РЕЖИМ РАЗРАБОТЧИКА
# ============================================================


async def ask_developer_mode(
    message: Message,
    state: FSMContext,
):

    await state.set_state(
        SetupStates.developer_mode
    )

    await message.answer(
        "🛠 <b>Режим разработчика</b>\n\n"

        "Обычному пользователю он не нужен.\n\n"

        "В обычном режиме бот показывает только "
        "необходимые суммы и рекомендации.\n\n"

        "В режиме разработчика будут видны:\n"
        "• внутренние слои подушки;\n"
        "• промежуточные вычисления;\n"
        "• нулевые строки;\n"
        "• подробные формулы и проверки.\n\n"

        "Оставить обычный режим?",
        reply_markup=keyboard([
            [
                (
                    "✅ Обычный режим",
                    "developer:no",
                )
            ],
            [
                (
                    "🛠 Режим разработчика",
                    "developer:yes",
                )
            ],
        ]),
    )


@router.callback_query(
    SetupStates.developer_mode,
    F.data.startswith("developer:")
)
async def save_developer_mode(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    enabled = (
        callback.data
        == "developer:yes"
    )

    await state.update_data(
        developer_mode=enabled
    )

    await show_confirmation(
        callback.message,
        state,
    )


# ============================================================
# СОЗДАНИЕ ОБЪЕКТОВ
# ============================================================


def build_settings_from_data(
    data: dict,
) -> UserSettings:

    goals = [
        Goal(
            name=item["name"],
            percentage=Decimal(
                item["percentage"]
            ),
        )
        for item in data.get(
            "goals",
            []
        )
    ]

    credits = []

    for item in data.get(
        "credits",
        []
    ):

        full_repayment = item.get(
            "full_repayment_amount"
        )

        if full_repayment is not None:
            full_repayment = Decimal(
                full_repayment
            )

        credits.append(
            Credit(
                name=item["name"],

                principal_balance=Decimal(
                    item["principal_balance"]
                ),

                full_repayment_amount=
                    full_repayment,

                annual_rate=Decimal(
                    item["annual_rate"]
                ),

                minimum_payment=Decimal(
                    item["minimum_payment"]
                ),

                payment_type=
                    item["payment_type"],

                early_repayment_action=
                    item["early_repayment_action"],

                status=
                    item.get(
                        "status",
                        "Активный",
                    ),
            )
        )

    life_categories = {
        name: Decimal(value)
        for name, value in data.get(
            "life_categories",
            {}
        ).items()
    }

    return UserSettings(
        has_debts=data[
            "has_debts"
        ],

        employment_type=data[
            "employment_type"
        ],

        critical_life=Decimal(
            data["critical_life"]
        ),

        household_reserve=Decimal(
            data["household_reserve"]
        ),

        average_income=Decimal(
            data["average_income"]
        ),

        tax_rate=Decimal(
            data["tax_rate"]
        ),

        taxable_income_types=data.get(
            "taxable_income_types",
            [],
        ),

        minimum_reserve_months=Decimal(
            data.get(
                "minimum_reserve_months",
                "0",
            )
        ),

        force_majeure_months=Decimal(
            data["force_majeure_months"]
        ),

        # Бракеты остаются безопасными значениями
        # по умолчанию из исходной системы.
        bracket_a=Decimal("20"),
        bracket_b=Decimal("25"),
        bracket_c=Decimal("30"),
        bracket_d=Decimal("35"),
        bracket_e=Decimal("40"),

        goals_share_c=Decimal("50"),
        pillow_share_c=Decimal("50"),

        life_categories=
            life_categories,

        goals=goals,
        credits=credits,

        debt_strategy=data.get(
            "debt_strategy",
            "Лавина",
        ),

        calculate_interest_savings=
            data.get(
                "calculate_interest_savings",
                False,
            ),

        developer_mode=
            data.get(
                "developer_mode",
                False,
            ),
    )


def build_state_from_data(
    data: dict,
    settings: UserSettings,
) -> AllocatorState:

    current_pillow = Decimal(
        data.get(
            "current_pillow",
            "0",
        )
    )

    # --------------------------------------------------------
    # Раскладываем уже существующую подушку по слоям
    # водопадом.
    # --------------------------------------------------------

    pillow_minimum = Decimal("0")
    pillow_force = Decimal("0")
    pillow_stabilizer = Decimal("0")

    remaining = current_pillow

    if settings.has_debts:

        minimum_limit = (
            settings.minimum_reserve_limit
        )

        pillow_minimum = min(
            remaining,
            minimum_limit,
        )

        remaining -= pillow_minimum

    force_limit = (
        settings.force_majeure_limit
    )

    pillow_force = min(
        remaining,
        force_limit,
    )

    remaining -= pillow_force

    if (
        settings.employment_type
        == "Фрилансер"
    ):

        stabilizer_limit = (
            settings.stabilizer_full_limit
        )

        pillow_stabilizer = min(
            remaining,
            stabilizer_limit,
        )

    return AllocatorState(
        life_balance=Decimal(
            data.get(
                "current_life_balance",
                "0",
            )
        ),

        accumulated_minimum_payments=
            Decimal(
                data.get(
                    "current_minimum_payments",
                    "0",
                )
            ),

        pillow_minimum=
            pillow_minimum,

        pillow_force_majeure=
            pillow_force,

        pillow_stabilizer=
            pillow_stabilizer,
    )


# ============================================================
# ПОДТВЕРЖДЕНИЕ
# ============================================================


async def show_confirmation(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    settings = build_settings_from_data(
        data
    )

    state_object = build_state_from_data(
        data,
        settings,
    )

    try:
        allocator = FinancialAllocator(
            settings=settings,
            state=state_object,
        )

    except ValueError as error:

        await message.answer(
            "⚠️ В настройках обнаружена ошибка:\n\n"
            f"<code>{escape(str(error))}</code>\n\n"
            "Настройка не сохранена."
        )

        return

    mode = allocator.active_mode()

    mode_name = {
        1: "🟤 1",
        2: "🔴 2",
        3: "🟠 3",
        4: "🟣 4",
        5: "🔵 5",
        6: "🟢 6",
    }[mode]

    categories = allocator.life_category_targets()

    category_text = "\n".join(
        f"❤️ {escape(name)} — {rub(amount)}"
        for name, amount in categories.items()
        if name != "Мин. платеж"
    )

    credit_total = (
        settings.minimum_payment_total
    )

    goals_text = ""

    if settings.goals:

        goals_text = "\n".join(
            f"⭐️ {escape(goal.name)} — "
            f"{goal.percentage}%"
            for goal in settings.goals
        )

    else:

        goals_text = (
            "⭐️ Цели (всего) — автоматически"
        )

    credit_text = ""

    if settings.credits:

        credit_lines = [
            f"💳 {escape(credit.name)} — "
            f"{rub(credit.principal_balance)}, "
            f"мин. платёж {rub(credit.minimum_payment)}"
            for credit in settings.credits
        ]

        credit_text = (
            "\n\n<b>Кредиты</b>\n"
            + "\n".join(
                credit_lines
            )
            + f"\n\nМинимальные платежи всего: "
              f"<b>{rub(credit_total)}</b>"
        )

    tax_types = (
        ", ".join(
            settings.taxable_income_types
        )
        if settings.taxable_income_types
        else "не используются"
    )

    await state.set_state(
        SetupStates.confirmation
    )

    await message.answer(
        "📋 <b>ПРОВЕРЬТЕ НАСТРОЙКИ</b>\n\n"

        f"👤 Профиль: "
        f"<b>{escape(settings.employment_type)}</b>\n"

        f"💳 Долги: "
        f"<b>{'есть' if settings.has_debts else 'нет'}</b>\n\n"

        f"🔴 Обязательная жизнь: "
        f"<b>{rub(settings.critical_life)}</b>\n"

        f"🟢 Бытовой резерв: "
        f"<b>{rub(settings.household_reserve)}</b>\n"

        f"🔄 Устойчивая жизнь: "
        f"<b>{rub(settings.household_life)}</b>\n"

        f"💰 Средний доход: "
        f"<b>{rub(settings.average_income)}</b>\n\n"

        f"🏛 Налог: "
        f"<b>{settings.tax_rate}%</b>\n"

        f"Типы дохода для налога: "
        f"<b>{escape(tax_types)}</b>\n\n"

        f"<b>Обязательные категории</b>\n"
        f"{category_text}\n"

        + credit_text
        +

        f"\n\n<b>Цели</b>\n"
        f"{goals_text}\n\n"

        f"🛟 Подушка сейчас: "
        f"<b>{rub(state_object.pillow_balance)}</b>\n"

        f"🔄 Баланс жизни сейчас: "
        f"<b>{rub(state_object.life_balance)}</b>\n\n"

        f"⚙️ Стартовый финансовый режим: "
        f"<b>{mode_name}</b>\n\n"

        "Если всё верно — сохраните профиль.",
        reply_markup=keyboard([
            [
                (
                    "✅ Сохранить",
                    "confirm:save",
                )
            ],
            [
                (
                    "🔄 Начать заново",
                    "confirm:restart",
                )
            ],
        ]),
    )


# ============================================================
# СОХРАНЕНИЕ ПРОФИЛЯ
# ============================================================


@router.callback_query(
    SetupStates.confirmation,
    F.data == "confirm:save"
)
async def confirm_save(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    settings = build_settings_from_data(
        data
    )

    state_object = build_state_from_data(
        data,
        settings,
    )

    allocator = FinancialAllocator(
        settings=settings,
        state=state_object,
    )

    telegram_id = (
        callback.from_user.id
    )

    db.save_allocator(
        telegram_id,
        allocator,
    )

    await state.clear()

    snapshot = (
        allocator.get_state_snapshot()
    )

    mode = snapshot[
        "mode_name"
    ]

    next_info = (
        allocator.next_mode_info()
    )

    next_text = ""

    if next_info:

        next_text = (
            f"\n🏆 До следующего режима "
            f"{next_info['next_name']} осталось: "
            f"<b>{rub(next_info['remaining'])}</b>"
        )

    await callback.message.answer(
        "✅ <b>ФИНАНСОВЫЙ ПРОФИЛЬ СОХРАНЁН</b>\n\n"

        "Аллокатор готов к работе.\n\n"

        f"⚙️ Текущий режим: <b>{mode}</b>"
        f"{next_text}\n\n"

        "Теперь можно добавить первое поступление денег "
        "или посмотреть текущее финансовое состояние.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(
    SetupStates.confirmation,
    F.data == "confirm:restart"
)
async def confirm_restart(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await show_intro(
        callback.message,
        state,
    )
