from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
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
    income_method = State()
    income_month_amount = State()

    # Калькулятор Критического минимума
    km_menu = State()
    km_item_name = State()
    km_item_amount = State()
    km_item_period = State()
    km_custom_period = State()
    km_edit_name = State()
    km_edit_amount = State()
    km_edit_period = State()
    km_edit_custom_period = State()
    km_override_amount = State()

    # Как физически хранить деньги Критического минимума
    km_envelopes_menu = State()
    km_envelope_name = State()

    # Калькулятор Бытового резерва
    br_menu = State()
    br_item_name = State()
    br_item_amount = State()
    br_item_period = State()
    br_custom_period = State()
    br_edit_name = State()
    br_edit_amount = State()
    br_edit_period = State()
    br_edit_custom_period = State()
    br_override_amount = State()

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
            ("Да", f"{prefix}:yes"),
            ("Нет", f"{prefix}:no"),
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
                        "Настроить заново",
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
# ДИНАМИЧЕСКАЯ ПЕРВИЧНАЯ НАСТРОЙКА
# ============================================================


KM_CATEGORIES = {
    "housing": (
        "Жильё, Аренда, ЖКХ",
        "Сюда относятся аренда квартиры или дома, обязательный платёж по ипотеке, ЖКХ, "
        "аренда студии, кабинета или рабочего места, без которых невозможно получать основной доход, "
        "и другие обязательные расходы на помещение.",
    ),
    "food": (
        "Питание",
        "Продукты, питьевая вода и другое питание, без которого нельзя нормально прожить месяц.",
    ),
    "communication": (
        "Связь",
        "Мобильная связь, домашний интернет, необходимый VPN и действительно необходимые подписки.",
    ),
    "transport": (
        "Транспорт",
        "Обязательный общественный транспорт или необходимые расходы на автомобиль.",
    ),
    "education": (
        "Образование",
        "Колледж, ВУЗ или другой платёж, который нельзя без последствий прекратить. "
        "Курсы и репетитора для души лучше учитывать в Бытовом резерве.",
    ),
    "children": (
        "Дети",
        "Детский сад, школа, питание и другие действительно обязательные расходы на детей.",
    ),
    "pets": (
        "Питомцы",
        "Корм, наполнитель и другие регулярные обязательные расходы на питомцев.",
    ),
    "health": (
        "Здоровье",
        "Сюда относятся лекарства при простуде и других заболеваниях, стоматолог, "
        "плановые врачи, анализы, необходимые товары из аптеки, витамины и другие "
        "расходы на здоровье. Если трата повторяется нерегулярно, укажите сумму за "
        "несколько месяцев или за год — Аллокатор сам приведёт её к среднемесячной.",
    ),
    "other": (
        "Другое",
        "Любой обязательный расход, которого нет в списке. Если при резком падении дохода от него можно отказаться на несколько месяцев — это, скорее всего, не Критический минимум.",
    ),
}


BR_CATEGORIES = {
    "clothes": ("Одежда, обувь и аксессуары", "Одежда, обувь, сезонные вещи, сумки и другие покупки, которые нужны в обычной жизни, но возникают не каждый месяц."),
    "care": ("Стрижка и уход", "Парикмахерская, базовый уход и другие периодические траты на внешний вид."),
    "gym": ("Спортзал", "Абонемент в спортзал, бассейн, секции и другие регулярные расходы на физическую активность."),
    "leisure": ("Такси, кафе, развлечения", "Такси, кафе, кино, встречи и другие расходы обычной жизни, которые при серьёзном падении дохода можно временно сократить."),
    "courses": ("Образовательные курсы для души", "Необязательные курсы, хобби и занятия, которые полезны или приятны, но не являются обязательной частью Критического минимума."),
    "repairs": ("Мелкий ремонт и бытовые траты", "Мелкий ремонт, расходники, бытовые покупки и другие нерегулярные траты по дому."),
    "comfort": ("Домашний уют", "Текстиль, посуда, декор, растения и другие покупки, которые делают дом удобнее и приятнее."),
    "other": ("Другое", "Любой нерегулярный расход нормальной жизни, который не относится к Критическому минимуму, но периодически требует денег."),
}


def route_total(data: dict) -> int:
    return 9 if data.get("has_debts") else 8


def progress_bar(done: int, total: int) -> str:
    done = max(0, min(done, total))
    return "💎" * done + "➖" * (total - done)


def setup_progress(data: dict, done: int) -> str:
    return progress_bar(done, route_total(data))


def money2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_up_thousand(value: Decimal) -> Decimal:
    value = Decimal(value)
    return (
        (value / Decimal("1000")).to_integral_value(rounding=ROUND_CEILING)
        * Decimal("1000")
    )


def km_group_totals(items: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in items:
        label = item["category_label"]
        result[label] = money2(
            result.get(label, Decimal("0"))
            + Decimal(item["monthly"])
        )
    return result


def default_km_storage(item: dict) -> dict:
    """
    Рекомендует способ хранения конкретного расхода Критического минимума.

    salary   — деньги остаются на операционном счёте «Зарплата»;
    separate — деньги физически изолируются в отдельном конверте/накопительном счёте.
    """
    category = item.get("category")
    name = (item.get("name") or item.get("category_label") or "Расход").strip()
    lowered = name.lower()
    months = Decimal(str(item.get("months", "1")))

    storage = "salary"
    envelope_name = None

    # Эти деньги не должны конкурировать с обычным потреблением.
    if category == "health":
        storage = "separate"
        envelope_name = "Здоровье"
    elif category == "pets":
        storage = "separate"
        envelope_name = "Питомцы"

    # Аренда жилья — отдельный конверт независимо от периодичности.
    elif category == "housing":
        if "ипотек" in lowered:
            storage = "separate"
            envelope_name = "Ипотека"
        elif any(token in lowered for token in ("студи", "кабинет", "офис", "рабоч")):
            storage = "separate"
            envelope_name = name
        elif "аренд" in lowered or ("квартир" in lowered and "жкх" not in lowered):
            storage = "separate"
            envelope_name = "Квартира"
        elif any(token in lowered for token in ("жкх", "коммун", "свет", "электр", "вода", "газ")):
            storage = "salary"
        elif months > 1:
            storage = "separate"
            envelope_name = name

    # Продукты и связь обычно тратятся прямо в течение месяца.
    elif category in {"food", "communication"}:
        storage = "salary"

    # Для транспорта, детей, образования и прочих расходов периодичность
    # даёт хорошую рекомендацию: крупный будущий платёж лучше копить отдельно.
    elif category in {"transport", "children", "education", "other"}:
        if months > 1:
            storage = "separate"
            defaults = {
                "transport": "Транспорт",
                "children": "Дети",
                "education": "Образование",
            }
            envelope_name = defaults.get(category, name)

    return {
        "item_name": name,
        "category": category,
        "category_label": item.get("category_label", name),
        "monthly": str(money2(Decimal(item["monthly"]))),
        "storage": storage,
        "envelope_name": envelope_name,
    }


def build_default_km_storage(items: list[dict]) -> list[dict]:
    return [default_km_storage(item) for item in items]


def life_categories_from_storage(storage_items: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in storage_items:
        if item.get("storage") != "separate":
            continue
        envelope = (item.get("envelope_name") or item.get("item_name") or "Конверт").strip()
        amount = Decimal(item["monthly"])
        result[envelope] = money2(result.get(envelope, Decimal("0")) + amount)
    return result


def km_storage_summary(storage_items: list[dict], critical_life: Decimal) -> str:
    separate = life_categories_from_storage(storage_items)
    separate_sum = sum(separate.values(), Decimal("0"))
    salary = money2(critical_life - separate_sum)

    separate_lines = [
        f"• {escape(name)} — {rub(amount)}"
        for name, amount in separate.items()
    ]

    salary_items = [
        item["item_name"]
        for item in storage_items
        if item.get("storage") == "salary"
    ]

    text = "<b>Отдельные конверты</b>\n"
    text += "\n".join(separate_lines) if separate_lines else "• нет"
    text += "\n\n<b>Зарплата</b> — " + rub(salary)
    if salary_items:
        text += "\n" + "• " + "\n• ".join(escape(name) for name in salary_items)
    return text


def br_group_totals(items: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in items:
        label = item["category_label"]
        result[label] = money2(result.get(label, Decimal("0")) + Decimal(item["monthly"]))
    return result


async def remove_setup_button(callback: CallbackQuery):
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data == "setup:start")
async def setup_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await remove_old_intro_button(callback)

    await state.clear()
    await state.update_data(
        credits=[],
        goals=[],
        debt_strategy="Лавина",
        calculate_interest_savings=False,
        developer_mode=False,
        current_minimum_payments="0",
        km_items=[],
        km_storage_items=[],
        br_items=[],
    )
    await state.set_state(SetupStates.employment)

    await callback.message.answer(
        f"{progress_bar(1, 2)}\n\n"
        "<b>КАК ВЫ ПОЛУЧАЕТЕ ОСНОВНОЙ ДОХОД?</b>\n\n"
        "Выберите <b>Наёмный</b>, если у вас регулярная зарплата от работодателя.\n\n"
        "Выберите <b>Фрилансер</b>, если доход заметно меняется от месяца к месяцу: "
        "проекты, заказы, самозанятость, небольшой бизнес и другие нерегулярные поступления.",
        reply_markup=keyboard([
            [("Наёмный", "employment:employee")],
            [("Фрилансер", "employment:freelancer")],
        ]),
    )


@router.callback_query(SetupStates.employment, F.data.startswith("employment:"))
async def save_employment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await remove_setup_button(callback)

    employment = "Наёмный" if callback.data == "employment:employee" else "Фрилансер"
    await state.update_data(employment_type=employment)
    await state.set_state(SetupStates.has_debts)

    await callback.message.answer(
        f"{progress_bar(2, 2)}\n\n"
        "<b>ЕСТЬ КРЕДИТЫ ИЛИ ДОЛГИ?</b>\n\n"
        "Сейчас учитываем потребительские и автокредиты, рассрочки и задолженность по кредитным картам.\n\n"
        "Кредитная карта без задолженности долгом не считается.",
        reply_markup=keyboard([
            [("Есть", "debts:yes")],
            [("Нет", "debts:no")],
        ]),
    )


@router.callback_query(SetupStates.has_debts, F.data.startswith("debts:"))
async def save_debts(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await remove_setup_button(callback)

    has_debts = callback.data == "debts:yes"
    await state.update_data(has_debts=has_debts, credits=[])
    await ask_income(callback.message, state)


async def ask_income(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(SetupStates.average_income)
    bar = setup_progress(data, 3)

    if data["employment_type"] == "Наёмный":
        text = (
            f"{bar}\n\n"
            "<b>КАКОЙ ДОХОД ВЫ СТАБИЛЬНО ПОЛУЧАЕТЕ КАЖДЫЙ МЕСЯЦ?</b>\n\n"
            "Нужен <b>минимальный регулярный доход</b>, на который вы с большой вероятностью можете рассчитывать каждый месяц.\n\n"
            "Постоянный оклад и регулярные ежемесячные надбавки учитывайте. "
            "Премии, годовые бонусы, гранты, случайные подработки и другие нерегулярные поступления — нет. "
            "Для Аллокатора это сверхдоход.\n\n"
            "Не оценивайте сумму по памяти. Откройте историю поступлений за последние 6–12 месяцев и найдите устойчивую месячную базу.\n\n"
            "Введите сумму.\n\n"
            "Например: <code>180000</code>"
        )
    else:
        text = (
            f"{bar}\n\n"
            "<b>КАКОЙ У ВАС СРЕДНИЙ ДОХОД?</b>\n\n"
            "Для нерегулярного дохода нужна реальная средняя за последние 6–12 месяцев. "
            "Лучший месяц не считается вашей новой нормой.\n\n"
            "Откройте историю поступлений и посчитайте среднее. Если хотите, можете отправить готовую сумму одним сообщением.\n\n"
            "Введите среднемесячный доход.\n\n"
            "Например: <code>180000</code>"
        )

    await message.answer(text)


@router.message(SetupStates.average_income)
async def save_average_income(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму. Например: <code>180000</code>")
        return

    await state.update_data(average_income=str(money2(value)))
    await ask_tax(message, state)


async def ask_tax(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(SetupStates.tax_rate)
    await message.answer(
        f"{setup_progress(data, 4)}\n\n"
        "<b>НУЖНО ЛИ ВАМ САМОСТОЯТЕЛЬНО ОТКЛАДЫВАТЬ НАЛОГ С КАКИХ-ТО ДОХОДОВ?</b>\n\n"
        "С зарплаты налог обычно удерживает работодатель. Но подработки, частные заказы, самозанятость "
        "или другие поступления могут требовать отдельного резерва.\n\n"
        "Налог конкретного поступления потом можно будет изменить перед распределением.",
        reply_markup=keyboard([
            [("Да", "taxsetup:yes")],
            [("Нет", "taxsetup:no")],
        ]),
    )


@router.callback_query(SetupStates.tax_rate, F.data.startswith("taxsetup:"))
async def tax_setup_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await remove_setup_button(callback)

    if callback.data == "taxsetup:no":
        await state.update_data(tax_rate="0", taxable_income_types=[])
        await start_critical_minimum(callback.message, state)
        return

    data = await state.get_data()
    await callback.message.answer(
        f"{setup_progress(data, 4)}\n\n"
        "<b>КАКУЮ СТАВКУ ИСПОЛЬЗОВАТЬ ПО УМОЛЧАНИЮ?</b>\n\n"
        "Введите число без знака %. Например: <code>6</code>."
    )


@router.message(SetupStates.tax_rate)
async def save_tax_rate(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0 or value > 100:
        await message.answer("Введите ставку от 0 до 100. Например: <code>6</code>")
        return

    await state.update_data(tax_rate=str(value))
    if value == 0:
        await state.update_data(taxable_income_types=[])
        await start_critical_minimum(message, state)
        return

    await state.set_state(SetupStates.taxable_types)
    data = await state.get_data()
    await message.answer(
        f"{setup_progress(data, 4)}\n\n"
        "<b>С КАКИХ ПОСТУПЛЕНИЙ ОБЫЧНО НУЖНО РЕЗЕРВИРОВАТЬ НАЛОГ?</b>\n\n"
        "Введите названия через запятую. Например:\n"
        "<code>Халтура, Частник, Фриланс</code>\n\n"
        "Это правило по умолчанию: налог отдельного поступления можно будет изменить вручную."
    )


@router.message(SetupStates.taxable_types)
async def save_taxable_types(message: Message, state: FSMContext):
    types = [item.strip() for item in message.text.split(",") if item.strip()]
    if not types:
        await message.answer("Укажите хотя бы один тип дохода.")
        return
    await state.update_data(taxable_income_types=types)
    await start_critical_minimum(message, state)


async def start_critical_minimum(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.update_data(km_items=[])
    await state.set_state(SetupStates.km_menu)
    await show_km_menu(message, state, intro=True)


async def show_km_menu(message: Message, state: FSMContext, intro: bool = False):
    data = await state.get_data()
    items = data.get("km_items", [])
    exact = money2(sum((Decimal(item["monthly"]) for item in items), Decimal("0")))

    item_lines = [
        f"• {escape(item['name'])} — {rub(Decimal(item['monthly']))} / мес."
        for item in items
    ]
    summary = ""
    if item_lines:
        summary = "\n\n<b>Уже добавлено</b>\n" + "\n".join(item_lines)
        summary += f"\n\nСейчас найдено: <b>{rub(exact)}</b> / мес."

    intro_text = ""
    if intro:
        intro_text = (
            "\n\nКритический минимум — обязательная стоимость вашей жизни. "
            "Не угадывайте суммы: открывайте банковскую аналитику, договоры и тарифы.\n\n"
            "Если при резком падении дохода от расхода можно без серьёзных последствий отказаться на несколько месяцев, "
            "скорее всего, ему место в Бытовом резерве, а не здесь.\n\n"
            "<b>Одну и ту же кнопку можно нажимать несколько раз.</b> Например, в «Жильё, Аренда, ЖКХ» "
            "можно отдельно добавить Квартиру, ЖКХ, Ипотеку и Студию."
        )

    rows = [
        [("Жильё, Аренда, ЖКХ", "kmcat:housing"), ("Питание", "kmcat:food")],
        [("Связь", "kmcat:communication"), ("Транспорт", "kmcat:transport")],
        [("Образование", "kmcat:education"), ("Дети", "kmcat:children")],
        [("Питомцы", "kmcat:pets"), ("Здоровье", "kmcat:health")],
        [("Другое", "kmcat:other")],
    ]
    if items:
        rows.append([("Редактировать расходы", "kmedit:list")])
    rows.append([("Рассчитать минимум", "km:finish")])

    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>ПОСЧИТАЕМ ВАШ КРИТИЧЕСКИЙ МИНИМУМ</b>"
        + intro_text
        + summary,
        reply_markup=keyboard(rows),
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmcat:"))
async def choose_km_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key = callback.data.split(":", 1)[1]
    if key not in KM_CATEGORIES:
        return

    label, hint = KM_CATEGORIES[key]
    await state.update_data(pending_km_category=key, pending_km_category_label=label)
    await state.set_state(SetupStates.km_item_name)
    data = await state.get_data()
    await callback.message.answer(
        f"{setup_progress(data, 5)}\n\n"
        f"<b>{escape(label.upper())}</b>\n\n"
        f"{escape(hint)}\n\n"
        "Введите короткое название расхода. Например: <code>Аренда квартиры</code>, <code>Ипотека</code>, <code>Студия</code> или <code>ЖКХ</code>.",
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


@router.message(SetupStates.km_item_name)
async def km_item_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if parse_decimal(name) is not None:
        await message.answer(
            "Похоже, вы ввели сумму вместо названия. Сначала напишите короткое название, "
            "например <code>Квартира</code>. Сумму я спрошу следующим сообщением."
        )
        return
    if len(name) < 2:
        await message.answer("Введите понятное название расхода.")
        return
    await state.update_data(pending_km_item_name=name)
    await state.set_state(SetupStates.km_item_amount)
    data = await state.get_data()
    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        f"<b>{escape(name.upper())}</b>\n\n"
        "Сколько вы тратите? Введите сумму. Период укажем следующим сообщением.",
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


@router.message(SetupStates.km_item_amount)
async def km_item_amount(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму.")
        return
    await state.update_data(pending_km_item_amount=str(value))
    await state.set_state(SetupStates.km_item_period)
    data = await state.get_data()
    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>ЗА КАКОЙ ПЕРИОД ЭТА СУММА?</b>",
        reply_markup=keyboard([
            [("В месяц", "kmperiod:1"), ("За 3 месяца", "kmperiod:3")],
            [("За 6 месяцев", "kmperiod:6"), ("В год", "kmperiod:12")],
            [("Другой период", "kmperiod:custom")],
            [("Отмена", "km:cancel")],
        ]),
    )


@router.callback_query(SetupStates.km_item_period, F.data.startswith("kmperiod:"))
async def km_item_period(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await state.set_state(SetupStates.km_custom_period)
        data = await state.get_data()
        await callback.message.answer(
            f"{setup_progress(data, 5)}\n\n"
            "<b>ЗА СКОЛЬКО МЕСЯЦЕВ?</b>\n\n"
            "Введите число месяцев. Например: <code>2</code> или <code>18</code>.",
            reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
        )
        return
    await save_km_item(callback.message, state, Decimal(value))


@router.message(SetupStates.km_custom_period)
async def km_custom_period(message: Message, state: FSMContext):
    months = parse_decimal(message.text)
    if months is None or months <= 0:
        await message.answer("Введите число месяцев больше нуля.")
        return
    await save_km_item(message, state, months)


async def save_km_item(message: Message, state: FSMContext, months: Decimal):
    data = await state.get_data()
    amount = Decimal(data["pending_km_item_amount"])
    monthly = money2(amount / months)
    item = {
        "category": data["pending_km_category"],
        "category_label": data["pending_km_category_label"],
        "name": data["pending_km_item_name"],
        "amount": str(amount),
        "months": str(months),
        "monthly": str(monthly),
    }
    items = list(data.get("km_items", []))
    items.append(item)
    await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu)

    index = len(items) - 1
    await message.answer(
        f"<b>{escape(item['name'])}</b> — {rub(monthly)} / мес.",
        reply_markup=keyboard([[('Изменить', f'kmedit:item:{index}'), ('Удалить', f'kmedit:delete:{index}')]])
    )
    await show_km_menu(message, state)


@router.callback_query(SetupStates.km_menu, F.data == "km:finish")
async def finish_km(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    items = data.get("km_items", [])
    groups = km_group_totals(items)
    exact = money2(sum(groups.values(), Decimal("0")))

    if exact <= 0:
        await callback.message.answer("Добавьте хотя бы один обязательный расход.")
        return

    rounded = round_up_thousand(exact)
    storage_items = build_default_km_storage(items)

    await state.update_data(
        critical_life=str(rounded),
        critical_life_exact=str(exact),
        km_storage_items=storage_items,
    )

    data = await state.get_data()
    lines = [f"• {escape(name)} — {rub(value)}" for name, value in groups.items()]

    await state.set_state(SetupStates.km_menu)
    await callback.message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>КРИТИЧЕСКИЙ МИНИМУМ РАССЧИТАН</b>\n\n"
        + "\n".join(lines)
        + f"\n\nПо категориям — <b>{rub(exact)}</b>\n"
        + f"Критический минимум — <b>{rub(rounded)}</b>\n\n"
        "Сумма округлена вверх до ближайшей 1 000 ₽. Можно исправить расходы или увеличить итоговый минимум, если хотите дополнительный запас.",
        reply_markup=keyboard([
            [('Продолжить', 'kmfinal:continue')],
            [('Редактировать расходы', 'kmedit:list')],
            [('Изменить сумму КМ', 'kmfinal:override')],
        ]),
    )


async def clear_pending_km(state: FSMContext):
    await state.update_data(
        pending_km_category=None,
        pending_km_category_label=None,
        pending_km_item_name=None,
        pending_km_item_amount=None,
        pending_km_edit_index=None,
    )


@router.callback_query(F.data == "km:cancel")
async def cancel_km_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Ввод отменён")
    await clear_pending_km(state)
    await state.set_state(SetupStates.km_menu)
    await show_km_menu(callback.message, state)


@router.callback_query(SetupStates.km_menu, F.data == "kmedit:list")
async def km_edit_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    items = data.get("km_items", [])
    if not items:
        await callback.message.answer("Пока нечего редактировать.")
        return
    rows = [[(f"{item['name']} — {rub(Decimal(item['monthly']))}", f"kmedit:item:{i}")] for i, item in enumerate(items)]
    rows.append([("Назад", "kmedit:back")])
    await callback.message.answer("<b>ЧТО ИЗМЕНИТЬ?</b>", reply_markup=keyboard(rows))


@router.callback_query(F.data == "kmedit:back")
async def km_edit_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SetupStates.km_menu)
    await show_km_menu(callback.message, state)


@router.callback_query(F.data.startswith("kmedit:item:"))
async def km_edit_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = data.get("km_items", [])
    if not (0 <= index < len(items)):
        await show_km_menu(callback.message, state)
        return
    item = items[index]
    await state.update_data(pending_km_edit_index=index)
    await callback.message.answer(
        f"<b>{escape(item['name'])}</b>\n\n"
        f"Категория — {escape(item['category_label'])}\n"
        f"Исходная сумма — {rub(Decimal(item['amount']))}\n"
        f"Период — {item['months']} мес.\n"
        f"В расчёте — <b>{rub(Decimal(item['monthly']))} / мес.</b>",
        reply_markup=keyboard([
            [("Изменить название", f"kmedit:name:{index}")],
            [("Изменить сумму", f"kmedit:amount:{index}")],
            [("Изменить период", f"kmedit:period:{index}")],
            [("Удалить", f"kmedit:delete:{index}")],
            [("Назад", "kmedit:list")],
        ]),
    )


@router.callback_query(F.data.startswith("kmedit:delete:"))
async def km_delete_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Удалено")
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = list(data.get("km_items", []))
    if 0 <= index < len(items):
        items.pop(index)
        await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu)
    await show_km_menu(callback.message, state)


@router.callback_query(F.data.startswith("kmedit:name:"))
async def km_edit_name_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(pending_km_edit_index=int(callback.data.rsplit(":", 1)[1]))
    await state.set_state(SetupStates.km_edit_name)
    await callback.message.answer("Введите новое название.", reply_markup=keyboard([[('Отмена', 'km:cancel')]]))


@router.message(SetupStates.km_edit_name)
async def km_edit_name_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if parse_decimal(name) is not None:
        await message.answer("Похоже, это сумма. Введите название словами.")
        return
    if len(name) < 2:
        await message.answer("Введите понятное название.")
        return
    data = await state.get_data(); index = int(data.get("pending_km_edit_index", -1)); items = list(data.get("km_items", []))
    if 0 <= index < len(items):
        item = dict(items[index]); item["name"] = name; items[index] = item; await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu)
    await show_km_menu(message, state)


@router.callback_query(F.data.startswith("kmedit:amount:"))
async def km_edit_amount_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.update_data(pending_km_edit_index=int(callback.data.rsplit(":",1)[1])); await state.set_state(SetupStates.km_edit_amount)
    await callback.message.answer("Введите новую сумму.", reply_markup=keyboard([[('Отмена','km:cancel')]]))


@router.message(SetupStates.km_edit_amount)
async def km_edit_amount_save(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму."); return
    data=await state.get_data(); index=int(data.get("pending_km_edit_index",-1)); items=list(data.get("km_items",[]))
    if 0 <= index < len(items):
        item=dict(items[index]); item["amount"]=str(value); item["monthly"]=str(money2(value/Decimal(item["months"]))); items[index]=item; await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu); await show_km_menu(message,state)


@router.callback_query(F.data.startswith("kmedit:period:"))
async def km_edit_period_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.update_data(pending_km_edit_index=int(callback.data.rsplit(":",1)[1])); await state.set_state(SetupStates.km_edit_period)
    await callback.message.answer("<b>НОВЫЙ ПЕРИОД</b>", reply_markup=keyboard([[('1 месяц','kmeditperiod:1'),('3 месяца','kmeditperiod:3')],[('6 месяцев','kmeditperiod:6'),('12 месяцев','kmeditperiod:12')],[('Другой','kmeditperiod:custom')],[('Отмена','km:cancel')]]))


@router.callback_query(SetupStates.km_edit_period, F.data.startswith("kmeditperiod:"))
async def km_edit_period_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); value=callback.data.split(":",1)[1]
    if value == 'custom':
        await state.set_state(SetupStates.km_edit_custom_period); await callback.message.answer("Введите количество месяцев.", reply_markup=keyboard([[('Отмена','km:cancel')]])); return
    await apply_km_edit_period(callback.message,state,Decimal(value))


@router.message(SetupStates.km_edit_custom_period)
async def km_edit_custom_period_save(message: Message, state: FSMContext):
    months=parse_decimal(message.text)
    if months is None or months <= 0: await message.answer("Введите число месяцев больше нуля."); return
    await apply_km_edit_period(message,state,months)


async def apply_km_edit_period(message: Message, state: FSMContext, months: Decimal):
    data=await state.get_data(); index=int(data.get("pending_km_edit_index",-1)); items=list(data.get("km_items",[]))
    if 0 <= index < len(items):
        item=dict(items[index]); item["months"]=str(months); item["monthly"]=str(money2(Decimal(item["amount"])/months)); items[index]=item; await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu); await show_km_menu(message,state)


@router.callback_query(SetupStates.km_menu, F.data == "kmfinal:continue")
async def km_final_continue(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await show_km_storage_review(callback.message,state)


@router.callback_query(SetupStates.km_menu, F.data == "kmfinal:override")
async def km_override_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); data=await state.get_data(); await state.set_state(SetupStates.km_override_amount)
    await callback.message.answer(f"Текущий Критический минимум — <b>{rub(Decimal(data['critical_life']))}</b>.\n\nВведите новую сумму. Она не может быть меньше расчётной суммы, округлённой вверх.", reply_markup=keyboard([[('Отмена','km:cancel')]]))


@router.message(SetupStates.km_override_amount)
async def km_override_save(message: Message, state: FSMContext):
    value=parse_decimal(message.text); data=await state.get_data(); minimum=round_up_thousand(Decimal(data['critical_life_exact']))
    if value is None or value < minimum:
        await message.answer(f"Сумма не может быть меньше <b>{rub(minimum)}</b>."); return
    value=round_up_thousand(value); await state.update_data(critical_life=str(value)); await state.set_state(SetupStates.km_menu)
    await message.answer(f"Критический минимум установлен: <b>{rub(value)}</b>.", reply_markup=keyboard([[('Продолжить','kmfinal:continue')],[('Редактировать расходы','kmedit:list')]]))


async def show_km_storage_review(message: Message, state: FSMContext):
    data = await state.get_data()
    storage_items = data.get("km_storage_items", [])
    critical = Decimal(data["critical_life"])
    await state.set_state(SetupStates.km_envelopes_menu)

    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>КАК ХРАНИТЬ ДЕНЬГИ НА КРИТИЧЕСКИЙ МИНИМУМ</b>\n\n"
        "Аллокатор отделяет деньги, которые важно не смешивать с повседневными расходами. "
        "Остальное остаётся на операционном счёте «Зарплата».\n\n"
        + km_storage_summary(storage_items, critical)
        + "\n\nЭто рекомендуемая структура. Её можно изменить под ваши банковские счета и привычки.",
        reply_markup=keyboard([
            [("Всё устраивает", "kmstorage:accept")],
            [("Изменить", "kmstorage:edit")],
        ]),
    )


@router.callback_query(SetupStates.km_envelopes_menu, F.data == "kmstorage:accept")
async def accept_km_storage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    categories = life_categories_from_storage(data.get("km_storage_items", []))
    await state.update_data(
        life_categories={name: str(value) for name, value in categories.items()}
    )
    await start_household_reserve(callback.message, state)


@router.callback_query(SetupStates.km_envelopes_menu, F.data == "kmstorage:edit")
async def edit_km_storage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_km_storage_edit_menu(callback.message, state)


async def show_km_storage_edit_menu(message: Message, state: FSMContext):
    data = await state.get_data()
    items = data.get("km_storage_items", [])
    await state.set_state(SetupStates.km_envelopes_menu)

    rows = []
    for index, item in enumerate(items):
        if item.get("storage") == "separate":
            destination = item.get("envelope_name") or "Отдельно"
        else:
            destination = "Зарплата"
        label = f"{item['item_name']} → {destination}"
        if len(label) > 50:
            label = label[:47] + "…"
        rows.append([(label, f"kmstorage:item:{index}")])

    rows.append([("Готово", "kmstorage:review")])

    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>ИЗМЕНИТЬ СПОСОБ ХРАНЕНИЯ</b>\n\n"
        "Нажмите на расход, чтобы оставить его на «Зарплате», вынести в отдельный конверт или изменить название конверта.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(SetupStates.km_envelopes_menu, F.data.startswith("kmstorage:item:"))
async def km_storage_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = data.get("km_storage_items", [])
    if index < 0 or index >= len(items):
        return

    item = items[index]
    current = (
        f"отдельный конверт «{escape(item.get('envelope_name') or item['item_name'])}»"
        if item.get("storage") == "separate"
        else "счёт «Зарплата»"
    )

    rows = []
    if item.get("storage") == "separate":
        rows.append([("Оставить на Зарплате", f"kmstorage:salary:{index}")])
        rows.append([("Изменить название конверта", f"kmstorage:rename:{index}")])
    else:
        rows.append([("Создать отдельный конверт", f"kmstorage:separate:{index}")])
    rows.append([("Назад", "kmstorage:edit")])

    await callback.message.answer(
        f"<b>{escape(item['item_name'].upper())}</b>\n\n"
        f"Среднемесячно — <b>{rub(Decimal(item['monthly']))}</b>\n"
        f"Сейчас: <b>{current}</b>.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(SetupStates.km_envelopes_menu, F.data.startswith("kmstorage:salary:"))
async def km_storage_to_salary(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = list(data.get("km_storage_items", []))
    if 0 <= index < len(items):
        items[index] = dict(items[index])
        items[index]["storage"] = "salary"
        items[index]["envelope_name"] = None
        await state.update_data(km_storage_items=items)
    await show_km_storage_edit_menu(callback.message, state)


@router.callback_query(SetupStates.km_envelopes_menu, F.data.startswith("kmstorage:separate:"))
async def km_storage_to_separate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = list(data.get("km_storage_items", []))
    if 0 <= index < len(items):
        item = dict(items[index])
        item["storage"] = "separate"
        if not item.get("envelope_name"):
            defaults = {
                "housing": "Квартира",
                "transport": "Транспорт",
                "health": "Здоровье",
                "pets": "Питомцы",
                "children": "Дети",
                "education": "Образование",
            }
            item["envelope_name"] = defaults.get(item.get("category"), item["item_name"])
        items[index] = item
        await state.update_data(km_storage_items=items)
    await show_km_storage_edit_menu(callback.message, state)


@router.callback_query(SetupStates.km_envelopes_menu, F.data.startswith("kmstorage:rename:"))
async def km_storage_rename(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(pending_km_storage_index=index)
    await state.set_state(SetupStates.km_envelope_name)
    await callback.message.answer(
        "<b>КАК НАЗВАТЬ КОНВЕРТ?</b>\n\n"
        "Например: <code>Кот</code>, <code>Безлимит</code> или <code>Квартира</code>."
    )


@router.message(SetupStates.km_envelope_name)
async def save_km_envelope_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 40:
        await message.answer("Введите название длиной от 2 до 40 символов.")
        return

    data = await state.get_data()
    index = int(data.get("pending_km_storage_index", -1))
    items = list(data.get("km_storage_items", []))
    if not (0 <= index < len(items)):
        await show_km_storage_review(message, state)
        return

    old_name = items[index].get("envelope_name")
    # Если несколько расходов уже объединены в один рекомендуемый конверт
    # (например корм + ветеринар = «Питомцы»), переименовываем весь конверт целиком.
    for i, raw in enumerate(items):
        item = dict(raw)
        if item.get("storage") == "separate" and item.get("envelope_name") == old_name:
            item["envelope_name"] = name
            items[i] = item

    await state.update_data(
        km_storage_items=items,
        pending_km_storage_index=None,
    )
    await show_km_storage_edit_menu(message, state)


@router.callback_query(SetupStates.km_envelopes_menu, F.data == "kmstorage:review")
async def review_km_storage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_km_storage_review(callback.message, state)


async def start_household_reserve(message: Message, state: FSMContext):
    await state.update_data(br_items=[])
    await state.set_state(SetupStates.br_menu)
    await show_br_menu(message, state, intro=True)


async def show_br_menu(message: Message, state: FSMContext, intro: bool = False):
    data = await state.get_data()
    items = data.get("br_items", [])
    exact = money2(sum((Decimal(item["monthly"]) for item in items), Decimal("0")))
    item_lines = [f"• {escape(item['name'])} — {rub(Decimal(item['monthly']))} / мес." for item in items]
    summary = ""
    if item_lines:
        summary = "\n\n<b>Уже добавлено</b>\n" + "\n".join(item_lines) + f"\n\nСейчас найдено: <b>{rub(exact)}</b> / мес."
    intro_text = ""
    if intro:
        intro_text = (
            "\n\nБытовой Резерв — это расходы нормальной жизни, которые трудно прогнозировать. "
            "Они возникают регулярно, но не каждый месяц. При серьёзном падении дохода их можно временно сократить или перенести.\n\n"
            "Откройте банковскую аналитику и введите суммы.\n\n"
            "<b>Одну и ту же кнопку можно нажимать несколько раз</b>, если внутри категории несколько разных расходов."
        )
    rows = [
        [("Одежда, обувь и аксессуары", "brcat:clothes")],
        [("Стрижка и уход", "brcat:care"), ("Спортзал", "brcat:gym")],
        [("Такси, кафе, развлечения", "brcat:leisure")],
        [("Образовательные курсы для души", "brcat:courses")],
        [("Мелкий ремонт и бытовые траты", "brcat:repairs")],
        [("Домашний уют", "brcat:comfort")],
        [("Другое", "brcat:other")],
    ]
    if items:
        rows.append([("Редактировать расходы", "bredit:list")])
    rows.append([("Рассчитать Бытовой резерв", "br:finish")])
    await message.answer(f"{setup_progress(data, 6)}\n\n<b>ПОСЧИТАЕМ ВАШ БЫТОВОЙ РЕЗЕРВ</b>" + intro_text + summary, reply_markup=keyboard(rows))


@router.callback_query(SetupStates.br_menu, F.data.startswith("brcat:"))
async def choose_br_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key = callback.data.split(":", 1)[1]
    if key not in BR_CATEGORIES:
        return

    label, hint = BR_CATEGORIES[key]
    await state.update_data(
        pending_br_category=key,
        pending_br_category_label=label,
    )
    await state.set_state(SetupStates.br_item_name)
    data = await state.get_data()

    await callback.message.answer(
        f"{setup_progress(data, 6)}\n\n"
        f"<b>{escape(label.upper())}</b>\n\n"
        f"{escape(hint)}\n\n"
        "Введите короткое название расхода.\n\n"
        "Например: <code>Зимняя обувь</code> или <code>Абонемент</code>.",
        reply_markup=keyboard([[('Отмена','br:cancel')]]),
    )


@router.message(SetupStates.br_item_name)
async def br_item_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if parse_decimal(name) is not None:
        await message.answer("Похоже, вы ввели сумму вместо названия. Сначала напишите короткое название, например <code>Зимняя обувь</code>.")
        return
    if len(name) < 2:
        await message.answer("Введите понятное название расхода.")
        return

    await state.update_data(pending_br_item_name=name)
    await state.set_state(SetupStates.br_item_amount)
    data = await state.get_data()

    await message.answer(
        f"{setup_progress(data, 6)}\n\n"
        f"<b>{escape(name.upper())}</b>\n\n"
        "Сколько вы тратите? Введите сумму. Период укажем следующим сообщением.\n\n"
        "Например: <code>12000</code>.",
        reply_markup=keyboard([[('Отмена','br:cancel')]]),
    )


@router.message(SetupStates.br_item_amount)
async def br_item_amount(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму.")
        return

    await state.update_data(pending_br_item_amount=str(value))
    await state.set_state(SetupStates.br_item_period)
    data = await state.get_data()

    await message.answer(
        f"{setup_progress(data, 6)}\n\n"
        "<b>ЗА КАКОЙ ПЕРИОД ЭТА СУММА?</b>",
        reply_markup=keyboard([
            [("В месяц", "brperiod:1"), ("За 3 месяца", "brperiod:3")],
            [("За 6 месяцев", "brperiod:6"), ("В год", "brperiod:12")],
            [("Другой период", "brperiod:custom")],
            [("Отмена", "br:cancel")],
        ]),
    )


@router.callback_query(SetupStates.br_item_period, F.data.startswith("brperiod:"))
async def br_item_period(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]

    if value == "custom":
        await state.set_state(SetupStates.br_custom_period)
        data = await state.get_data()
        await callback.message.answer(
            f"{setup_progress(data, 6)}\n\n"
            "<b>ЗА СКОЛЬКО МЕСЯЦЕВ?</b>\n\n"
            "Введите число месяцев. Например: <code>2</code> или <code>18</code>.",
            reply_markup=keyboard([[('Отмена','br:cancel')]]),
        )
        return

    await save_br_item(callback.message, state, Decimal(value))


@router.message(SetupStates.br_custom_period)
async def br_custom_period(message: Message, state: FSMContext):
    months = parse_decimal(message.text)
    if months is None or months <= 0:
        await message.answer("Введите число месяцев больше нуля.")
        return

    await save_br_item(message, state, months)


async def save_br_item(message: Message, state: FSMContext, months: Decimal):
    data = await state.get_data()
    amount = Decimal(data["pending_br_item_amount"])
    monthly = money2(amount / months)

    item = {
        "category": data["pending_br_category"],
        "category_label": data["pending_br_category_label"],
        "name": data["pending_br_item_name"],
        "amount": str(amount),
        "months": str(months),
        "monthly": str(monthly),
    }

    items = list(data.get("br_items", []))
    items.append(item)

    await state.update_data(br_items=items)
    await state.set_state(SetupStates.br_menu)

    index = len(items) - 1
    await message.answer(
        f"<b>{escape(item['name'])}</b> — {rub(monthly)} / мес.",
        reply_markup=keyboard([[('Изменить', f'bredit:item:{index}'), ('Удалить', f'bredit:delete:{index}')]])
    )
    await show_br_menu(message, state)


@router.callback_query(SetupStates.br_menu, F.data == "br:finish")
async def finish_br(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    items = data.get("br_items", [])
    groups = br_group_totals(items)
    exact = money2(sum(groups.values(), Decimal("0")))

    if exact <= 0:
        await callback.message.answer(
            "Добавьте хотя бы один расход Бытового резерва."
        )
        return

    rounded = round_up_thousand(exact)

    await state.update_data(
        household_reserve=str(rounded),
        household_reserve_exact=str(exact),
        household_reserve_categories={name: str(value) for name, value in groups.items()},
    )

    data = await state.get_data()
    critical = Decimal(data["critical_life"])
    sustainable = money2(critical + rounded)
    lines = [f"• {escape(name)} — {rub(value)}" for name, value in groups.items()]

    await state.set_state(SetupStates.br_menu)
    await callback.message.answer(
        f"{setup_progress(data, 6)}\n\n"
        "<b>БЫТОВОЙ РЕЗЕРВ РАССЧИТАН</b>\n\n"
        + "\n".join(lines)
        + f"\n\nПо категориям — <b>{rub(exact)}</b>\n"
        + f"Бытовой резерв — <b>{rub(rounded)}</b>\n"
        + f"Устойчивая жизнь — <b>{rub(sustainable)}</b>\n\n"
        "Сумма округлена вверх до ближайшей 1 000 ₽. Можно исправить расходы или увеличить итоговый резерв.",
        reply_markup=keyboard([
            [('Продолжить', 'brfinal:continue')],
            [('Редактировать расходы', 'bredit:list')],
            [('Изменить сумму БР', 'brfinal:override')],
        ]),
    )


async def clear_pending_br(state: FSMContext):
    await state.update_data(pending_br_category=None, pending_br_category_label=None, pending_br_item_name=None, pending_br_item_amount=None, pending_br_edit_index=None)


@router.callback_query(F.data == "br:cancel")
async def cancel_br_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Ввод отменён"); await clear_pending_br(state); await state.set_state(SetupStates.br_menu); await show_br_menu(callback.message,state)


@router.callback_query(SetupStates.br_menu, F.data == "bredit:list")
async def br_edit_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); data=await state.get_data(); items=data.get("br_items",[])
    if not items: await callback.message.answer("Пока нечего редактировать."); return
    rows=[[(f"{item['name']} — {rub(Decimal(item['monthly']))}",f"bredit:item:{i}")] for i,item in enumerate(items)]; rows.append([('Назад','bredit:back')])
    await callback.message.answer("<b>ЧТО ИЗМЕНИТЬ?</b>",reply_markup=keyboard(rows))


@router.callback_query(F.data == "bredit:back")
async def br_edit_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.set_state(SetupStates.br_menu); await show_br_menu(callback.message,state)


@router.callback_query(F.data.startswith("bredit:item:"))
async def br_edit_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); index=int(callback.data.rsplit(":",1)[1]); data=await state.get_data(); items=data.get("br_items",[])
    if not (0<=index<len(items)): await show_br_menu(callback.message,state); return
    item=items[index]; await state.update_data(pending_br_edit_index=index)
    await callback.message.answer(f"<b>{escape(item['name'])}</b>\n\nКатегория — {escape(item['category_label'])}\nИсходная сумма — {rub(Decimal(item['amount']))}\nПериод — {item['months']} мес.\nВ расчёте — <b>{rub(Decimal(item['monthly']))} / мес.</b>",reply_markup=keyboard([[('Изменить название',f'bredit:name:{index}')],[('Изменить сумму',f'bredit:amount:{index}')],[('Изменить период',f'bredit:period:{index}')],[('Удалить',f'bredit:delete:{index}')],[('Назад','bredit:list')]]))


@router.callback_query(F.data.startswith("bredit:delete:"))
async def br_delete_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Удалено"); index=int(callback.data.rsplit(":",1)[1]); data=await state.get_data(); items=list(data.get("br_items",[]))
    if 0<=index<len(items): items.pop(index); await state.update_data(br_items=items)
    await state.set_state(SetupStates.br_menu); await show_br_menu(callback.message,state)


@router.callback_query(F.data.startswith("bredit:name:"))
async def br_edit_name_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.update_data(pending_br_edit_index=int(callback.data.rsplit(":",1)[1])); await state.set_state(SetupStates.br_edit_name); await callback.message.answer("Введите новое название.",reply_markup=keyboard([[('Отмена','br:cancel')]]))


@router.message(SetupStates.br_edit_name)
async def br_edit_name_save(message: Message, state: FSMContext):
    name=(message.text or '').strip()
    if parse_decimal(name) is not None: await message.answer("Похоже, это сумма. Введите название словами."); return
    if len(name)<2: await message.answer("Введите понятное название."); return
    data=await state.get_data(); index=int(data.get('pending_br_edit_index',-1)); items=list(data.get('br_items',[]))
    if 0<=index<len(items): item=dict(items[index]); item['name']=name; items[index]=item; await state.update_data(br_items=items)
    await state.set_state(SetupStates.br_menu); await show_br_menu(message,state)


@router.callback_query(F.data.startswith("bredit:amount:"))
async def br_edit_amount_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.update_data(pending_br_edit_index=int(callback.data.rsplit(":",1)[1])); await state.set_state(SetupStates.br_edit_amount); await callback.message.answer("Введите новую сумму.",reply_markup=keyboard([[('Отмена','br:cancel')]]))


@router.message(SetupStates.br_edit_amount)
async def br_edit_amount_save(message: Message, state: FSMContext):
    value=parse_decimal(message.text)
    if value is None or value<=0: await message.answer("Введите положительную сумму."); return
    data=await state.get_data(); index=int(data.get('pending_br_edit_index',-1)); items=list(data.get('br_items',[]))
    if 0<=index<len(items): item=dict(items[index]); item['amount']=str(value); item['monthly']=str(money2(value/Decimal(item['months']))); items[index]=item; await state.update_data(br_items=items)
    await state.set_state(SetupStates.br_menu); await show_br_menu(message,state)


@router.callback_query(F.data.startswith("bredit:period:"))
async def br_edit_period_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.update_data(pending_br_edit_index=int(callback.data.rsplit(":",1)[1])); await state.set_state(SetupStates.br_edit_period); await callback.message.answer("<b>НОВЫЙ ПЕРИОД</b>",reply_markup=keyboard([[('1 месяц','breditperiod:1'),('3 месяца','breditperiod:3')],[('6 месяцев','breditperiod:6'),('12 месяцев','breditperiod:12')],[('Другой','breditperiod:custom')],[('Отмена','br:cancel')]]))


@router.callback_query(SetupStates.br_edit_period, F.data.startswith("breditperiod:"))
async def br_edit_period_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); value=callback.data.split(":",1)[1]
    if value=='custom': await state.set_state(SetupStates.br_edit_custom_period); await callback.message.answer("Введите количество месяцев.",reply_markup=keyboard([[('Отмена','br:cancel')]])); return
    await apply_br_edit_period(callback.message,state,Decimal(value))


@router.message(SetupStates.br_edit_custom_period)
async def br_edit_custom_period_save(message: Message, state: FSMContext):
    months=parse_decimal(message.text)
    if months is None or months<=0: await message.answer("Введите число месяцев больше нуля."); return
    await apply_br_edit_period(message,state,months)


async def apply_br_edit_period(message: Message, state: FSMContext, months: Decimal):
    data=await state.get_data(); index=int(data.get('pending_br_edit_index',-1)); items=list(data.get('br_items',[]))
    if 0<=index<len(items): item=dict(items[index]); item['months']=str(months); item['monthly']=str(money2(Decimal(item['amount'])/months)); items[index]=item; await state.update_data(br_items=items)
    await state.set_state(SetupStates.br_menu); await show_br_menu(message,state)


@router.callback_query(SetupStates.br_menu, F.data == "brfinal:continue")
async def br_final_continue(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await ask_pillow_policy(callback.message,state)


@router.callback_query(SetupStates.br_menu, F.data == "brfinal:override")
async def br_override_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); data=await state.get_data(); await state.set_state(SetupStates.br_override_amount); await callback.message.answer(f"Текущий Бытовой резерв — <b>{rub(Decimal(data['household_reserve']))}</b>.\n\nВведите новую сумму. Она не может быть меньше расчётной суммы, округлённой вверх.",reply_markup=keyboard([[('Отмена','br:cancel')]]))


@router.message(SetupStates.br_override_amount)
async def br_override_save(message: Message, state: FSMContext):
    value=parse_decimal(message.text); data=await state.get_data(); minimum=round_up_thousand(Decimal(data['household_reserve_exact']))
    if value is None or value<minimum: await message.answer(f"Сумма не может быть меньше <b>{rub(minimum)}</b>."); return
    value=round_up_thousand(value); await state.update_data(household_reserve=str(value)); critical=Decimal(data['critical_life']); await state.set_state(SetupStates.br_menu)
    await message.answer(f"Бытовой резерв установлен: <b>{rub(value)}</b>.\nУстойчивая жизнь — <b>{rub(critical+value)}</b>.",reply_markup=keyboard([[('Продолжить','brfinal:continue')],[('Редактировать расходы','bredit:list')]]))


async def ask_pillow_policy(message: Message, state: FSMContext):
    data = await state.get_data()
    step = 7
    bar = setup_progress(data, step)

    if data.get("has_debts"):
        await state.set_state(SetupStates.minimum_reserve_months)
        await message.answer(
            f"{bar}\n\n"
            "<b>КАКОЙ МИНИМАЛЬНЫЙ ЗАПАС СОЗДАТЬ ДО АКТИВНОГО ПОГАШЕНИЯ ДОЛГОВ?</b>\n\n"
            "Один месяц — быстрее перейти к досрочному погашению. Два месяца — больше защиты от нового долга.",
            reply_markup=keyboard([
                [("1 месяц", "minmonths:1"), ("2 месяца", "minmonths:2")],
            ]),
        )
    else:
        await state.update_data(minimum_reserve_months="0")
        await state.set_state(SetupStates.force_majeure_months)
        await show_force_majeure_question_new(message, state)


@router.callback_query(SetupStates.minimum_reserve_months, F.data.startswith("minmonths:"))
async def save_minimum_months(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(minimum_reserve_months=callback.data.split(":", 1)[1])
    await state.set_state(SetupStates.force_majeure_months)
    await show_force_majeure_question_new(callback.message, state)


async def show_force_majeure_question_new(message: Message, state: FSMContext):
    data = await state.get_data()
    if data["employment_type"] == "Фрилансер":
        buttons = [[("6 месяцев", "fmmonths:6"), ("9 месяцев", "fmmonths:9")], [("12 месяцев", "fmmonths:12"), ("Свой вариант", "fmmonths:custom")]]
        hint = "Для фрилансера рекомендуется накопить <b>6–12 месяцев Критического минимума</b>."
    else:
        buttons = [[("3 месяца", "fmmonths:3"), ("4 месяца", "fmmonths:4")], [("6 месяцев", "fmmonths:6"), ("Свой вариант", "fmmonths:custom")]]
        hint = "Ориентир для финансовой подушки — <b>3–6 месяцев Критического минимума</b>."

    await message.answer(
        f"{setup_progress(data, 7)}\n\n"
        "<b>РАЗМЕР ФОРС-МАЖОРНОЙ ПОДУШКИ</b>\n\n"
        "Это резерв на случай событий, которые действительно переворачивают жизнь с ног на голову: потеря жилья, серьёзная болезнь, аварийный переезд, смерть близкого человека и другие крупные форс-мажоры.\n\n"
        + hint,
        reply_markup=keyboard(buttons),
    )


@router.callback_query(SetupStates.force_majeure_months, F.data.startswith("fmmonths:"))
async def save_force_months_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        data = await state.get_data()
        await callback.message.answer(
            f"{setup_progress(data, 7)}\n\nВведите количество месяцев числом."
        )
        return
    await state.update_data(force_majeure_months=value)
    await after_pillow_policy(callback.message, state)


@router.message(SetupStates.force_majeure_months)
async def save_force_months_text(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите количество месяцев больше нуля.")
        return
    await state.update_data(force_majeure_months=str(value))
    await after_pillow_policy(message, state)


async def after_pillow_policy(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.update_data(goals=[])

    if data.get("has_debts"):
        await start_credit_block(message, state)
    else:
        await state.update_data(debt_strategy="Лавина", credits=[])
        await start_current_state(message, state)


async def start_credit_block(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(SetupStates.debt_strategy)
    await message.answer(
        f"{setup_progress(data, 8)}\n\n"
        "<b>КАК ГАСИТЬ КРЕДИТЫ ДОСРОЧНО?</b>\n\n"
        "<b>Лавина</b> — сначала долг с самой высокой ставкой. Обычно это минимизирует переплату.\n\n"
        "<b>Снежный ком</b> — сначала самый маленький остаток. Так быстрее исчезают отдельные долги.\n\n"
        "Если не уверены — выбирайте «Лавина».",
        reply_markup=keyboard([
            [("Лавина", "strategy:avalanche")],
            [("Снежный ком", "strategy:snowball")],
            [("Ручной выбор", "strategy:manual")],
        ]),
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
        "<b>ДОБАВИМ ПЕРВЫЙ КРЕДИТ</b>\n\n"

        "Введите короткое понятное название.\n\n"

        "Например:\n"
        "<code>Кредитка Т-Банк</code>\n"
        "<code>Автокредит</code>\n"
        "<code>Потребительский кредит</code>"
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
        f"<b>{escape(name).upper()}</b>\n\n"

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

        "В этом случае бот не будет сам утверждать, "
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
                    "Уменьшать срок",
                    "early:term",
                )
            ],
            [
                (
                    "Уменьшать платёж",
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
        f"Кредит <b>{escape(credit['name'])}</b> добавлен.\n\n"
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

    await state.set_state(SetupStates.current_pillow)
    data = await state.get_data()
    step = 9 if data.get("has_debts") else 8

    await message.answer(
        f"{setup_progress(data, step)}\n\n"
        "<b>СКОЛЬКО УЖЕ НАКОПЛЕНО В ПОДУШКЕ?</b>\n\n"
        "Укажите деньги, которые действительно считаете финансовым резервом. "
        "Не отпуск, не новый телефон и не сумму, которую вы просто стараетесь не трогать.\n\n"
        "Если Подушки пока нет — отправьте <code>0</code>."
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

    data = await state.get_data()
    step = 9 if data.get("has_debts") else 8
    await message.answer(
        f"{setup_progress(data, step)}\n\n"
        "<b>СКОЛЬКО УЖЕ ОТЛОЖЕНО НА ТЕКУЩУЮ ЖИЗНЬ?</b>\n\n"
        "Это деньги на Критический минимум и Бытовой резерв текущего расчётного периода.\n\n"
        "Если начинаете с чистого листа — отправьте <code>0</code>."
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
            "сумму Критического минимума и "
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

        step = 9 if data.get("has_debts") else 8
        await message.answer(
            f"{setup_progress(data, step)}\n\n"
            "<b>СКОЛЬКО УЖЕ ОТЛОЖЕНО НА МИНИМАЛЬНЫЕ ПЛАТЕЖИ?</b>\n\n"

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

        await state.update_data(
            calculate_interest_savings=False,
            developer_mode=False,
        )
        await show_confirmation(message, state)


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

    await state.update_data(
        calculate_interest_savings=False,
        developer_mode=False,
    )
    await show_confirmation(message, state)


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
    settings = build_settings_from_data(data)
    state_object = build_state_from_data(data, settings)

    try:
        allocator = FinancialAllocator(settings=settings, state=state_object)
    except ValueError as error:
        await message.answer(
            "<b>В НАСТРОЙКАХ ЕСТЬ ОШИБКА</b>\n\n"
            f"<code>{escape(str(error))}</code>"
        )
        return

    mode = allocator.active_mode()
    mode_name = {
        1: "Небо помогает тому, кто помогает себе.",
        2: "Ланистеры всегда платят свои долги.",
        3: "Подготовка к Апокалипсису.",
        4: "Заказов нет. Паники тоже.",
        5: "Защита есть. Пора расти.",
        6: "Философский камень найден.",
    }[mode]
    mode_progress = "🏆" * mode + "➖" * (6 - mode)

    tax_types = (
        ", ".join(settings.taxable_income_types)
        if settings.taxable_income_types
        else "нет"
    )

    accounts: list[tuple[str, str]] = []
    if settings.tax_rate > 0:
        accounts.append(("🏛️", "Налог"))

    accounts.append(("🛡️", "Подушка"))

    for name in settings.life_categories.keys():
        accounts.append(("❤️", name))

    accounts.append(("❤️", "Зарплата"))
    accounts.append(("💚", "Бытовой резерв"))

    accounts_text = "\n".join(
        f"{index}. {icon} <b>{escape(name)}</b>"
        for index, (icon, name) in enumerate(accounts, start=1)
    )

    income_label = (
        "Стабильный доход"
        if settings.employment_type == "Наёмный"
        else "Средний доход"
    )

    await state.set_state(SetupStates.confirmation)
    await message.answer(
        "<b>ФИНАНСОВЫЙ ПРОФИЛЬ ГОТОВ</b>\n\n"
        f"Профиль — <b>{escape(settings.employment_type)}</b>\n"
        f"{income_label} — <b>{rub(settings.average_income)}</b>\n"
        f"Критический минимум — <b>{rub(settings.critical_life)}</b>\n"
        f"Бытовой резерв — <b>{rub(settings.household_reserve)}</b>\n"
        f"Устойчивая жизнь — <b>{rub(settings.household_life)}</b>\n"
        f"Баланс жизни сейчас — <b>{rub(state_object.life_balance)}</b>\n"
        f"Типы дохода для налога — <b>{escape(tax_types)}</b>\n\n"
        f"<b>ОТКРОЙТЕ {len(accounts)} НАКОПИТЕЛЬНЫХ СЧЕТОВ В СВОЁМ БАНКЕ:</b>\n\n"
        f"{accounts_text}\n\n"
        "<b>СТАРТОВЫЙ РЕЖИМ:</b>\n\n"
        f"{mode_progress}\n\n"
        f"{escape(mode_name)}\n\n"
        "<b>P.S.:</b> Цели и инвестиции появятся тогда, когда ваш финансовый режим "
        "действительно будет готов направлять туда деньги.",
        reply_markup=keyboard([
            [("Сохранить профиль", "confirm:save")],
            [("Начать заново", "confirm:restart")],
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
            f"\nДо следующего режима "
            f"{next_info['next_name']} осталось: "
            f"<b>{rub(next_info['remaining'])}</b>"
        )

    await callback.message.answer(
        "<b>ФИНАНСОВЫЙ ПРОФИЛЬ СОХРАНЁН</b>\n\n"

        "Аллокатор готов к работе.\n\n"

        f"Текущий режим: <b>{mode}</b>"
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
