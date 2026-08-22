from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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
from mode_presentation import FIRE_EFFECT_ID, mode_image_path


router = Router()


BASE_DIR = Path(__file__).resolve().parent
INTRO_IMAGES_DIR = BASE_DIR / "assets" / "onboarding"

INTRO_IMAGE_1 = INTRO_IMAGES_DIR / "intro_1.png"
INTRO_IMAGE_2 = INTRO_IMAGES_DIR / "intro_2.png"
INTRO_IMAGE_3 = INTRO_IMAGES_DIR / "intro_3.png"
INTRO_IMAGE_4 = INTRO_IMAGES_DIR / "intro_4.png"
FINANCIAL_PROFILE_IMAGE = INTRO_IMAGES_DIR / "financial_profile.png"
CRITICAL_MINIMUM_IMAGE = INTRO_IMAGES_DIR / "critical_minimum.png"
HOUSEHOLD_RESERVE_IMAGE = INTRO_IMAGES_DIR / "household_reserve.png"
CRITICAL_MINIMUM_CALCULATED_IMAGE = (
    INTRO_IMAGES_DIR / "critical_minimum_calculated.png"
)
HOUSEHOLD_RESERVE_CALCULATED_IMAGE = (
    INTRO_IMAGES_DIR / "household_reserve_calculated.png"
)

# Стандартный эффект Telegram «Праздник / конфетти». Эффекты работают только
# в личных чатах и могут быть недоступны в отдельных версиях клиента.
PARTY_POPPER_EFFECT_ID = "5046509860389126442"


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
    income_rhythm = State()
    income_gap_months = State()
    income_work_months = State()
    fund_salary_intro = State()
    stabilizer_target_months = State()
    contract_obligations_menu = State()
    income_method = State()
    income_month_amount = State()

    # Калькулятор Критического минимума
    km_menu = State()
    km_item_name = State()
    km_housing_object_name = State()
    km_housing_expense_name = State()
    km_communication_name = State()
    km_item_amount = State()
    km_item_period = State()
    km_tax_due_date = State()
    km_education_lesson_count = State()
    km_education_custom_count = State()
    km_education_due_date = State()
    km_education_confirm = State()
    km_custom_period = State()
    km_edit_name = State()
    km_edit_amount = State()
    km_edit_period = State()
    km_edit_custom_period = State()
    km_edit_tax_due_date = State()
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
    income_types_menu = State()
    income_type_name = State()
    income_type_tax_choice = State()
    income_type_rate = State()
    income_type_confirm = State()
    income_type_edit_name = State()

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
    current_stabilizer = State()
    current_intercontract = State()
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
        text = (
            "<b>ФИНАНСОВЫЙ ПРОФИЛЬ УЖЕ НАСТРОЕН</b>\n\n"
            "Изменить отдельные параметры можно в <b>Настройках</b>.\n\n"
            "Чтобы пройти настройку с нуля, нажмите <b>Настроить заново</b>."
        )
        reply_markup = keyboard([
            [
                (
                    "Настройки",
                    "settings:open",
                ),
                (
                    "Настроить заново",
                    "setup:restart",
                )
            ]
        ])

        try:
            await message.answer(
                text,
                reply_markup=reply_markup,
                message_effect_id=(
                    PARTY_POPPER_EFFECT_ID
                    if message.chat.type == "private"
                    else None
                ),
            )
        except TelegramBadRequest:
            # Если Telegram изменит или отключит эффект, /start всё равно
            # должен вернуть пользователю основное сообщение.
            await message.answer(
                text,
                reply_markup=reply_markup,
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
        "<b>Я — Богатый Алхимик, ваш финансовый аллокатор.</b>\n\n"
        "Я не буду заставлять вас записывать каждую трату. Ничего, кроме дисциплины "
        "и тревожности это вам не принесет.\n\n"
        "Вместо того чтобы разбираться, куда деньги исчезли, мы будем решать, "
        "<b>куда им отправиться</b>, пока они ещё у вас."
    )

    await send_intro_photo(
        message=message,
        image_path=INTRO_IMAGE_1,
        caption=caption,
        callback_data="intro:2",
        button_text="И куда же? →",
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
        "Каждый раз, когда приходят деньги, я рассчитываю, <b>сколько и куда отправить</b>.\n\n"
        "Вы переводите эти суммы по отдельным <b>накопительным счетам</b> в своём банке — "
        "финансовым «конвертам» (это бесплатно).\n\n"
        "Некоторые покажутся очевидными. <b>Другие конверты, скорее всего, сами бы не создали. "
        "И вот тут начинается самое интересное</b>."
    )

    await send_intro_photo(
        message=callback.message,
        image_path=INTRO_IMAGE_2,
        caption=caption,
        callback_data="intro:3",
        button_text="В чём секрет? →",
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
        "<b>Секрет философского камня прост: чтобы начать богатеть, сначала нужно "
        "научиться управлять тем, что уже зарабатываешь.</b>\n\n"
        "Можно получать миллион и спускать его в тот же месяц. Второй миллион проблему "
        "вряд ли решит.\n\n"
        "Бюджет человека, компании или целого государства — это <b>работа с вероятностями</b>. "
        "Будущее неизвестно, поэтому хороший бюджет должен быть готов не к одному идеальному "
        "сценарию, а к <b>разным</b>.\n\n"
        "В Аллокатор заложены те же базовые <b>принципы управления денежными потоками, "
        "резервами и рисками</b>, которыми пользуются финансовые специалисты.\n\n"
        "Будущее предсказать нельзя. <b>Наша задача — быть к нему готовыми</b>."
    )

    await send_intro_photo(
        message=callback.message,
        image_path=INTRO_IMAGE_3,
        caption=caption,
        callback_data="intro:4",
        button_text="Звучит разумно →",
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
        "<b>Волшебная таблетка существует. Но её нужно приготовить.</b>\n\n"
        "Чтобы Аллокатор работал именно под вашу жизнь, сначала создадим "
        "<b>финансовый профиль</b>.\n\n"
        "Я буду задавать вопросы по одному и объяснять, <b>что означает каждая цифра "
        "и где её взять</b>.\n\n"
        "Заварите чай и приготовьтесь немного поколдовать над своими финансами."
    )

    await send_intro_photo(
        message=callback.message,
        image_path=INTRO_IMAGE_4,
        caption=caption,
        callback_data="setup:start",
        button_text="Начать настройку →",
    )


# ============================================================
# ДИНАМИЧЕСКАЯ ПЕРВИЧНАЯ НАСТРОЙКА
# ============================================================


KM_CATEGORIES = {
    "housing": (
        "Недвижимость",
        "Выберите вид обязательного расхода. Если хотите отдельно учитывать "
        "конкретную квартиру, дом или другое помещение, нажмите «Уточнить объект».",
    ),
    "food": (
        "Питание",
        "• Супермаркеты\n"
        "• Питьевая вода\n"
        "• Еда вне дома\n"
        "• Доставки, если это необходимо\n"
        "• Другое питание, без которого нельзя нормально прожить месяц.\n\n"
        "——————\n"
        "<b>→ Введите название расхода.</b>\n"
        "<b>Например:</b> Вода, Супермаркет, Столовка",
    ),
    "communication": (
        "Связь и подписки",
        "• Мобильная связь\n"
        "• Домашний интернет\n"
        "• VPN с ежемесячной оплатой\n"
        "• Необходимые подписки с ежемесячной оплатой\n\n"
        "<b>Совет:</b> если вы оплачиваете подписку НЕ каждый месяц, то лучше добавьте её "
        "в Бытовой резерв. В противном случае система предложит создать отдельный конверт «Подписки».\n\n"
        "——————\n"
        "<b>→ Введите название расхода.</b>\n"
        "<b>Например:</b> МТС, Домашний интернет, Яндекс Плюс",
    ),
    "transport": (
        "Транспорт",
        "Выберите обязательный транспортный расход.",
    ),
    "education": (
        "Образование",
        "Колледж, ВУЗ или другой платёж, который нельзя без последствий прекратить. "
        "Курсы и репетитора для души лучше учитывать в Бытовом резерве.",
    ),
    "children": (
        "Дети",
        "- Детский сад\n"
        "- Школа\n"
        "- Питание\n"
        "- Секции и другие действительно <b>обязательные ежемесячные</b> расходы на детей.\n\n"
        "——————\n"
        "<b>→ Введите название расхода.</b>\n"
        "<b>Например:</b> Музыкальная школа, Питание",
    ),
    "pets": (
        "Питомцы",
        "• Корм\n• Наполнитель\n• Пелёнки\n• Аксессуары\n• Ветеринар\n\n"
        "——————\n"
        "<b>→ Введите название расхода.</b>\n"
        "<b>Например:</b> Кошачий корм, Ветеринарка, Пелёнки собаке",
    ),
    "health": (
        "Здоровье",
        "- Лекарства\n"
        "- Стоматолог\n"
        "- Плановые врачи\n"
        "- Анализы\n"
        "- Аптека\n"
        "- Витамины и др.\n\n"
        "Если трата повторяется нерегулярно, укажите сумму за несколько месяцев или "
        "за год — Аллокатор сам приведёт её к среднемесячной.\n\n"
        "——————\n"
        "<b>→ Введите название расхода.</b>\n"
        "<b>Например:</b> Стоматолог, Анализы, Лекарства",
    ),
    "habits": (
        "Вредные привычки",
        "Сигареты, табак, вейпы, алкогольные и безалкогольные напитки или другие привычки, "
        "без которых вы сейчас фактически не обходитесь.\n\n"
        "Здесь важна честность, а не идеальная версия бюджета. В Критический минимум добавляйте только "
        "реальную обязательную ежемесячную сумму. То, что можно сократить или покупать нерегулярно, "
        "лучше учитывать в Бытовом резерве.",
    ),
    "fees": (
        "Комиссии",
        "Обязательные регулярные комиссии и сборы:\n"
        "• обслуживание банковской карты или счёта;\n"
        "• неизбежные комиссии за переводы;\n"
        "• регулярные почтовые, платёжные и сервисные сборы.\n\n"
        "Госпошлины и другие нерегулярные комиссии обычно удобнее учитывать в Бытовом резерве. "
        "Если расход возникает раз в несколько месяцев, укажите сумму за весь период — Аллокатор "
        "приведёт её к среднемесячной.",
    ),
    "other": (
        "Другое",
        "Здесь можно добавить страховые взносы и другие индивидуальные обязательные ежемесячные "
        "расходы, которые не подходят ни к одной категории выше.\n\n"
        "Если при резком падении дохода от расхода можно отказаться на несколько месяцев — "
        "это, скорее всего, не Критический минимум.",
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
    "subscriptions": ("Подписки", "Подписки с оплатой раз в несколько месяцев или раз в год, которые вы хотите заранее учитывать в обычном бюджете."),
    "habits": (
        "Вредные привычки",
        "Сигареты, табак, вейпы, алкогольные и безалкогольные напитки и другие привычки, "
        "расходы на которые возникают нерегулярно или могут быть сокращены. Учитывайте реальную сумму без осуждения — "
        "так бюджет не забудет заметную часть обычной жизни.",
    ),
    "fees": (
        "Комиссии",
        "Нерегулярные банковские комиссии сверх лимита, госпошлины, почтовые сборы, комиссии платёжных "
        "сервисов и другие подобные расходы. Укажите сумму за несколько месяцев или за год — "
        "Аллокатор рассчитает среднемесячное пополнение.",
    ),
    "other": ("Другое", "Любой нерегулярный расход нормальной жизни, который не относится к Критическому минимуму, но периодически требует денег."),
}


def route_total(data: dict) -> int:
    base = 10 if data.get("has_debts") else 9
    return base + (4 if data.get("income_rhythm") == "cyclic" else 0)


def progress_bar(done: int, total: int) -> str:
    done = max(0, min(done, total))
    return "💎" * done + "➖" * (total - done)


def setup_progress(data: dict, done: int) -> str:
    return progress_bar(done + int(data.get("progress_offset", 0)), route_total(data))


def money2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_up_thousand(value: Decimal) -> Decimal:
    value = Decimal(value)
    return (
        (value / Decimal("1000")).to_integral_value(rounding=ROUND_CEILING)
        * Decimal("1000")
    )


def normalize_pass_months(value: Decimal) -> Decimal:
    """Берёт число полных накопительных месяцев до покупки проездного."""
    return Decimal(max(1, int(Decimal(value))))


def km_group_totals(items: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in items:
        label = item["category_label"]
        result[label] = money2(
            result.get(label, Decimal("0"))
            + Decimal(item["monthly"])
        )
    return result


def km_item_display_name(item: dict) -> str:
    """Показывает назначение налога, сохраняя внутри чистое имя объекта."""
    name = (item.get("name") or "Расход").strip()
    tax_labels = {
        "property_tax": "Налог на имущество",
        "land_tax": "Земельный налог",
        "tax": "Транспортный налог",
    }
    tax_label = tax_labels.get(item.get("subcategory"))
    return f"{tax_label} · {name}" if tax_label else name


def km_item_totals_by_name(items: list[dict]) -> list[tuple[str, Decimal]]:
    """Суммирует одноимённые расходы для компактного показа в меню КМ.

    Исходные записи остаются раздельными, чтобы пользователь мог независимо
    исправить или удалить платёж с конкретной карты. Разные категории,
    подкатегории и налоговые сроки не смешиваются.
    """
    totals: dict[tuple[str, str, str, str], Decimal] = {}
    display_names: dict[tuple[str, str, str, str], str] = {}
    for item in items:
        name = (item.get("name") or "Расход").strip()
        normalized_name = " ".join(name.split()).casefold()
        key = (
            str(item.get("category") or ""),
            str(item.get("subcategory") or ""),
            str(item.get("due_date") or ""),
            normalized_name,
        )
        display_names.setdefault(key, km_item_display_name(item))
        totals[key] = money2(
            totals.get(key, Decimal("0")) + Decimal(item["monthly"])
        )
    return [(display_names[key], total) for key, total in totals.items()]


def default_km_storage(item: dict) -> dict:
    """
    Рекомендует способ хранения конкретного расхода Критического минимума.

    salary   — деньги остаются на операционном счёте «Зарплата»;
    separate — деньги физически изолируются в отдельном конверте/накопительном счёте.
    """
    category = item.get("category")
    name = (item.get("name") or item.get("category_label") or "Расход").strip()
    months = Decimal(str(item.get("months", "1")))
    subtype = item.get("subcategory")

    storage = "salary"
    envelope_name = None

    # Эти деньги не должны конкурировать с обычным потреблением.
    if category == "health":
        storage = "separate"
        envelope_name = "Здоровье"
    elif category == "pets":
        storage = "separate"
        envelope_name = "Питомцы"

    # Все обязательства по недвижимости хранятся вместе. Налоги остаются
    # частью КЖ, но физически направляются в общий налоговый конверт.
    elif category == "housing":
        if subtype in {"property_tax", "land_tax"}:
            storage = "separate"
            envelope_name = "Налоги"
        else:
            storage = "separate"
            envelope_name = "Недвижимость"

    # Продукты обычно тратятся прямо в течение месяца.
    elif category == "food":
        storage = "salary"

    # Редкую подписку можно оставить в КЖ, но тогда деньги лучше
    # физически отделить от ежемесячных расходов.
    elif category == "communication":
        if months > 1:
            storage = "separate"
            envelope_name = "Подписки"

    # Для транспорта учитываем назначение расхода, а не период, за который
    # пользователь ввёл сумму. Период общественного транспорта может быть
    # выбран только для усреднения и не должен создавать лишний конверт.
    elif category == "transport":
        if subtype == "tax":
            storage = "separate"
            envelope_name = "Налоги"
        elif subtype == "pass":
            storage = "separate"
            envelope_name = "Проездной"
        elif subtype == "car" or str(subtype or "").startswith("car_"):
            storage = "separate"
            envelope_name = "Автомобиль"
        elif subtype in {"public", "taxi"}:
            storage = "salary"
        elif months > 1:
            storage = "separate"
            envelope_name = "Транспорт"

    # Расходы на детей образуют общий критический фонд.
    elif category == "children":
        storage = "separate"
        envelope_name = "Дети"

    # Для образования и прочих расходов периодичность
    # даёт хорошую рекомендацию: крупный будущий платёж лучше копить отдельно.
    elif category in {"education", "other"}:
        if months > 1:
            storage = "separate"
            defaults = {
                "education": "Образование",
            }
            envelope_name = defaults.get(category, name)

    result = {
        "item_name": name,
        "category": category,
        "category_label": item.get("category_label", name),
        "monthly": str(money2(Decimal(item["monthly"]))),
        "storage": storage,
        "envelope_name": envelope_name,
        "subcategory": subtype,
    }
    if item.get("due_date"):
        result["due_date"] = item["due_date"]
    if item.get("one_time"):
        result["one_time"] = True
        result["target_amount"] = str(item.get("amount", "0"))
    return result


def build_default_km_storage(items: list[dict]) -> list[dict]:
    return [default_km_storage(item) for item in items]


def life_categories_from_storage(storage_items: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in storage_items:
        is_tax = item.get("subcategory") in {"tax", "property_tax", "land_tax"}
        if item.get("storage") != "separate" and not is_tax:
            continue
        if is_tax:
            envelope = "Налоги"
        else:
            envelope = (item.get("envelope_name") or item.get("item_name") or "Конверт").strip()
        amount = Decimal(item["monthly"])
        result[envelope] = money2(result.get(envelope, Decimal("0")) + amount)
    return result


def planned_taxes_from_storage(storage_items: list[dict]) -> dict[str, Decimal]:
    """Возвращает внутреннюю детализацию налоговой части КЖ."""
    labels = {
        "tax": "Транспортный налог",
        "property_tax": "Налог на имущество",
        "land_tax": "Земельный налог",
    }
    result: dict[str, Decimal] = {}
    for item in storage_items:
        subtype = item.get("subcategory")
        if subtype not in labels:
            continue
        object_name = (item.get("item_name") or labels[subtype]).strip()
        key = f"{labels[subtype]} · {object_name}"
        result[key] = money2(
            result.get(key, Decimal("0")) + Decimal(item["monthly"])
        )
    return result


def parse_tax_due_date(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def months_until_due_date(today: date, due_date: date) -> int:
    """Количество ежемесячных пополнений до месяца уплаты включительно."""
    return max(1, (due_date.year - today.year) * 12 + due_date.month - today.month)


def add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    days_in_month = (date(year + month // 12, month % 12 + 1, 1) - date(year, month, 1)).days
    return date(year, month, min(value.day, days_in_month))


def km_storage_summary(storage_items: list[dict], critical_life: Decimal) -> str:
    separate = life_categories_from_storage(storage_items)
    separate_sum = sum(separate.values(), Decimal("0"))
    salary = money2(critical_life - separate_sum)

    separate_lines = [
        f"• <b>{escape(name.upper())}</b> — {rub(amount)}"
        for name, amount in separate.items()
    ]

    salary_items = [
        item["item_name"]
        for item in storage_items
        if item.get("storage") == "salary"
    ]

    text = "Давайте утвердим отдельные конверты:\n\n"
    if separate_lines:
        text += "\n".join(separate_lines) + "\n"
    text += "• <b>ЗАРПЛАТА</b> — " + rub(salary)
    if salary_items:
        text += "\n  — " + "\n  — ".join(escape(name) for name in salary_items)
    return text


def km_storage_help_text(storage_items: list[dict]) -> str:
    separate = list(life_categories_from_storage(storage_items))
    salary_items = [
        item["item_name"]
        for item in storage_items
        if item.get("storage") == "salary"
    ]

    lines = [
        "<b>КАК ФОРМИРУЮТСЯ КОНВЕРТЫ</b>",
        "",
        "Отдельный конверт нужен, когда обязательные деньги важно сохранить до момента оплаты: "
        "расход может быть крупным, нерегулярным или особенно ответственным.",
        "",
        "Все налоги объединяются в одном конверте «Налоги». Повседневные и небольшие "
        "ежемесячные расходы можно оставить на операционном счёте «Зарплата».",
    ]
    if separate:
        lines.extend([
            "",
            "<b>В вашей рекомендации отдельно:</b> "
            + ", ".join(escape(name) for name in separate)
            + ".",
        ])
    if salary_items:
        lines.extend([
            "",
            "<b>На «Зарплате»:</b> "
            + ", ".join(escape(name) for name in salary_items)
            + ".",
        ])
    lines.extend([
        "",
        "Это рекомендация, а не запрет. Например, регулярное ЖКХ можно вернуть на «Зарплату», "
        "если отдельный конверт создаёт лишнюю сложность.",
    ])
    return "\n".join(lines)


def km_storage_item_display_name(item: dict) -> str:
    tax_labels = {
        "tax": "Транспортный налог",
        "property_tax": "Налог на имущество",
        "land_tax": "Земельный налог",
    }
    name = item.get("item_name") or "Расход"
    tax_label = tax_labels.get(item.get("subcategory"))
    return f"{tax_label} · {name}" if tax_label else name


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
    await state.set_state(SetupStates.has_debts)
    await callback.message.answer_photo(
        photo=FSInputFile(FINANCIAL_PROFILE_IMAGE),
        caption=(
        f"{progress_bar(1, 2)}\n\n"
        "<b>ЕСТЬ КРЕДИТЫ ИЛИ ДОЛГИ?</b>\n\n"
        "• Потребительский кредит\n"
        "• Автокредит\n"
        "• Рассрочка\n"
        "• Микрозайм\n"
        "• Задолженность по кредитке\n"
        "• Долги людям\n\n"
        "(Кредитка без задолженности долгом не считается)"
        ),
        reply_markup=keyboard([
            [("Есть долги", "debts:yes"), ("Нет долгов", "debts:no")],
        ]),
    )


@router.callback_query(SetupStates.has_debts, F.data.startswith("debts:"))
async def save_debts(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await remove_setup_button(callback)

    has_debts = callback.data == "debts:yes"
    await state.update_data(has_debts=has_debts, credits=[], progress_offset=0)
    await ask_income_rhythm(callback.message, state)


async def ask_income(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(SetupStates.average_income)
    bar = setup_progress(data, 3)

    if data.get("income_rhythm") == "monthly":
        text = (
            f"{bar}\n\n"
            "<b>КАКОЙ ДОХОД ВЫ СТАБИЛЬНО ПОЛУЧАЕТЕ КАЖДЫЙ МЕСЯЦ?</b>\n\n"
            "Нужен <b>минимальный регулярный доход</b>, на который вы с большой вероятностью можете рассчитывать каждый месяц.\n\n"
            "Постоянный оклад и регулярные ежемесячные надбавки учитывайте. "
            "Премии, годовые бонусы, гранты, случайные подработки и другие нерегулярные поступления — нет. "
            "Для Аллокатора это сверхдоход.\n\n"
            "Не оценивайте сумму по памяти. Откройте историю поступлений за последние 6–12 месяцев и найдите устойчивую месячную базу.\n\n"
            "——————\n"
            "<b>→ Введите сумму.</b>\n"
            "<b>Например:</b> <code>180000</code>"
        )
    elif data.get("income_rhythm") == "cyclic":
        text = (
            f"{bar}\n\n"
            "<b>СРЕДНЕМЕСЯЧНЫЙ ДОХОД ЗА ВЕСЬ ФИНАНСОВЫЙ ЦИКЛ</b>\n\n"
            "Сложите весь доход, которым вы лично располагаете за полный цикл, <b>до покупок и переводов "
            "в накопления</b>. "
            "Если доход приходит в другой валюте, сначала переведите всю сумму в примерный "
            "рублёвый эквивалент. Все расчёты Аллокатора ведутся в рублях.\n\n"
            "<b>Например:</b>\n"
            "• Петя — механик, работает вахтами. За 1 рабочий месяц получил <b>150 000 ₽</b>. "
            "Затем 1 месяц не работал. Его цикл — <b>2</b> месяца. Петя делит 150 000 на 2 и "
            "получившуюся сумму округляет в <b>меньшую</b> сторону.\n\n"
            "• Маша — артист на контракте за рубежом. За 5 рабочих месяцев получила $8200. "
            "Затем 7 месяцев дохода в России нет. Её цикл — 12 месяцев. Сначала Маша навскидку "
            "переводит $8200 в примерный <b>рублёвый эквивалент</b> и делит на 12. Получившуюся "
            "сумму Маша округляет в <b>меньшую</b> сторону.\n\n"
            "Эта цифра помогает отличать обычную часть дохода от сверхдохода и не означает, "
            "что деньги действительно приходят каждый месяц.\n\n"
            "——————\n"
            "<b>→ Введите среднемесячный доход за полный цикл в рублях.</b>\n"
            "<b>Например:</b> <code>57000</code>"
        )
    else:
        text = (
            f"{bar}\n\n"
            "<b>КАКОЙ У ВАС СРЕДНИЙ ДОХОД?</b>\n\n"
            "Для нерегулярного дохода нужна реальная средняя за последние 6–12 месяцев. "
            "Лучший месяц не считается вашей новой нормой.\n\n"
            "В банковском приложении посмотрите ваш доход за период и разделите на количество "
            "месяцев. <b>Округлите в меньшую сторону</b>.\n\n"
            "——————\n"
            "<b>→ Введите среднемесячный доход.</b>\n"
            "<b>Например:</b> <code>180000</code>"
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


async def ask_income_rhythm(message: Message, state: FSMContext):
    await state.set_state(SetupStates.income_rhythm)
    await message.answer(
        (
            f"{progress_bar(2, 2)}\n\n"
            "<b>ОХАРАКТЕРИЗУЙТЕ ВАШ ОСНОВНОЙ ДОХОД, КОТОРЫЙ ПРИНОСИТ ВАМ БОЛЬШЕ ВСЕГО ДЕНЕГ</b>"
        ),
        reply_markup=keyboard([
            [("Стабильный", "rhythm:monthly"), ("Сдельный", "rhythm:irregular")],
            [("Цикличный (контрактный)", "rhythm:cyclic")],
            [("ℹ️", "rhythm:help")],
        ]),
    )


@router.callback_query(SetupStates.income_rhythm, F.data == "rhythm:help")
async def show_income_rhythm_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "<b>КАК ВЫБРАТЬ ФИНАНСОВЫЙ ЦИКЛ</b>\n\n"
        "<b>Стабильный</b> — сотрудник с постоянным окладом, пенсионер, студент с регулярной "
        "стипендией, получатель стабильного пособия или вахтовик, которому достаточная сумма "
        "приходит каждый календарный месяц.\n\n"
        "<b>Сдельный</b> — фрилансер, самозанятый, преподаватель с оплатой за урок, музыкант, "
        "мастер или продавец с комиссией: деньги приходят большую часть года, но сумма меняется.\n\n"
        "<b>Цикличный (контрактный)</b> — моряк, сезонный работник, артист по контракту или "
        "вахтовик, у которого заранее бывают периоды с недостаточным доходом.\n\n"
        "Профиль определяет движение всех денег, а не название профессии.",
        reply_markup=keyboard([
            [("Стабильный", "rhythm:monthly"), ("Сдельный", "rhythm:irregular")],
            [("Цикличный (контрактный)", "rhythm:cyclic")],
        ]),
    )


@router.callback_query(SetupStates.income_rhythm, F.data.startswith("rhythm:"))
async def save_income_rhythm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    rhythm = callback.data.split(":", 1)[1]
    if rhythm != "cyclic":
        await state.update_data(
            income_rhythm=rhythm,
            profile_type="stable" if rhythm == "monthly" else "piecework",
            employment_type="Наёмный" if rhythm == "monthly" else "Фрилансер",
            income_gap_months="1",
            income_work_months="1",
            reliable_gap_income="0",
            stabilizer_target_months="1",
        )
        await ask_income(callback.message, state)
        return
    await state.update_data(
        income_rhythm="cyclic",
        profile_type="cyclic",
        employment_type="Фрилансер",
    )
    await state.set_state(SetupStates.income_work_months)
    await callback.message.answer(
        f"{setup_progress(await state.get_data(), 3)}\n\n"
        "<b>СКОЛЬКО ОБЫЧНО ДЛИТСЯ РАБОЧАЯ ЧАСТЬ ЦИКЛА?</b>\n\n"
        "ℹ️ Цикл — это полный повторяющийся отрезок: рабочая часть + нерабочая часть.\n\n"
        "Поэтому:\n"
        "• Маша работает 5 месяцев и 7 месяцев живёт до следующего контракта — цикл 12 месяцев;\n"
        "• Петя работает месяц и месяц отдыхает — цикл 2 месяца.\n\n"
        "Если число рабочих месяцев не целое, округляйте по правилам арифметики. Например:\n"
        "5 месяцев 2 недели → 6\n"
        "5 месяцев и 1 неделя → 5\n\n"
        "——————\n"
        "→ Введите количество <b>рабочих</b> месяцев числом.\n"
        "<b>Например:</b> <code>2</code>."
    )


@router.message(SetupStates.income_work_months)
async def save_income_work_months(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 24 or value != value.to_integral_value():
        await message.answer("Введите целое количество месяцев от 1 до 24.")
        return
    await state.update_data(income_work_months=str(value))
    await state.set_state(SetupStates.income_gap_months)
    await message.answer(
        f"{setup_progress(await state.get_data(), 4)}\n\n"
        "<b>СКОЛЬКО ОБЫЧНО ДЛИТСЯ ПЕРИОД С НЕДОСТАТОЧНЫМ ДОХОДОМ?</b>\n\n"
        "——————\n"
        "→ Укажите плановую <b>нерабочую</b> часть цикла.\n"
        "<b>Например:</b> <code>7</code>",
        reply_markup=keyboard([
            [("1 месяц", "gap:1"), ("2 месяца", "gap:2")],
            [("3 месяца", "gap:3"), ("6 месяцев", "gap:6")],
            [("Указать другое", "gap:custom")],
        ]),
    )


@router.callback_query(SetupStates.income_gap_months, F.data.startswith("gap:"))
async def save_income_gap_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await callback.message.answer("Введите количество полных месяцев без надёжного дохода.")
        return
    await state.update_data(income_rhythm="cyclic", income_gap_months=value)
    await show_fund_salary_intro(callback.message, state)


@router.message(SetupStates.income_gap_months)
async def save_income_gap_text(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 24 or value != value.to_integral_value():
        await message.answer("Введите целое число от 1 до 24.")
        return
    await state.update_data(income_rhythm="cyclic", income_gap_months=str(value))
    await show_fund_salary_intro(message, state)


async def show_fund_salary_intro(message: Message, state: FSMContext):
    await state.set_state(SetupStates.fund_salary_intro)
    await message.answer(
        f"{setup_progress(await state.get_data(), 4)}\n\n"
        "<b>ФОНД ЗАРПЛАТЫ</b>\n\n"
        "Во время перерыва между контрактами вы будете платить зарплату самому себе.\n\n"
        "Аллокатор рассчитает необходимую сумму и поможет проводить ежемесячные выплаты.",
        reply_markup=keyboard([[("Понятно →", "fundsalary:intro")]]),
    )


@router.callback_query(SetupStates.fund_salary_intro, F.data == "fundsalary:intro")
async def continue_after_fund_salary_intro(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(reliable_gap_income="0")
    await ask_stabilizer_target(callback.message, state)


async def ask_stabilizer_target(message: Message, state: FSMContext):
    await state.set_state(SetupStates.stabilizer_target_months)
    await message.answer(
        f"{setup_progress(await state.get_data(), 5)}\n\n"
        "<b>НА СКОЛЬКО МОЖЕТ УВЕЛИЧИТЬСЯ ПЕРЕРЫВ МЕЖДУ КОНТРАКТАМИ?</b>\n\n"
        "Я сформирую <b>Стабилизатор дохода</b>, который защитит вас, если следующий контракт "
        "задержится, отменится или предыдущий закончится раньше.\n\n"
        "Рекомендую выбрать 2 месяца.",
        reply_markup=keyboard([
            [("1 месяц", "stabilizermonths:1"), ("2 месяца", "stabilizermonths:2")],
            [("3 месяца", "stabilizermonths:3"), ("6 месяцев", "stabilizermonths:6")],
            [("Свой вариант", "stabilizermonths:custom")],
        ]),
    )


@router.callback_query(SetupStates.stabilizer_target_months, F.data.startswith("stabilizermonths:"))
async def save_stabilizer_target_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await callback.message.answer("Введите количество месяцев от 1 до 12.")
        return
    await state.update_data(stabilizer_target_months=value, progress_offset=3)
    await ask_income(callback.message, state)


@router.message(SetupStates.stabilizer_target_months)
async def save_stabilizer_target_text(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 12:
        await message.answer("Введите количество месяцев от 1 до 12.")
        return
    await state.update_data(stabilizer_target_months=str(value), progress_offset=3)
    await ask_income(message, state)


async def ask_tax(message: Message, state: FSMContext):
    await state.update_data(income_type_tax_rates={})
    await show_income_types_setup(message, state)


async def show_income_types_setup(message: Message, state: FSMContext):
    data = await state.get_data()
    rates = data.get("income_type_tax_rates", {})
    await state.set_state(SetupStates.income_types_menu)
    lines = [
        f"• {escape(name)} — " + (f"налог {rate}%" if Decimal(str(rate)) > 0 else "без налога")
        for name, rate in rates.items()
    ]
    rows = [[(name, f"profileincome:view:{index}")] for index, name in enumerate(rates)]
    rows.append([("＋ Добавить доход", "profileincome:add")])
    if rates:
        rows.append([("✔️ Готово", "profileincome:done")])
    await message.answer(
        f"{setup_progress(data, 4)}\n\n"
        "<b>ТИПЫ ДОХОДОВ</b>\n\n"
        "Добавьте свои названия доходов, которые планируете учитывать. Они станут кнопками "
        "при добавлении нового дохода. Для каждого типа можно отдельно указать налог.\n\n"
        + ("\n".join(lines) if lines else "Пока ничего не добавлено."),
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data == "profileincome:add")
async def profile_income_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SetupStates.income_type_name)
    await callback.message.answer(
        "<b>НОВЫЙ ТИП ДОХОДА</b>\n\n——————\n"
        "<b>→ Введите короткое название.</b>\n"
        "<b>Например:</b> Зарплата, Заказ ФЛ, Консультация",
        reply_markup=keyboard([[("Отмена", "profileincome:back")]]),
    )


@router.message(SetupStates.income_type_name)
async def profile_income_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    rates = data.get("income_type_tax_rates", {})
    if len(name) < 2 or len(name) > 40:
        await message.answer("Введите название длиной от 2 до 40 символов.")
        return
    if name.casefold() in {item.casefold() for item in rates}:
        await message.answer("Такой тип дохода уже существует. Введите другое название.")
        return
    await state.update_data(pending_income_type_name=name)
    await state.set_state(SetupStates.income_type_tax_choice)
    await message.answer(
        f"<b>{escape(name.upper())}</b>\n\nНужно самостоятельно откладывать налог с этого дохода?",
        reply_markup=keyboard([
            [("Есть налог", "profileincome:tax:yes"), ("Без налога", "profileincome:tax:no")],
            [("Отмена", "profileincome:back")],
        ]),
    )


@router.callback_query(SetupStates.income_type_tax_choice, F.data.startswith("profileincome:tax:"))
async def profile_income_tax_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data.endswith(":no"):
        await save_profile_income_type(callback.message, state, Decimal("0"))
        return
    await state.set_state(SetupStates.income_type_rate)
    await callback.message.answer(
        "<b>СТАВКА НАЛОГА</b>\n\n——————\n"
        "<b>→ Введите число без знака %.</b>\n"
        "<b>Например:</b> <code>4</code>"
    )


@router.message(SetupStates.income_type_rate)
async def profile_income_rate(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0 or value > 100:
        await message.answer("Введите ставку больше 0 и не больше 100.")
        return
    await save_profile_income_type(message, state, value)


async def save_profile_income_type(message: Message, state: FSMContext, rate: Decimal):
    data = await state.get_data()
    name = data["pending_income_type_name"]
    await state.update_data(pending_income_type_rate=str(rate))
    await state.set_state(SetupStates.income_type_confirm)
    await message.answer(
        "<b>ПРОВЕРЬТЕ ТИП ДОХОДА</b>\n\n"
        f"Название — <b>{escape(name)}</b>\n"
        + (f"Налог — <b>{rate}%</b>" if rate > 0 else "Налог — <b>не резервируется</b>"),
        reply_markup=keyboard([
            [("Исправить", "profileincome:add"), ("✔️ Сохранить", "profileincome:save")],
            [("Отмена", "profileincome:back")],
        ]),
    )


@router.callback_query(SetupStates.income_type_confirm, F.data == "profileincome:save")
async def profile_income_save(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    rates = dict(data.get("income_type_tax_rates", {}))
    original = data.get("pending_income_type_original")
    if original:
        rates.pop(original, None)
    rates[data["pending_income_type_name"]] = data["pending_income_type_rate"]
    await state.update_data(
        income_type_tax_rates=rates,
        pending_income_type_name=None,
        pending_income_type_rate=None,
        pending_income_type_original=None,
    )
    await show_income_types_setup(callback.message, state)


@router.callback_query(SetupStates.income_types_menu, F.data.startswith("profileincome:view:"))
async def profile_income_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    rates = (await state.get_data()).get("income_type_tax_rates", {})
    names = list(rates)
    if not 0 <= index < len(names):
        await show_income_types_setup(callback.message, state)
        return
    name = names[index]
    await callback.message.answer(
        f"<b>{escape(name.upper())}</b>\n\n"
        + (f"Налог — <b>{rates[name]}%</b>" if Decimal(str(rates[name])) > 0 else "Без налога"),
        reply_markup=keyboard([
            [("Изменить название", f"profileincome:editname:{index}")],
            [("Изменить налог", f"profileincome:edittax:{index}")],
            [("Удалить", f"profileincome:delete:{index}")],
            [("Назад", "profileincome:back")],
        ]),
    )


@router.callback_query(F.data.startswith("profileincome:editname:"))
async def profile_income_edit_name_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    rates = (await state.get_data()).get("income_type_tax_rates", {})
    names = list(rates)
    if not 0 <= index < len(names):
        await show_income_types_setup(callback.message, state)
        return
    await state.update_data(pending_income_type_original=names[index])
    await state.set_state(SetupStates.income_type_edit_name)
    await callback.message.answer("Введите исправленное название.", reply_markup=keyboard([[("Отмена", "profileincome:back")]]))


@router.message(SetupStates.income_type_edit_name)
async def profile_income_edit_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    rates = data.get("income_type_tax_rates", {})
    original = data["pending_income_type_original"]
    if len(name) < 2 or len(name) > 40:
        await message.answer("Введите название длиной от 2 до 40 символов.")
        return
    if name.casefold() != original.casefold() and name.casefold() in {item.casefold() for item in rates}:
        await message.answer("Такой тип дохода уже существует.")
        return
    await state.update_data(
        pending_income_type_name=name,
        pending_income_type_rate=str(rates[original]),
    )
    await save_profile_income_type(message, state, Decimal(str(rates[original])))


@router.callback_query(F.data.startswith("profileincome:edittax:"))
async def profile_income_edit_tax_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    rates = (await state.get_data()).get("income_type_tax_rates", {})
    names = list(rates)
    if not 0 <= index < len(names):
        await show_income_types_setup(callback.message, state)
        return
    name = names[index]
    await state.update_data(pending_income_type_original=name, pending_income_type_name=name)
    await state.set_state(SetupStates.income_type_tax_choice)
    await callback.message.answer(
        f"<b>{escape(name.upper())}</b>\n\nНужно самостоятельно откладывать налог с этого дохода?",
        reply_markup=keyboard([
            [("Есть налог", "profileincome:tax:yes"), ("Без налога", "profileincome:tax:no")],
            [("Отмена", "profileincome:back")],
        ]),
    )


@router.callback_query(F.data.startswith("profileincome:delete:"))
async def profile_income_delete(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Удалено")
    index = int(callback.data.rsplit(":", 1)[1])
    rates = dict((await state.get_data()).get("income_type_tax_rates", {}))
    names = list(rates)
    if 0 <= index < len(names):
        rates.pop(names[index])
        await state.update_data(income_type_tax_rates=rates)
    await show_income_types_setup(callback.message, state)


@router.callback_query(F.data == "profileincome:back")
async def profile_income_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_income_types_setup(callback.message, state)


@router.callback_query(SetupStates.income_types_menu, F.data == "profileincome:done")
async def profile_income_done(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    rates = (await state.get_data()).get("income_type_tax_rates", {})
    taxable = [name for name, rate in rates.items() if Decimal(str(rate)) > 0]
    default_rate = next((Decimal(str(rate)) for rate in rates.values() if Decimal(str(rate)) > 0), Decimal("0"))
    await state.update_data(tax_rate=str(default_rate), taxable_income_types=taxable)
    await start_critical_minimum(callback.message, state)


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
        f"• {escape(name)} — {rub(monthly)} / мес."
        for name, monthly in km_item_totals_by_name(items)
    ]
    summary = ""
    if item_lines:
        summary = "\n\n<b>Уже добавлено</b>\n" + "\n".join(item_lines)
        summary += f"\n\nСейчас найдено: <b>{rub(exact)}</b> / мес."

    intro_text = ""
    if intro:
        intro_text = (
            "\n\nℹ️ Критический минимум — обязательная стоимость вашей жизни. "
            "Не угадывайте суммы: открывайте банковскую аналитику, договоры и тарифы.\n\n"
            "Если при резком падении дохода от этих расходов можно отказаться на несколько месяцев "
            "без серьёзных последствий, отнесите их в <b>Бытовой резерв.</b>\n\n"
            "<b>Одну и ту же кнопку можно нажимать несколько раз.</b> Например, в категории "
            "<b>Недвижимость</b> "
            "можно отдельно добавить Квартиру, ЖКХ, Ипотеку, Студию и др."
        )

    debt_note = ""
    if data.get("has_debts"):
        debt_note = (
            "\n\nДолги и платежи по кредитам здесь не указывайте — "
            "Аллокатор учтёт их отдельно."
        )

    rows = [
        [("Недвижимость", "kmcat:housing"), ("Здоровье", "kmcat:health")],
        [("Связь и подписки", "kmcat:communication"), ("Питомцы", "kmcat:pets")],
        [("Транспорт", "kmcat:transport"), ("Дети", "kmcat:children")],
        [("Питание", "kmcat:food"), ("Образование", "kmcat:education")],
        [("Вредные привычки", "kmcat:habits"), ("Комиссии", "kmcat:fees")],
        [("Другое", "kmcat:other")],
    ]
    if items:
        rows.append([("Редактировать", "kmedit:list"), ("✔️ Готово", "km:finish")])
    else:
        rows.append([("✔️ Готово", "km:finish")])

    text = (
        f"{setup_progress(data, 5)}\n\n"
        "<b>ПОСЧИТАЕМ ВАШ КРИТИЧЕСКИЙ МИНИМУМ</b>"
        + intro_text
        + debt_note
        + summary
    )
    reply_markup = keyboard(rows)

    if intro and CRITICAL_MINIMUM_IMAGE.exists() and len(text) <= 1024:
        await message.answer_photo(
            photo=FSInputFile(CRITICAL_MINIMUM_IMAGE),
            caption=text,
            reply_markup=reply_markup,
        )
        return

    await message.answer(
        text,
        reply_markup=reply_markup,
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmcat:"))
async def choose_km_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key = callback.data.split(":", 1)[1]
    if key not in KM_CATEGORIES:
        return

    label, hint = KM_CATEGORIES[key]
    await state.update_data(
        pending_km_category=key,
        pending_km_category_label=label,
        pending_km_subcategory=None,
    )
    if key == "housing":
        await state.set_state(SetupStates.km_menu)
        await show_housing_landing(callback.message, state)
        return
    if key == "transport":
        await state.set_state(SetupStates.km_menu)
        data = await state.get_data()
        await callback.message.answer(
            f"{setup_progress(data, 5)}\n\n<b>ТРАНСПОРТ</b>\n\n{hint}",
            reply_markup=keyboard([
                [("Общественный транспорт", "kmtransport:public"), ("Проездной", "kmtransport:pass")],
                [("Необходимое такси", "kmtransport:taxi"), ("Автомобиль", "kmtransport:car")],
                [("Транспортный налог", "kmtransport:tax"), ("+ Свой расход", "kmtransport:other")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    if key == "communication":
        await state.set_state(SetupStates.km_menu)
        await callback.message.answer(
            f"{setup_progress(await state.get_data(), 5)}\n\n"
            "<b>СВЯЗЬ И ПОДПИСКИ</b>\n\n"
            "Выберите обязательный расход.",
            reply_markup=keyboard([
                [("Мобильная связь", "kmcommunication:mobile"), ("Домашний интернет", "kmcommunication:internet")],
                [("VPN", "kmcommunication:vpn"), ("Подписки", "kmcommunication:subscription")],
                [("ТВ", "kmcommunication:tv"), ("+ Свой расход", "kmcommunication:other")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    if key == "education":
        await state.set_state(SetupStates.km_menu)
        await callback.message.answer(
            f"{setup_progress(await state.get_data(), 5)}\n\n"
            "<b>КАК УСТРОЕНА ОПЛАТА?</b>",
            reply_markup=keyboard([
                [("За каждое занятие", "kmeducation:lesson"), ("Каждый месяц", "kmeducation:monthly")],
                [("Крупный платёж", "kmeducation:large"), ("Другое", "kmeducation:other")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    quick_categories = {
        "health": [
            [("Лекарства", "medicine"), ("Стоматолог", "dentist")],
            [("Врачи", "doctors"), ("Анализы", "tests")],
            [("Витамины", "vitamins"), ("Другое", "other")],
        ],
        "pets": [
            [("Корм", "food"), ("Наполнитель", "litter")],
            [("Пелёнки", "pads"), ("Ветеринар", "vet")],
            [("Аксессуары", "accessories"), ("Другое", "other")],
        ],
        "children": [
            [("Детский сад", "kindergarten"), ("Школа", "school")],
            [("Питание", "food"), ("Секция", "club")],
            [("Другое", "other")],
        ],
        "food": [
            [("Супермаркет", "supermarket"), ("Питьевая вода", "water")],
            [("Еда вне дома", "outside"), ("Доставка", "delivery")],
            [("Другое", "other")],
        ],
    }
    if key in quick_categories:
        rows = [
            [(text, f"kmquick:{key}:{code}") for text, code in row]
            for row in quick_categories[key]
        ]
        rows.append([("← Назад", "km:cancel")])
        await state.set_state(SetupStates.km_menu)
        await callback.message.answer(
            f"{setup_progress(await state.get_data(), 5)}\n\n"
            f"<b>{escape(label.upper())}</b>\n\n"
            "Выберите расход или добавьте свой.",
            reply_markup=keyboard(rows),
        )
        return
    await state.set_state(SetupStates.km_item_name)
    data = await state.get_data()
    prompt = hint
    if "→ Введите название расхода" not in prompt:
        prompt += "\n\n——————\n<b>→ Введите название расхода.</b>"
    await callback.message.answer(
        f"{setup_progress(data, 5)}\n\n"
        f"<b>{escape(label.upper())}</b>\n\n"
        + prompt,
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


HOUSING_EXPENSE_LABELS = {
    "utilities": "ЖКХ",
    "rent": "Аренда",
    "mortgage": "Ипотека",
    "insurance": "Страхование",
    "property_tax": "Налог на имущество",
    "land_tax": "Земельный налог",
}


async def show_housing_landing(message: Message, state: FSMContext):
    await state.set_state(SetupStates.km_menu)
    await message.answer(
        f"{setup_progress(await state.get_data(), 5)}\n\n"
        "<b>НЕДВИЖИМОСТЬ</b>\n\n"
        "Здесь учитываются обязательные расходы на жильё и другие необходимые помещения.",
        reply_markup=keyboard([
            [("+ Добавить расход", "kmhousing:add")],
            [("← Назад", "km:cancel")],
        ]),
    )


async def show_housing_expense_types(message: Message, state: FSMContext):
    await state.set_state(SetupStates.km_menu)
    await message.answer(
        f"{setup_progress(await state.get_data(), 5)}\n\n<b>КАКОЙ ЭТО РАСХОД?</b>",
        reply_markup=keyboard([
            [("ЖКХ", "kmhousingexpense:utilities"), ("Аренда", "kmhousingexpense:rent")],
            [("Ипотека", "kmhousingexpense:mortgage"), ("Страхование", "kmhousingexpense:insurance")],
            [("Налог на имущество", "kmhousingexpense:property_tax")],
            [("Земельный налог", "kmhousingexpense:land_tax")],
            [("+ Свой расход", "kmhousingexpense:custom")],
            [("← Назад", "kmhousing:landing")],
        ]),
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmhousing:"))
async def choose_housing_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    if action == "add":
        await show_housing_expense_types(callback.message, state)
    elif action == "landing":
        await show_housing_landing(callback.message, state)


HOUSING_OBJECT_LABELS = {
    "apartment": "Квартира",
    "house": "Дом",
    "dorm": "Общежитие",
    "studio": "Студия",
    "office": "Офис",
    "workshop": "Мастерская",
    "warehouse": "Склад",
    "garage": "Гараж",
    "dacha": "Дача",
}

async def show_housing_objects(message: Message, state: FSMContext):
    await state.set_state(SetupStates.km_menu)
    data = await state.get_data()
    expense_label = data.get("pending_km_housing_expense_label") or "Расход"
    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        f"<b>К КАКОМУ ОБЪЕКТУ ОТНОСИТСЯ РАСХОД «{escape(expense_label.upper())}»?</b>\n\n"
        "Выберите готовое название или введите своё.",
        reply_markup=keyboard([
            [("Квартира", "kmhousingobj:apartment"), ("Дом", "kmhousingobj:house")],
            [("Общежитие", "kmhousingobj:dorm"), ("Дача", "kmhousingobj:dacha")],
            [("Офис", "kmhousingobj:office"), ("Студия", "kmhousingobj:studio")],
            [("Мастерская", "kmhousingobj:workshop"), ("Склад", "kmhousingobj:warehouse")],
            [("Гараж", "kmhousingobj:garage"), ("Своё название", "kmhousingobj:custom")],
            [("← Назад", "kmhousingobj:back")],
        ]),
    )


def housing_item_name(subtype: str, expense_label: str, object_name: str) -> str:
    if subtype in {"property_tax", "land_tax"}:
        return object_name.strip()
    return f"{expense_label.strip()} · {object_name.strip()}"


def matching_housing_total(items: list[dict], subtype: str, item_name: str) -> Decimal:
    normalized_name = " ".join(item_name.split()).casefold()
    return money2(
        sum(
            (
                Decimal(item["monthly"])
                for item in items
                if item.get("category") == "housing"
                and item.get("subcategory") == subtype
                and " ".join(str(item.get("name") or "").split()).casefold() == normalized_name
            ),
            Decimal("0"),
        )
    )


async def ask_housing_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    subtype = data.get("pending_km_subcategory") or "other"
    expense_label = data.get("pending_km_housing_expense_label") or "Расход"
    object_name = data.get("pending_km_housing_object") or "Объект"
    item_name = housing_item_name(subtype, expense_label, object_name)
    await state.update_data(pending_km_item_name=item_name)
    await state.set_state(SetupStates.km_item_amount)
    explanations = {
        "utilities": "Обязательные коммунальные платежи по выбранному объекту.",
        "rent": "Обязательная арендная плата. ЖКХ добавьте отдельно, если оно не входит в аренду.",
        "mortgage": "Только обязательный платёж по ипотеке, без досрочного погашения.",
        "insurance": "Обязательное страхование недвижимости, включая обязательную страховку по ипотеке.",
        "property_tax": "Налог входит в Критический минимум, но будет храниться в общем конверте «Налоги».",
        "land_tax": "Налог входит в Критический минимум, но будет храниться в общем конверте «Налоги».",
        "other": "Обязательный расход по выбранному объекту.",
    }
    suffix = (
        "Срок уплаты укажем следующим сообщением"
        if subtype in {"property_tax", "land_tax"}
        else "Период укажем следующим сообщением"
    )
    await message.answer(
        f"<b>{escape(expense_label.upper())} · {escape(object_name.upper())}</b>\n\n"
        f"{explanations.get(subtype, explanations['other'])}\n\n"
        "——————\n<b>→ Введите сумму.</b>\n"
        f"({suffix})",
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


async def continue_housing_object(message: Message, state: FSMContext, object_name: str):
    data = await state.get_data()
    subtype = data.get("pending_km_subcategory") or "other"
    expense_label = data.get("pending_km_housing_expense_label") or "Расход"
    item_name = housing_item_name(subtype, expense_label, object_name)
    existing_total = matching_housing_total(data.get("km_items", []), subtype, item_name)
    await state.update_data(
        pending_km_housing_object=object_name,
        pending_km_item_name=item_name,
    )
    if existing_total <= 0:
        await ask_housing_amount(message, state)
        return
    await state.set_state(SetupStates.km_menu)
    await message.answer(
        f"<b>{escape(expense_label.upper())} · {escape(object_name.upper())} УЖЕ ДОБАВЛЕН</b>\n\n"
        f"Сейчас учтено — <b>{rub(existing_total)}</b> в месяц.\n\n"
        "Если это ещё один платёж по тому же объекту, добавьте сумму к существующему расходу.\n\n"
        "Если речь идёт о другом объекте, уточните его название.",
        reply_markup=keyboard([
            [(f"Добавить к «{object_name}»", "kmhousingdup:add")],
            [("Уточнить объект", "kmhousingdup:clarify")],
            [("← Назад", "kmhousingdup:back")],
        ]),
    )


@router.callback_query(F.data.startswith("kmhousingdup:"))
async def choose_duplicate_housing_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    if action == "add":
        await ask_housing_amount(callback.message, state)
        return
    if action == "clarify":
        await state.set_state(SetupStates.km_housing_object_name)
        await callback.message.answer(
            "<b>КАК НАЗВАТЬ ДРУГОЙ ОБЪЕКТ?</b>\n\n"
            "Введите название, по которому вы сразу поймёте, о каком объекте идёт речь.\n\n"
            "<b>Например:</b> Квартира родителей, Однушка, Двушка, Квартира на Ленина.\n\n"
            "——————\n<b>→ Введите название объекта.</b>",
            reply_markup=keyboard([[('← Назад', 'kmhousinginput:objects')]]),
        )
        return
    if action == "back":
        await show_housing_objects(callback.message, state)


@router.callback_query(F.data.startswith("kmhousinginput:"))
async def housing_input_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target = callback.data.split(":", 1)[1]
    if target == "expenses":
        await show_housing_expense_types(callback.message, state)
    elif target == "objects":
        await show_housing_objects(callback.message, state)


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmhousingobj:"))
async def choose_housing_object(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.split(":", 1)[1]
    if code == "back":
        await show_housing_expense_types(callback.message, state)
        return
    if code == "custom":
        await state.set_state(SetupStates.km_housing_object_name)
        await callback.message.answer(
            "<b>СВОЁ НАЗВАНИЕ</b>\n\nВведите понятное вам название объекта.\n"
            "<b>Например:</b> Квартира родителей, Репетиционная база, Павильон.\n\n"
            "——————\n<b>→ Введите название объекта.</b>",
            reply_markup=keyboard([[('← Назад', 'kmhousinginput:objects')]]),
        )
        return
    object_name = HOUSING_OBJECT_LABELS.get(code)
    if object_name is None:
        return
    await state.update_data(pending_km_housing_object=object_name)
    await continue_housing_object(callback.message, state, object_name)


@router.message(SetupStates.km_housing_object_name)
async def custom_housing_object_name(message: Message, state: FSMContext):
    object_name = (message.text or "").strip()
    if len(object_name) < 2 or parse_decimal(object_name) is not None:
        await message.answer("Введите понятное название объекта, например <code>Квартира родителей</code>.")
        return
    await state.update_data(pending_km_housing_object=object_name)
    await continue_housing_object(message, state, object_name)


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmhousingexpense:"))
async def choose_housing_object_expense(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subtype = callback.data.split(":", 1)[1]
    if subtype == "custom":
        await state.set_state(SetupStates.km_housing_expense_name)
        await callback.message.answer(
            "<b>СВОЙ РАСХОД НА НЕДВИЖИМОСТЬ</b>\n\n"
            "Введите понятное название обязательного расхода. Он останется внутри категории «Недвижимость».\n\n"
            "<b>Например:</b> Охрана территории, Взносы товариществу, Плата за общежитие.\n\n"
            "——————\n<b>→ Введите название расхода.</b>",
            reply_markup=keyboard([[('← Назад', 'kmhousinginput:expenses')]]),
        )
        return
    expense_label = HOUSING_EXPENSE_LABELS.get(subtype)
    if expense_label is None:
        return
    await state.update_data(
        pending_km_category="housing",
        pending_km_category_label="Недвижимость",
        pending_km_subcategory=subtype,
        pending_km_housing_expense_label=expense_label,
        pending_km_housing_object=None,
    )
    await show_housing_objects(callback.message, state)


@router.message(SetupStates.km_housing_expense_name)
async def custom_housing_expense_name(message: Message, state: FSMContext):
    expense_name = (message.text or "").strip()
    if len(expense_name) < 2 or parse_decimal(expense_name) is not None:
        await message.answer("Введите понятное название расхода.")
        return
    await state.update_data(
        pending_km_category="housing",
        pending_km_category_label="Недвижимость",
        pending_km_subcategory="other",
        pending_km_housing_expense_label=expense_name,
        pending_km_housing_object=None,
    )
    await show_housing_objects(message, state)


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmtransport:"))
async def choose_transport_subcategory(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subtype = callback.data.split(":", 1)[1]
    labels = {
        "public": "Общественный транспорт",
        "pass": "Безлимитный проездной",
        "taxi": "Такси — необходимое",
        "tax": "Транспортный налог",
        "other": "Свой расход",
    }
    if subtype == "back":
        await callback.message.answer(
            "<b>ТРАНСПОРТ</b>\n\nВыберите обязательный транспортный расход.",
            reply_markup=keyboard([
                [("Общественный транспорт", "kmtransport:public"), ("Проездной", "kmtransport:pass")],
                [("Необходимое такси", "kmtransport:taxi"), ("Автомобиль", "kmtransport:car")],
                [("Транспортный налог", "kmtransport:tax"), ("+ Свой расход", "kmtransport:other")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    if subtype == "car":
        await callback.message.answer(
            "<b>АВТОМОБИЛЬ</b>\n\nВыберите обязательный расход.",
            reply_markup=keyboard([
                [("Бензин", "kmtransportcar:fuel"), ("Страхование", "kmtransportcar:insurance")],
                [("ТО", "kmtransportcar:maintenance"), ("Расходники", "kmtransportcar:supplies")],
                [("Автосервис", "kmtransportcar:service"), ("Шиномонтаж", "kmtransportcar:tireservice")],
                [("Платные дороги", "kmtransportcar:tolls"), ("Мойка", "kmtransportcar:wash")],
                [("Резина", "kmtransportcar:tires"), ("Штрафы ГИБДД", "kmtransportcar:fines")],
                [("← Назад", "kmtransport:back"), ("+ Свой расход", "kmtransportcar:other")],
            ]),
        )
        return
    if subtype not in labels:
        return
    await state.update_data(
        pending_km_category="transport",
        pending_km_category_label="Транспорт",
        pending_km_subcategory=subtype,
    )
    data = await state.get_data()
    if subtype == "tax":
        await state.set_state(SetupStates.km_item_name)
        await callback.message.answer(
            f"{setup_progress(data, 5)}\n\n"
            "<b>ТРАНСПОРТНЫЙ НАЛОГ</b>\n\n"
            "Сумму и срок уплаты посмотрите в налоговом уведомлении — в Личном кабинете "
            "налогоплательщика на сайте ФНС или на Госуслугах. В уведомлении указаны объект, "
            "начисленная сумма и дата платежа.\n\n"
            "Можно указать точную сумму или осторожную оценку, если уведомление ещё не пришло.\n\n"
            "Отдельный конверт «Транспортный налог» не создаётся. Эта сумма будет учитываться "
            "в общем конверте «Налоги» вместе с другими налоговыми обязательствами.\n\n"
            "——————\n"
            "<b>→ Введите название автомобиля.</b>\n"
            "<b>Например:</b> Автомобиль, Лада, Volkswagen",
            reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
        )
        return
    if subtype == "other":
        await state.set_state(SetupStates.km_item_name)
        await callback.message.answer(
            "<b>СВОЙ ТРАНСПОРТНЫЙ РАСХОД</b>\n\n"
            "Добавьте обязательный расход, которого нет среди готовых вариантов.\n\n"
            "——————\n<b>→ Введите понятное название.</b>",
            reply_markup=keyboard([[('← Назад', 'kmtransportinput:menu')]]),
        )
        return
    texts = {
        "public": (
            "• Метро\n• Автобус\n• Трамвай\n• Электричка\n• Другой регулярный общественный транспорт\n\n"
            "Если вы часто пользуетесь общественным транспортом, проверьте, есть ли подходящий "
            "безлимитный или льготный проездной. Во многих случаях он помогает заметно сократить расходы."
        ),
        "pass": (
            "Укажите полную стоимость проездного. Затем Аллокатор спросит, через сколько полных "
            "месяцев потребуется купить новый. Деньги будут накапливаться в отдельном конверте «Проездной»."
        ),
        "taxi": (
            "Укажите обязательные поездки, которые повторяются каждый месяц. Например, если вы регулярно "
            "возите родственника в поликлинику, питомца к ветеринару или возвращаетесь с работы ночью, "
            "когда другого транспорта нет.\n\nРедкие поездки до аэропорта или вокзала относятся к "
            "Бытовому резерву. Поездки из-за того, что просто не хочется ехать на общественном транспорте, — тоже."
        ),
    }
    await state.update_data(pending_km_item_name=labels[subtype])
    await state.set_state(SetupStates.km_item_amount)
    await callback.message.answer(
        f"{setup_progress(data, 5)}\n\n<b>{escape(labels[subtype].upper())}</b>\n\n"
        f"{texts[subtype]}\n\n——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


TRANSPORT_CAR_LABELS = {
    "fuel": "Бензин",
    "insurance": "Страхование автомобиля",
    "maintenance": "ТО",
    "supplies": "Расходники",
    "service": "Автосервис",
    "tireservice": "Шиномонтаж",
    "tolls": "Платные дороги",
    "wash": "Мойка",
    "tires": "Резина",
    "fines": "Штрафы ГИБДД",
}


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmtransportcar:"))
async def choose_car_expense(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[1]
    await state.update_data(
        pending_km_category="transport",
        pending_km_category_label="Транспорт",
        pending_km_subcategory=f"car_{code}",
    )
    if code == "other":
        await state.set_state(SetupStates.km_item_name)
        await callback.message.answer(
            "<b>СВОЙ РАСХОД НА АВТОМОБИЛЬ</b>\n\n"
            "——————\n<b>→ Введите понятное название расхода.</b>",
            reply_markup=keyboard([[('← Назад', 'kmtransportinput:car')]]),
        )
        return
    label = TRANSPORT_CAR_LABELS.get(code)
    if label is None:
        return
    explanations = {
        "fuel": "Укажите необходимую сумму на регулярные поездки.",
        "insurance": "ОСАГО, КАСКО или другое необходимое страхование автомобиля.",
        "maintenance": "Обязательное плановое техническое обслуживание.",
        "supplies": "Масла, жидкости, фильтры и другие необходимые расходные материалы.",
        "service": "Необходимые диагностика и ремонт автомобиля.",
        "tireservice": "Сезонная смена колёс, балансировка и обязательные работы с шинами.",
        "tolls": "Регулярные обязательные поездки по платным дорогам.",
        "wash": "Необходимая мойка автомобиля. Необязательные услуги лучше учитывать в Бытовом резерве.",
        "tires": "Плановая покупка необходимой сезонной резины.",
        "fines": "Учитывайте реальную сумму, если такие расходы фактически возникают. Безопаснее стремиться сократить их до нуля.",
    }
    await state.update_data(pending_km_item_name=label)
    await state.set_state(SetupStates.km_item_amount)
    await callback.message.answer(
        f"<b>{escape(label.upper())}</b>\n\n{explanations[code]}\n\n"
        "——————\n<b>→ Введите сумму.</b>\n(Период укажем следующим сообщением)",
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


@router.callback_query(F.data.startswith("kmtransportinput:"))
async def transport_input_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target = callback.data.rsplit(":", 1)[1]
    await state.set_state(SetupStates.km_menu)
    if target == "car":
        await callback.message.answer(
            "<b>АВТОМОБИЛЬ</b>\n\nВыберите обязательный расход.",
            reply_markup=keyboard([
                [("Бензин", "kmtransportcar:fuel"), ("Страхование", "kmtransportcar:insurance")],
                [("ТО", "kmtransportcar:maintenance"), ("Расходники", "kmtransportcar:supplies")],
                [("Автосервис", "kmtransportcar:service"), ("Шиномонтаж", "kmtransportcar:tireservice")],
                [("Платные дороги", "kmtransportcar:tolls"), ("Мойка", "kmtransportcar:wash")],
                [("Резина", "kmtransportcar:tires"), ("Штрафы ГИБДД", "kmtransportcar:fines")],
                [("← Назад", "kmtransport:back"), ("+ Свой расход", "kmtransportcar:other")],
            ]),
        )
        return
    await callback.message.answer(
        "<b>ТРАНСПОРТ</b>\n\nВыберите обязательный транспортный расход.",
        reply_markup=keyboard([
            [("Общественный транспорт", "kmtransport:public"), ("Проездной", "kmtransport:pass")],
            [("Необходимое такси", "kmtransport:taxi"), ("Автомобиль", "kmtransport:car")],
            [("Транспортный налог", "kmtransport:tax"), ("+ Свой расход", "kmtransport:other")],
            [("← Назад", "km:cancel")],
        ]),
    )


async def ask_preset_km_amount(message: Message, state: FSMContext, name: str):
    await state.update_data(pending_km_item_name=name)
    await state.set_state(SetupStates.km_item_amount)
    data = await state.get_data()
    await message.answer(
        f"{setup_progress(data, 5)}\n\n<b>{escape(name.upper())}</b>\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[("Отмена", "km:cancel")]]),
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmquick:"))
async def choose_quick_km_subcategory(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, category, subtype = callback.data.split(":", 2)
    labels = {
        "health": {
            "medicine": "Лекарства", "dentist": "Стоматолог", "doctors": "Врачи",
            "tests": "Анализы", "vitamins": "Витамины",
        },
        "pets": {
            "food": "Корм", "litter": "Наполнитель", "pads": "Пелёнки",
            "vet": "Ветеринар", "accessories": "Аксессуары",
        },
        "children": {
            "kindergarten": "Детский сад", "school": "Школа", "food": "Питание", "club": "Секция",
        },
        "food": {
            "supermarket": "Супермаркет", "water": "Питьевая вода",
            "outside": "Еда вне дома", "delivery": "Доставка",
        },
    }
    category_labels = {
        "health": "Здоровье", "pets": "Питомцы", "children": "Дети", "food": "Питание",
    }
    if category not in labels or (subtype != "other" and subtype not in labels[category]):
        return
    await state.update_data(
        pending_km_category=category,
        pending_km_category_label=category_labels[category],
        pending_km_subcategory=subtype,
    )
    if subtype == "other":
        await state.set_state(SetupStates.km_item_name)
        await callback.message.answer(
            f"<b>{escape(category_labels[category].upper())}</b>\n\n——————\n"
            "<b>→ Введите название расхода.</b>",
            reply_markup=keyboard([[("← Назад", "km:cancel")]]),
        )
        return
    await ask_preset_km_amount(callback.message, state, labels[category][subtype])


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmcommunication:"))
async def choose_communication_subcategory(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subtype = callback.data.rsplit(":", 1)[1]
    labels = {
        "mobile": "Мобильная связь",
        "subscription": "Подписки",
        "internet": "Домашний интернет",
        "vpn": "VPN",
        "tv": "ТВ",
        "other": "Свой расход",
    }
    if subtype == "back":
        await state.set_state(SetupStates.km_menu)
        await callback.message.answer(
            "<b>СВЯЗЬ И ПОДПИСКИ</b>\n\nВыберите обязательный расход.",
            reply_markup=keyboard([
                [("Мобильная связь", "kmcommunication:mobile"), ("Домашний интернет", "kmcommunication:internet")],
                [("VPN", "kmcommunication:vpn"), ("Подписки", "kmcommunication:subscription")],
                [("ТВ", "kmcommunication:tv"), ("+ Свой расход", "kmcommunication:other")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    if subtype not in labels:
        return
    await state.update_data(
        pending_km_category="communication",
        pending_km_category_label="Связь и подписки",
        pending_km_subcategory=subtype,
        pending_km_communication_label=None,
    )
    if subtype == "mobile":
        await callback.message.answer(
            "<b>КАК НАЗВАТЬ НОМЕР?</b>\n\n"
            "Не вводите номер телефона. Выберите понятное назначение SIM-карты или придумайте своё название.",
            reply_markup=keyboard([
                [("Личный", "kmcommunicationlabel:personal"), ("Рабочий", "kmcommunicationlabel:work")],
                [("Для ребёнка", "kmcommunicationlabel:child"), ("Для родителей", "kmcommunicationlabel:parents")],
                [("Резервный", "kmcommunicationlabel:reserve"), ("Своё название", "kmcommunicationlabel:custom")],
                [("← Назад", "kmcommunication:back")],
            ]),
        )
        return
    if subtype == "internet":
        await callback.message.answer(
            "<b>ГДЕ ПОДКЛЮЧЁН ИНТЕРНЕТ?</b>",
            reply_markup=keyboard([
                [("Квартира", "kmcommunicationlabel:apartment"), ("Дом", "kmcommunicationlabel:house")],
                [("Дача", "kmcommunicationlabel:dacha"), ("Офис", "kmcommunicationlabel:office")],
                [("Студия", "kmcommunicationlabel:studio"), ("Своё название", "kmcommunicationlabel:custom")],
                [("← Назад", "kmcommunication:back")],
            ]),
        )
        return
    await ask_communication_name(callback.message, state)


COMMUNICATION_DETAIL_LABELS = {
    "personal": "Личный",
    "work": "Рабочий",
    "child": "Для ребёнка",
    "parents": "Для родителей",
    "reserve": "Резервный",
    "apartment": "Квартира",
    "house": "Дом",
    "dacha": "Дача",
    "office": "Офис",
    "studio": "Студия",
}


async def ask_communication_name(message: Message, state: FSMContext):
    data = await state.get_data()
    subtype = data.get("pending_km_subcategory")
    prompts = {
        "mobile": (
            "<b>СВОЁ НАЗВАНИЕ</b>\n\nНе указывайте номер телефона. Напишите безопасное название, "
            "по которому вы узнаете эту SIM-карту.\n\n<b>Например:</b> Мегафон основной, МТС для работы, SIM для планшета."
        ),
        "internet": (
            "<b>СВОЁ НАЗВАНИЕ</b>\n\nВведите понятное название места подключения.\n\n"
            "<b>Например:</b> Квартира родителей, Мастерская, Загородный дом."
        ),
        "vpn": (
            "<b>VPN</b>\n\nВведите название сервиса. Не указывайте логин, адрес электронной почты, "
            "пароль или другие данные учётной записи.\n\n<b>Например:</b> Amnezia, Outline, Рабочий VPN."
        ),
        "subscription": (
            "<b>ПОДПИСКИ</b>\n\nВ Критический минимум добавляйте только подписки, без которых нельзя "
            "нормально работать, учиться или выполнять обязательные задачи. Развлекательные и "
            "необязательные подписки относятся к Бытовому резерву.\n\n"
            "<b>Например:</b> Облачное хранилище, рабочая программа, Яндекс Плюс."
        ),
        "tv": (
            "<b>ТВ</b>\n\nЕсли телевидение уже входит в тариф домашнего интернета, не добавляйте его "
            "повторно. Необязательное телевидение лучше учитывать в Бытовом резерве.\n\n"
            "<b>Например:</b> ТВ дома, Кабельное ТВ, Wink."
        ),
        "other": (
            "<b>СВОЙ РАСХОД НА СВЯЗЬ</b>\n\nДобавьте обязательный расход на связь или цифровой "
            "сервис, которого нет среди готовых вариантов. Не вводите логины, пароли, номера "
            "договоров и другие данные учётной записи.\n\n"
            "<b>Например:</b> Спутниковая связь, Корпоративная телефония."
        ),
    }
    await state.set_state(SetupStates.km_communication_name)
    await message.answer(
        prompts.get(subtype, prompts["other"])
        + "\n\n——————\n<b>→ Введите название.</b>",
        reply_markup=keyboard([[('← Назад', 'kmcommunicationinput:back')]]),
    )


def communication_item_name(subtype: str, detail: str) -> str:
    prefixes = {
        "mobile": "Мобильная связь",
        "internet": "Домашний интернет",
        "vpn": "VPN",
        "subscription": "Подписки",
        "tv": "ТВ",
    }
    prefix = prefixes.get(subtype)
    return f"{prefix} · {detail.strip()}" if prefix else detail.strip()


def matching_communication_total(items: list[dict], subtype: str, item_name: str) -> Decimal:
    normalized_name = " ".join(item_name.split()).casefold()
    return money2(sum(
        (
            Decimal(item["monthly"])
            for item in items
            if item.get("category") == "communication"
            and item.get("subcategory") == subtype
            and " ".join(str(item.get("name") or "").split()).casefold() == normalized_name
        ),
        Decimal("0"),
    ))


async def ask_communication_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    subtype = data.get("pending_km_subcategory") or "other"
    detail = data.get("pending_km_communication_label") or "Расход"
    item_name = communication_item_name(subtype, detail)
    await state.update_data(pending_km_item_name=item_name)
    await state.set_state(SetupStates.km_item_amount)
    texts = {
        "mobile": (
            "Откройте приложение оператора и проверьте фактическое ежемесячное списание.\n\n"
            "Посмотрите подключённые платные услуги: среди них могут оказаться ненужные или "
            "забытые опции. Отключите их перед расчётом."
        ),
        "internet": (
            "Укажите регулярную абонентскую плату. Если телевидение уже входит в тариф, "
            "не добавляйте его второй раз отдельным расходом."
        ),
        "vpn": "Укажите полную стоимость выбранного VPN-сервиса.",
        "subscription": "Укажите полную стоимость необходимой подписки.",
        "tv": "Укажите полную стоимость телевидения или ТВ-сервиса.",
        "other": "Укажите полную стоимость обязательного расхода.",
    }
    await message.answer(
        f"<b>{escape(item_name.upper())}</b>\n\n{texts.get(subtype, texts['other'])}\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


async def continue_communication_detail(message: Message, state: FSMContext, detail: str):
    data = await state.get_data()
    subtype = data.get("pending_km_subcategory") or "other"
    item_name = communication_item_name(subtype, detail)
    existing_total = matching_communication_total(data.get("km_items", []), subtype, item_name)
    await state.update_data(pending_km_communication_label=detail, pending_km_item_name=item_name)
    if existing_total <= 0:
        await ask_communication_amount(message, state)
        return
    await state.set_state(SetupStates.km_menu)
    await message.answer(
        f"<b>{escape(item_name.upper())} УЖЕ ДОБАВЛЕН</b>\n\n"
        f"Сейчас учтено — <b>{rub(existing_total)}</b> в месяц.\n\n"
        "Если это ещё один платёж по той же услуге, добавьте сумму к существующему расходу.\n\n"
        "Если это другой номер, место или сервис, уточните название.",
        reply_markup=keyboard([
            [(f"Добавить к «{detail}»", "kmcommunicationdup:add")],
            [("← Назад", "kmcommunicationdup:back"), ("Уточнить название", "kmcommunicationdup:clarify")],
        ]),
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmcommunicationlabel:"))
async def choose_communication_label(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.rsplit(":", 1)[1]
    if code == "custom":
        await ask_communication_name(callback.message, state)
        return
    detail = COMMUNICATION_DETAIL_LABELS.get(code)
    if detail:
        await continue_communication_detail(callback.message, state, detail)


@router.message(SetupStates.km_communication_name)
async def custom_communication_name(message: Message, state: FSMContext):
    detail = (message.text or "").strip()
    if len(detail) < 2 or parse_decimal(detail) is not None:
        await message.answer("Введите понятное название без персональных данных.")
        return
    await continue_communication_detail(message, state, detail)


@router.callback_query(F.data.startswith("kmcommunicationdup:"))
async def choose_duplicate_communication_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.rsplit(":", 1)[1]
    if action == "add":
        await ask_communication_amount(callback.message, state)
    elif action == "clarify":
        await ask_communication_name(callback.message, state)
    elif action == "back":
        await state.set_state(SetupStates.km_menu)
        subtype = (await state.get_data()).get("pending_km_subcategory")
        if subtype == "mobile":
            await callback.message.answer(
                "<b>КАК НАЗВАТЬ НОМЕР?</b>",
                reply_markup=keyboard([
                    [("Личный", "kmcommunicationlabel:personal"), ("Рабочий", "kmcommunicationlabel:work")],
                    [("Для ребёнка", "kmcommunicationlabel:child"), ("Для родителей", "kmcommunicationlabel:parents")],
                    [("Резервный", "kmcommunicationlabel:reserve"), ("Своё название", "kmcommunicationlabel:custom")],
                    [("← Назад", "kmcommunication:back")],
                ]),
            )
        elif subtype == "internet":
            await callback.message.answer(
                "<b>ГДЕ ПОДКЛЮЧЁН ИНТЕРНЕТ?</b>",
                reply_markup=keyboard([
                    [("Квартира", "kmcommunicationlabel:apartment"), ("Дом", "kmcommunicationlabel:house")],
                    [("Дача", "kmcommunicationlabel:dacha"), ("Офис", "kmcommunicationlabel:office")],
                    [("Студия", "kmcommunicationlabel:studio"), ("Своё название", "kmcommunicationlabel:custom")],
                    [("← Назад", "kmcommunication:back")],
                ]),
            )
        else:
            await ask_communication_name(callback.message, state)


@router.callback_query(F.data == "kmcommunicationinput:back")
async def communication_input_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SetupStates.km_menu)
    await callback.message.answer(
        "<b>СВЯЗЬ И ПОДПИСКИ</b>\n\nВыберите обязательный расход.",
        reply_markup=keyboard([
            [("Мобильная связь", "kmcommunication:mobile"), ("Домашний интернет", "kmcommunication:internet")],
            [("VPN", "kmcommunication:vpn"), ("Подписки", "kmcommunication:subscription")],
            [("ТВ", "kmcommunication:tv"), ("+ Свой расход", "kmcommunication:other")],
            [("← Назад", "km:cancel")],
        ]),
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmeducation:"))
async def choose_education_subcategory(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subtype = callback.data.rsplit(":", 1)[1]
    labels = {
        "lesson": "Занятия",
        "monthly": "Обучение",
        "large": "Обучение — крупный платёж",
        "other": "Другое",
    }
    if subtype not in labels:
        return
    await state.update_data(
        pending_km_category="education",
        pending_km_category_label="Образование",
        pending_km_subcategory=subtype,
    )
    if subtype == "other":
        await state.set_state(SetupStates.km_item_name)
        await callback.message.answer(
            "<b>ДРУГОЙ РАСХОД НА ОБРАЗОВАНИЕ</b>\n\n——————\n"
            "<b>→ Введите название расхода.</b>",
            reply_markup=keyboard([[("Отмена", "km:cancel")]]),
        )
        return
    if subtype == "large":
        data = await state.get_data()
        payment_number = 1 + sum(
            1
            for item in data.get("km_items", [])
            if item.get("category") == "education" and item.get("subcategory") == "large"
        )
        await state.update_data(pending_km_item_name=f"Обучение — платёж {payment_number}")
        await state.set_state(SetupStates.km_item_amount)
        await callback.message.answer(
            "<b>КРУПНЫЙ ПЛАТЁЖ ЗА ОБУЧЕНИЕ</b>\n\n"
            "Укажите не полную стоимость обучения, а сумму, которую должны внести именно вы. "
            "Не учитывайте часть, которую оплачивают родители, работодатель, грант или другое лицо.\n\n"
            "——————\n<b>→ Какую сумму вам нужно внести самостоятельно?</b>",
            reply_markup=keyboard([[("← Назад", "km:cancel")]]),
        )
        return
    await ask_preset_km_amount(callback.message, state, labels[subtype])


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
    is_tax = data.get("pending_km_subcategory") in {"property_tax", "land_tax", "tax"}
    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        f"<b>{escape(name.upper())}</b>\n\n"
        "——————\n"
        "<b>→ Введите сумму.</b>\n"
        + (
            "(Срок уплаты укажем следующим сообщением)"
            if is_tax
            else "(Период укажем следующим сообщением)"
        ),
        reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
    )


@router.message(SetupStates.km_item_amount)
async def km_item_amount(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму.")
        return
    await state.update_data(pending_km_item_amount=str(value))
    data = await state.get_data()
    category = data.get("pending_km_category")
    subtype = data.get("pending_km_subcategory")
    if category == "communication" and subtype in {"mobile", "internet", "vpn", "subscription", "tv", "other"}:
        await state.set_state(SetupStates.km_item_period)
        if subtype in {"mobile", "internet"}:
            rows = [
                [("Каждый месяц", "kmperiod:1"), ("Другой период", "kmperiod:custom")],
                [("← Назад", "kmperiod:back")],
            ]
        else:
            rows = [
                [("Каждый месяц", "kmperiod:1"), ("Раз в 3 месяца", "kmperiod:3")],
                [("Раз в 6 месяцев", "kmperiod:6"), ("Раз в год", "kmperiod:12")],
                [("← Назад", "kmperiod:back"), ("Другой период", "kmperiod:custom")],
            ]
        await message.answer("<b>КАК ЧАСТО ВЫ ОПЛАЧИВАЕТЕ?</b>", reply_markup=keyboard(rows))
        return
    if category == "transport" and subtype == "pass":
        await state.set_state(SetupStates.km_item_period)
        await message.answer(
            "<b>ЧЕРЕЗ СКОЛЬКО ПОТРЕБУЕТСЯ НОВЫЙ ПРОЕЗДНОЙ?</b>\n\n"
            "Укажите, через сколько полных месяцев понадобится вся сумма. Если до покупки "
            "осталось полтора месяца, Аллокатор возьмёт один полный месяц, чтобы деньги были готовы вовремя.",
            reply_markup=keyboard([
                [("Через 1 месяц", "kmperiod:1"), ("Через 3 месяца", "kmperiod:3")],
                [("Через 6 месяцев", "kmperiod:6"), ("Через 12 месяцев", "kmperiod:12")],
                [("← Назад", "kmperiod:back"), ("Другой срок", "kmperiod:custom")],
            ]),
        )
        return
    if category == "education" and subtype == "monthly":
        await save_km_item(message, state, Decimal("1"))
        return
    if category == "education" and subtype == "lesson":
        await state.set_state(SetupStates.km_education_lesson_count)
        await message.answer(
            "<b>СКОЛЬКО ЗАНЯТИЙ ОБЫЧНО БЫВАЕТ?</b>",
            reply_markup=keyboard([
                [("Раз в неделю", "edulessons:1"), ("2 раза в неделю", "edulessons:2")],
                [("3 раза в неделю", "edulessons:3"), ("Свой вариант", "edulessons:custom")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    if category == "education" and subtype == "large":
        await state.set_state(SetupStates.km_education_due_date)
        await message.answer(
            "<b>КОГДА НУЖНО ВНЕСТИ ПЛАТЁЖ?</b>\n\n"
            "Посмотрите точную дату в договоре, личном кабинете студента или уведомлении учебного заведения.",
            reply_markup=keyboard([
                [("Через 1 месяц", "edudue:months:1"), ("Через 3 месяца", "edudue:months:3")],
                [("Через 6 месяцев", "edudue:months:6"), ("Указать дату", "edudue:date")],
                [("← Назад", "km:cancel")],
            ]),
        )
        return
    if data.get("pending_km_subcategory") in {"property_tax", "land_tax", "tax"}:
        await state.set_state(SetupStates.km_tax_due_date)
        current_date = date.today()
        example_year = current_date.year if current_date < date(current_date.year, 12, 1) else current_date.year + 1
        await message.answer(
            f"{setup_progress(data, 5)}\n\n"
            "<b>КОГДА НУЖНО УПЛАТИТЬ НАЛОГ?</b>\n\n"
            f"Сегодня — <b>{current_date.strftime('%d.%m.%Y')}</b>. Бот сам рассчитает, "
            "сколько нужно откладывать каждый месяц до срока платежа.\n\n"
            "——————\n"
            "<b>→ Введите точную или ориентировочную дату.</b>\n"
            f"<b>Например:</b> <code>01.12.{example_year}</code>",
            reply_markup=keyboard([[('Отмена', 'km:cancel')]]),
        )
        return
    await state.set_state(SetupStates.km_item_period)
    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>ЗА КАКОЙ ПЕРИОД ЭТА СУММА?</b>",
        reply_markup=keyboard([
            [("В неделю", "kmperiod:week"), ("В месяц", "kmperiod:1")],
            [("За 6 месяцев", "kmperiod:6"), ("В год", "kmperiod:12")],
            [("Другой период", "kmperiod:custom"), ("Отмена", "km:cancel")],
        ]),
    )


@router.message(SetupStates.km_tax_due_date)
async def km_tax_due_date(message: Message, state: FSMContext):
    due_date = parse_tax_due_date(message.text)
    current_date = date.today()
    if due_date is None:
        await message.answer("Введите дату в формате <code>ДД.ММ.ГГГГ</code>.")
        return
    if due_date <= current_date:
        await message.answer("Срок уплаты должен быть позже сегодняшней даты. Проверьте дату и введите её ещё раз.")
        return
    months = months_until_due_date(current_date, due_date)
    await state.update_data(pending_km_due_date=due_date.isoformat())
    await save_km_item(message, state, Decimal(months))


@router.callback_query(SetupStates.km_education_lesson_count, F.data.startswith("edulessons:"))
async def education_lesson_count(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.rsplit(":", 1)[1]
    if value == "custom":
        await state.set_state(SetupStates.km_education_custom_count)
        await callback.message.answer(
            "<b>СКОЛЬКО ЗАНЯТИЙ БЫВАЕТ В МЕСЯЦ?</b>\n\n——————\n"
            "<b>→ Введите целое число.</b>",
            reply_markup=keyboard([[("← Назад", "km:cancel")]]),
        )
        return
    lessons_per_week = Decimal(value)
    data = await state.get_data()
    lesson_price = Decimal(data["pending_km_item_amount"])
    annual_amount = lesson_price * lessons_per_week * Decimal("52")
    await state.update_data(pending_km_item_amount=str(annual_amount))
    await save_km_item(callback.message, state, Decimal("12"))


@router.message(SetupStates.km_education_custom_count)
async def education_custom_count(message: Message, state: FSMContext):
    count = parse_decimal(message.text)
    if count is None or count <= 0 or count != count.to_integral_value():
        await message.answer("Введите целое количество занятий больше нуля.")
        return
    data = await state.get_data()
    monthly = Decimal(data["pending_km_item_amount"]) * count
    await state.update_data(pending_km_item_amount=str(monthly))
    await save_km_item(message, state, Decimal("1"))


@router.callback_query(SetupStates.km_education_due_date, F.data.startswith("edudue:"))
async def education_due_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]
    if value == "date":
        await callback.message.answer(
            "——————\n<b>→ Введите дату в формате ДД.ММ.ГГГГ.</b>",
            reply_markup=keyboard([[("← Назад", "km:cancel")]]),
        )
        return
    months = int(value.rsplit(":", 1)[1])
    await prepare_education_large_payment(callback.message, state, add_calendar_months(date.today(), months))


@router.message(SetupStates.km_education_due_date)
async def education_due_date_text(message: Message, state: FSMContext):
    due_date = parse_tax_due_date(message.text)
    if due_date is None or due_date <= date.today():
        await message.answer("Введите будущую дату в формате <code>ДД.ММ.ГГГГ</code>.")
        return
    await prepare_education_large_payment(message, state, due_date)


async def prepare_education_large_payment(message: Message, state: FSMContext, due_date: date):
    data = await state.get_data()
    amount = Decimal(data["pending_km_item_amount"])
    months = months_until_due_date(date.today(), due_date)
    monthly = money2(amount / Decimal(months))
    await state.update_data(
        pending_km_due_date=due_date.isoformat(),
        pending_km_payment_months=str(months),
    )
    await state.set_state(SetupStates.km_education_confirm)
    await message.answer(
        "<b>ПРОВЕРЬТЕ ПЛАТЁЖ</b>\n\n"
        f"Обучение — <b>{rub(amount)}</b>\n"
        f"Оплатить до — <b>{due_date.strftime('%d.%m.%Y')}</b>\n"
        f"Откладывать — <b>{rub(monthly)} в месяц</b>",
        reply_markup=keyboard([
            [("Сохранить", "edupayment:save"), ("Изменить сумму", "edupayment:amount")],
            [("← Назад", "km:cancel"), ("Изменить срок", "edupayment:due")],
        ]),
    )


@router.callback_query(SetupStates.km_education_confirm, F.data.startswith("edupayment:"))
async def education_payment_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    if action == "amount":
        await state.set_state(SetupStates.km_item_amount)
        await callback.message.answer("——————\n<b>→ Введите исправленную сумму.</b>")
        return
    if action == "due":
        await state.set_state(SetupStates.km_education_due_date)
        await callback.message.answer("——————\n<b>→ Введите исправленную дату в формате ДД.ММ.ГГГГ.</b>")
        return
    await state.update_data(pending_km_one_time=True)
    await save_km_item(callback.message, state, Decimal(data["pending_km_payment_months"]))


@router.callback_query(SetupStates.km_item_period, F.data.startswith("kmperiod:"))
async def km_item_period(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]
    if value == "back":
        data = await state.get_data()
        await ask_preset_km_amount(callback.message, state, data["pending_km_item_name"])
        return
    if value == "week":
        await save_km_item(callback.message, state, Decimal("12") / Decimal("52"))
        return
    if value == "custom":
        await state.set_state(SetupStates.km_custom_period)
        data = await state.get_data()
        is_pass = (
            data.get("pending_km_category") == "transport"
            and data.get("pending_km_subcategory") == "pass"
        )
        await callback.message.answer(
            f"{setup_progress(data, 5)}\n\n"
            + (
                "<b>ЧЕРЕЗ СКОЛЬКО МЕСЯЦЕВ ПОТРЕБУЕТСЯ НОВЫЙ ПРОЕЗДНОЙ?</b>\n\n"
                "Введите срок числом. Дробный срок будет округлён вниз до полного месяца, "
                "чтобы необходимая сумма накопилась вовремя.\n\n"
                "<b>Например:</b> 2"
                if is_pass
                else "<b>ЗА СКОЛЬКО МЕСЯЦЕВ?</b>\n\n"
                "Введите число месяцев. Например: <code>2</code> или <code>18</code>."
            ),
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
    data = await state.get_data()
    if (
        data.get("pending_km_category") == "transport"
        and data.get("pending_km_subcategory") == "pass"
    ):
        safe_months = normalize_pass_months(months)
        if safe_months != months:
            await message.answer(
                f"Чтобы сумма была готова вовремя, срок округлён до <b>{safe_months}</b> полн. мес."
            )
        months = safe_months
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
        "subcategory": data.get("pending_km_subcategory"),
    }
    if data.get("pending_km_due_date") and item["subcategory"] in {"property_tax", "land_tax", "tax", "large"}:
        item["due_date"] = data["pending_km_due_date"]
    if data.get("pending_km_one_time"):
        item["one_time"] = True
    items = list(data.get("km_items", []))
    items.append(item)
    await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu)

    index = len(items) - 1
    if item["category"] == "communication" and months > 1:
        await message.answer(
            "<b>ГДЕ УЧИТЫВАТЬ ЭТОТ РАСХОД?</b>\n\n"
            "Оплата происходит не каждый месяц. Обычно такие расходы удобнее заранее "
            "накапливать в Бытовом резерве.",
            reply_markup=keyboard([
                [("Оставить здесь", f"kmstay:{index}"), ("✔️ В Бытовой резерв", f"kmmove:br:{index}")],
                [("← Назад", f"kmrecommend:back:{index}"), ("Изменить период", f"kmedit:period:{index}")],
            ]),
        )
        return

    tax_due_text = ""
    if item.get("due_date"):
        tax_due_text = f"\nСрок уплаты — <b>{date.fromisoformat(item['due_date']).strftime('%d.%m.%Y')}</b>"
    await message.answer(
        f"<b>{escape(km_item_display_name(item))}</b> — {rub(monthly)} / мес.{tax_due_text}",
        reply_markup=keyboard([[('Редактировать', f'kmedit:item:{index}'), ('Удалить', f'kmedit:delete:{index}')]])
    )
    if item.get("one_time") and item["category"] == "education":
        await message.answer(
            "<b>ЕСТЬ ЕЩЁ ОДИН ОБЯЗАТЕЛЬНЫЙ ПЛАТЁЖ ЗА ОБУЧЕНИЕ?</b>\n\n"
            "Например, оплата следующего семестра или дополнительный взнос по договору.",
            reply_markup=keyboard([
                [("Добавить ещё", "edupayment:add"), ("Нет, продолжить", "edupayment:continue")],
                [("← Назад", f"kmedit:item:{index}")],
            ]),
        )
        return
    await show_km_menu(message, state)


@router.callback_query(SetupStates.km_menu, F.data.in_({"edupayment:add", "edupayment:continue"}))
async def education_payment_next(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "edupayment:add":
        data = await state.get_data()
        payment_number = 1 + sum(
            1
            for item in data.get("km_items", [])
            if item.get("category") == "education" and item.get("subcategory") == "large"
        )
        await state.update_data(
            pending_km_category="education",
            pending_km_category_label="Образование",
            pending_km_subcategory="large",
            pending_km_item_name=f"Обучение — платёж {payment_number}",
            pending_km_due_date=None,
            pending_km_one_time=None,
        )
        await state.set_state(SetupStates.km_item_amount)
        await callback.message.answer(
            "<b>КРУПНЫЙ ПЛАТЁЖ ЗА ОБУЧЕНИЕ</b>\n\n"
            "Укажите сумму, которую должны внести именно вы.\n\n"
            "——————\n<b>→ Какую сумму вам нужно внести самостоятельно?</b>",
            reply_markup=keyboard([[("← Назад", "km:cancel")]]),
        )
        return
    await show_km_menu(callback.message, state)


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmstay:"))
async def keep_km_subscription(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Оставлено в Критическом минимуме")
    await remove_setup_button(callback)
    await show_km_menu(callback.message, state)


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmrecommend:back:"))
async def communication_recommendation_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = list(data.get("km_items", []))
    if 0 <= index < len(items):
        items.pop(index)
        await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu)
    await callback.message.answer(
        "<b>СВЯЗЬ И ПОДПИСКИ</b>\n\nВыберите обязательный расход.",
        reply_markup=keyboard([
            [("Мобильная связь", "kmcommunication:mobile"), ("Домашний интернет", "kmcommunication:internet")],
            [("VPN", "kmcommunication:vpn"), ("Подписки", "kmcommunication:subscription")],
            [("ТВ", "kmcommunication:tv"), ("+ Свой расход", "kmcommunication:other")],
            [("← Назад", "km:cancel")],
        ]),
    )


@router.callback_query(SetupStates.km_menu, F.data.startswith("kmmove:br:"))
async def move_km_item_to_household_reserve(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Перенесено в Бытовой резерв")
    await remove_setup_button(callback)
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    km_items = list(data.get("km_items", []))
    if not 0 <= index < len(km_items):
        await show_km_menu(callback.message, state)
        return

    item = dict(km_items.pop(index))
    item["category"] = "subscriptions"
    item["category_label"] = "Подписки"
    deferred = list(data.get("deferred_br_items", []))
    deferred.append(item)
    await state.update_data(km_items=km_items, deferred_br_items=deferred)
    await show_km_menu(callback.message, state)


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
    caption = (
        f"{setup_progress(data, 5)}\n\n"
        "<b>КРИТИЧЕСКИЙ МИНИМУМ РАССЧИТАН</b>\n\n"
        + "\n".join(lines)
        + f"\n\nПо категориям — <b>{rub(exact)}</b>\n"
        + f"Критический минимум — <b>{rub(rounded)}</b>\n\n"
        "Сумма округлена вверх до ближайшей 1 000 ₽. Можно исправить расходы или увеличить итоговый минимум, если хотите дополнительный запас."
    )
    reply_markup = keyboard([
        [('Продолжить', 'kmfinal:continue')],
        [('Редактировать расходы', 'kmedit:list')],
        [('Изменить сумму КМ', 'kmfinal:override')],
    ])
    try:
        await callback.message.answer_photo(
            photo=FSInputFile(CRITICAL_MINIMUM_CALCULATED_IMAGE),
            caption=caption,
            reply_markup=reply_markup,
            message_effect_id=(
                FIRE_EFFECT_ID
                if callback.message.chat.type == "private"
                else None
            ),
        )
    except TelegramBadRequest:
        await callback.message.answer_photo(
            photo=FSInputFile(CRITICAL_MINIMUM_CALCULATED_IMAGE),
            caption=caption,
            reply_markup=reply_markup,
        )


async def clear_pending_km(state: FSMContext):
    await state.update_data(
        pending_km_category=None,
        pending_km_category_label=None,
        pending_km_item_name=None,
        pending_km_item_amount=None,
        pending_km_subcategory=None,
        pending_km_due_date=None,
        pending_km_one_time=None,
        pending_km_payment_months=None,
        pending_km_edit_index=None,
        pending_km_housing_object=None,
        pending_km_housing_expense_label=None,
        pending_km_communication_label=None,
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
    rows = [[(f"{km_item_display_name(item)} — {rub(Decimal(item['monthly']))}", f"kmedit:item:{i}")] for i, item in enumerate(items)]
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
    await remove_setup_button(callback)
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = data.get("km_items", [])
    if not (0 <= index < len(items)):
        await show_km_menu(callback.message, state)
        return
    item = items[index]
    is_tax = item.get("subcategory") in {"property_tax", "land_tax", "tax"}
    has_deadline = is_tax or item.get("subcategory") == "large"
    period_line = (
        f"Срок уплаты — {date.fromisoformat(item['due_date']).strftime('%d.%m.%Y')}\n"
        if item.get("due_date")
        else f"Период — {item['months']} мес.\n"
    )
    await state.update_data(pending_km_edit_index=index)
    await callback.message.answer(
        f"<b>{escape(km_item_display_name(item))}</b>\n\n"
        f"Категория — {escape(item['category_label'])}\n"
        f"Исходная сумма — {rub(Decimal(item['amount']))}\n"
        + period_line
        + f"В расчёте — <b>{rub(Decimal(item['monthly']))} / мес.</b>",
        reply_markup=keyboard([
            [("Изменить название", f"kmedit:name:{index}")],
            [("Изменить сумму", f"kmedit:amount:{index}")],
            [(("Изменить срок платежа" if has_deadline else "Изменить период"), f"kmedit:period:{index}")],
            [("Удалить", f"kmedit:delete:{index}")],
            [("Назад", "kmedit:list")],
        ]),
    )


@router.callback_query(F.data.startswith("kmedit:delete:"))
async def km_delete_item(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Удалено")
    await remove_setup_button(callback)
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
    await callback.answer(); index=int(callback.data.rsplit(":",1)[1]); data=await state.get_data(); items=list(data.get("km_items",[])); await state.update_data(pending_km_edit_index=index)
    if 0 <= index < len(items) and items[index].get("subcategory") in {"property_tax", "land_tax", "tax", "large"}:
        await state.set_state(SetupStates.km_edit_tax_due_date)
        await callback.message.answer("<b>НОВЫЙ СРОК ПЛАТЕЖА</b>\n\n——————\n<b>→ Введите дату в формате ДД.ММ.ГГГГ.</b>", reply_markup=keyboard([[('Отмена','km:cancel')]]))
        return
    await state.set_state(SetupStates.km_edit_period)
    await callback.message.answer("<b>НОВЫЙ ПЕРИОД</b>", reply_markup=keyboard([[('1 месяц','kmeditperiod:1'),('3 месяца','kmeditperiod:3')],[('6 месяцев','kmeditperiod:6'),('12 месяцев','kmeditperiod:12')],[('Другой','kmeditperiod:custom')],[('Отмена','km:cancel')]]))


@router.message(SetupStates.km_edit_tax_due_date)
async def km_edit_tax_due_date_save(message: Message, state: FSMContext):
    due_date = parse_tax_due_date(message.text)
    current_date = date.today()
    if due_date is None or due_date <= current_date:
        await message.answer("Введите будущую дату в формате <code>ДД.ММ.ГГГГ</code>.")
        return
    data=await state.get_data(); index=int(data.get("pending_km_edit_index",-1)); items=list(data.get("km_items",[]))
    if 0 <= index < len(items):
        item=dict(items[index]); months=months_until_due_date(current_date,due_date); item["due_date"]=due_date.isoformat(); item["months"]=str(months); item["monthly"]=str(money2(Decimal(item["amount"])/Decimal(months))); items[index]=item; await state.update_data(km_items=items)
    await state.set_state(SetupStates.km_menu); await show_km_menu(message,state)


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
            [("Изменить", "kmstorage:edit"), ("✔️ Всё устраивает", "kmstorage:accept")],
            [("ℹ️", "kmstorage:help")],
        ]),
    )


@router.callback_query(SetupStates.km_envelopes_menu, F.data == "kmstorage:help")
async def show_km_storage_help(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await callback.message.answer(
        km_storage_help_text(data.get("km_storage_items", [])),
        reply_markup=keyboard([[('← Назад', 'kmstorage:review')]]),
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
            destination = (
                "Налоги"
                if item.get("subcategory") in {"tax", "property_tax", "land_tax"}
                else item.get("envelope_name") or "Отдельно"
            )
        else:
            destination = "Зарплата"
        label = f"{km_storage_item_display_name(item)} сейчас в конверте {destination.upper()}"
        if len(label) > 50:
            label = label[:47] + "…"
        rows.append([(label, f"kmstorage:item:{index}")])

    rows.append([("✔️ Готово", "kmstorage:review")])

    await message.answer(
        f"{setup_progress(data, 5)}\n\n"
        "<b>ИЗМЕНИТЬ КОНВЕРТ</b>\n\n"
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
    is_tax = item.get("subcategory") in {"tax", "property_tax", "land_tax"}
    current = (
        "общий конверт «Налоги»"
        if is_tax
        else f"отдельный конверт «{escape(item.get('envelope_name') or item['item_name'])}»"
        if item.get("storage") == "separate"
        else "счёт «Зарплата»"
    )

    rows = []
    if is_tax:
        rows.append([("Назад", "kmstorage:edit")])
    elif item.get("storage") == "separate":
        rows.append([("Оставить на Зарплате", f"kmstorage:salary:{index}")])
        rows.append([("Изменить название конверта", f"kmstorage:rename:{index}")])
    else:
        rows.append([("Создать отдельный конверт", f"kmstorage:separate:{index}")])
    if not is_tax:
        rows.append([("Назад", "kmstorage:edit")])

    await callback.message.answer(
        f"<b>{escape(km_storage_item_display_name(item).upper())}</b>\n\n"
        f"Среднемесячно — <b>{rub(Decimal(item['monthly']))}</b>\n"
        f"Сейчас: <b>{current}</b>."
        + (
            "\n\nВсе налоговые обязательства хранятся только в общем конверте «Налоги»."
            if is_tax
            else ""
        ),
        reply_markup=keyboard(rows),
    )


@router.callback_query(SetupStates.km_envelopes_menu, F.data.startswith("kmstorage:salary:"))
async def km_storage_to_salary(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    items = list(data.get("km_storage_items", []))
    if 0 <= index < len(items):
        if items[index].get("subcategory") in {"tax", "property_tax", "land_tax"}:
            await callback.message.answer("Все налоговые обязательства хранятся в общем конверте «Налоги».")
            await show_km_storage_edit_menu(callback.message, state)
            return
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
                "housing": "Недвижимость",
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
    data = await state.get_data()
    items = data.get("km_storage_items", [])
    if 0 <= index < len(items) and items[index].get("subcategory") in {"tax", "property_tax", "land_tax"}:
        await callback.message.answer("Все налоговые обязательства хранятся в общем конверте «Налоги».")
        await show_km_storage_edit_menu(callback.message, state)
        return
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
    data = await state.get_data()
    await state.update_data(br_items=list(data.get("deferred_br_items", [])))
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
        [("Подписки", "brcat:subscriptions")],
        [("Вредные привычки", "brcat:habits"), ("Комиссии", "brcat:fees")],
        [("Другое", "brcat:other")],
    ]
    if items:
        rows.append([("Редактировать расходы", "bredit:list")])
    rows.append([("Рассчитать Бытовой резерв", "br:finish")])
    text = (
        f"{setup_progress(data, 6)}\n\n"
        "<b>ПОСЧИТАЕМ ВАШ БЫТОВОЙ РЕЗЕРВ</b>"
        + intro_text
        + summary
    )
    reply_markup = keyboard(rows)

    if intro and HOUSEHOLD_RESERVE_IMAGE.exists() and len(text) <= 1024:
        await message.answer_photo(
            photo=FSInputFile(HOUSEHOLD_RESERVE_IMAGE),
            caption=text,
            reply_markup=reply_markup,
        )
        return

    await message.answer(
        text,
        reply_markup=reply_markup,
    )


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
        "——————\n"
        "<b>→ Введите название расхода.</b>\n"
        "<b>Например:</b> Зимняя обувь или Абонемент.",
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
        "——————\n"
        "<b>→ Введите сумму.</b>\n"
        "(Период укажем следующим сообщением)\n\n"
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
            [("В неделю", "brperiod:week"), ("В месяц", "brperiod:1")],
            [("За 6 месяцев", "brperiod:6"), ("В год", "brperiod:12")],
            [("Другой период", "brperiod:custom"), ("Отмена", "br:cancel")],
        ]),
    )


@router.callback_query(SetupStates.br_item_period, F.data.startswith("brperiod:"))
async def br_item_period(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":", 1)[1]

    if value == "week":
        await save_br_item(callback.message, state, Decimal("12") / Decimal("52"))
        return

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
    caption = (
        f"{setup_progress(data, 6)}\n\n"
        "<b>БЫТОВОЙ РЕЗЕРВ РАССЧИТАН</b>\n\n"
        + "\n".join(lines)
        + f"\n\nПо категориям — <b>{rub(exact)}</b>\n"
        + f"Бытовой резерв — <b>{rub(rounded)}</b>\n"
        + f"Устойчивая жизнь — <b>{rub(sustainable)}</b>\n\n"
        "Сумма округлена вверх до ближайшей 1 000 ₽. Можно исправить расходы или увеличить итоговый резерв."
    )
    reply_markup = keyboard([
        [('Продолжить', 'brfinal:continue')],
        [('Редактировать расходы', 'bredit:list')],
        [('Изменить сумму БР', 'brfinal:override')],
    ])
    try:
        await callback.message.answer_photo(
            photo=FSInputFile(HOUSEHOLD_RESERVE_CALCULATED_IMAGE),
            caption=caption,
            reply_markup=reply_markup,
            message_effect_id=(
                FIRE_EFFECT_ID
                if callback.message.chat.type == "private"
                else None
            ),
        )
    except TelegramBadRequest:
        await callback.message.answer_photo(
            photo=FSInputFile(HOUSEHOLD_RESERVE_CALCULATED_IMAGE),
            caption=caption,
            reply_markup=reply_markup,
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
    rows = [[('Изменить название',f'bredit:name:{index}')],[('Изменить сумму',f'bredit:amount:{index}')],[('Изменить период',f'bredit:period:{index}')]]
    if item.get("category") == "subscriptions":
        rows.append([('Вернуть в Критический минимум', f'brmove:km:{index}')])
    rows.extend([[('Удалить',f'bredit:delete:{index}')],[('Назад','bredit:list')]])
    await callback.message.answer(f"<b>{escape(item['name'])}</b>\n\nКатегория — {escape(item['category_label'])}\nИсходная сумма — {rub(Decimal(item['amount']))}\nПериод — {item['months']} мес.\nВ расчёте — <b>{rub(Decimal(item['monthly']))} / мес.</b>",reply_markup=keyboard(rows))


@router.callback_query(SetupStates.br_menu, F.data.startswith("brmove:km:"))
async def move_br_item_to_critical_minimum(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Возвращено в Критический минимум")
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    br_items = list(data.get("br_items", []))
    if not 0 <= index < len(br_items):
        await show_br_menu(callback.message, state)
        return

    item = dict(br_items.pop(index))
    item["category"] = "communication"
    item["category_label"] = "Связь и подписки"
    km_items = list(data.get("km_items", []))
    km_items.append(item)
    exact = money2(sum((Decimal(entry["monthly"]) for entry in km_items), Decimal("0")))
    storage_items = build_default_km_storage(km_items)
    categories = life_categories_from_storage(storage_items)
    await state.update_data(
        br_items=br_items,
        km_items=km_items,
        critical_life_exact=str(exact),
        critical_life=str(round_up_thousand(exact)),
        km_storage_items=storage_items,
        life_categories={name: str(value) for name, value in categories.items()},
    )
    await show_br_menu(callback.message, state)


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
    await callback.answer()
    data = await state.get_data()
    if data.get("income_rhythm") == "cyclic":
        await state.update_data(contract_obligation_keys=[])
        await show_contract_obligations(callback.message, state)
    else:
        await ask_pillow_policy(callback.message,state)


def contract_obligation_entries(data: dict) -> list[tuple[str, dict]]:
    return (
        [(f"km:{index}", item) for index, item in enumerate(data.get("km_items", []))]
        + [(f"br:{index}", item) for index, item in enumerate(data.get("br_items", []))]
    )


def contract_obligation_amount(item: dict, work_months: Decimal) -> Decimal:
    months = Decimal(str(item.get("months", "1")))
    if months > 1:
        return money2(Decimal(str(item.get("amount", "0"))))
    return money2(Decimal(str(item.get("monthly", "0"))) * work_months)


def contract_obligation_button_text(item: dict) -> str:
    months = Decimal(str(item.get("months", "1")))
    if months > 1:
        amount_text = f"{rub(Decimal(str(item.get('amount', '0'))))} / {months} мес."
    else:
        amount_text = f"{rub(Decimal(str(item.get('monthly', '0'))))} / мес."
    label = f"{item['name']} — {amount_text}"
    return label if len(label) <= 58 else label[:55] + "…"


def build_contract_obligations(
    data: dict,
) -> tuple[dict[str, str], list[str], Decimal]:
    selected = set(data.get("contract_obligation_keys", []))
    work_months = Decimal(str(data.get("income_work_months", "1")))
    obligations: dict[str, str] = {}
    lines: list[str] = []
    total = Decimal("0")

    for item_key, item in contract_obligation_entries(data):
        if item_key not in selected:
            continue
        amount = contract_obligation_amount(item, work_months)
        obligations[item["name"]] = str(
            Decimal(obligations.get(item["name"], "0")) + amount
        )
        total += amount
        months = Decimal(str(item.get("months", "1")))
        if months > 1:
            calculation = f"платёж за {months} мес."
        else:
            calculation = f"{rub(Decimal(str(item.get('monthly', '0'))))} × {work_months} мес."
        lines.append(
            f"• <b>{escape(item['name'])}</b> — {calculation} = <b>{rub(amount)}</b>"
        )

    return obligations, lines, money2(total)


async def show_contract_obligations(message: Message, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("contract_obligation_keys", []))
    rows = []
    for key, item in contract_obligation_entries(data):
        mark = "✔️ " if key in selected else ""
        rows.append([(f"{mark}{contract_obligation_button_text(item)}", f"contractobligation:{key}")])
    rows.append([("✔️ Готово", "contractobligation:done")])
    await state.set_state(SetupStates.contract_obligations_menu)
    await message.answer(
        f"{setup_progress(data, 7)}\n\n"
        "<b>КАКИЕ РАСХОДЫ ПРОДОЛЖАТСЯ ВО ВРЕМЯ РАБОТЫ?</b>\n\n"
        "Выберите конкретные расходы, которые нужно оплачивать во время рабочей части цикла. "
        "Аллокатор зарезервирует деньги на весь рабочий период.\n\n"
        "Например, один номер телефона можно продолжать оплачивать, а другой временно заблокировать.\n\n"
        "Нажмите на расход повторно, чтобы снять выбор.",
        reply_markup=keyboard(rows),
    )


async def show_contract_obligations_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()
    obligations, lines, total = build_contract_obligations(data)
    await state.update_data(contract_obligations=obligations)
    await message.answer(
        f"{setup_progress(data, 7)}\n\n"
        "<b>ОБЯЗАТЕЛЬСТВА НА РАБОЧУЮ ЧАСТЬ</b>\n\n"
        + ("\n".join(lines) if lines else "• Нет расходов, которые продолжатся во время работы")
        + f"\n\nВсего нужно зарезервировать — <b>{rub(total)}</b>",
        reply_markup=keyboard([
            [("Изменить", "contractobligation:edit"), ("✔️ Всё верно", "contractobligation:confirm")],
        ]),
    )


@router.callback_query(SetupStates.contract_obligations_menu, F.data.startswith("contractobligation:"))
async def toggle_contract_obligation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key = callback.data.split(":", 1)[1]
    if key == "done":
        await show_contract_obligations_confirmation(callback.message, state)
        return
    if key == "edit":
        await show_contract_obligations(callback.message, state)
        return
    if key == "confirm":
        await state.update_data(progress_offset=4)
        await ask_pillow_policy(callback.message, state)
        return
    selected = set((await state.get_data()).get("contract_obligation_keys", []))
    if key in selected:
        selected.remove(key)
    else:
        selected.add(key)
    await state.update_data(contract_obligation_keys=sorted(selected))
    await show_contract_obligations(callback.message, state)


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
    rhythm = data.get("income_rhythm", "monthly")
    gap = Decimal(str(data.get("income_gap_months", "1")))
    if rhythm == "cyclic" and gap > 1:
        minimum = 6
        buttons = [[("6 месяцев", "fmmonths:6"), ("9 месяцев", "fmmonths:9")], [("12 месяцев", "fmmonths:12"), ("Свой вариант", "fmmonths:custom")]]
    elif rhythm == "irregular":
        minimum = 4
        buttons = [[("4 месяца", "fmmonths:4"), ("6 месяцев", "fmmonths:6")], [("9 месяцев", "fmmonths:9"), ("12 месяцев", "fmmonths:12")], [("Свой вариант", "fmmonths:custom")]]
    else:
        minimum = 3
        buttons = [[("3 месяца", "fmmonths:3"), ("4 месяца", "fmmonths:4")], [("6 месяцев", "fmmonths:6"), ("Свой вариант", "fmmonths:custom")]]
    await state.update_data(force_majeure_minimum=str(minimum))
    hint = f"Допустимый диапазон для вашего профиля — <b>{minimum}–12 месяцев Критического минимума</b>."

    await message.answer(
        f"{setup_progress(data, 7)}\n\n"
        "<b>РАЗМЕР ФОРС-МАЖОРНОЙ ПОДУШКИ</b>\n\n"
        "Это резерв на случай событий, которые действительно переворачивают жизнь с ног на голову:\n"
        "- потеря жилья\n"
        "- серьёзная болезнь\n"
        "- аварийный переезд\n"
        "- смерть близкого человека и др.\n\n"
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
    data = await state.get_data()
    minimum = Decimal(str(data.get("force_majeure_minimum", "3")))
    numeric = Decimal(value)
    if numeric < minimum or numeric > 12:
        await callback.message.answer(f"Выберите значение от {minimum} до 12 месяцев.")
        return
    await state.update_data(force_majeure_months=value)
    await after_pillow_policy(callback.message, state)


@router.message(SetupStates.force_majeure_months)
async def save_force_months_text(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    data = await state.get_data()
    minimum = Decimal(str(data.get("force_majeure_minimum", "3")))
    if value is None or value < minimum or value > 12:
        await message.answer(f"Введите количество месяцев от {minimum} до 12.")
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
        "Есть 2 метода:\n\n"
        "- <b>Лавина</b> — сначала долг с самой высокой ставкой. Обычно это минимизирует переплату.\n\n"
        "- <b>Снежный ком</b> — сначала самый маленький остаток. С экономической стороны способ "
        "менее выгоден, а с психологической — гасить проще.\n\n"
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

    data = await state.get_data()
    step = 9 if data.get("has_debts") else 8

    if data.get("income_rhythm") == "cyclic":
        await state.set_state(SetupStates.current_intercontract)
        await message.answer(
            f"{setup_progress(data, step)}\n\n"
            "<b>СКОЛЬКО УЖЕ НАКОПЛЕНО В ФОНДЕ ЗАРПЛАТЫ?</b>\n\n"
            "Это деньги на плановую жизнь во время следующего перерыва. Если Фонда пока нет — отправьте <code>0</code>."
        )
        return

    await ask_current_pillow(message, state)


async def ask_current_pillow(message: Message, state: FSMContext):
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


@router.message(SetupStates.current_intercontract)
async def save_current_intercontract(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    await state.update_data(current_intercontract=str(value))
    await ask_current_pillow(message, state)


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

    data = await state.get_data()
    if data.get("profile_type") in {"piecework", "cyclic"}:
        await state.set_state(SetupStates.current_stabilizer)
        step = 9 if data.get("has_debts") else 8
        await message.answer(
            f"{setup_progress(data, step)}\n\n"
            "<b>СКОЛЬКО УЖЕ НАКОПЛЕНО В СТАБИЛИЗАТОРЕ ДОХОДА?</b>\n\n"
            "Подушка и Стабилизатор дохода — разные резервы. Укажите только деньги, "
            "отложенные на случай временного снижения или задержки дохода.\n\n"
            "Если Стабилизатора пока нет — отправьте <code>0</code>."
        )
        return

    await ask_current_life_balance(message, state)


@router.message(SetupStates.current_stabilizer)
async def save_current_stabilizer(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    await state.update_data(current_stabilizer=str(value))
    await ask_current_life_balance(message, state)


async def ask_current_life_balance(message: Message, state: FSMContext):

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
        "<b>Расчёт экономии на процентах</b>\n\n"

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
    planned_taxes = planned_taxes_from_storage(
        data.get("km_storage_items", [])
    )

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

        profile_type=data.get("profile_type", ""),

        income_rhythm=data.get("income_rhythm", "monthly"),
        income_gap_months=Decimal(str(data.get("income_gap_months", "1"))),
        income_work_months=Decimal(str(data.get("income_work_months", "1"))),
        reliable_gap_income=Decimal(str(data.get("reliable_gap_income", "0"))),
        stabilizer_target_months=Decimal(str(data.get("stabilizer_target_months", "1"))),
        contract_obligations={
            name: Decimal(str(amount))
            for name, amount in data.get("contract_obligations", {}).items()
        },

        tax_rate=Decimal(
            data["tax_rate"]
        ),

        taxable_income_types=data.get(
            "taxable_income_types",
            [],
        ),

        income_type_tax_rates={
            name: Decimal(str(rate))
            for name, rate in data.get("income_type_tax_rates", {}).items()
        },

        planned_taxes=planned_taxes,

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
    intercontract_reserve = Decimal("0")
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

    intercontract_reserve = min(
        Decimal(data.get("current_intercontract", "0")),
        settings.intercontract_full_limit,
    )

    # Введённая пользователем Подушка целиком остаётся Подушкой, даже если
    # она уже превышает рекомендуемый ориентир.
    pillow_force = remaining

    if settings.needs_stabilizer:
        pillow_stabilizer = Decimal(data.get("current_stabilizer", "0"))

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

        intercontract_reserve=
            intercontract_reserve,

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
    mode_name = allocator.mode_title(mode)
    mode_progress = "🏆" * mode + "➖" * (allocator.profile_mode_total - mode)

    tax_types = "\n".join(
        f"• {escape(name)} — <b>{rate}%</b>" if rate > 0 else f"• {escape(name)} — без налога"
        for name, rate in settings.income_type_tax_rates.items()
    ) or "• Нет добавленных типов дохода"

    accounts: list[tuple[str, str]] = []
    if any(rate > 0 for rate in settings.income_type_tax_rates.values()) or "Налоги" in settings.life_categories:
        accounts.append(("🏛️", "Налоги"))

    accounts.append(("🛡️", "Подушка"))

    if settings.income_rhythm == "cyclic":
        accounts.append(("🏦", "Фонд Зарплаты"))

    if settings.needs_stabilizer:
        accounts.append(("🛟", "Стабилизатор дохода"))

    for name in settings.life_categories.keys():
        if name == "Налоги":
            continue
        accounts.append(("❤️", name))

    accounts.append(("❤️", "Зарплата"))
    accounts.append(("💚", "Бытовой резерв"))

    accounts_text = "\n".join(
        f"{index}. {icon} <b>{escape(name)}</b>"
        for index, (icon, name) in enumerate(accounts, start=1)
    )

    income_label = "Стабильный доход" if settings.profile_type == "stable" else "Средний доход"
    deficit = settings.total_critical_life - settings.average_income
    deficit_warning = ""
    if deficit > 0:
        deficit_warning = (
            "\n\n⚠️ <b>ДОХОД НИЖЕ ОБЯЗАТЕЛЬНЫХ РАСХОДОВ</b>\n\n"
            f"Средней месячной базе не хватает <b>{rub(deficit)}</b> для "
            "Критического минимума и минимальных платежей по долгам. "
            "Профиль можно сохранить, но финансовая система остаётся дефицитной."
        )
    rhythm_labels = {
        "monthly": "Стабильный",
        "irregular": "Сдельный",
        "cyclic": "Цикличный (контрактный)",
    }
    cycle_text = ""
    if settings.income_rhythm == "cyclic":
        cycle_text = (
            f"\nФинансовый цикл — <b>{settings.income_work_months} / {settings.income_gap_months}</b> "
            f"({settings.income_work_months} мес. работы · {settings.income_gap_months} мес. перерыва)\n"
            f"Стабилизатор — <b>{settings.stabilizer_target_months} мес.</b>\n"
            f"Цель Фонда Зарплаты — <b>{rub(settings.intercontract_full_limit)}</b>\n"
            f"Обязательства на время контракта — <b>{rub(settings.contract_obligations_total)}</b>"
        )

    await state.set_state(SetupStates.confirmation)
    confirmation_text = (
        "<b>ФИНАНСОВЫЙ ПРОФИЛЬ ГОТОВ</b>\n\n"
        f"Профиль — <b>{rhythm_labels.get(settings.income_rhythm)}</b>\n"
        f"{income_label} — <b>{rub(settings.average_income)}</b>\n"
        f"{cycle_text}\n"
        f"Критический минимум — <b>{rub(settings.critical_life)}</b>\n"
        f"Бытовой резерв — <b>{rub(settings.household_reserve)}</b>\n"
        f"Устойчивая жизнь — <b>{rub(settings.household_life)}</b>\n"
        f"Баланс жизни сейчас — <b>{rub(state_object.life_balance)}</b>\n"
        f"\n<b>ТИПЫ ДОХОДОВ</b>\n{tax_types}"
        f"{deficit_warning}\n\n"
        "<b>ПОДГОТОВЬТЕ СЧЕТА И ФИНАНСОВЫЕ КОНВЕРТЫ:</b>\n\n"
        f"{accounts_text}\n\n"
        "<b>СТАРТОВЫЙ РЕЖИМ:</b>\n\n"
        f"{mode_progress}\n\n"
        f"{escape(mode_name)}\n\n"
        "<b>P.S.:</b> Цели и инвестиции появятся тогда, когда ваш финансовый режим "
        "действительно будет готов направлять туда деньги."
    )
    confirmation_markup = keyboard([
        [("Сохранить профиль", "confirm:save")],
        [("Начать заново", "confirm:restart")],
    ])
    image_path = mode_image_path(allocator.profile_id, mode)
    if image_path is not None and len(confirmation_text) <= 1024:
        try:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=confirmation_text,
                reply_markup=confirmation_markup,
                message_effect_id=(FIRE_EFFECT_ID if message.chat.type == "private" else None),
            )
        except TelegramBadRequest:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=confirmation_text,
                reply_markup=confirmation_markup,
            )
    elif image_path is not None:
        # У подписи к фотографии жёсткий лимит Telegram — 1024 символа.
        # Для необычно большого профиля сохраняем и картинку, и полный текст.
        short_caption = (
            "<b>ФИНАНСОВЫЙ ПРОФИЛЬ ГОТОВ</b>\n\n"
            f"{escape(mode_name)}"
        )
        try:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=short_caption,
                message_effect_id=(FIRE_EFFECT_ID if message.chat.type == "private" else None),
            )
        except TelegramBadRequest:
            await message.answer_photo(photo=FSInputFile(image_path), caption=short_caption)
        await message.answer(confirmation_text, reply_markup=confirmation_markup)
    else:
        await message.answer(confirmation_text, reply_markup=confirmation_markup)

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

    # Налоги на имущество, землю и транспорт уже входят в КЖ,
    # но дополнительно сохраняются как понятные накопительные цели.
    if not db.load_tax_obligations(telegram_id):
        tax_labels = {
            "tax": "Транспортный налог",
            "property_tax": "Налог на имущество",
            "land_tax": "Земельный налог",
        }
        for item in data.get("km_storage_items", []):
            subtype = item.get("subcategory")
            if subtype not in tax_labels:
                continue
            months_decimal = Decimal(str(item.get("months", "1")))
            months = max(1, int(months_decimal.to_integral_value(rounding=ROUND_CEILING)))
            db.add_tax_obligation(
                telegram_id=telegram_id,
                tax_type=tax_labels[subtype],
                object_name=item.get("item_name") or tax_labels[subtype],
                target_amount=Decimal(str(item.get("amount", "0"))),
                saved_before=Decimal("0"),
                months=months,
                monthly_amount=Decimal(str(item.get("monthly", "0"))),
                due_date=item.get("due_date"),
            )

    db.deactivate_all_planned_payments(telegram_id)
    for item in data.get("km_storage_items", []):
        if not item.get("one_time") or not item.get("due_date"):
            continue
        db.add_planned_payment(
            telegram_id=telegram_id,
            category=item.get("category_label", "Плановый платёж"),
            envelope_name=item.get("envelope_name") or item.get("category_label", "Плановый платёж"),
            payment_name=item.get("item_name") or "Плановый платёж",
            target_amount=Decimal(str(item.get("target_amount", "0"))),
            monthly_amount=Decimal(str(item.get("monthly", "0"))),
            due_date=item["due_date"],
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
        reply_markup=main_menu_keyboard(callback.from_user.id),
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
