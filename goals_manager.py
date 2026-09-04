from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from financial_engine import (
    Goal,
    VACATION_BUDGET_ITEMS,
    goal_percentage_bounds,
    normalize_active_goal_percentages,
    sequential_goal_percentages,
    vacation_budget,
)
from storage import db
from ui import keyboard, main_menu_keyboard


router = Router()


class GoalManagerStates(StatesGroup):
    name = State()
    target = State()
    current = State()
    deadline = State()
    buffer = State()
    percentages = State()
    edit_name = State()
    edit_target = State()
    edit_deadline = State()
    edit_buffer = State()
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


def parse_date(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def rub(value) -> str:
    formatted = f"{Decimal(str(value)):,.2f}"
    return formatted.replace(",", " ").replace(".", ",") + " ₽"


def icon(goal: Goal) -> str:
    return "🪎" if goal.is_chest else "⭐️"


def goal_line(allocator, goal: Goal) -> str:
    state_labels = {
        "paused": " · на паузе",
        "completed": " · выполнена",
        "archived": " · в архиве",
    }
    state_label = state_labels.get(goal.status, "")
    target = ""
    if goal.target_amount is not None:
        current = allocator.state.goal_balances.get(goal.name, goal.balance)
        target = f"\n  {rub(current)} из {rub(goal.full_target_amount)}"
    return (
        f"{icon(goal)} <b>{escape(goal.name)}</b> — {goal.percentage}%{state_label}"
        f"{target}"
    )


async def show_goals_manager(message: Message, telegram_id: int) -> None:
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль через /start.")
        return
    goals = allocator.settings.goals
    visible = [
        (index, goal)
        for index, goal in enumerate(goals)
        if goal.status != "archived"
    ]
    archived_count = sum(goal.status == "archived" for goal in goals)
    if visible:
        listing = "\n\n".join(goal_line(allocator, goal) for _, goal in visible)
    else:
        listing = "<b>Пока список пуст.</b>"
    rows = [
        [(f"{icon(goal)} {goal.name}", f"goalmanage:view:{index}")]
        for index, goal in visible
    ]
    rows.extend([
        [("+ Добавить", "goalmanage:add")],
        [("Калькулятор отпуска", "goalmanage:vacation:start")],
        *([[("Настроить проценты", "goalmanage:percent:start")]] if allocator.settings.active_goals else []),
        *([[(f"Архив · {archived_count}", "goalmanage:archive:list")]] if archived_count else []),
        [("← Главное меню", "menu:back")],
    ])
    await message.answer(
        "<b><u>ЦЕЛИ И СУНДУКИ</u></b>\n\n"
        "⭐️ Цель — конкретная сумма, которую нужно накопить.\n"
        "🪎 Сундук — постоянный запас, который можно пополнять и использовать снова.\n\n"
        f"{listing}\n\n"
        "Проценты показывают, как делятся только деньги, уже выделенные Аллокатором на Цели.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data == "goalmanage:archive:list")
async def show_goals_archive(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    archived = [
        (index, goal)
        for index, goal in enumerate(allocator.settings.goals)
        if goal.status == "archived"
    ]
    if not archived:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    lines = "\n".join(
        f"• {icon(goal)} <b>{escape(goal.name)}</b>"
        for _, goal in archived
    )
    rows = [
        [(f"Вернуть · {goal.name}", f"goalmanage:restore:{index}")]
        for index, goal in archived
    ]
    rows.append([("← Назад", "goals:manage")])
    await callback.message.answer(
        "<b><u>АРХИВ ЦЕЛЕЙ</u></b>\n\n"
        f"{lines}\n\n"
        "Архивные Цели не получают деньги и не участвуют в процентах.",
        reply_markup=keyboard(rows),
    )


@router.callback_query(F.data.in_({"goals:manage", "menu:goals", "settings:goals"}))
async def open_goals_manager(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_goals_manager(callback.message, callback.from_user.id)


@router.callback_query(F.data == "goalmanage:add")
async def choose_position_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if len(allocator.settings.active_goals) >= 10:
        await callback.message.answer(
            "Одновременно можно использовать не больше 10 Целей и Сундуков. "
            "Поставьте ненужную позицию на паузу или удалите её."
        )
        return
    await callback.message.answer(
        "<b>ЧТО ДОБАВИТЬ?</b>\n\n"
        "⭐️ <b>Цель</b> — конкретная сумма: путёвка, автомобиль или парфюм.\n\n"
        "🪎 <b>Сундук</b> — постоянный запас: Подарки, Хотелки или Замена техники.",
        reply_markup=keyboard([
            [("⭐️ Цель", "goalmanage:type:goal"), ("🪎 Сундук", "goalmanage:type:chest")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(F.data == "goalmanage:vacation:start")
async def start_vacation_calculator(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if any(goal.name.casefold() == "отпуск" for goal in allocator.settings.goals):
        await callback.message.answer(
            "Позиция «Отпуск» уже существует. Откройте её, чтобы изменить сумму или срок.",
            reply_markup=keyboard([[("← К целям", "goals:manage")]]),
        )
        return
    if len(allocator.settings.active_goals) >= 10:
        await callback.message.answer("Сначала освободите место среди десяти активных позиций.")
        return
    await state.set_state(GoalManagerStates.vacation_item)
    await state.update_data(vacation_index=0, vacation_amounts={})
    await callback.message.answer(
        "<b>⭐️ КАЛЬКУЛЯТОР ОТПУСКА</b>\n\n"
        "Соберём бюджет по частям. Если статья вам не нужна или уже оплачена — отправьте <b>0</b>."
    )
    await ask_vacation_item(callback.message, state)


async def ask_vacation_item(message: Message, state: FSMContext):
    data = await state.get_data()
    index = int(data.get("vacation_index", 0))
    if index >= len(VACATION_BUDGET_ITEMS):
        await show_vacation_review(message, state)
        return
    _, label = VACATION_BUDGET_ITEMS[index]
    await state.set_state(GoalManagerStates.vacation_item)
    await message.answer(
        f"<b>{escape(label.upper())}</b>\n\n"
        "——————\n<b>→ Введите предполагаемую сумму.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.message(GoalManagerStates.vacation_item)
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
    await state.set_state(GoalManagerStates.vacation_review)
    await message.answer(
        "<b>⭐️ БЮДЖЕТ ОТПУСКА</b>\n\n"
        + "\n".join(lines)
        + f"\n\nРасходы — <b>{rub(result['subtotal'])}</b>"
        + f"\nЗапас 10% — <b>{rub(result['buffer'])}</b>"
        + f"\nВаша Цель — <b>{rub(result['total'])}</b>\n\n"
        "Дальше укажем, сколько уже накоплено и к какой дате нужны деньги.",
        reply_markup=keyboard([
            [("✔️ Создать Цель", "goalmanage:vacation:confirm")],
            [("Посчитать заново", "goalmanage:vacation:start")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(GoalManagerStates.vacation_review, F.data == "goalmanage:vacation:confirm")
async def confirm_vacation_goal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.callback_query(F.data.startswith("goalmanage:type:"))
async def save_position_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    position_type = callback.data.rsplit(":", 1)[1]
    if position_type not in {"goal", "chest"}:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    await state.update_data(goal_draft={"position_type": position_type})
    await state.set_state(GoalManagerStates.name)
    await callback.message.answer(
        "<b>КАК НАЗЫВАЕТСЯ ПОЗИЦИЯ?</b>\n\n"
        "——————\n<b>→ Введите название.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.message(GoalManagerStates.name)
async def save_position_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    allocator = db.load_allocator(message.from_user.id)
    if not name or len(name) > 60:
        await message.answer("Введите название длиной от 1 до 60 символов.")
        return
    if any(goal.name.casefold() == name.casefold() for goal in allocator.settings.goals):
        await message.answer("Позиция с таким названием уже существует. Введите другое.")
        return
    data = await state.get_data()
    draft = dict(data.get("goal_draft") or {})
    draft["name"] = name
    await state.update_data(goal_draft=draft)
    if draft["position_type"] == "chest":
        await persist_new_position(message, state)
        return
    await state.set_state(GoalManagerStates.target)
    await message.answer(
        "<b>СКОЛЬКО НУЖНО НАКОПИТЬ?</b>\n\n"
        "——————\n<b>→ Введите конечную сумму.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.message(GoalManagerStates.target)
async def save_target(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите сумму больше 0.")
        return
    data = await state.get_data()
    draft = dict(data["goal_draft"])
    draft["target_amount"] = str(value)
    await state.update_data(goal_draft=draft)
    await state.set_state(GoalManagerStates.current)
    await message.answer(
        "<b>СКОЛЬКО УЖЕ НАКОПЛЕНО?</b>\n\n"
        "Это стартовая сумма. Постоянно сверять её с банковскими процентами не потребуется.\n\n"
        "Если пока ничего нет — отправьте <b>0</b>.\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.message(GoalManagerStates.current)
async def save_current(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 и выше.")
        return
    data = await state.get_data()
    draft = dict(data["goal_draft"])
    draft["balance"] = str(value)
    await state.update_data(goal_draft=draft)
    await state.set_state(GoalManagerStates.deadline)
    await message.answer(
        "<b>ЕСТЬ ЛИ СРОК?</b>\n\n"
        "Если дата известна, Аллокатор проверит, реалистичен ли план.",
        reply_markup=keyboard([
            [("Указать дату", "goalmanage:deadline:yes"), ("Без срока", "goalmanage:deadline:no")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(GoalManagerStates.deadline, F.data.startswith("goalmanage:deadline:"))
async def choose_deadline(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data.endswith(":no"):
        await ask_buffer(callback.message, state)
        return
    await callback.message.answer(
        "——————\n<b>→ Введите дату в формате ДД.ММ.ГГГГ.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.message(GoalManagerStates.deadline)
async def save_deadline(message: Message, state: FSMContext):
    value = parse_date(message.text)
    if value is None or value <= date.today():
        await message.answer("Введите будущую дату в формате ДД.ММ.ГГГГ.")
        return
    data = await state.get_data()
    draft = dict(data["goal_draft"])
    draft["deadline"] = value.isoformat()
    await state.update_data(goal_draft=draft)
    await ask_buffer(message, state)


async def ask_buffer(message: Message, state: FSMContext):
    await state.set_state(GoalManagerStates.buffer)
    await message.answer(
        "<b>ДОБАВИТЬ ФИНАНСОВЫЙ ЗАПАС?</b>\n\n"
        "Для изменения цен и непредвиденных расходов обычно достаточно 10%.",
        reply_markup=keyboard([
            [("✔️ 10%", "goalmanage:buffer:10"), ("Без запаса", "goalmanage:buffer:0")],
            [("Свой процент", "goalmanage:buffer:custom")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(GoalManagerStates.buffer, F.data.startswith("goalmanage:buffer:"))
async def choose_buffer(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.rsplit(":", 1)[1]
    if value == "custom":
        await callback.message.answer("——————\n<b>→ Введите целое число от 1 до 50.</b>")
        return
    await persist_new_position(callback.message, state, Decimal(value))


@router.message(GoalManagerStates.buffer)
async def save_custom_buffer(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value != value.to_integral_value() or not 1 <= value <= 50:
        await message.answer("Введите целое число от 1 до 50.")
        return
    await persist_new_position(message, state, value)


async def persist_new_position(message: Message, state: FSMContext, buffer: Decimal = Decimal("0")):
    data = await state.get_data()
    draft = dict(data["goal_draft"])
    now = datetime.now(timezone.utc).isoformat()
    goal = Goal(
        name=draft["name"],
        percentage=Decimal("1"),
        balance=Decimal(draft.get("balance", "0")),
        position_type=draft["position_type"],
        target_amount=(Decimal(draft["target_amount"]) if draft.get("target_amount") else None),
        deadline=draft.get("deadline"),
        buffer_enabled=buffer > 0,
        buffer_percent=buffer,
        created_at=now,
        updated_at=now,
    )
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.goals.append(goal)
    allocator.state.goal_balances[goal.name] = goal.balance
    normalize_active_goal_percentages(allocator.settings.goals)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await begin_percentage_setup(message, message.from_user.id, state)


async def begin_percentage_setup(message: Message, telegram_id: int, state: FSMContext):
    allocator = db.load_allocator(telegram_id)
    active = allocator.settings.active_goals
    if not active:
        await show_goals_manager(message, telegram_id)
        return
    if len(active) == 1:
        active[0].percentage = Decimal("100")
        active[0].is_auto_percentage = True
        db.save_allocator(telegram_id, allocator)
        await message.answer("Единственная позиция получает <b>100%</b> денег на Цели.")
        await show_goals_manager(message, telegram_id)
        return
    await state.set_state(GoalManagerStates.percentages)
    await state.update_data(goal_percentages=[])
    await ask_percentage(message, telegram_id, state)


@router.callback_query(F.data == "goalmanage:percent:start")
async def start_percentage_setup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await begin_percentage_setup(callback.message, callback.from_user.id, state)


async def ask_percentage(message: Message, telegram_id: int, state: FSMContext):
    allocator = db.load_allocator(telegram_id)
    active = allocator.settings.active_goals
    data = await state.get_data()
    chosen = [Decimal(value) for value in data.get("goal_percentages", [])]
    index = len(chosen)
    if index >= len(active) - 1:
        values = sequential_goal_percentages(chosen, len(active))
        for goal, value in zip(active, values):
            goal.percentage = value
            goal.is_auto_percentage = False
        active[-1].is_auto_percentage = True
        db.save_allocator(telegram_id, allocator)
        await state.clear()
        await message.answer("✔️ Проценты сохранены. Последняя позиция получила остаток автоматически.")
        await show_goals_manager(message, telegram_id)
        return
    minimum, maximum = goal_percentage_bounds(chosen, len(active) - index - 1)
    current = "\n".join(
        f"• {icon(goal)} {escape(goal.name)}" + (f" — <b>{chosen[i]}%</b>" if i < len(chosen) else "")
        for i, goal in enumerate(active)
    )
    await message.answer(
        "<b>РАСПРЕДЕЛЕНИЕ МЕЖДУ ЦЕЛЯМИ</b>\n\n"
        f"{current}\n\n"
        f"Введите долю для {icon(active[index])} <b>{escape(active[index].name)}</b>: "
        f"целое число от <b>{minimum}</b> до <b>{maximum}</b>.\n\n"
        "Это процент от денег на Цели, а не от всей зарплаты.\n"
        "——————\n<b>→ Введите процент.</b>",
        reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]),
    )


@router.message(GoalManagerStates.percentages)
async def save_percentage(message: Message, state: FSMContext):
    allocator = db.load_allocator(message.from_user.id)
    active = allocator.settings.active_goals
    data = await state.get_data()
    chosen = [Decimal(value) for value in data.get("goal_percentages", [])]
    index = len(chosen)
    minimum, maximum = goal_percentage_bounds(chosen, len(active) - index - 1)
    value = parse_decimal(message.text)
    if value is None or value != value.to_integral_value() or not minimum <= value <= maximum:
        await message.answer(f"Введите целое число от {minimum} до {maximum}.")
        return
    chosen.append(value)
    await state.update_data(goal_percentages=[str(item) for item in chosen])
    await ask_percentage(message, message.from_user.id, state)


@router.callback_query(F.data.startswith("goalmanage:view:"))
async def view_position(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    try:
        index = int(callback.data.rsplit(":", 1)[1])
        goal = allocator.settings.goals[index]
    except (ValueError, IndexError):
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    target = ""
    if goal.is_goal:
        forecast = allocator.goal_forecast(goal)
        if forecast["target"] is None:
            target = "\nКонечная сумма — <b>ещё не задана</b>\n"
        else:
            target = (
                f"\nНакоплено — <b>{rub(forecast['current'])}</b>\n"
                f"Нужно — <b>{rub(forecast['target'])}</b>\n"
                + (f"Срок — <b>{date.fromisoformat(goal.deadline).strftime('%d.%m.%Y')}</b>\n" if goal.deadline else "Срок — <b>не задан</b>\n")
            )
    status = "На паузе" if goal.status == "paused" else "Активна"
    if goal.status == "completed":
        status = "Выполнена"
    elif goal.status == "archived":
        status = "В архиве"
    actions = [
        [("Изменить название", f"goalmanage:edit:name:{index}")],
        *([[("Изменить сумму", f"goalmanage:edit:target:{index}"), ("Изменить срок", f"goalmanage:edit:deadline:{index}")],
           [("Изменить запас", f"goalmanage:edit:buffer:{index}")]] if goal.is_goal and goal.status not in {"completed", "archived"} else []),
    ]
    if goal.status in {"active", "paused"}:
        actions.append([(("Возобновить" if goal.status == "paused" else "Поставить на паузу"), f"goalmanage:toggle:{index}")])
    if goal.is_goal and goal.status in {"active", "paused"}:
        actions.append([("✔️ Отметить выполненной", f"goalmanage:complete:ask:{index}")])
    if goal.status == "completed":
        actions.append([("Перенести в архив", f"goalmanage:archive:{index}")])
    if goal.status == "archived":
        actions.append([("Вернуть из архива", f"goalmanage:restore:{index}")])
    actions.extend([
        [("Удалить", f"goalmanage:delete:ask:{index}")],
        [("← Назад", "goals:manage")],
    ])
    await callback.message.answer(
        f"<b>{icon(goal)} {escape(goal.name.upper())}</b>\n\n"
        f"Доля — <b>{goal.percentage}%</b>\n"
        f"Статус — <b>{status}</b>{target}",
        reply_markup=keyboard(actions),
    )


async def begin_edit(callback: CallbackQuery, state: FSMContext, field: str, state_value: State, prompt: str):
    await callback.answer()
    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    await state.update_data(edit_goal_index=index)
    await state.set_state(state_value)
    await callback.message.answer(prompt, reply_markup=keyboard([[("✖️ Отмена", "goals:manage")]]))


@router.callback_query(F.data.startswith("goalmanage:edit:name:"))
async def edit_name(callback: CallbackQuery, state: FSMContext):
    await begin_edit(callback, state, "name", GoalManagerStates.edit_name, "——————\n<b>→ Введите новое название.</b>")


@router.message(GoalManagerStates.edit_name)
async def save_edited_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    index = int(data["edit_goal_index"])
    if not name or len(name) > 60 or any(i != index and goal.name.casefold() == name.casefold() for i, goal in enumerate(allocator.settings.goals)):
        await message.answer("Введите уникальное название длиной до 60 символов.")
        return
    goal = allocator.settings.goals[index]
    old_name = goal.name
    goal.name = name
    if old_name in allocator.state.goal_balances:
        allocator.state.goal_balances[name] = allocator.state.goal_balances.pop(old_name)
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_goals_manager(message, message.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:edit:target:"))
async def edit_target(callback: CallbackQuery, state: FSMContext):
    await begin_edit(callback, state, "target", GoalManagerStates.edit_target, "——————\n<b>→ Введите новую конечную сумму.</b>")


@router.message(GoalManagerStates.edit_target)
async def save_edited_target(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите сумму больше 0.")
        return
    await save_simple_goal_edit(message, state, "target_amount", value)


@router.callback_query(F.data.startswith("goalmanage:edit:deadline:"))
async def edit_deadline(callback: CallbackQuery, state: FSMContext):
    await begin_edit(callback, state, "deadline", GoalManagerStates.edit_deadline, "Введите новую дату ДД.ММ.ГГГГ или слово <b>нет</b>.")


@router.message(GoalManagerStates.edit_deadline)
async def save_edited_deadline(message: Message, state: FSMContext):
    if (message.text or "").strip().casefold() == "нет":
        await save_simple_goal_edit(message, state, "deadline", None)
        return
    value = parse_date(message.text)
    if value is None or value <= date.today():
        await message.answer("Введите будущую дату ДД.ММ.ГГГГ или слово «нет».")
        return
    await save_simple_goal_edit(message, state, "deadline", value.isoformat())


@router.callback_query(F.data.startswith("goalmanage:edit:buffer:"))
async def edit_buffer(callback: CallbackQuery, state: FSMContext):
    await begin_edit(callback, state, "buffer", GoalManagerStates.edit_buffer, "Введите новый запас от 0 до 50%. Ноль отключит запас.")


@router.message(GoalManagerStates.edit_buffer)
async def save_edited_buffer(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value != value.to_integral_value() or not 0 <= value <= 50:
        await message.answer("Введите целое число от 0 до 50.")
        return
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    goal = allocator.settings.goals[int(data["edit_goal_index"])]
    goal.buffer_enabled = value > 0
    goal.buffer_percent = value
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_goals_manager(message, message.from_user.id)


async def save_simple_goal_edit(message: Message, state: FSMContext, field: str, value):
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    goal = allocator.settings.goals[int(data["edit_goal_index"])]
    setattr(goal, field, value)
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_goals_manager(message, message.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:toggle:"))
async def toggle_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    index = int(callback.data.rsplit(":", 1)[1])
    goal = allocator.settings.goals[index]
    if goal.status not in {"active", "paused"}:
        await callback.message.answer("Эту позицию сначала нужно вернуть из архива.")
        return
    if goal.status == "paused":
        if len(allocator.settings.active_goals) >= 10:
            await callback.message.answer(
                "Сначала поставьте на паузу другую позицию: одновременно можно использовать не больше 10."
            )
            return
        goal.status = "active"
        goal.percentage = goal.previous_percentage or Decimal("1")
    else:
        goal.previous_percentage = goal.percentage
        goal.status = "paused"
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    normalize_active_goal_percentages(allocator.settings.goals)
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    if goal.status == "active":
        await begin_percentage_setup(callback.message, callback.from_user.id, state)
    else:
        await show_goals_manager(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:complete:ask:"))
async def ask_complete_position(callback: CallbackQuery):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    goal = allocator.settings.goals[index]
    await callback.message.answer(
        f"Отметить ⭐️ <b>{escape(goal.name)}</b> выполненной? Она перестанет получать новые деньги.",
        reply_markup=keyboard([
            [("✔️ Цель выполнена", f"goalmanage:complete:yes:{index}")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(F.data.startswith("goalmanage:complete:yes:"))
async def complete_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    index = int(callback.data.rsplit(":", 1)[1])
    goal = allocator.settings.goals[index]
    if not goal.is_goal:
        await callback.message.answer("Сундук не имеет конечной точки и не завершается автоматически.")
        return
    goal.previous_percentage = goal.percentage
    goal.status = "completed"
    goal.completed_at = datetime.now(timezone.utc).isoformat()
    goal.updated_at = goal.completed_at
    normalize_active_goal_percentages(allocator.settings.goals)
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"✔️ Цель <b>{escape(goal.name)}</b> выполнена. Её доля уже распределена между активными позициями."
    )
    await show_goals_manager(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:archive:"))
async def archive_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    goal = allocator.settings.goals[int(callback.data.rsplit(":", 1)[1])]
    if goal.status != "completed":
        await callback.message.answer("В архив можно перенести завершённую Цель.")
        return
    goal.status = "archived"
    goal.archived_at = datetime.now(timezone.utc).isoformat()
    goal.updated_at = goal.archived_at
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_goals_manager(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:restore:"))
async def restore_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    goal = allocator.settings.goals[int(callback.data.rsplit(":", 1)[1])]
    if goal.status != "archived":
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    goal.status = "paused"
    goal.archived_at = None
    goal.completed_at = None
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        "Позиция возвращена на паузу. Откройте её и нажмите «Возобновить», когда она снова должна получать деньги."
    )
    await show_goals_manager(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:delete:ask:"))
async def ask_delete_position(callback: CallbackQuery):
    await callback.answer()
    index = int(callback.data.rsplit(":", 1)[1])
    allocator = db.load_allocator(callback.from_user.id)
    goal = allocator.settings.goals[index]
    await callback.message.answer(
        f"Удалить {icon(goal)} <b>{escape(goal.name)}</b>? Это действие нельзя отменить.",
        reply_markup=keyboard([
            [("Удалить", f"goalmanage:delete:yes:{index}")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(F.data.startswith("goalmanage:delete:yes:"))
async def delete_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    index = int(callback.data.rsplit(":", 1)[1])
    try:
        goal = allocator.settings.goals.pop(index)
    except IndexError:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    allocator.state.goal_balances.pop(goal.name, None)
    normalize_active_goal_percentages(allocator.settings.goals)
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_goals_manager(callback.message, callback.from_user.id)
