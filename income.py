from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
import re
from secrets import token_hex

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
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
    goal_display_name,
)

from storage import db
from mode_presentation import FIRE_EFFECT_ID, mode_image_path
from ui import main_menu_keyboard, button_text
from taxes import apply_planned_tax_allocation, refresh_planned_tax_targets
from planned_payments import apply_planned_payment_allocation, refresh_planned_payment_targets
from income_deletion import (
    attach_income_rollback_context,
    capture_income_rollback_context,
)
from time_utils import moscow_today


router = Router()


# Telegram can deliver two callback updates before the first one has finished
# writing to SQLite.  Serialise the irreversible part per user and re-check
# the FSM state after acquiring the lock so a repeated tap cannot create a
# second income operation.
_income_commit_locks: dict[int, asyncio.Lock] = {}


class _IncomeChecksumError(RuntimeError):
    def __init__(self, difference: Decimal):
        super().__init__(str(difference))
        self.difference = difference


def _income_commit_lock(telegram_id: int) -> asyncio.Lock:
    return _income_commit_locks.setdefault(telegram_id, asyncio.Lock())


NEW_INCOME_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "menu" / "new_income.png"
INCOME_DISTRIBUTION_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "menu" / "income_distribution.png"
MAX_MONEY_INPUT = Decimal("1000000000000")


async def send_mode_unlock_image(message: Message, allocator: FinancialAllocator, result) -> None:
    if result.mode_after <= result.mode_before:
        return
    image_path = mode_image_path(allocator.profile_id, result.mode_after)
    if image_path is None:
        return
    caption = f"<b>{escape(allocator.mode_title(result.mode_after))}</b>"
    try:
        await message.answer_photo(
            photo=FSInputFile(image_path),
            caption=caption,
            message_effect_id=(FIRE_EFFECT_ID if message.chat.type == "private" else None),
        )
    except TelegramBadRequest:
        await message.answer_photo(photo=FSInputFile(image_path), caption=caption)


# ============================================================
# FSM
# ============================================================


class IncomeStates(StatesGroup):

    amount = State()
    income_type = State()
    custom_income_type = State()
    custom_income_tax_choice = State()
    custom_income_tax_rate = State()
    custom_income_confirm = State()
    income_date = State()
    confirmation = State()
    processing = State()
    strategy_choice = State()

    # Редактирование налога конкретного поступления
    tax_edit = State()
    tax_custom_percent = State()
    tax_custom_amount = State()
    note = State()


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
                    text=button_text(text),
                    callback_data=data,
                )
                for text, data in row
            ]
            for row in rows
        ]
    )


def flow_callback(base: str, flow_id: str | None) -> str:
    """Bind a callback to one concrete income wizard instance."""

    return f"{base}|{flow_id}" if flow_id else base


def callback_base(data: str | None) -> str:
    return str(data or "").split("|", 1)[0]


async def current_flow_id(state: FSMContext) -> str | None:
    return (await state.get_data()).get("income_flow_id")


async def income_navigation(
    state: FSMContext,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    flow_id = await current_flow_id(state)
    rows = []
    if back_callback:
        rows.append([("← Назад", flow_callback(back_callback, flow_id))])
    rows.append([
        ("✗ Отмена", flow_callback("income:cancel", flow_id)),
        ("← Главное меню", "menu:back"),
    ])
    return keyboard(rows)


async def require_current_flow(callback: CallbackQuery, state: FSMContext) -> bool:
    """Reject a button left by an older wizard without changing current state."""

    raw = str(getattr(callback, "data", "") or "")
    # Direct unit calls do not carry callback_data. They still exercise the
    # handler's accounting guard and are not Telegram stale-button events.
    if not raw:
        return True
    current_state = await state.get_state()
    income_states = {item.state for item in IncomeStates.__all_states__}
    if current_state is not None and current_state not in income_states:
        await callback.answer("Этот экран уже неактуален.", show_alert=True)
        await callback.message.answer(
            "Сейчас открыта другая операция. Эта кнопка её не изменила.",
            reply_markup=keyboard([[('← Главное меню', 'menu:back')]]),
        )
        return False
    data = await state.get_data()
    current = str(data.get("income_flow_id") or "")
    supplied = raw.split("|", 1)[1] if "|" in raw else ""
    if (current and supplied == current) or (not current and not supplied):
        return True
    await callback.answer("Этот экран уже неактуален.", show_alert=True)
    await callback.message.answer(
        "Эта кнопка относится к уже закрытой операции.",
        reply_markup=keyboard([[('← Главное меню', 'menu:back')]]),
    )
    return False


# ============================================================
# ЧИСЛА
# ============================================================


def parse_decimal(
    text: str,
    *,
    allow_zero: bool = False,
) -> Decimal | None:
    """Parse a human-entered decimal without accepting machine notation.

    Both ``1,234.56`` and ``1.234,56`` are supported.  Scientific notation,
    non-finite values and fractions smaller than one kopeck are rejected.
    """

    if not text:
        return None
    value = (
        str(text).strip().replace("₽", "").replace("\u00a0", "").replace(" ", "")
    )
    if not value or re.search(r"[eE]", value) or not re.fullmatch(r"\+?\d[\d.,]*", value):
        return None
    if value.startswith("+"):
        value = value[1:]

    def one_separator(raw: str, separator: str) -> str | None:
        parts = raw.split(separator)
        if any(not part.isdigit() for part in parts):
            return None
        if len(parts) == 2:
            whole, tail = parts
            if len(tail) <= 2:
                return f"{whole}.{tail}"
            if len(tail) == 3:
                if whole == "0":
                    return None
                return whole + tail
            return None
        if (
            1 <= len(parts[0]) <= 3
            and all(len(part) == 3 for part in parts[1:])
        ):
            return "".join(parts)
        return None

    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        whole, fraction = value.rsplit(decimal_separator, 1)
        groups = whole.split(grouping_separator)
        if (
            not fraction.isdigit()
            or len(fraction) > 2
            or any(not group.isdigit() for group in groups)
            or (len(groups) > 1 and not 1 <= len(groups[0]) <= 3)
            or (len(groups) > 1 and any(len(group) != 3 for group in groups[1:]))
        ):
            return None
        normalized = "".join(groups) + "." + fraction
    elif "," in value:
        normalized = one_separator(value, ",")
    elif "." in value:
        normalized = one_separator(value, ".")
    else:
        normalized = value
    if normalized is None:
        return None
    try:
        result = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None
    if (
        not result.is_finite()
        or result > MAX_MONEY_INPUT
        or (result == 0 and not allow_zero)
        or (result != 0 and result < Decimal("0.01"))
    ):
        return None
    return result


def rub(
    value,
) -> str:

    return (
        fmt_money(
            Decimal(str(value))
        )
        + " ₽"
    )


def tax_display_line(tax, percent=None) -> str:
    tax = Decimal(str(tax))
    if percent is not None:
        rate = Decimal(str(percent))
        if tax > Decimal("0") and rate > Decimal("0"):
            rate_text = format(rate.normalize(), "f").replace(".", ",")
            return f"🏛️ Налог • {rate_text}% — {fmt_money(tax)}"
    return f"🏛️ Налог — {fmt_money(tax)}"


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

    db.close_initial_distribution(telegram_id)
    await state.clear()
    flow_id = token_hex(5)
    await state.update_data(income_flow_id=flow_id)

    await state.set_state(
        IncomeStates.amount
    )

    prompt = (
        "<b>Введите полную сумму поступления</b>\n\n"
        "Примеры:\n"
        "<code>50000</code>\n"
        "<code>125 000</code>\n"
        "<code>47850,50</code>"
    )

    if NEW_INCOME_IMAGE_PATH.exists():
        await message.answer_photo(
            photo=FSInputFile(NEW_INCOME_IMAGE_PATH),
            caption=prompt,
            reply_markup=keyboard([[ 
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ]]),
        )
    else:
        await message.answer(
            prompt,
            reply_markup=keyboard([[
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ]]),
        )


# ============================================================
# СУММА
# ============================================================


@router.message(
    IncomeStates.amount,
    F.text & ~F.text.startswith("/"),
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
        or amount < Decimal("0.01")
    ):

        await message.answer(
            "Не получилось распознать сумму.\n\n"

            "Введите положительное число.\n"
            "Например: <code>75000</code>",
            reply_markup=await income_navigation(state),
        )

        return

    await state.update_data(
        income_amount=str(amount)
    )

    allocator = db.load_allocator(
        message.from_user.id
    )

    settings = allocator.settings

    await show_income_types(message, state, settings)


async def show_income_types(message: Message, state: FSMContext, settings=None) -> None:
    if settings is None:
        allocator = db.load_allocator(message.from_user.id)
        settings = allocator.settings if allocator is not None else None
    if settings is None:
        await state.clear()
        await message.answer(
            "Финансовый профиль не найден.",
            reply_markup=keyboard([[('← Главное меню', 'menu:back')]]),
        )
        return
    await state.set_state(IncomeStates.income_type)
    types = list(settings.income_type_tax_rates)
    await state.update_data(available_income_types=types)
    flow_id = await current_flow_id(state)
    rows = [
        [
            (
                types[item_index],
                flow_callback(f"incometype:{item_index}", flow_id),
            )
            for item_index in range(index, min(index + 2, len(types)))
        ]
        for index in range(0, len(types), 2)
    ]
    rows.extend([
        [("+ Новый тип", flow_callback("incometype:custom", flow_id))],
        [
            ("← Назад", flow_callback("income:back_amount", flow_id)),
            ("✗ Отмена", flow_callback("income:cancel", flow_id)),
        ],
        [("← Главное меню", "menu:back")],
    ])
    await message.answer(
        "<b>ВЫБЕРИТЕ ТИП ДОХОДА</b>",
        reply_markup=keyboard(rows),
    )


async def show_income_amount_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(IncomeStates.amount)
    flow_id = await current_flow_id(state)
    await message.answer(
        "<b>Введите полную сумму поступления</b>\n\n"
        "Примеры:\n<code>50000</code>\n<code>125 000</code>\n<code>47850,50</code>",
        reply_markup=keyboard([[
            ("✗ Отмена", flow_callback("income:cancel", flow_id)),
            ("← Главное меню", "menu:back"),
        ]]),
    )


@router.callback_query(
    (F.data == "income:back_amount") | F.data.startswith("income:back_amount|"),
)
async def income_back_to_amount(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await show_income_amount_prompt(callback.message, state)


@router.callback_query(
    (F.data == "income:back_types") | F.data.startswith("income:back_types|"),
)
async def income_back_to_types(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await show_income_types(callback.message, state)


@router.callback_query(
    (F.data == "income:back_custom_type") | F.data.startswith("income:back_custom_type|"),
)
async def income_back_to_custom_type(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await state.set_state(IncomeStates.custom_income_type)
    flow_id = await current_flow_id(state)
    await callback.message.answer(
        "Введите название типа дохода.\n\nНапример:\n<code>Продажа техники</code>",
        reply_markup=keyboard([
            [("← Назад", flow_callback("income:back_types", flow_id))],
            [
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.callback_query(
    (F.data == "income:back_tax_choice") | F.data.startswith("income:back_tax_choice|"),
)
async def income_back_to_tax_choice(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    data = await state.get_data()
    await state.set_state(IncomeStates.custom_income_tax_choice)
    flow_id = data.get("income_flow_id")
    await callback.message.answer(
        f"<b>{escape(str(data.get('income_type', '')).upper())}</b>\n\n"
        "Нужно самостоятельно откладывать налог с этого дохода?",
        reply_markup=keyboard([
            [
                ("Есть налог", flow_callback("newincome:tax:yes", flow_id)),
                ("Без налога", flow_callback("newincome:tax:no", flow_id)),
            ],
            [("← Назад", flow_callback("income:back_custom_type", flow_id))],
            [
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.callback_query(
    (F.data == "income:back_custom_confirm")
    | F.data.startswith("income:back_custom_confirm|"),
)
async def income_back_to_custom_confirm(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    data = await state.get_data()
    rate = Decimal(str(data.get("custom_income_type_rate", "0")))
    await save_custom_income_type(callback.message, state, callback.from_user.id, rate)


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
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    value = callback_base(callback.data).split(
        ":",
        1,
    )[1]

    if value == "custom":

        await state.set_state(
            IncomeStates.custom_income_type
        )
        flow_id = await current_flow_id(state)
        await callback.message.answer(
            "Введите название типа дохода.\n\n"
            "Например:\n"
            "<code>Продажа техники</code>",
            reply_markup=keyboard([
                [("← Назад", flow_callback("income:back_types", flow_id))],
                [
                    ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                    ("← Главное меню", "menu:back"),
                ],
            ]),
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
            "Попробуйте ещё раз.",
            reply_markup=await income_navigation(state, "income:back_amount"),
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
    IncomeStates.custom_income_type,
    F.text & ~F.text.startswith("/"),
)
async def custom_income_type(
    message: Message,
    state: FSMContext,
):

    value = message.text.strip()

    if len(value) < 2 or value.startswith("/"):

        await message.answer(
            "Введите понятное название.",
            reply_markup=await income_navigation(state, "income:back_types"),
        )

        return

    allocator = db.load_allocator(message.from_user.id)
    existing = allocator.settings.income_type_tax_rates if allocator is not None else {}
    if value.casefold() in {name.casefold() for name in existing}:
        await message.answer(
            "Такой тип дохода уже существует. Выберите его в списке или введите другое название.",
            reply_markup=await income_navigation(state, "income:back_types"),
        )
        return

    await state.update_data(
        income_type=value
    )

    await state.set_state(IncomeStates.custom_income_tax_choice)
    flow_id = await current_flow_id(state)
    await message.answer(
        f"<b>{escape(value.upper())}</b>\n\n"
        "Нужно самостоятельно откладывать налог с этого дохода?",
        reply_markup=keyboard([
            [
                ("Есть налог", flow_callback("newincome:tax:yes", flow_id)),
                ("Без налога", flow_callback("newincome:tax:no", flow_id)),
            ],
            [("← Назад", flow_callback("income:back_custom_type", flow_id))],
            [
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.callback_query(IncomeStates.custom_income_tax_choice, F.data.startswith("newincome:tax:"))
async def custom_income_tax_choice(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    if callback_base(callback.data).endswith(":yes"):
        await state.set_state(IncomeStates.custom_income_tax_rate)
        flow_id = await current_flow_id(state)
        await callback.message.answer(
            "<b>СТАВКА НАЛОГА</b>\n\n—————\n"
            "<b>→ Введите число без знака %.</b>\n"
            "<b>Например:</b> <code>4</code>",
            reply_markup=keyboard([
                [("← Назад", flow_callback("income:back_tax_choice", flow_id))],
                [
                    ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                    ("← Главное меню", "menu:back"),
                ],
            ]),
        )
        return
    await save_custom_income_type(callback.message, state, callback.from_user.id, Decimal("0"))


@router.message(IncomeStates.custom_income_tax_rate, F.text & ~F.text.startswith("/"))
async def custom_income_tax_rate(message: Message, state: FSMContext):
    rate = parse_decimal(message.text)
    if rate is None or rate <= 0 or rate > 100:
        await message.answer(
            "Введите ставку больше 0 и не больше 100.",
            reply_markup=await income_navigation(state, "income:back_tax_choice"),
        )
        return
    await save_custom_income_type(message, state, message.from_user.id, rate)


async def save_custom_income_type(message: Message, state: FSMContext, telegram_id: int, rate: Decimal):
    data = await state.get_data()
    name = data["income_type"]
    await state.update_data(custom_income_type_rate=str(rate))
    await state.set_state(IncomeStates.custom_income_confirm)
    flow_id = await current_flow_id(state)
    await message.answer(
        "<b>ПРОВЕРЬТЕ ТИП ДОХОДА</b>\n\n"
        f"Название — <b>{escape(name)}</b>\n"
        + (f"Налог — <b>{rate}%</b>" if rate > 0 else "Налог — <b>не резервируется</b>"),
        reply_markup=keyboard([
            [
                ("✎ Исправить", flow_callback("newincome:fix", flow_id)),
                ("✓ Сохранить", flow_callback("newincome:save", flow_id)),
            ],
            [
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.callback_query(
    IncomeStates.custom_income_confirm,
    (F.data == "newincome:fix") | F.data.startswith("newincome:fix|"),
)
async def fix_custom_income_type(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await state.set_state(IncomeStates.custom_income_type)
    flow_id = await current_flow_id(state)
    await callback.message.answer(
        "Введите исправленное название типа дохода.",
        reply_markup=keyboard([
            [("← Назад", flow_callback("income:back_custom_confirm", flow_id))],
            [
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.callback_query(
    IncomeStates.custom_income_confirm,
    (F.data == "newincome:save") | F.data.startswith("newincome:save|"),
)
async def commit_custom_income_type(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    data = await state.get_data()
    name = data["income_type"]
    rate = Decimal(data["custom_income_type_rate"])
    telegram_id = callback.from_user.id
    allocator = db.load_allocator(telegram_id)
    if allocator is not None:
        allocator.settings.income_type_tax_rates[name] = rate
        allocator.settings.ensure_income_type_id(name)
        allocator.settings.taxable_income_types = [
            item for item, item_rate in allocator.settings.income_type_tax_rates.items() if item_rate > 0
        ]
        db.save_allocator(telegram_id, allocator)
    await callback.message.answer(
        f"Тип дохода <b>{escape(name)}</b> сохранён"
        + (f" со ставкой <b>{rate}%</b>." if rate > 0 else " без налога.")
    )
    await ask_date(callback.message, state)


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

    today = moscow_today()
    flow_id = await current_flow_id(state)

    await message.answer(
        "📅 <b>ДАТА ПОСТУПЛЕНИЯ</b>\n\n"

        f"Сегодня: "
        f"<b>{today.strftime('%d.%m.%Y')}</b>\n\n"

        "——————\n"
        "→ Введите дату в формате <code>ДД.ММ.ГГГГ</code>\n"
        "или нажмите <b>Сегодня</b>.",
        reply_markup=keyboard([
            [("Сегодня", flow_callback("incomedate:today", flow_id))],
            [("← Назад", flow_callback("income:back_types", flow_id))],
            [
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.callback_query(
    IncomeStates.income_date,
    (F.data == "incomedate:today") | F.data.startswith("incomedate:today|")
)
async def income_date_today(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    await state.update_data(
        income_date=moscow_today().isoformat()
    )

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.message(
    IncomeStates.income_date,
    F.text & ~F.text.startswith("/"),
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
            f"<code>{moscow_today().strftime('%d.%m.%Y')}</code>",
            reply_markup=keyboard([
                [("← Назад", flow_callback("income:back_types", await current_flow_id(state)))],
                [
                    ("✗ Отмена", flow_callback("income:cancel", await current_flow_id(state))),
                    ("← Главное меню", "menu:back"),
                ],
            ]),
        )

        return

    if parsed > moscow_today():

        await message.answer(
            "Дата поступления не может быть "
            "в будущем.",
            reply_markup=keyboard([
                [("← Назад", flow_callback("income:back_types", await current_flow_id(state)))],
                [
                    ("✗ Отмена", flow_callback("income:cancel", await current_flow_id(state))),
                    ("← Главное меню", "menu:back"),
                ],
            ]),
        )

        return

    allocator = db.load_allocator(message.from_user.id)
    period_started_at = (
        allocator.state.period_started_at
        if allocator is not None and allocator.state.period_status == "active"
        else None
    )
    if period_started_at:
        try:
            period_start = datetime.fromisoformat(period_started_at).date()
        except ValueError:
            period_start = None
        if period_start is not None and parsed < period_start:
            await message.answer(
                "Эта дата относится к уже закрытому расчётному периоду.\n\n"
                f"Введите дату не раньше <b>{period_start.strftime('%d.%m.%Y')}</b>.",
                reply_markup=keyboard([
                    [("← Назад", flow_callback("income:back_types", await current_flow_id(state)))],
                    [
                        ("✗ Отмена", flow_callback("income:cancel", await current_flow_id(state))),
                        ("← Главное меню", "menu:back"),
                    ],
                ]),
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
        tax_percent = allocator.settings.income_type_tax_rates.get(income_type, Decimal("0"))

    else:

        tax = Decimal(
            str(tax_override)
        )

        tax_rule = data.get(
            "tax_override_label",
            "изменён вручную",
        )
        tax_percent = data.get("tax_override_percent")

    after_tax = (
        amount
        - tax
    )

    await state.set_state(
        IncomeStates.confirmation
    )
    flow_id = data.get("income_flow_id")

    await message.answer(
        "<b>ПРОВЕРЬТЕ ПОСТУПЛЕНИЕ</b>\n\n"

        f"{income_date.strftime('%d.%m.%Y')}\n"
        f"{escape(income_type)} — {rub(amount)}\n"
        + (
            "————————————\n"
            f"📝 {escape(str(data['income_note']))}\n"
            if data.get("income_note")
            else ""
        )
        +
        "————————————\n"
        f"{tax_display_line(tax, tax_percent)}\n"
        "————————————\n"
        f"К распределению — {fmt_money(after_tax)}",

        reply_markup=keyboard([
            [
                (
                    "✗ Отмена",
                    flow_callback("income:cancel", flow_id),
                ),
                (
                    "✓ Распределить",
                    flow_callback("income:confirm", flow_id),
                ),
            ],
            [
                ("✎ Налог", flow_callback("income:edit_tax", flow_id)),
                (
                    "✎ Заметка" if data.get("income_note") else "+ Заметка",
                    flow_callback("income:note", flow_id),
                ),
            ],
        ]),
    )


# ============================================================
# ЗАМЕТКА К ПОСТУПЛЕНИЮ
# ============================================================


@router.callback_query(
    IncomeStates.confirmation,
    (F.data == "income:note") | F.data.startswith("income:note|"),
)
async def ask_income_note(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Ask for a short private marker before the income is committed."""
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await state.set_state(IncomeStates.note)

    await callback.message.answer(
        "<b>ЗАМЕТКА К ПОСТУПЛЕНИЮ</b>\n\n"
        "Напишите короткую пометку, по которой вы потом узнаете "
        "это поступление. Например: <i>Урок с Машей</i>.\n\n"
        "Не более 60 символов.",
        reply_markup=keyboard([
            [
                ("← Назад", flow_callback("income:note_back", await current_flow_id(state))),
                ("✗ Отмена", flow_callback("income:cancel", await current_flow_id(state))),
            ],
            [("← Главное меню", "menu:back")],
        ]),
    )


@router.message(IncomeStates.note, F.text & ~F.text.startswith("/"))
async def save_income_note(
    message: Message,
    state: FSMContext,
):
    note = " ".join((message.text or "").split())
    if not note or note.startswith("/"):
        await message.answer(
            "Введите короткую заметку или нажмите «← Назад».",
            reply_markup=await income_navigation(state, "income:note_back"),
        )
        return
    if len(note) > 60:
        await message.answer(
            "Заметка должна быть не длиннее 60 символов.",
            reply_markup=await income_navigation(state, "income:note_back"),
        )
        return

    await state.update_data(income_note=note)
    await show_income_confirmation(message, state, message.from_user.id)


@router.callback_query(
    IncomeStates.note,
    (F.data == "income:note_back") | F.data.startswith("income:note_back|"),
)
async def income_note_back(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await show_income_confirmation(callback.message, state, callback.from_user.id)


# ============================================================
# РЕДАКТИРОВАНИЕ НАЛОГА КОНКРЕТНОГО ПОСТУПЛЕНИЯ
# ============================================================


@router.callback_query(
    IncomeStates.confirmation,
    (F.data == "income:edit_tax") | F.data.startswith("income:edit_tax|")
)
async def edit_income_tax(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not await require_current_flow(callback, state):
        return
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
    shown_tax = Decimal(str(data["tax_override"])) if data.get("tax_override") is not None else automatic_tax
    shown_percent = data.get("tax_override_percent")
    if shown_percent is None and data.get("tax_override") is None:
        shown_percent = allocator.settings.income_type_tax_rates.get(income_type, Decimal("0"))

    await state.set_state(
        IncomeStates.tax_edit
    )
    flow_id = data.get("income_flow_id")

    await callback.message.answer(
        "🏛️ <b>НАЛОГ ЭТОГО ПОСТУПЛЕНИЯ</b>\n\n"
        f"{escape(income_type)} — {rub(amount)}\n"
        "————————————\n"
        f"{tax_display_line(shown_tax, shown_percent)}\n"
        "————————————\n"
        "Изменение ниже действует <b>только на это "
        "поступление</b> и не меняет налоговые "
        "настройки профиля.",
        reply_markup=keyboard([
            [("3%", flow_callback("taxedit:pct:3", flow_id)), ("4%", flow_callback("taxedit:pct:4", flow_id))],
            [("6%", flow_callback("taxedit:pct:6", flow_id)), ("15%", flow_callback("taxedit:pct:15", flow_id))],
            [
                (
                    "Ввести свой %",
                    flow_callback("taxedit:custom_percent", flow_id),
                )
            ],
            [
                (
                    "Ввести сумму налога",
                    flow_callback("taxedit:custom_amount", flow_id),
                )
            ],
            [
                (
                    "← Назад",
                    flow_callback("taxedit:back", flow_id),
                ),
                ("✗ Отмена", flow_callback("income:cancel", flow_id)),
            ],
            [("← Главное меню", "menu:back")],
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
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    await state.update_data(
        tax_override=None,
        tax_override_label="по настройкам профиля",
        tax_override_percent=None,
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
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    await state.update_data(
        tax_override="0",
        tax_override_label="НДФЛ платит работодатель — налог не резервируется",
        tax_override_percent="0",
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
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    try:
        percent = Decimal(
            callback_base(callback.data).split(
                ":",
                2,
            )[2]
        )
    except (
        InvalidOperation,
        IndexError,
    ):
        await callback.message.answer(
            "Не удалось определить ставку.",
            reply_markup=await income_navigation(state, "taxedit:back"),
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
        tax_override_percent=str(percent),
        tax_override_label=(
            "самозанятость от физлиц — 4%"
            if percent == Decimal("4")
            else "самозанятость от юрлиц и ИП — 6%"
            if percent == Decimal("6")
            else f"вручную {percent}%"
        ),
    )

    await show_income_confirmation(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    (F.data == "taxedit:custom_percent") | F.data.startswith("taxedit:custom_percent|")
)
async def ask_custom_tax_percent(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    await state.set_state(
        IncomeStates.tax_custom_percent
    )

    await callback.message.answer(
        "Введите процент налога для этого "
        "поступления.\n\n"
        "Например: <code>7,5</code>",
        reply_markup=keyboard([
            [("← Назад", flow_callback("taxedit:back", await current_flow_id(state)))],
            [
                ("✗ Отмена", flow_callback("income:cancel", await current_flow_id(state))),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.message(
    IncomeStates.tax_custom_percent,
    F.text & ~F.text.startswith("/"),
)
async def save_custom_tax_percent(
    message: Message,
    state: FSMContext,
):

    percent = parse_decimal(message.text, allow_zero=True)

    if (
        percent is None
        or percent < 0
        or percent > 100
    ):
        await message.answer(
            "Введите процент от 0 до 100.",
            reply_markup=await income_navigation(state, "taxedit:back"),
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
        tax_override_percent=str(percent),
    )

    await show_income_confirmation(
        message,
        state,
        message.from_user.id,
    )


@router.callback_query(
    IncomeStates.tax_edit,
    (F.data == "taxedit:custom_amount") | F.data.startswith("taxedit:custom_amount|")
)
async def ask_custom_tax_amount(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    await state.set_state(
        IncomeStates.tax_custom_amount
    )

    await callback.message.answer(
        "Введите точную сумму налога, которую "
        "нужно зарезервировать из этого поступления.\n\n"
        "Например: <code>8450</code>",
        reply_markup=keyboard([
            [("← Назад", flow_callback("taxedit:back", await current_flow_id(state)))],
            [
                ("✗ Отмена", flow_callback("income:cancel", await current_flow_id(state))),
                ("← Главное меню", "menu:back"),
            ],
        ]),
    )


@router.message(
    IncomeStates.tax_custom_amount,
    F.text & ~F.text.startswith("/"),
)
async def save_custom_tax_amount(
    message: Message,
    state: FSMContext,
):

    tax = parse_decimal(message.text, allow_zero=True)

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
            f"поступления {rub(amount)}.",
            reply_markup=await income_navigation(state, "taxedit:back"),
        )
        return

    await state.update_data(
        tax_override=str(tax),
        tax_override_label="сумма введена вручную",
        tax_override_percent=None,
    )

    await show_income_confirmation(
        message,
        state,
        message.from_user.id,
    )


@router.callback_query(
    StateFilter(
        IncomeStates.tax_edit,
        IncomeStates.tax_custom_percent,
        IncomeStates.tax_custom_amount,
    ),
    (F.data == "taxedit:back") | F.data.startswith("taxedit:back|")
)
async def tax_edit_back(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_current_flow(callback, state):
        return
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
    (F.data == "income:cancel") | F.data.startswith("income:cancel|")
)
async def cancel_income(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    await state.clear()

    await callback.message.answer(
        "Операция отменена.\n\n"
        "Деньги не распределялись.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# РАСПРЕДЕЛЕНИЕ
# ============================================================


@router.callback_query(
    IncomeStates.confirmation,
    (F.data == "income:confirm") | F.data.startswith("income:confirm|")
)
async def confirm_income(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    lock = _income_commit_lock(telegram_id)
    if lock.locked():
        await callback.message.answer(
            "Поступление уже обрабатывается. Подождите несколько секунд.",
            reply_markup=keyboard([
                [("← Главное меню", "menu:back")],
            ]),
        )
        return

    async with lock:
        # Both callback updates may have passed Aiogram's state filter before
        # the first handler cleared the state.  Re-reading it inside the lock
        # makes the database write idempotent.
        current_state = await state.get_state()
        if current_state != IncomeStates.confirmation.state:
            await callback.message.answer(
                "Это поступление уже обработано или отменено.",
                reply_markup=keyboard([
                    [("← Главное меню", "menu:back")],
                ]),
            )
            return

        await state.set_state(IncomeStates.processing)
        try:
            await _confirm_income_locked(callback, state)
        finally:
            # Validation and database errors keep the prepared operation so
            # the user may retry instead of being trapped in a dead state.
            if await state.get_state() == IncomeStates.processing.state:
                await state.set_state(IncomeStates.confirmation)


async def _confirm_income_locked(
    callback: CallbackQuery,
    state: FSMContext,
):

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
            "Финансовый профиль не найден.",
            reply_markup=keyboard([[('← Главное меню', 'menu:back')]]),
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

    if income_date > moscow_today():
        await state.set_state(IncomeStates.income_date)
        await callback.message.answer(
            "Дата поступления не может быть в будущем.",
            reply_markup=await income_navigation(state, "income:back_types"),
        )
        return
    if allocator.state.period_status == "active" and allocator.state.period_started_at:
        try:
            period_start = date.fromisoformat(
                str(allocator.state.period_started_at).split("T", 1)[0]
            )
        except ValueError:
            await callback.message.answer(
                "Не удалось проверить текущий расчётный период. "
                "Операция не сохранена.",
                reply_markup=await income_navigation(state),
            )
            return
        if income_date < period_start:
            await state.set_state(IncomeStates.income_date)
            await callback.message.answer(
                "Пока вы заполняли поступление, начался новый расчётный период.\n\n"
                f"Введите дату не раньше <b>{period_start.strftime('%d.%m.%Y')}</b>.",
                reply_markup=await income_navigation(state, "income:back_types"),
            )
            return

    tax_override_raw = data.get("tax_override")
    tax_override = (
        None if tax_override_raw is None else Decimal(str(tax_override_raw))
    )

    chosen_strategy = data.get("income_strategy")
    if not allocator.settings.goals:
        chosen_strategy = "protection"
    if chosen_strategy not in {"balanced", "protection"}:
        try:
            variants = {}
            for strategy in ("balanced", "protection"):
                simulated = deepcopy(allocator)
                simulated.settings.protective_stage_c_strategy = strategy
                variants[strategy] = simulated.process_income(
                    income=income,
                    income_type=income_type,
                    income_date=income_date,
                    tax_override=tax_override,
                )
        except Exception as error:
            await callback.message.answer(
                "⚠️ Не удалось подготовить варианты распределения.\n\n"
                f"<code>{escape(str(error))}</code>",
                reply_markup=await income_navigation(state),
            )
            return

        balanced = variants["balanced"]
        protection = variants["protection"]
        if balanced.allocations != protection.allocations:
            def choice_totals(result):
                allocations = result.allocations
                protection_total = sum((
                    allocations.get("Подушка", Decimal("0")),
                    allocations.get("Фонд Зарплаты", Decimal("0")),
                    allocations.get("Стабилизатор дохода", Decimal("0")),
                ), Decimal("0"))
                goals_total = sum(
                    (value for key, value in allocations.items() if key.startswith("Цели:")),
                    Decimal("0"),
                )
                return protection_total, goals_total

            balanced_protection, balanced_goals = choice_totals(balanced)
            full_protection, full_goals = choice_totals(protection)
            protection_labels = {
                "Подушка": protection.allocations.get("Подушка", Decimal("0")),
                "Фонд Зарплаты": protection.allocations.get("Фонд Зарплаты", Decimal("0")),
                "Стабилизатор": protection.allocations.get("Стабилизатор дохода", Decimal("0")),
            }
            destination = max(protection_labels, key=protection_labels.get)
            await state.set_state(IncomeStates.strategy_choice)
            await callback.message.answer(
                "<b>КАК РАСПРЕДЕЛИТЬ СВОБОДНУЮ ЧАСТЬ?</b>\n\n"
                "Оба варианта учитывают текущую жизнь и обязательные платежи. "
                "Разница — в распределении свободной части.\n\n"
                "<b>Пополнять защиту и свои планы</b>\n"
                f"• защита — примерно <b>{rub(balanced_protection)}</b>\n"
                f"• Цели и Сундуки — примерно <b>{rub(balanced_goals)}</b>\n\n"
                f"<b>Быстрее наполнить «{escape(destination)}»</b>\n"
                f"• защита — примерно <b>{rub(full_protection)}</b>\n"
                f"• Цели и Сундуки — <b>{rub(full_goals)}</b>\n\n"
                "Это предварительный расчёт. Деньги ещё не распределены.",
                reply_markup=keyboard([
                    [
                        ("Часть — в цели", flow_callback("income:strategy:balanced", data.get("income_flow_id"))),
                        ("Без части на цели", flow_callback("income:strategy:protection", data.get("income_flow_id"))),
                    ],
                    [
                        ("← Назад", flow_callback("income:strategy:back", data.get("income_flow_id"))),
                        ("✗ Отмена", flow_callback("income:cancel", data.get("income_flow_id"))),
                    ],
                    [("← Главное меню", "menu:back")],
                ]),
            )
            return
        chosen_strategy = "balanced"

    # ========================================================
    # ЗАПУСК ФИНАНСОВОГО ЯДРА
    # ========================================================

    result = None
    try:
        # Dynamic payment targets, the allocator state and the ledger row form
        # one accounting operation. Any exception rolls the whole unit back.
        with db.transaction():
            refresh_planned_payment_targets(telegram_id, allocator, income_date)
            refresh_planned_tax_targets(telegram_id, allocator, income_date)
            rollback_context = capture_income_rollback_context(
                db, telegram_id, allocator,
            )
            original_strategy = allocator.settings.protective_stage_c_strategy
            allocator.settings.protective_stage_c_strategy = chosen_strategy
            try:
                result = allocator.process_income(
                    income=income,
                    income_type=income_type,
                    income_date=income_date,
                    tax_override=tax_override,
                    note=data.get("income_note"),
                )
            finally:
                # The choice belongs to this income only.
                allocator.settings.protective_stage_c_strategy = original_strategy
            if not result.checks["ok"]:
                raise _IncomeChecksumError(result.checks["difference"])
            apply_planned_tax_allocation(
                telegram_id,
                allocator,
                result.allocations.get("КЖ:Налоги", Decimal("0")),
            )
            for envelope_name in {
                item["envelope_name"]
                for item in db.load_planned_payments(telegram_id)
            }:
                apply_planned_payment_allocation(
                    telegram_id,
                    allocator,
                    envelope_name,
                    result.allocations.get(f"КЖ:{envelope_name}", Decimal("0")),
                )
            attach_income_rollback_context(
                db,
                telegram_id,
                allocator,
                rollback_context,
            )
            db.save_allocator(
                telegram_id,
                allocator,
            )
    except _IncomeChecksumError as error:
        await callback.message.answer(
            "❌ <b>РАСПРЕДЕЛЕНИЕ НЕ СОХРАНЕНО</b>\n\n"
            "Контрольная сумма не сошлась.\n\n"
            f"Расхождение: <b>{rub(error.difference)}</b>\n\n"
            "Это защитная остановка: бот не будет записывать финансовую "
            "операцию, пока математика не сходится.",
            reply_markup=await income_navigation(state),
        )
        return
    except Exception as error:
        await callback.message.answer(
            "<b>РАСПРЕДЕЛЕНИЕ НЕ СОХРАНЕНО</b>\n\n"
            "Во время сохранения произошла ошибка. Бот не изменил балансы.\n\n"
            "Попробуйте ещё раз или обратитесь в поддержку.\n\n"
            f"<code>{escape(str(error))}</code>",
            reply_markup=keyboard([
                [
                    ("✗ Отмена", flow_callback("income:cancel", data.get("income_flow_id"))),
                    ("✓ Повторить", flow_callback("income:confirm", data.get("income_flow_id"))),
                ],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return

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
        allocator,
        result,
    )


@router.callback_query(
    IncomeStates.strategy_choice,
    F.data.startswith("income:strategy:") & ~F.data.startswith("income:strategy:back"),
)
async def choose_income_strategy(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    strategy = callback_base(callback.data).rsplit(":", 1)[1]
    if strategy not in {"balanced", "protection"}:
        return
    await state.update_data(income_strategy=strategy)
    await state.set_state(IncomeStates.confirmation)
    await confirm_income(callback, state)


@router.callback_query(
    IncomeStates.strategy_choice,
    (F.data == "income:strategy:back") | F.data.startswith("income:strategy:back|"),
)
async def income_strategy_back(callback: CallbackQuery, state: FSMContext):
    if not await require_current_flow(callback, state):
        return
    await callback.answer()
    await state.update_data(income_strategy=None)
    await show_income_confirmation(callback.message, state, callback.from_user.id)


@router.message(
    StateFilter(
        IncomeStates.income_type,
        IncomeStates.custom_income_tax_choice,
        IncomeStates.custom_income_confirm,
        IncomeStates.confirmation,
        IncomeStates.tax_edit,
        IncomeStates.strategy_choice,
        IncomeStates.processing,
    ),
    F.text & ~F.text.startswith("/"),
)
async def income_waits_for_button(message: Message, state: FSMContext):
    """Never leave typed text unanswered on a button-only screen."""

    current = await state.get_state()
    flow_id = await current_flow_id(state)
    back_by_state = {
        IncomeStates.income_type.state: "income:back_amount",
        IncomeStates.custom_income_tax_choice.state: "income:back_custom_type",
        IncomeStates.custom_income_confirm.state: "income:back_custom_type",
        IncomeStates.tax_edit.state: "taxedit:back",
        IncomeStates.strategy_choice.state: "income:strategy:back",
    }
    rows = []
    if current in back_by_state:
        rows.append([("← Назад", flow_callback(back_by_state[current], flow_id))])
    rows.append([
        ("✗ Отмена", flow_callback("income:cancel", flow_id)),
        ("← Главное меню", "menu:back"),
    ])
    await message.answer(
        "На этом экране нужно нажать одну из кнопок ниже.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(
    F.data.startswith("income:")
    | F.data.startswith("incometype:")
    | F.data.startswith("newincome:")
    | F.data.startswith("incomedate:")
    | F.data.startswith("taxedit:"),
)
async def stale_income_callback(callback: CallbackQuery, state: FSMContext):
    """Give navigation for old or contextually invalid income buttons."""

    if not await require_current_flow(callback, state):
        return
    await callback.answer("Этот шаг уже пройден.", show_alert=True)
    await callback.message.answer(
        "Эта кнопка относится к предыдущему экрану.",
        reply_markup=keyboard([[('← Главное меню', 'menu:back')]]),
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
    ]

    # ========================================================
    # РАСПРЕДЕЛЕНИЕ
    # ========================================================

    distribution_groups = [[], [], [], [], [], []]

    def add_distribution_line(
        emoji: str,
        name: str,
        amount: Decimal,
        group: int,
    ):

        amount = Decimal(
            str(amount)
        )

        # Обычный пользователь видит только ненулевые строки.
        # Разработчик — все строки.
        if developer_mode or amount > ZERO:

            distribution_groups[group].append(
                f"{emoji} <b>{escape(name)}</b> — "
                f"{money_plain(amount)}"
            )

    # Налог с дохода и плановые налоги физически хранятся на одном
    # банковском счёте. В отчёте это всегда одна строка, независимо от того,
    # какими ветками ядра она была наполнена.
    planned_tax = sum(
        (
            Decimal(str(value))
            for key, value in allocations.items()
            if key in {"КЖ:Налог", "КЖ:Налоги"}
        ),
        ZERO,
    )
    add_distribution_line(
        "🏛️",
        "Налоги",
        Decimal(str(result.tax)) + planned_tax,
        0,
    )

    if allocator.profile_id == "cyclic":
        add_distribution_line("🏦", "Фонд Зарплаты", allocations.get("Фонд Зарплаты", ZERO), 1)
    add_distribution_line("🛡️", "Подушка", allocations.get("Подушка", ZERO), 1)
    if settings.needs_stabilizer:
        add_distribution_line("🛟", "Стабилизатор", allocations.get("Стабилизатор дохода", ZERO), 1)
    add_distribution_line("📈", "Инвестиции", allocations.get("Инвестиции", ZERO), 1)

    add_distribution_line("💳", "Минимальные платежи", allocations.get("Мин. платеж", ZERO), 2)
    add_distribution_line("💳", "Досрочное погашение", allocations.get("Досрочное", ZERO), 2)
    for key, value in allocations.items():
        if key.startswith("Рабочие обязательства:"):
            parts = key.split(":", 2)
            if len(parts) == 3:
                _, envelope, expense = parts
                label = f"{expense} → {envelope}"
            else:
                label = key.split(":", 1)[1]
            add_distribution_line("💳", label, value, 2)

    life_names = [
        name for name in settings.life_categories
        if name not in {"Налог", "Налоги", "Зарплата"}
    ]
    for name in sorted(life_names, key=lambda name: Decimal(str(allocations.get(f"КЖ:{name}", ZERO))), reverse=True):

        add_distribution_line(
            "❤️",
            name,
            allocations.get(
                f"КЖ:{name}",
                ZERO,
            ), 3,
        )

    # «Зарплата» — остаток Критического минимума, поэтому всегда последняя.
    add_distribution_line(
        "❤️",
        "Зарплата",
        allocations.get("КЖ:Зарплата", ZERO),
        3,
    )

    household_items = [
        (key[3:], Decimal(str(value)))
        for key, value in allocations.items()
        if key.startswith("БР:")
    ]
    for name, value in sorted(
        household_items, key=lambda item: item[1], reverse=True,
    ):
        add_distribution_line("💚", name, value, 4)
    add_distribution_line(
        "💚", "Бытовой резерв",
        allocations.get("Бытовой резерв", ZERO), 4,
    )

    if settings.active_goals:

        for goal in sorted(settings.active_goals, key=lambda goal: Decimal(str(allocations.get(f"Цели:{goal.name}", ZERO))), reverse=True):

            add_distribution_line(
                "🧳" if goal.is_chest else "⭐️",
                goal_display_name(goal.name, goal.is_chest),
                allocations.get(
                    f"Цели:{goal.name}",
                    ZERO,
                ), 5,
            )

    else:

        add_distribution_line(
            "⭐️",
            "Цели",
            allocations.get(
                "Цели:ЦЕЛИ (всего)",
                ZERO,
            ), 5,
        )


    # В Telegram блок цитаты.
    lines.append("<b>РАСПРЕДЕЛЕНИЕ</b>")

    lines.append(
        "<blockquote>"
        + "\n\n".join("\n".join(group) for group in distribution_groups if group)
        + "</blockquote>"
    )

    lines.extend([
        "",
    ])

    mode = allocator.active_mode()

    reward = "🏆" * mode + "➖" * (allocator.profile_mode_total - mode)

    lines.append(reward)

    lines.extend([
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
        "————————————",
        f"↺ <b>Баланс жизни</b> — {money_plain(state.life_balance)}",
        f"➤ До <b>Критич. минимума</b> — {money_plain(life_remaining)}",
        f"➤ До <b>Устойчив. жизни</b> — {money_plain(sustainable_remaining)}",
        "",
        *([f"🏦 <b>Фонд Зарплаты</b> — {money_plain(state.intercontract_reserve)}"] if settings.income_rhythm == "cyclic" else []),
        f"🛡️ <b>Подушка</b> — {money_plain(state.pillow_balance)}",
        *([f"🛟 <b>Стабилизатор</b> — {money_plain(state.stabilizer_balance)}"] if settings.needs_stabilizer else []),
    ])

    main_sections = ["\n".join(lines).strip()]
    main_sections = [
        section
        for section in main_sections
        if section
    ]

    # ========================================================
    # УРОВЕНЬ РАЗРАБОТЧИКА
    # ========================================================

    if developer_mode:

        check = result.checks

        developer_lines = [
            "<b>РАСЧЁТ — УРОВЕНЬ РАЗРАБОТЧИКА</b>",
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
        ]

        for number, step in enumerate(
            result.steps,
            start=1,
        ):

            developer_lines.append(
                f"{number}. "
                f"{escape(str(step))}"
            )

    menu = main_menu_keyboard(message.from_user.id)

    await send_photo_with_sections(
        message,
        (INCOME_DISTRIBUTION_IMAGE_PATH.with_name("super_income_distribution.png")
         if result.super_stage_allocated > 0 else INCOME_DISTRIBUTION_IMAGE_PATH),
        main_sections,
        reply_markup=(None if developer_mode else menu),
    )

    if developer_mode:
        await send_long_message(
            message,
            "\n".join(developer_lines),
            reply_markup=menu,
        )


# ============================================================
# ДЛИННЫЕ СООБЩЕНИЯ
# ============================================================


async def send_photo_with_sections(
    message: Message,
    image_path: Path,
    sections: list[str],
    reply_markup=None,
):
    caption_sections = []
    remaining_sections = []

    for section in sections:
        candidate = "\n\n".join(
            [*caption_sections, section]
        )

        if not remaining_sections and len(candidate) <= 1024:
            caption_sections.append(section)
        else:
            remaining_sections.append(section)

    caption = "\n\n".join(caption_sections)

    if image_path.exists():
        try:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=caption or None,
                reply_markup=(
                    reply_markup
                    if not remaining_sections
                    else None
                ),
            )
        except TelegramBadRequest:
            # The income is already committed. A media failure must never
            # hide its report or strand the user without navigation.
            await send_long_message(
                message,
                "\n\n".join(sections),
                reply_markup=reply_markup,
            )
            return
    else:
        await send_long_message(
            message,
            "\n\n".join(sections),
            reply_markup=reply_markup,
        )
        return

    if remaining_sections:
        await send_long_message(
            message,
            "\n\n".join(remaining_sections),
            reply_markup=reply_markup,
        )


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
