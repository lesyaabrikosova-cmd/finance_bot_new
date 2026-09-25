from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from financial_engine import VACATION_BUDGET_ITEMS, vacation_budget
from goals_manager import GoalManagerStates
from storage import db
from ui import keyboard


router = Router()


class CalculatorStates(StatesGroup):
    vacation_item = State()
    vacation_review = State()


def parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    value = text.strip().replace("₽", "").replace("%", "").replace(" ", "").replace("\u00a0", "")
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def rub(value) -> str:
    formatted = f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",")
    return (formatted[:-3] if formatted.endswith(",00") else formatted) + " ₽"


async def show_calculators(message: Message) -> None:
    await message.answer(
        "<b>🧮 КАЛЬКУЛЯТОРЫ</b>\n\nЧто хотите рассчитать?",
        reply_markup=keyboard([
            [("Отпуск", "calculator:vacation:start")],
            [("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "menu:calculators")
async def open_calculators(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_calculators(callback.message)


@router.callback_query(F.data.in_({"goalmanage:vacation:start", "calculator:vacation:start"}))
async def start_vacation_calculator(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(CalculatorStates.vacation_item)
    await state.update_data(vacation_index=0, vacation_amounts={})
    await callback.message.answer(
        "🏖️ <b>КАЛЬКУЛЯТОР ОТПУСКА</b>\n\n"
        "Посчитаем отпуск по частям: сколько понадобится всего и сколько нужно "
        "откладывать каждый месяц.\n\n"
        "Если какая-то статья расходов вам не нужна или уже оплачена — отправьте <b>0</b>."
    )
    await ask_vacation_item(callback.message, state)


async def ask_vacation_item(message: Message, state: FSMContext):
    data = await state.get_data()
    index = int(data.get("vacation_index", 0))
    if index >= len(VACATION_BUDGET_ITEMS):
        await show_vacation_review(message, state)
        return
    _, label = VACATION_BUDGET_ITEMS[index]
    progress = "💎" * (index + 1) + "➖" * (len(VACATION_BUDGET_ITEMS) - index - 1)
    await state.set_state(CalculatorStates.vacation_item)
    await message.answer(
        f"{progress}\n<b>{escape(label.upper())}</b>\n\n"
        "——————\n<b>→ Введите предполагаемую сумму.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "menu:calculators")]]),
    )


@router.message(CalculatorStates.vacation_item)
async def save_vacation_item(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 и выше.")
        return
    data = await state.get_data()
    index = int(data.get("vacation_index", 0))
    amounts = dict(data.get("vacation_amounts", {}))
    key, _ = VACATION_BUDGET_ITEMS[index]
    amounts[key] = str(value)
    await state.update_data(vacation_index=index + 1, vacation_amounts=amounts)
    await ask_vacation_item(message, state)


async def show_vacation_review(message: Message, state: FSMContext):
    data = await state.get_data()
    amounts = {
        key: Decimal(value)
        for key, value in data.get("vacation_amounts", {}).items()
    }
    result = vacation_budget(amounts)
    if result["subtotal"] <= 0:
        await message.answer("Бюджет получился нулевым. Введите хотя бы одну сумму больше 0.")
        await state.update_data(vacation_index=0, vacation_amounts={})
        await ask_vacation_item(message, state)
        return
    lines = [
        f"• {label} — <b>{rub(result.get(key, 0))}</b>"
        for key, label in VACATION_BUDGET_ITEMS
        if result.get(key, Decimal("0")) > 0
    ]
    await state.set_state(CalculatorStates.vacation_review)
    await message.answer(
        "🏖️ <b>БЮДЖЕТ ОТПУСКА</b>\n\n"
        + "\n".join(lines)
        + f"\n\nРасходы — <b>{rub(result['subtotal'])}</b>"
        + f"\nЗапас 10% — <b>{rub(result['buffer'])}</b>"
        + f"\nВаша Цель — <b>{rub(result['total'])}</b>\n\n"
        "Дальше можно создать Цель и указать, сколько уже накоплено и к какой дате нужны деньги.",
        reply_markup=keyboard([
            [("✔️ Создать Цель", "calculator:vacation:confirm")],
            [("Посчитать заново", "calculator:vacation:start")],
            [("✖️ Отмена", "menu:calculators")],
        ]),
    )


@router.callback_query(
    CalculatorStates.vacation_review,
    F.data.in_({"goalmanage:vacation:confirm", "calculator:vacation:confirm"}),
)
async def confirm_vacation_goal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    existing = next(
        (goal for goal in allocator.settings.goals if goal.name.casefold() == "отпуск"),
        None,
    )
    if existing is not None:
        await state.clear()
        await callback.message.answer(
            "Расчёт готов, но Цель «Отпуск» уже существует. Можно открыть её и обновить сумму вручную.",
            reply_markup=keyboard([
                [("⭐️ Открыть Цель", f"goalmanage:view:{existing.uid}")],
                [("← К калькуляторам", "menu:calculators")],
            ]),
        )
        return
    data = await state.get_data()
    amounts = {key: Decimal(value) for key, value in data.get("vacation_amounts", {}).items()}
    result = vacation_budget(amounts)
    await state.update_data(goal_draft={
        "name": "Отпуск",
        "position_type": "goal",
        "target_amount": str(result["subtotal"]),
        "buffer_enabled": True,
        "buffer_percent": "10",
    })
    await state.set_state(GoalManagerStates.current)
    await callback.message.answer(
        "<b>СКОЛЬКО УЖЕ НАКОПЛЕНО НА ОТПУСК?</b>\n\n"
        "Если пока ничего нет — отправьте <b>0</b>.\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "menu:calculators")]]),
    )
