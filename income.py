from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    FSInputFile,
)

from financial_engine import (
    MODE_NAMES,
    MODE_TITLES,
    FinancialAllocator,
    fmt_money,
)

from storage import db
from ui import main_menu_keyboard


router = Router()


NEW_INCOME_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "menu" / "new_income.png"


MODE_IMAGE_PATHS = {2: Path(__file__).resolve().parent / "assets" / "modes" / "mode_2.png", 3: Path(__file__).resolve().parent / "assets" / "modes" / "mode_3.png", 4: Path(__file__).resolve().parent / "assets" / "modes" / "mode_4.png", 5: Path(__file__).resolve().parent / "assets" / "modes" / "mode_5.png", 6: Path(__file__).resolve().parent / "assets" / "modes" / "mode_6.png"}

async def send_mode_unlock_image(message: Message, result) -> None:
    if result.mode_after <= result.mode_before:
        return
    image_path = MODE_IMAGE_PATHS.get(result.mode_after)
    if image_path is None or not image_path.exists():
        return
    mode_label = (
        "МАКСИМАЛЬНЫЙ РЕЖИМ"
        if result.mode_after == 6
        else f"РЕЖИМ {result.mode_after}"
    )
    await message.answer_photo(
        photo=FSInputFile(image_path),
        caption=f"<b>{mode_label}</b>\n{escape(MODE_TITLES[result.mode_after])}",
    )


# ============================================================
# FSM
# ============================================================


class IncomeStates(StatesGroup):

    amount = State()
    income_type = State()
    custom_income_type = State()
    income_date = State()
    confirmation = State()

    # Редактирование налога конкретного поступления
    tax_edit = State()
    tax_custom_percent = State()
    tax_custom_amount = State()


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

    if NEW_INCOME_IMAGE_PATH.exists():
        await message.answer_photo(
            photo=FSInputFile(NEW_INCOME_IMAGE_PATH),
        )

    await message.answer(
        "<b>Введите полную сумму поступления</b>\n\n"
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
        "Халтура",
        "Частник",
        "Подарок",
        "Авито",
        "Подработка",
        "Премия",
        "Фриланс",
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

    tax_override = data.get(
        "tax_override"
    )

    if tax_override is None:

        tax = allocator.calculate_tax(
            amount,
            income_type,
        )

        tax_rule = (
            "по настройкам профиля"
        )

    else:

        tax = Decimal(
            str(tax_override)
        )

        tax_rule = data.get(
            "tax_override_label",
            "изменён вручную",
        )

    after_tax = (
        amount
        - tax
    )

    await state.set_state(
        IncomeStates.confirmation
    )

    await message.answer(
        "<b>ПРОВЕРЬТЕ ПОСТУПЛЕНИЕ</b>\n\n"

        f"{income_date.strftime('%d.%m.%Y')}\n"
        f"{escape(income_type)} — {rub(amount)}\n\n"

        f"🏛 <b>Налог</b> — {fmt_money(tax)}\n"
        f"💰 <b>За вычетом налога</b> — "
        f"{fmt_money(after_tax)}\n\n"

        f"Правило: <i>{escape(tax_rule)}</i>",

        reply_markup=keyboard([
            [
                (
                    "✅ Распределить",
                    "income:confirm",
                )
            ],
            [
                (
                    "🏛️ Редактировать налог",
                    "income:edit_tax",
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
# РЕДАКТИРОВАНИЕ НАЛОГА КОНКРЕТНОГО ПОСТУПЛЕНИЯ
# ============================================================


@router.callback_query(
    IncomeStates.confirmation,
    F.data == "income:edit_tax"
)
async def edit_income_tax(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    amount = Decimal(
        data["income_amount"]
    )

    income_type = data[
        "income_type"
    ]

    allocator = db.load_allocator(
        callback.from_user.id
    )

    automatic_tax = (
        allocator.calculate_tax(
            amount,
            income_type,
        )
    )

    await state.set_state(
        IncomeStates.tax_edit
    )

    await callback.message.answer(
        "🏛️ <b>НАЛОГ ЭТОГО ПОСТУПЛЕНИЯ</b>\n\n"
        f"Сумма поступления: <b>{rub(amount)}</b>\n"
        f"Тип: <b>{escape(income_type)}</b>\n\n"
        f"По настройкам профиля сейчас: "
        f"<b>{rub(automatic_tax)}</b>\n\n"
        "Изменение ниже действует <b>только на это "
        "поступление</b> и не меняет налоговые "
        "настройки профиля.",
        reply_markup=keyboard([
            [
                (
                    "По настройкам",
                    "taxedit:auto",
                ),
                (
                    "Без налога",
                    "taxedit:none",
                ),
            ],
            [
                (
                    "4%",
                    "taxedit:pct:4",
                ),
                (
                    "6%",
                    "taxedit:pct:6",
                ),
                (
                    "13%",
                    "taxedit:pct:13",
                ),
            ],
            [
                (
                    "Ввести свой %",
                    "taxedit:custom_percent",
                )
            ],
            [
                (
                    "Ввести сумму налога",
                    "taxedit:custom_amount",
                )
            ],
            [
                (
                    "⬅️ Назад",
                    "taxedit:back",
                )
            ],
        ]),
    )


@router.callback_query(
    IncomeStates.tax_edit,
    F.data == "taxedit:auto"
)
async def tax_edit_auto(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.update_data(
        tax_override=None,
        tax_override_label="по настройкам профиля",
    )

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    F.data == "taxedit:none"
)
async def tax_edit_none(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.update_data(
        tax_override="0",
        tax_override_label="без налога",
    )

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    F.data.startswith("taxedit:pct:")
)
async def tax_edit_fixed_percent(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    try:
        percent = Decimal(
            callback.data.split(
                ":",
                2,
            )[2]
        )
    except (
        InvalidOperation,
        IndexError,
    ):
        await callback.message.answer(
            "Не удалось определить ставку."
        )
        return

    data = await state.get_data()

    amount = Decimal(
        data["income_amount"]
    )

    tax = (
        amount
        * percent
        / Decimal("100")
    )

    await state.update_data(
        tax_override=str(tax),
        tax_override_label=f"вручную {percent}%",
    )

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    F.data == "taxedit:custom_percent"
)
async def ask_custom_tax_percent(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.set_state(
        IncomeStates.tax_custom_percent
    )

    await callback.message.answer(
        "Введите процент налога для этого "
        "поступления.\n\n"
        "Например: <code>7,5</code>"
    )


@router.message(
    IncomeStates.tax_custom_percent
)
async def save_custom_tax_percent(
    message: Message,
    state: FSMContext,
):

    percent = parse_decimal(
        message.text
    )

    if (
        percent is None
        or percent < 0
        or percent > 100
    ):
        await message.answer(
            "Введите процент от 0 до 100."
        )
        return

    data = await state.get_data()

    amount = Decimal(
        data["income_amount"]
    )

    tax = (
        amount
        * percent
        / Decimal("100")
    )

    await state.update_data(
        tax_override=str(tax),
        tax_override_label=f"вручную {percent}%",
    )

    await show_income_confirmation(
        message,
        state,
        message.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    F.data == "taxedit:custom_amount"
)
async def ask_custom_tax_amount(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.set_state(
        IncomeStates.tax_custom_amount
    )

    await callback.message.answer(
        "Введите точную сумму налога, которую "
        "нужно зарезервировать из этого поступления.\n\n"
        "Например: <code>8450</code>"
    )


@router.message(
    IncomeStates.tax_custom_amount
)
async def save_custom_tax_amount(
    message: Message,
    state: FSMContext,
):

    tax = parse_decimal(
        message.text
    )

    data = await state.get_data()

    amount = Decimal(
        data["income_amount"]
    )

    if (
        tax is None
        or tax < 0
        or tax > amount
    ):
        await message.answer(
            "Введите сумму от 0 ₽ до суммы "
            f"поступления {rub(amount)}."
        )
        return

    await state.update_data(
        tax_override=str(tax),
        tax_override_label="сумма введена вручную",
    )

    await show_income_confirmation(
        message,
        state,
        message.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    F.data == "taxedit:back"
)
async def tax_edit_back(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
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

        tax_override_raw = data.get(
            "tax_override"
        )

        tax_override = (
            None
            if tax_override_raw is None
            else Decimal(
                str(tax_override_raw)
            )
        )

        result = allocator.process_income(
            income=income,
            income_type=income_type,
            income_date=income_date,
            tax_override=tax_override,
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

    await send_mode_unlock_image(
        callback.message,
        result,
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

    # Без знака ₽ — для компактного основного отчёта.
    def money_plain(value) -> str:
        return fmt_money(
            Decimal(str(value))
        )

    lines = [
        f"{income_date.strftime('%d.%m.%Y')}",
        f"{escape(income_type)} — "
        f"{money_plain(result.income)}",
        "",
        "",
    ]

    # ========================================================
    # РАСПРЕДЕЛЕНИЕ
    # ========================================================

    distribution_lines = []

    def add_distribution_line(
        emoji: str,
        name: str,
        amount: Decimal,
    ):

        amount = Decimal(
            str(amount)
        )

        # Обычный пользователь видит только ненулевые строки.
        # Разработчик — все строки.
        if developer_mode or amount > ZERO:

            distribution_lines.append(
                f"{emoji} <b>{escape(name)}</b> — "
                f"{money_plain(amount)}"
            )

    add_distribution_line(
        "🏛️",
        "Налог",
        result.tax,
    )

    add_distribution_line(
        "🛡️",
        "Подушка",
        allocations.get(
            "Подушка",
            ZERO,
        ),
    )

    for name in settings.life_categories.keys():

        add_distribution_line(
            "❤️",
            name,
            allocations.get(
                f"КЖ:{name}",
                ZERO,
            ),
        )

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

    add_distribution_line(
        "💳",
        "Минимальные платежи",
        allocations.get(
            "Мин. платеж",
            ZERO,
        ),
    )

    add_distribution_line(
        "💳",
        "Досрочное погашение",
        allocations.get(
            "Досрочное",
            ZERO,
        ),
    )

    add_distribution_line(
        "💚",
        "Бытовой резерв",
        allocations.get(
            "Бытовой резерв",
            ZERO,
        ),
    )

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

    add_distribution_line(
        "📈",
        "Инвестиции",
        allocations.get(
            "Инвестиции",
            ZERO,
        ),
    )

    # В Telegram блок цитаты.
    lines.append(
        "<b>РАСПРЕДЕЛЕНИЕ</b>"
    )

    lines.append("")

    lines.append(
        "<blockquote>"
        + "\n".join(
            distribution_lines
        )
        + "</blockquote>"
    )

    lines.extend([
        "",
        "",
    ])

    mode = allocator.active_mode()

    if settings.employment_type == "Фрилансер":

        reward_map = {
            1: "🏆➖➖➖➖➖",
            2: "🏆🏆➖➖➖➖",
            3: "🏆🏆🏆➖➖➖",
            4: "🏆🏆🏆🏆➖➖",
            5: "🏆🏆🏆🏆🏆➖",
            6: "🏆🏆🏆🏆🏆🏆",
        }

    else:

        reward_map = {
            1: "🏆➖➖➖",
            2: "🏆🏆➖➖",
            3: "🏆🏆🏆➖",
            6: "🏆🏆🏆🏆",
        }

    reward = reward_map.get(
        mode,
        "",
    )

    next_info = allocator.next_mode_info()

    lines.append(reward)

    if next_info:

        remaining = money_plain(
            next_info["remaining"]
        )

        if settings.employment_type == "Фрилансер":
            reward_text = {
                1: (
                    "Отложи на Подушку еще "
                    f"{remaining} ₽ и защити себя от новых долгов!"
                ),
                2: (
                    "Погаси еще "
                    f"{remaining} ₽ долгов и начни формировать надежную "
                    "форс-мажорную Подушку!"
                ),
                3: (
                    "Отложи на Подушку еще "
                    f"{remaining} ₽ и открой Стабилизатор дохода!"
                ),
                4: (
                    "Отложи на Подушку еще "
                    f"{remaining} ₽ — и откроются инвестиции и цели!"
                ),
                5: (
                    "Отложи на Подушку еще "
                    f"{remaining} ₽ и копи на цели еще быстрее!"
                ),
            }
        else:
            reward_text = {
                1: (
                    "Отложи на Подушку еще "
                    f"{remaining} ₽ и защити себя от новых долгов!"
                ),
                2: (
                    "Погаси еще "
                    f"{remaining} ₽ долгов и начни формировать надежную "
                    "форс-мажорную Подушку!"
                ),
                3: (
                    "Отложи на Подушку еще "
                    f"{remaining} ₽ и копи на цели еще быстрее!"
                ),
            }

        lines.append(
            reward_text.get(
                mode,
                f"До следующего режима осталось {remaining} ₽."
            )
        )

    else:

        lines.append(
            "Философский камень найден."
        )

    lines.extend([
        "",
        "",
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
        "<b>БАЛАНСЫ ПОСЛЕ ОПЕРАЦИИ</b>",
        "",
        f"🔄 <b>Баланс жизни</b> — "
        f"{money_plain(state.life_balance)}",
        f"🛡️ <b>Подушка</b> — "
        f"{money_plain(state.pillow_balance)}",
        f"🆘 <b>До Критического минимума осталось</b> — "
        f"{money_plain(life_remaining)}",
        f"✳️ <b>До Устойчивой жизни осталось</b> — "
        f"{money_plain(sustainable_remaining)}",
    ])

    # ========================================================
    # РЕЖИМ РАЗРАБОТЧИКА
    # ========================================================

    if developer_mode:

        check = result.checks

        lines.extend([
            "",
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
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


# ============================================================
# ДЛИННЫЕ СООБЩЕНИЯ
# ============================================================


async def send_long_message(
    message: Message,
    text: str,
    max_length: int = 3800,
    reply_markup=None,
):

    if len(text) <= max_length:

        await message.answer(
            text,
            reply_markup=reply_markup,
        )

        return

    paragraphs = text.split(
        "\n"
    )

    chunks = []
    current = ""

    for paragraph in paragraphs:

        candidate = (
            current
            + paragraph
            + "\n"
        )

        if len(candidate) > max_length:

            if current:
                chunks.append(
                    current
                )

            current = (
                paragraph
                + "\n"
            )

        else:

            current = candidate

    if current:
        chunks.append(
            current
        )

    for index, chunk in enumerate(
        chunks
    ):

        is_last = (
            index
            == len(chunks) - 1
        )

        await message.answer(
            chunk,
            reply_markup=(
                reply_markup
                if is_last
                else None
            ),
        )
