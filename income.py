from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from financial_engine import (
    MODE_NAMES,
    MODE_TITLES,
    FinancialAllocator,
    fmt_money,
)

from storage import db


router = Router()


# ============================================================
# FSM
# ============================================================


class IncomeStates(StatesGroup):

    amount = State()
    income_type = State()
    custom_income_type = State()
    income_date = State()
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


# ============================================================
# ЧИСЛА
# ============================================================


def parse_decimal(
    text: str,
) -> Decimal | None:

    if not text:
        return None

    value = (
        text.strip()
        .replace("₽", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )

    if "," in value and "." not in value:

        value = value.replace(
            ",",
            ".",
        )

    elif (
        "," in value
        and "." in value
    ):

        value = (
            value
            .replace(".", "")
            .replace(",", ".")
        )

    try:

        return Decimal(
            value
        )

    except (
        InvalidOperation,
        ValueError,
    ):

        return None


def rub(
    value,
) -> str:

    return (
        fmt_money(
            Decimal(str(value))
        )
        + " ₽"
    )


# ============================================================
# ЗАПУСК ДОБАВЛЕНИЯ ДОХОДА
# ============================================================


@router.message(
    Command("income")
)
async def income_command(
    message: Message,
    state: FSMContext,
):

    await start_income(
        message,
        state,
        message.from_user.id,
    )


@router.callback_query(
    F.data == "menu:income"
)
async def income_callback(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await start_income(
        callback.message,
        state,
        callback.from_user.id,
    )


async def start_income(
    message: Message,
    state: FSMContext,
    telegram_id: int,
):

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:

        await message.answer(
            "Сначала нужно настроить финансовый профиль.\n\n"
            "Отправьте /start."
        )

        return

    await state.clear()

    await state.set_state(
        IncomeStates.amount
    )

    await message.answer(
        "💰 <b>НОВОЕ ПОСТУПЛЕНИЕ</b>\n\n"

        "Сколько денег вы получили?\n\n"

        "Введите полную сумму поступления "
        "<b>до удержания налога</b>.\n\n"

        "Примеры:\n"
        "<code>50000</code>\n"
        "<code>125 000</code>\n"
        "<code>47850,50</code>"
    )


# ============================================================
# СУММА
# ============================================================


@router.message(
    IncomeStates.amount
)
async def income_amount(
    message: Message,
    state: FSMContext,
):

    amount = parse_decimal(
        message.text
    )

    if (
        amount is None
        or amount <= 0
    ):

        await message.answer(
            "Не получилось распознать сумму.\n\n"

            "Введите положительное число.\n"
            "Например: <code>75000</code>"
        )

        return

    await state.update_data(
        income_amount=str(amount)
    )

    allocator = db.load_allocator(
        message.from_user.id
    )

    settings = allocator.settings

    await state.set_state(
        IncomeStates.income_type
    )

    # --------------------------------------------------------
    # Собираем удобный список типов.
    # --------------------------------------------------------

    types = []

    for item in (
        settings.taxable_income_types
    ):

        if item not in types:
            types.append(item)

    common = [
        "Зарплата",
        "Фриланс",
        "Подарок",
        "Кэшбэк",
    ]

    for item in common:

        if item not in types:
            types.append(item)

    # Telegram callback_data ограничен,
    # поэтому используем номер типа.
    await state.update_data(
        available_income_types=types
    )

    rows = []

    for index, item in enumerate(
        types
    ):

        rows.append([
            (
                item,
                f"incometype:{index}",
            )
        ])

    rows.append([
        (
            "✏️ Другой тип",
            "incometype:custom",
        )
    ])

    tax_note = ""

    if settings.tax_rate > 0:

        taxable = (
            ", ".join(
                settings.taxable_income_types
            )
            if settings.taxable_income_types
            else "не указаны"
        )

        tax_note = (
            f"\n\n🏛 Ваша ставка налога: "
            f"<b>{settings.tax_rate}%</b>\n"
            f"Облагаемые типы: "
            f"<b>{escape(taxable)}</b>"
        )

    await message.answer(
        "🏷 <b>Что это за доход?</b>\n\n"

        "Тип дохода нужен, в частности, чтобы бот "
        "понимал, следует ли резервировать с этого "
        "поступления налог."
        + tax_note,
        reply_markup=keyboard(
            rows
        ),
    )


# ============================================================
# ТИП ДОХОДА
# ============================================================


@router.callback_query(
    IncomeStates.income_type,
    F.data.startswith("incometype:")
)
async def income_type_callback(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    value = callback.data.split(
        ":",
        1,
    )[1]

    if value == "custom":

        await state.set_state(
            IncomeStates.custom_income_type
        )

        await callback.message.answer(
            "Введите название типа дохода.\n\n"
            "Например:\n"
            "<code>Продажа техники</code>"
        )

        return

    data = await state.get_data()

    types = data[
        "available_income_types"
    ]

    try:

        income_type = types[
            int(value)
        ]

    except (
        ValueError,
        IndexError,
    ):

        await callback.message.answer(
            "Не удалось определить тип дохода. "
            "Попробуйте ещё раз."
        )

        return

    await state.update_data(
        income_type=income_type
    )

    await ask_date(
        callback.message,
        state,
    )


@router.message(
    IncomeStates.custom_income_type
)
async def custom_income_type(
    message: Message,
    state: FSMContext,
):

    value = message.text.strip()

    if len(value) < 2:

        await message.answer(
            "Введите понятное название."
        )

        return

    await state.update_data(
        income_type=value
    )

    await ask_date(
        message,
        state,
    )


# ============================================================
# ДАТА
# ============================================================


async def ask_date(
    message: Message,
    state: FSMContext,
):

    await state.set_state(
        IncomeStates.income_date
    )

    today = date.today()

    await message.answer(
        "📅 <b>Когда поступили деньги?</b>\n\n"

        f"Сегодня: "
        f"<b>{today.strftime('%d.%m.%Y')}</b>\n\n"

        "Можно выбрать сегодня или ввести другую дату "
        "в формате <code>ДД.ММ.ГГГГ</code>.",
        reply_markup=keyboard([
            [
                (
                    "Сегодня",
                    "incomedate:today",
                )
            ]
        ]),
    )


@router.callback_query(
    IncomeStates.income_date,
    F.data == "incomedate:today"
)
async def income_date_today(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.update_data(
        income_date=date.today().isoformat()
    )

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.message(
    IncomeStates.income_date
)
async def income_date_text(
    message: Message,
    state: FSMContext,
):

    try:

        parsed = datetime.strptime(
            message.text.strip(),
            "%d.%m.%Y",
        ).date()

    except ValueError:

        await message.answer(
            "Не удалось распознать дату.\n\n"
            "Используйте формат:\n"
            "<code>11.08.2026</code>"
        )

        return

    if parsed > date.today():

        await message.answer(
            "Дата поступления не может быть "
            "в будущем."
        )

        return

    await state.update_data(
        income_date=parsed.isoformat()
    )

    await show_income_confirmation(
        message,
        state,
        message.from_user.id,
    )


# ============================================================
# ПОДТВЕРЖДЕНИЕ
# ============================================================


async def show_income_confirmation(
    message: Message,
    state: FSMContext,
    telegram_id: int,
):

    data = await state.get_data()

    allocator = db.load_allocator(
        telegram_id
    )

    amount = Decimal(
        data["income_amount"]
    )

    income_type = data[
        "income_type"
    ]

    income_date = date.fromisoformat(
        data["income_date"]
    )

    tax = allocator.calculate_tax(
        amount,
        income_type,
    )

    after_tax = (
        amount
        - tax
    )

    taxable = (
        tax > 0
    )

    tax_text = (
        f"🏛 Налог: <b>{rub(tax)}</b>\n"
        if taxable
        else "🏛 Налог: <b>не удерживается</b>\n"
    )

    await state.set_state(
        IncomeStates.confirmation
    )

    await message.answer(
        "📋 <b>ПРОВЕРЬТЕ ПОСТУПЛЕНИЕ</b>\n\n"

        f"💰 Сумма: "
        f"<b>{rub(amount)}</b>\n"

        f"🏷 Тип: "
        f"<b>{escape(income_type)}</b>\n"

        f"📅 Дата: "
        f"<b>{income_date.strftime('%d.%m.%Y')}</b>\n\n"

        + tax_text +

        f"💵 После налога: "
        f"<b>{rub(after_tax)}</b>\n\n"

        "После подтверждения бот сразу распределит "
        "всю сумму по вашему финансовому алгоритму.",
        reply_markup=keyboard([
            [
                (
                    "✅ Распределить",
                    "income:confirm",
                )
            ],
            [
                (
                    "❌ Отмена",
                    "income:cancel",
                )
            ],
        ]),
    )


# ============================================================
# ОТМЕНА
# ============================================================


@router.callback_query(
    IncomeStates.confirmation,
    F.data == "income:cancel"
)
async def cancel_income(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        "Операция отменена.\n\n"
        "Деньги не распределялись."
    )


# ============================================================
# РАСПРЕДЕЛЕНИЕ
# ============================================================


@router.callback_query(
    IncomeStates.confirmation,
    F.data == "income:confirm"
)
async def confirm_income(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    telegram_id = (
        callback.from_user.id
    )

    data = await state.get_data()

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:

        await state.clear()

        await callback.message.answer(
            "Финансовый профиль не найден."
        )

        return

    income = Decimal(
        data["income_amount"]
    )

    income_type = data[
        "income_type"
    ]

    income_date = date.fromisoformat(
        data["income_date"]
    )

    # ========================================================
    # ЗАПУСК ФИНАНСОВОГО ЯДРА
    # ========================================================

    try:

        result = allocator.process_income(
            income=income,
            income_type=income_type,
            income_date=income_date,
        )

    except Exception as error:

        await callback.message.answer(
            "⚠️ Во время расчёта произошла ошибка.\n\n"

            f"<code>{escape(str(error))}</code>\n\n"

            "Операция не была сохранена."
        )

        return

    # ========================================================
    # СНАЧАЛА ПРОВЕРКА
    # ========================================================

    if not result.checks["ok"]:

        difference = result.checks[
            "difference"
        ]

        await callback.message.answer(
            "❌ <b>РАСПРЕДЕЛЕНИЕ НЕ СОХРАНЕНО</b>\n\n"

            "Контрольная сумма не сошлась.\n\n"

            f"Расхождение: "
            f"<b>{rub(difference)}</b>\n\n"

            "Это защитная остановка: бот не будет "
            "записывать финансовую операцию, пока "
            "математика не сходится."
        )

        return

    # ========================================================
    # СОХРАНЕНИЕ
    # ========================================================

    db.save_allocator(
        telegram_id,
        allocator,
    )

    await state.clear()

    await send_distribution_report(
        callback.message,
        allocator,
        result,
        income_type,
        income_date,
    )


# ============================================================
# ОТЧЁТ
# ============================================================


async def send_distribution_report(
    message: Message,
    allocator: FinancialAllocator,
    result,
    income_type: str,
    income_date: date,
):

    allocations = result.allocations
    settings = allocator.settings
    state = allocator.state

    developer_mode = settings.developer_mode

    ZERO = Decimal("0")

    # ========================================================
    # ДАНО
    # ========================================================

    lines = [
        "<b>ДАНО</b>",
        "",
        f"Поступление: <b>{rub(result.income)}</b>",
        f"Тип: <b>{escape(income_type)}</b>",
        f"Дата: <b>{income_date.strftime('%d.%m.%Y')}</b>",
        "",
        "<b>РАСПРЕДЕЛЕНИЕ</b>",
        "",
    ]

    # ========================================================
    # ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
    #
    # Обычный режим:
    # показываем только суммы > 0.
    #
    # Режим разработчика:
    # показываем вообще всё, включая 0 ₽.
    # ========================================================

    def add_distribution_line(
        emoji: str,
        name: str,
        amount: Decimal,
    ):

        amount = Decimal(str(amount))

        if developer_mode or amount > ZERO:

            lines.append(
                f"{emoji} {escape(name)}: "
                f"<b>{rub(amount)}</b>"
            )

    # ========================================================
    # НАЛОГ
    # ========================================================

    add_distribution_line(
        "🏛️",
        "Налог",
        result.tax,
    )

    # ========================================================
    # ПОДУШКА
    # ========================================================

    add_distribution_line(
        "🛟",
        "Подушка",
        allocations.get(
            "Подушка",
            ZERO,
        ),
    )

    # ========================================================
    # КАТЕГОРИИ КЖ
    # ========================================================

    for name in settings.life_categories.keys():

        add_distribution_line(
            "❤️",
            name,
            allocations.get(
                f"КЖ:{name}",
                ZERO,
            ),
        )

    # Остаточная категория КЖ "Зарплата"

    if (
        "Зарплата"
        not in settings.life_categories
    ):

        add_distribution_line(
            "❤️",
            "Зарплата",
            allocations.get(
                "КЖ:Зарплата",
                ZERO,
            ),
        )

    # ========================================================
    # ДОЛГИ
    # ========================================================

    minimum_payment = allocations.get(
        "Мин. платеж",
        ZERO,
    )

    early_payment = allocations.get(
        "Досрочное",
        ZERO,
    )

    add_distribution_line(
        "💳",
        "Минимальные платежи",
        minimum_payment,
    )

    add_distribution_line(
        "💳",
        "Досрочное погашение",
        early_payment,
    )

    # ========================================================
    # БЫТОВОЙ РЕЗЕРВ
    # ========================================================

    add_distribution_line(
        "💚",
        "Бытовой резерв",
        allocations.get(
            "Бытовой резерв",
            ZERO,
        ),
    )

    # ========================================================
    # ЦЕЛИ
    # ========================================================

    if settings.goals:

        for goal in settings.goals:

            add_distribution_line(
                "⭐️",
                goal.name,
                allocations.get(
                    f"Цели:{goal.name}",
                    ZERO,
                ),
            )

    else:

        add_distribution_line(
            "⭐️",
            "Цели",
            allocations.get(
                "Цели:ЦЕЛИ (всего)",
                ZERO,
            ),
        )

    # ========================================================
    # ИНВЕСТИЦИИ
    # ========================================================

    add_distribution_line(
        "📈",
        "Инвестиции",
        allocations.get(
            "Инвестиции",
            ZERO,
        ),
    )

    # ========================================================
    # ТЕКУЩИЙ РЕЖИМ
    # ========================================================

    mode = allocator.active_mode()

    MODE_EMOJI = {
        1: "🟤",
        2: "🔴",
        3: "🟠",
        4: "🟣",
        5: "🔵",
        6: "🟢",
    }

    mode_emoji = MODE_EMOJI.get(
        mode,
        "",
    )

    lines.extend([
        "",
        "<b>ТЕКУЩИЙ РЕЖИМ</b>",
        "",
        f"{mode_emoji} "
        f"<b>{escape(MODE_TITLES[mode])}</b>",
    ])

    # ========================================================
    # БАЛАНСЫ ПОСЛЕ ОПЕРАЦИИ
    # ========================================================

    life_remaining = max(
        ZERO,
        settings.critical_life
        - state.life_balance,
    )

    sustainable_remaining = max(
        ZERO,
        settings.household_life
        - state.life_balance,
    )

    lines.extend([
        "",
        "<b>БАЛАНСЫ ПОСЛЕ ОПЕРАЦИИ</b>",
        "",
        f"🔄 Баланс жизни: "
        f"<b>{rub(state.life_balance)}</b>",
        f"🛟 Подушка: "
        f"<b>{rub(state.pillow_balance)}</b>",
        f"До КЖ осталось: "
        f"<b>{rub(life_remaining)}</b>",
        f"До УЖ осталось: "
        f"<b>{rub(sustainable_remaining)}</b>",
    ])

    # ========================================================
    # РЕЖИМ РАЗРАБОТЧИКА
    # ========================================================

    if developer_mode:

        check = result.checks

        lines.extend([
            "",
            "<b>РАСЧЁТ — РЕЖИМ РАЗРАБОТЧИКА</b>",
            "",
            f"Контрольная сумма: "
            f"{rub(check['total'])}",
            f"Доход: "
            f"{rub(check['income'])}",
            f"Расхождение: "
            f"{rub(check['difference'])}",
            f"Проверка: "
            f"{'сходится' if check['ok'] else 'НЕ сходится'}",
            "",
            "<b>ШАГИ РАСЧЁТА</b>",
        ])

        for number, step in enumerate(
            result.steps,
            start=1,
        ):

            lines.append(
                f"{number}. "
                f"{escape(str(step))}"
            )

    await send_long_message(
        message,
        "\n".join(lines),
    )

# ============================================================
# ДЛИННЫЕ СООБЩЕНИЯ
# ============================================================


async def send_long_message(
    message: Message,
    text: str,
    max_length: int = 3800,
):

    if len(text) <= max_length:

        await message.answer(
            text
        )

        return

    paragraphs = text.split(
        "\n"
    )

    current = ""

    for paragraph in paragraphs:

        candidate = (
            current
            + paragraph
            + "\n"
        )

        if (
            len(candidate)
            > max_length
        ):

            if current:

                await message.answer(
                    current
                )

            current = (
                paragraph
                + "\n"
            )

        else:

            current = candidate

    if current:

        await message.answer(
            current
        )
