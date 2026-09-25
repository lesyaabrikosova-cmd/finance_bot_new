from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from copy import deepcopy
from charts import send_chart_report
from semantic_chart_colors import (
    BALANCE_CHEST_COLORS,
    BALANCE_GOAL_COLORS,
    position_palette_color,
)
from planned_payments import refresh_planned_payment_targets
from taxes import refresh_planned_tax_targets
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from financial_engine import (
    Goal,
    MAX_ACTIVE_POSITIONS,
    RECOMMENDED_ACTIVE_GOALS,
    goal_percentage_bounds,
    sequential_goal_percentages,
    goal_display_name,
)
from storage import db
from ui import keyboard, main_menu_keyboard
from goals_card import render_goals_card


router = Router()

LAST_ACTIVE_CHEST_TEXT = (
    "Этот сундук нельзя удалить или заморозить. Он принимает свободные деньги, "
    "когда другие цели заполнены. Его можно переименовать или изменить долю."
)


class GoalManagerStates(StatesGroup):
    name = State()
    target = State()
    current = State()
    deadline = State()
    buffer = State()
    percentages = State()
    percentage_review = State()
    edit_name = State()
    edit_target = State()
    edit_deadline = State()
    edit_buffer = State()
    edit_balance = State()


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


def parse_percentage(text: str | None) -> Decimal | None:
    """Процент от 1 с необязательным завершающим знаком % и 0–2 знаками."""
    if not text:
        return None
    value = text.strip().replace("\u00a0", "").replace(" ", "")
    if value.endswith("%"):
        value = value[:-1]
    if "%" in value:
        return None
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    if value.count(".") > 1:
        return None
    fraction = value.partition(".")[2]
    if not value or len(fraction) > 2:
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def parse_date(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def rub(value) -> str:
    formatted = f"{Decimal(str(value)):,.2f}"
    formatted = formatted.replace(",", " ").replace(".", ",")
    return (formatted[:-3] if formatted.endswith(",00") else formatted) + " ₽"


def rub_number(value) -> str:
    return rub(value).removesuffix(" ₽")


def format_percentage(value: Decimal) -> str:
    normalized = Decimal(value).quantize(Decimal("0.01"))
    return format(normalized.normalize(), "f")


def money_range(minimum: Decimal, maximum: Decimal, *, approximate: bool = False) -> str:
    prefix = "≈ " if approximate else ""
    minimum = Decimal(minimum).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    maximum = Decimal(maximum).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if minimum == maximum:
        return f"{prefix}{rub(minimum)}"
    return f"{prefix}{rub_number(minimum)}–{rub(maximum)}"


def position_count_phrase(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} цель или сундук"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return f"{count} цели и сундука"
    return f"{count} целей и сундуков"


def active_position_phrase(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} активная позиция"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return f"{count} активные позиции"
    return f"{count} активных позиций"


def allocation_basis_text(profile_id: str) -> str:
    return {
        "stable": "При вашем минимальном гарантированном доходе",
        "piecework": "При вашем среднем доходе",
        "cyclic": "В среднем за ваш финансовый цикл",
    }[profile_id]


def allocation_variability_note(profile_id: str) -> str:
    if profile_id == "piecework":
        return (
            "Это ориентир для обычного месяца. В «голодные» месяцы сумма может быть меньше, "
            "а при сверхдоходе — больше."
        )
    if profile_id == "cyclic":
        return (
            "Это среднемесячный ориентир за финансовый цикл. В отдельных месяцах сумма может "
            "быть меньше, а при сверхдоходе — больше."
        )
    return "Это ориентир для обычного месяца. При сверхдоходе сумма может быть больше."


def percentage_money_range(capacity: dict, percentage: Decimal) -> str:
    share = Decimal(percentage) / Decimal("100")
    return money_range(
        Decimal(capacity["minimum"]) * share,
        Decimal(capacity["maximum"]) * share,
        approximate=True,
    )


def money_range_floor_hundreds(minimum: Decimal, maximum: Decimal, *, approximate: bool = False) -> str:
    """Compact forecast used where whole hundreds are easier to scan."""
    prefix = "≈ " if approximate else ""
    low = _plain_amount(minimum)
    high = _plain_amount(maximum)
    return f"{prefix}{low} ₽" if low == high else f"{prefix}{low}–{high} ₽"


def _floor_hundreds(value: Decimal) -> Decimal:
    return (max(Decimal("0"), Decimal(value)) / Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_DOWN
    ) * Decimal("100")


def _plain_amount(value: Decimal) -> str:
    return f"{int(_floor_hundreds(value)):,}".replace(",", " ")


def _visible_length(value: str) -> int:
    return len(value.replace("\ufe0f", ""))


def _pad_visible(value: str, width: int) -> str:
    return value + " " * max(0, width - _visible_length(value))


def allocation_progress_table(
    goals: list[Goal],
    values: list[Decimal],
    capacity: dict,
) -> str:
    """Compact aligned allocation rows; pending positions keep only their name."""
    labels = [f"{icon(goal)} {goal.name.strip()}" for goal in goals]
    label_width = max((_visible_length(label) for label in labels), default=0)
    percent_texts = [format_percentage(value) for value in values]
    percent_width = max((len(value) for value in percent_texts), default=1)
    lows = [
        _plain_amount(Decimal(capacity["minimum"]) * value / Decimal("100"))
        for value in values
    ]
    low_width = max((len(value) for value in lows), default=1)
    rows = []
    for index, label in enumerate(labels):
        padded_label = _pad_visible(label, label_width)
        if index >= len(values):
            rows.append(padded_label.rstrip())
            continue
        percentage = percent_texts[index].rjust(percent_width)
        low = lows[index].rjust(low_width)
        high = _plain_amount(
            Decimal(capacity["maximum"]) * values[index] / Decimal("100")
        )
        rows.append(f"{padded_label} — {percentage}% ≈ {low}—{high}")
    return "<pre>" + escape("\n".join(rows)) + "</pre>"


def funded_goal_overflow_note(allocator, goal: Goal, capacity: dict, percentage: Decimal) -> str:
    if not goal.is_goal or goal.full_target_amount is None:
        return ""
    remaining = allocator.goal_remaining_capacity(goal)
    expected_maximum = Decimal(capacity["maximum"]) * Decimal(percentage) / Decimal("100")
    if remaining is None or remaining >= expected_maximum:
        return ""
    chests = [item for item in allocator.settings.active_goals if item.is_chest]
    if len(chests) == 1:
        destination = f"в {escape(display_name(chests[0]))}"
    else:
        destination = "между активными Сундуками по их долям"
    return (
        f"\nДо завершения Цели осталось {rub(remaining)}. "
        f"Оставшаяся часть распределится {destination}."
    )


def icon(goal: Goal) -> str:
    return "🧳" if goal.is_chest else "⭐️"


def status_icon(goal: Goal) -> str:
    if goal.status == "paused":
        return "❄️"
    if goal.status == "completed":
        return "✅"
    return icon(goal)


def position_list_callback(goal: Goal) -> str:
    return "goalmanage:chests" if goal.is_chest else "goalmanage:goals"


def creation_cancel_callback(data: dict) -> str:
    if "vacation_amounts" in data:
        return "menu:calculators"
    draft = data.get("goal_draft") or {}
    return "goalmanage:chests" if draft.get("position_type") == "chest" else "goalmanage:goals"


def display_name(goal: Goal) -> str:
    return goal_display_name(goal.name, goal.is_chest)


def freeze_button_name(goal: Goal) -> str:
    """Short recipient label without changing ordinary Goal names."""
    if goal.is_goal:
        return display_name(goal)
    name = goal.name.strip()
    if name.casefold().startswith("сундук "):
        name = name[7:].strip()
    if name.casefold() == "замена техники":
        return "Техника"
    return name


def goal_by_token(allocator, token: str) -> tuple[int, Goal] | None:
    """Resolve new UID callbacks and buttons sent by older index-based builds."""
    token = str(token or "")
    for index, goal in enumerate(allocator.settings.goals):
        if goal.uid == token:
            return index, goal
    try:
        index = int(token)
        return index, allocator.settings.goals[index]
    except (TypeError, ValueError, IndexError):
        return None


def goal_line(allocator, goal: Goal) -> str:
    state_labels = {
        "paused": " · " + ("заморожена" if goal.is_goal else "заморожен"),
        "completed": " · выполнена",
        "archived": " · в архиве",
    }
    state_label = state_labels.get(goal.status, "")
    if goal.is_goal and goal.target_amount is not None and goal.currency_code != "RUB":
        state_label += " · укажите цель в рублях для автопополнения"
    elif goal.is_goal and goal.target_amount is not None and goal.status == "active" and allocator.goal_remaining_capacity(goal) == 0:
        state_label += " · сумма собрана, новые деньги идут в Сундуки"
    target = ""
    if goal.target_amount is not None:
        current = allocator.state.goal_balances.get(goal.name, goal.balance)
        target_text = rub(goal.full_target_amount).replace(" ₽", f" {goal.currency_code}" if goal.currency_code != "RUB" else " ₽")
        target = f"\n  Учтено: {rub(current)} · цель: {target_text}"
    funded_percentage = goal.percentage if goal.status == "active" else Decimal("0")
    return (
        f"{icon(goal)} <b>{escape(display_name(goal))}</b> — {funded_percentage}%{state_label}"
        f"{target}"
    )


def goal_income_preview(allocator, telegram_id):
    """Simulate one next receipt against today's balances; never persist it."""
    prepared = deepcopy(allocator)
    amount = prepared.settings.average_income
    values = {display_name(g): Decimal("0") for g in prepared.settings.goals if g.status != "archived"}
    if amount <= 0:
        return values, "Укажите доход в настройках, чтобы увидеть примерные суммы пополнения."
    refresh_planned_payment_targets(telegram_id, prepared, date.today(), persist=False)
    refresh_planned_tax_targets(telegram_id, prepared, date.today(), persist=False)
    rates = prepared.settings.income_type_tax_rates
    income_type = max(rates, key=rates.get) if rates else "Доход"
    result = prepared.process_income(amount, income_type, income_date=date.today())
    for goal in prepared.settings.goals:
        if goal.status != "archived":
            values[display_name(goal)] = result.allocations.get(f"Цели:{goal.name}", Decimal("0"))
    basis = "обычном доходе" if allocator.profile_id == "stable" else "среднем доходе"
    total = sum(values.values(), Decimal("0"))
    text = (f"При {basis} <b>{rub(amount)}</b> до налога на Цели и Сундуки "
            f"в этом примере пойдёт примерно <b>{rub(total)}</b>.\n"
            f"Налог: {rub(result.tax)} · тип дохода: {escape(income_type)}.\n"
            "Это пример одного следующего поступления с текущими балансами, а не обещание ежемесячной суммы.")
    if len(set(rates.values())) > 1:
        text += " Для примера взята наибольшая из настроенных ставок налога."
    if allocator.profile_id == "cyclic":
        text += " Для циклического профиля взято одно поступление в размере указанного среднего дохода в текущей фазе цикла."
    if total == 0:
        text += " Сейчас эта сумма направляется на другие финансовые приоритеты."
    return values, text


async def show_goals_manager(message: Message, telegram_id: int) -> None:
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала создайте финансовый профиль через /start.")
        return
    allocation_label = (
        "⚠️ Настройте доли"
        if allocator.settings.allocation_needs_review
        else "✎ Доли распределения"
    )
    rows = [
        [("⭐️ Мои Цели", "goalmanage:goals"), ("🧳 Мои Сундуки", "goalmanage:chests")],
        [(allocation_label, "goalmanage:allocation")],
        [("← Главное меню", "menu:back"), ("ℹ️ Помощь", "goalmanage:help")],
    ]
    try:
        image = await asyncio.to_thread(
            render_goals_card,
            allocator.settings.goals,
            allocator.state.goal_balances,
        )
        await message.answer_photo(
            photo=BufferedInputFile(image, filename="goals-and-chests.png"),
            reply_markup=keyboard(rows),
        )
    except Exception:
        await message.answer(
            "<b>ЦЕЛИ И СУНДУКИ</b>",
            reply_markup=keyboard(rows),
        )


def legacy_goal_names(allocator) -> list[str]:
    current_names = {goal.name for goal in allocator.settings.goals}
    return [
        name for name, amount in allocator.state.goal_balances.items()
        if name not in current_names and Decimal(str(amount)) > 0
    ]


async def show_goals_list(message: Message, telegram_id: int) -> None:
    allocator = db.load_allocator(telegram_id)
    goals = [
        goal for goal in allocator.settings.goals
        if goal.is_goal and goal.status != "archived"
    ]
    rows = [
        [(f"{status_icon(goal)} {goal.name}", f"goalmanage:view:{goal.uid}")]
        for goal in goals
    ]
    rows.extend([
        *([[('Связать прежние суммы', 'goalmanage:repair')]] if legacy_goal_names(allocator) else []),
        [("＋ Добавить", "goalmanage:add:goal")],
        *([[("Архив", "goalmanage:archive:list")]] if any(
            goal.is_goal and goal.status == "archived" for goal in allocator.settings.goals
        ) else []),
        [("← Назад", "goals:manage"), ("← Главное меню", "menu:back")],
    ])
    await message.answer(
        "<b>⭐️ МОИ ЦЕЛИ</b>\n\nВыберите Цель, чтобы посмотреть или изменить её.",
        reply_markup=keyboard(rows),
    )


async def show_chests_list(message: Message, telegram_id: int) -> None:
    allocator = db.load_allocator(telegram_id)
    chests = [
        goal for goal in allocator.settings.goals
        if goal.is_chest and goal.status != "archived"
    ]
    rows = [
        [(f"{status_icon(chest)} {display_name(chest)}", f"goalmanage:view:{chest.uid}")]
        for chest in chests
    ]
    rows.extend([
        [("＋ Добавить", "goalmanage:add:chest")],
        [("← Назад", "goals:manage"), ("← Главное меню", "menu:back")],
    ])
    await message.answer(
        "<b>🧳 МОИ СУНДУКИ</b>\n\nВыберите Сундук, чтобы посмотреть или изменить его.",
        reply_markup=keyboard(rows),
    )


async def show_allocation(message: Message, telegram_id: int) -> None:
    allocator = db.load_allocator(telegram_id)
    active = allocator.settings.active_goals
    configured = sorted(allocator.settings.goals, key=lambda goal: goal.order_index)
    chest_order = tuple(goal.uid or goal.name for goal in configured if goal.is_chest)
    goal_order = tuple(goal.uid or goal.name for goal in configured if not goal.is_chest)
    ordered_active = sorted(
        active,
        key=lambda goal: (
            0 if goal.is_chest else 1,
            -goal.percentage,
            goal.order_index,
        ),
    )
    chart_values = {display_name(goal): goal.percentage for goal in ordered_active}
    capacity = allocator.allocation_setup_capacity_range()
    legend_amounts = {
        display_name(goal): money_range_floor_hundreds(
            Decimal(capacity["minimum"]) * Decimal(goal.percentage) / Decimal("100"),
            Decimal(capacity["maximum"]) * Decimal(goal.percentage) / Decimal("100"),
            approximate=True,
        )
        for goal in ordered_active
    }
    colors = {}
    for goal in ordered_active:
        palette = BALANCE_CHEST_COLORS if goal.is_chest else BALANCE_GOAL_COLORS
        ordered_ids = chest_order if goal.is_chest else goal_order
        colors[display_name(goal)] = position_palette_color(
            goal.uid or goal.name,
            palette,
            color_index=goal.color_index,
            ordered_ids=ordered_ids,
        )
    rows = []
    if len(active) > 1:
        rows.append([("✎ Настроить доли", "goalmanage:percent:start")])
    rows.append([("← Назад", "goals:manage")])
    chart_caption = ""
    fallback = "<b>ЦЕЛИ И СУНДУКИ</b>\n\nДоли распределения"
    if allocator.settings.allocation_needs_review:
        warning = "⚠️ Состав активных Целей и Сундуков изменился. Проверьте доли."
        chart_caption = warning
        fallback += f"\n\n{warning}"
    await send_chart_report(
        message,
        chart_values,
        "ЦЕЛИ И СУНДУКИ",
        chart_caption,
        reply_markup=keyboard(rows),
        subtitle="Доли распределения",
        colors=colors,
        percentages_only=True,
        center_amount=(Decimal(capacity["minimum"]), Decimal(capacity["maximum"])),
        center_label="100%",
        center_suffix="в месяц",
        legend_amounts=legend_amounts,
        preserve_order=True,
        fallback_text=fallback,
    )


async def show_goals_help(message: Message) -> None:
    await message.answer(
        "<b>ℹ️ КАК ЭТО РАБОТАЕТ?</b>\n\n"
        "⭐️ Цель — конкретная сумма, которую нужно накопить к сроку.\n\n"
        "🧳 Сундук — постоянный запас: его можно пополнять, использовать и снова пополнять.\n\n"
        "❄️ Замороженные Цели и Сундуки сохраняются, но временно не получают новые деньги.\n\n"
        "Доли показывают, как делятся деньги, которые Аллокатор уже направил на Цели и Сундуки.\n\n"
        "Хотя бы один Сундук должен оставаться активным, чтобы Аллокатору было куда направлять свободные деньги.",
        reply_markup=keyboard([[("← Назад", "goals:manage")]]),
    )


@router.callback_query(F.data == "goalmanage:repair")
async def choose_legacy_goal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    current_names = {goal.name for goal in allocator.settings.goals}
    legacy = [
        (name, Decimal(str(amount)))
        for name, amount in allocator.state.goal_balances.items()
        if name not in current_names and Decimal(str(amount)) > 0
    ]
    rows = [[(f"{name} — {rub(amount)}", f"goalmanage:repair_old:{name}")]
            for name, amount in legacy]
    await callback.message.answer(
        "<b>ПРЕЖНИЕ ЦЕЛИ И СУНДУКИ</b>\n\n"
        "Выберите прежнее название, затем его текущую цель или сундук. "
        "Аллокатор объединит накопления и историю распределений.",
        reply_markup=keyboard(rows + [[("← Назад", "goals:manage")]]),
    )


@router.callback_query(F.data.startswith("goalmanage:repair_old:"))
async def choose_current_goal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    old = callback.data.split(":", 2)[2]
    allocator = db.load_allocator(callback.from_user.id)
    await state.update_data(legacy_goal_name=old)
    rows = [[(display_name(goal), f"goalmanage:repair_apply:{goal.uid}")]
            for goal in allocator.settings.goals]
    await callback.message.answer(
        f"Прежнее название: <b>{escape(old)}</b>\n\nВыберите его новое название.",
        reply_markup=keyboard(rows + [[("← Назад", "goalmanage:repair")]]),
    )


@router.callback_query(F.data.startswith("goalmanage:repair_apply:"))
async def apply_legacy_goal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    old = data.get("legacy_goal_name")
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if not old or resolved is None:
        await callback.message.answer("Не удалось связать цель. Откройте список заново.")
        return
    _, goal = resolved
    amount = Decimal(str(allocator.state.goal_balances.pop(old, Decimal("0"))))
    allocator.state.goal_balances[goal.name] = (
        Decimal(str(allocator.state.goal_balances.get(goal.name, 0))) + amount
    )
    db.record_envelope_rename(
        callback.from_user.id, allocator, "Цели:", old, goal.name, goal.uid,
    )
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"Сумма «{escape(old)}» добавлена в «{escape(display_name(goal))}».",
        reply_markup=keyboard([[("К целям и сундукам", "goals:manage")]]),
    )


@router.callback_query(F.data == "goalmanage:archive:list")
async def show_goals_archive(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    archived = [
        goal for goal in allocator.settings.goals
        if goal.is_goal and goal.status == "archived"
    ]
    if not archived:
        await show_goals_list(callback.message, callback.from_user.id)
        return
    lines = "\n".join(
        f"• {icon(goal)} <b>{escape(display_name(goal))}</b>"
        for goal in archived
    )
    rows = [
        [(f"Вернуть · {display_name(goal)}", f"goalmanage:restore:{goal.uid}")]
        for goal in archived
    ]
    rows.append([("← Назад", "goalmanage:goals")])
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


@router.callback_query(F.data == "goalmanage:goals")
async def open_goals_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_goals_list(callback.message, callback.from_user.id)


@router.callback_query(F.data == "goalmanage:chests")
async def open_chests_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_chests_list(callback.message, callback.from_user.id)


@router.callback_query(F.data == "goalmanage:allocation")
async def open_allocation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_allocation(callback.message, callback.from_user.id)


@router.callback_query(F.data == "goalmanage:help")
async def open_goals_help(callback: CallbackQuery):
    await callback.answer()
    await show_goals_help(callback.message)


@router.callback_query(F.data == "goalmanage:add")
async def choose_position_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    current_count = sum(
        goal.status in {"active", "paused"} for goal in allocator.settings.goals
    )
    limit_note = (
        f"\n\nСейчас у вас <b>{current_count} из {MAX_ACTIVE_POSITIONS}</b> текущих позиций."
    )
    await callback.message.answer(
        "<b>ЧТО ДОБАВИТЬ?</b>\n\n"
        "⭐️ <b>Цель</b> — конкретная сумма: путёвка, автомобиль или парфюм.\n\n"
        "🧳 <b>Сундук</b> — постоянный запас: Подарки, Хотелки или Замена техники."
        f"{limit_note}",
        reply_markup=keyboard([
            [("⭐️ Цель", "goalmanage:type:goal"), ("🧳 Сундук", "goalmanage:type:chest")],
            [("✖️ Отмена", "goals:manage")],
        ]),
    )


@router.callback_query(F.data.in_({"goalmanage:add:goal", "goalmanage:add:chest"}))
async def add_position_from_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    position_type = callback.data.rsplit(":", 1)[1]
    allocator = db.load_allocator(callback.from_user.id)
    limit_error = allocator.position_creation_error(position_type)
    if limit_error:
        await callback.message.answer(
            limit_error,
            reply_markup=keyboard([[ (
                "← Назад",
                "goalmanage:goals" if position_type == "goal" else "goalmanage:chests",
            ) ]]),
        )
        return
    await state.update_data(goal_draft={"position_type": position_type})
    await state.set_state(GoalManagerStates.name)
    await callback.message.answer(
        "<b>КАК НАЗЫВАЕТСЯ "
        + ("ЦЕЛЬ" if position_type == "goal" else "СУНДУК")
        + "?</b>\n\n——————\n<b>→ Введите название.</b>",
        reply_markup=keyboard([[(
            "✖️ Отмена",
            "goalmanage:goals" if position_type == "goal" else "goalmanage:chests",
        )]]),
    )


@router.callback_query(F.data.startswith("goalmanage:type:"))
async def save_position_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    position_type = callback.data.rsplit(":", 1)[1]
    if position_type not in {"goal", "chest"}:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    allocator = db.load_allocator(callback.from_user.id)
    limit_error = allocator.position_creation_error(position_type)
    if limit_error:
        await state.clear()
        await callback.message.answer(
            limit_error,
            reply_markup=keyboard([[ (
                "← Назад",
                "goalmanage:goals" if position_type == "goal" else "goalmanage:chests",
            ) ]]),
        )
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
        reply_markup=keyboard([[("✖️ Отмена", creation_cancel_callback(data))]]),
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
        reply_markup=keyboard([[("✖️ Отмена", creation_cancel_callback(data))]]),
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
            [("✖️ Отмена", creation_cancel_callback(data))],
        ]),
    )


@router.callback_query(GoalManagerStates.deadline, F.data.startswith("goalmanage:deadline:"))
async def choose_deadline(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data.endswith(":no"):
        await ask_buffer(callback.message, state)
        return
    data = await state.get_data()
    await callback.message.answer(
        "——————\n<b>→ Введите дату в формате ДД.ММ.ГГГГ.</b>",
        reply_markup=keyboard([[("✖️ Отмена", creation_cancel_callback(data))]]),
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
    data = await state.get_data()
    await message.answer(
        "<b>ДОБАВИТЬ ФИНАНСОВЫЙ ЗАПАС?</b>\n\n"
        "Для изменения цен и непредвиденных расходов обычно достаточно 10%.",
        reply_markup=keyboard([
            [("✔️ 10%", "goalmanage:buffer:10"), ("Без запаса", "goalmanage:buffer:0")],
            [("Свой процент", "goalmanage:buffer:custom")],
            [("✖️ Отмена", creation_cancel_callback(data))],
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


def goal_percentage_recommendation(allocator, goal: Goal, today: date | None = None) -> dict | None:
    """Рекомендуемая доля Цели по остатку, сроку и ориентиру общего потока."""
    if not goal.is_goal or not goal.deadline or goal.full_target_amount is None:
        return None
    today = today or date.today()
    try:
        deadline = date.fromisoformat(goal.deadline)
    except ValueError:
        return None
    raw_months = (deadline.year - today.year) * 12 + deadline.month - today.month
    if deadline.day > today.day:
        raw_months += 1
    months = max(0, raw_months)
    current = Decimal(allocator.state.goal_balances.get(goal.name, goal.balance))
    remaining = max(Decimal("0"), Decimal(goal.full_target_amount) - current)
    if months <= 0 or remaining <= 0:
        return None
    capacity = allocator.allocation_setup_capacity_range()
    pool_min = Decimal(capacity["minimum"])
    pool_max = Decimal(capacity["maximum"])
    required = remaining / Decimal(months)

    def required_share(pool: Decimal) -> Decimal | None:
        if pool <= 0:
            return None
        return max(
            Decimal("1"),
            (required / pool * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING,
            ),
        )

    optimistic = required_share(pool_max)
    conservative = required_share(pool_min)
    active_count = len(allocator.settings.active_goals)
    available_max = Decimal("100") - Decimal(max(0, active_count - 1))
    status = "unavailable"
    if optimistic is not None:
        if optimistic > available_max:
            status = "unreachable"
        elif conservative is None or conservative > available_max:
            status = "depends_on_income"
        else:
            status = "recommended"
    return {
        "capacity": capacity,
        "remaining": remaining,
        "months": months,
        "required_monthly": required,
        "optimistic_percentage": optimistic,
        "conservative_percentage": conservative,
        "available_max": available_max,
        "status": status,
    }


def goal_recommendation_markup(goal: Goal, recommendation: dict):
    uid = goal.uid
    status = recommendation["status"]
    if status == "recommended":
        percentage = recommendation["conservative_percentage"]
        return keyboard([
            [(f"Использовать {format_percentage(percentage)}%", f"goalmanage:recommend:apply:{uid}")],
            [("✎ Срок", f"goalmanage:edit:deadline:{uid}"), ("✎ Сумму", f"goalmanage:edit:target:{uid}")],
            [("❄️ Заморозить Цель", f"goalmanage:toggle:{uid}")],
        ])
    if status in {"unreachable", "depends_on_income"}:
        return keyboard([
            [("Использовать максимум", f"goalmanage:recommend:max:{uid}")],
            [("✎ Срок", f"goalmanage:edit:deadline:{uid}"), ("✎ Сумму", f"goalmanage:edit:target:{uid}")],
            [("❄️ Заморозить Цель", f"goalmanage:toggle:{uid}")],
        ])
    return keyboard([
        [("✎ Срок", f"goalmanage:edit:deadline:{uid}"), ("✎ Сумму", f"goalmanage:edit:target:{uid}")],
        [("❄️ Заморозить Цель", f"goalmanage:toggle:{uid}")],
    ])


def goal_portfolio_requirement(allocator, today: date | None = None) -> dict:
    """Сумма рекомендуемых долей активных Целей и доступный им максимум."""
    required = Decimal("0")
    goals_count = 0
    for item in allocator.settings.active_goals:
        if not item.is_goal:
            continue
        goals_count += 1
        recommendation = goal_percentage_recommendation(allocator, item, today=today)
        if recommendation is None:
            required += Decimal("1")
            continue
        share = (
            recommendation["conservative_percentage"]
            or recommendation["optimistic_percentage"]
            or Decimal("1")
        )
        required += max(Decimal("1"), Decimal(share))
    chest_count = sum(item.is_chest for item in allocator.settings.active_goals)
    available = Decimal("100") - Decimal(chest_count)
    return {
        "required": required,
        "available": available,
        "chest_count": chest_count,
        "goals_count": goals_count,
        "conflict": required > available,
    }


async def show_goal_percentage_recommendation(message: Message, allocator, goal: Goal) -> bool:
    recommendation = goal_percentage_recommendation(allocator, goal)
    if recommendation is None:
        return False
    optimistic = recommendation["optimistic_percentage"]
    conservative = recommendation["conservative_percentage"]
    maximum = recommendation["available_max"]
    required = rub(recommendation["required_monthly"])
    status = recommendation["status"]
    if status == "recommended":
        if optimistic == conservative:
            range_text = f"<b>{format_percentage(conservative)}%</b>"
        else:
            range_text = (
                f"<b>{format_percentage(optimistic)}–"
                f"{format_percentage(conservative)}%</b>"
            )
        text = (
            f"Чтобы накопить сумму к выбранному сроку, нужно откладывать примерно "
            f"<b>{required} в месяц</b>.\n\n"
            f"Рекомендуемая доля Цели — {range_text}.\n"
            f"Для более надёжного плана используйте <b>{format_percentage(conservative)}%</b>."
        )
    elif status == "depends_on_income":
        text = (
            "Цель достижима к сроку только при более высоком доходе.\n\n"
            f"Нужно откладывать примерно <b>{required} в месяц</b>.\n"
            f"При хорошем сценарии потребуется не меньше "
            f"<b>{format_percentage(optimistic)}%</b>, а сейчас можно выделить максимум "
            f"<b>{format_percentage(maximum)}%</b>."
        )
    elif status == "unreachable":
        capacity = recommendation["capacity"]
        max_monthly_low = Decimal(capacity["minimum"]) * maximum / Decimal("100")
        max_monthly_high = Decimal(capacity["maximum"]) * maximum / Decimal("100")
        text = (
            "К выбранному сроку накопить всю сумму при текущем доходе, скорее всего, не получится.\n\n"
            f"Нужно откладывать примерно <b>{required} в месяц</b>, а при максимальной "
            f"доступной доле <b>{format_percentage(maximum)}%</b> Цель будет получать "
            f"{money_range(max_monthly_low, max_monthly_high, approximate=True)} в месяц."
        )
    else:
        text = (
            "При таком доходе после обязательных расходов и Бытового резерва "
            "свободной суммы на Цели и Сундуки пока не остаётся."
        )
    portfolio = goal_portfolio_requirement(allocator)
    markup_recommendation = recommendation
    if portfolio["conflict"]:
        text += (
            "\n\nДля всех активных Целей одновременно потребуется примерно "
            f"<b>{format_percentage(portfolio['required'])}%</b>. Сейчас между ними можно "
            f"распределить не больше <b>{format_percentage(portfolio['available'])}%</b>, "
            f"потому что активным Сундукам нужно оставить минимум "
            f"<b>{portfolio['chest_count']}%</b>. Измените сроки или суммы либо заморозьте одну из Целей."
        )
        markup_recommendation = {**recommendation, "status": "unreachable"}
    await message.answer(
        text,
        reply_markup=goal_recommendation_markup(goal, markup_recommendation),
    )
    return True


async def persist_new_position(message: Message, state: FSMContext, buffer: Decimal = Decimal("0")):
    data = await state.get_data()
    draft = dict(data["goal_draft"])
    now = datetime.now(timezone.utc).isoformat()
    allocator = db.load_allocator(message.from_user.id)
    creation_error = allocator.position_creation_error(draft["position_type"])
    if creation_error:
        await state.clear()
        await message.answer(creation_error)
        if draft["position_type"] == "goal":
            await show_goals_list(message, message.from_user.id)
        else:
            await show_chests_list(message, message.from_user.id)
        return
    limit_error = allocator.position_activation_error(draft["position_type"])
    goal = Goal(
        name=draft["name"],
        percentage=Decimal("0"),
        balance=Decimal(draft.get("balance", "0")),
        position_type=draft["position_type"],
        target_amount=(Decimal(draft["target_amount"]) if draft.get("target_amount") else None),
        deadline=draft.get("deadline"),
        buffer_enabled=buffer > 0,
        buffer_percent=buffer,
        status="paused" if limit_error else "active",
        previous_percentage=Decimal("1") if limit_error else None,
        created_at=now,
        updated_at=now,
    )
    allocator.settings.goals.append(goal)
    allocator.ensure_position_color_indices()
    allocator.state.goal_balances[goal.name] = goal.balance
    if goal.status == "active":
        allocator.settle_active_composition_change()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    if limit_error:
        created_word = "создана" if goal.is_goal else "создан"
        frozen_word = "Заморожена" if goal.is_goal else "Заморожен"
        await message.answer(
            f"{icon(goal)} <b>{escape(display_name(goal))}</b> {created_word} в статусе "
            f"<b>«{frozen_word}»</b>. {limit_error}"
        )
        if goal.is_goal:
            await show_goals_list(message, message.from_user.id)
        else:
            await show_chests_list(message, message.from_user.id)
        return
    active_goal_count = sum(item.is_goal for item in allocator.settings.active_goals)
    if goal.is_goal and active_goal_count > RECOMMENDED_ACTIVE_GOALS:
        await message.answer(
            f"У вас уже {active_goal_count} активных Цели. Чем их больше, тем медленнее "
            "копится каждая. Можно продолжить или позже заморозить одну из Целей."
        )
    await message.answer(
        f"{icon(goal)} <b>{escape(display_name(goal))}</b> добавлен. "
        "Пока доля равна 0% — настройте распределение, когда будете готовы."
    )
    if goal.is_goal and await show_goal_percentage_recommendation(message, allocator, goal):
        return
    if goal.is_goal:
        await show_goals_list(message, message.from_user.id)
    else:
        await show_chests_list(message, message.from_user.id)


async def begin_percentage_setup(message: Message, telegram_id: int, state: FSMContext):
    allocator = db.load_allocator(telegram_id)
    active = allocator.settings.active_goals
    if not active:
        await show_goals_manager(message, telegram_id)
        return
    if len(active) == 1:
        active[0].percentage = Decimal("100")
        active[0].is_auto_percentage = True
        allocator.settings.allocation_needs_review = False
        db.save_allocator(telegram_id, allocator)
        await message.answer("Единственная активная позиция получает <b>100%</b>.")
        await show_allocation(message, telegram_id)
        return
    await state.set_state(GoalManagerStates.percentages)
    await state.update_data(
        goal_percentages=[],
        goal_percentage_uids=[goal.uid for goal in active],
    )
    await ask_percentage(message, telegram_id, state)


@router.callback_query(F.data == "goalmanage:percent:start")
async def start_percentage_setup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await begin_percentage_setup(callback.message, callback.from_user.id, state)


async def ask_percentage(message: Message, telegram_id: int, state: FSMContext):
    allocator = db.load_allocator(telegram_id)
    active = allocator.settings.active_goals
    data = await state.get_data()
    expected_uids = data.get("goal_percentage_uids", [])
    if [goal.uid for goal in active] != expected_uids:
        await state.clear()
        await message.answer(
            "Состав активных Целей и Сундуков изменился. Начнём настройку долей заново."
        )
        await begin_percentage_setup(message, telegram_id, state)
        return
    chosen = [Decimal(value) for value in data.get("goal_percentages", [])]
    index = len(chosen)
    capacity = allocator.allocation_setup_capacity_range()
    if index >= len(active) - 1:
        values = sequential_goal_percentages(chosen, len(active))
        await state.set_state(GoalManagerStates.percentage_review)
        await state.update_data(goal_percentages_pending=[str(value) for value in values])
        summary = allocation_progress_table(active, values, capacity)
        await message.answer(
            "<b>ПРОВЕРЬТЕ ДОЛИ</b>\n\n"
            f"{summary}\n\nИтого — <b>100%</b>\n\n"
            "Суммы ориентировочные. В слабые месяцы они могут быть меньше, "
            "а при сверхдоходе — больше.",
            reply_markup=keyboard([
                [("✓ Сохранить", "goalmanage:percent:save")],
                [("← Назад", "goalmanage:allocation")],
            ]),
        )
        return
    minimum, maximum = goal_percentage_bounds(chosen, len(active) - index - 1)
    active_list = "\n".join(
        f"{icon(goal)} {escape(display_name(goal))}" for goal in active
    )
    progress_table = allocation_progress_table(active, chosen, capacity)
    ten_percent = percentage_money_range(capacity, Decimal("10"))
    has_capacity = Decimal(capacity["maximum"]) > 0
    zero_capacity_text = (
        "При таком доходе деньги распределяются на обязательные расходы и Бытовой резерв. "
        "После этого свободной суммы на Цели и Сундуки пока не остаётся. "
        "Доли можно настроить заранее."
    )
    current_goal = f"{icon(active[index])} <b>{escape(display_name(active[index]))}</b>"
    bounds = f"{format_percentage(minimum)}% до {format_percentage(maximum)}%"
    if index == 0:
        basis = allocation_basis_text(allocator.profile_id)
        capacity_explanation = (
            f"{basis} Аллокатор сможет направлять на них примерно:\n\n"
            f"<b>{money_range_floor_hundreds(Decimal(capacity['minimum']), Decimal(capacity['maximum']))} в месяц</b>\n\n"
            "Чтобы было проще выбирать проценты:\n\n"
            f"<b>10% {ten_percent}</b>"
            if has_capacity
            else zero_capacity_text
        )
        text = (
            "<b>ДОЛИ РАСПРЕДЕЛЕНИЯ</b>\n\n"
            f"У вас сейчас <b>{active_position_phrase(len(active))}</b>:\n\n"
            f"<pre>{active_list}</pre>\n\n"
            f"{capacity_explanation}\n\n"
            "Сейчас настраиваем:\n\n"
            f"{current_goal}\n"
            "——————\n"
            f"<b>→ Введите процент от {bounds}.</b>"
        )
    else:
        capacity_explanation = (
            "Чтобы было проще выбирать проценты:\n\n"
            f"<b>10% {ten_percent}</b>"
            if has_capacity
            else zero_capacity_text
        )
        text = (
            "<b>ДОЛИ РАСПРЕДЕЛЕНИЯ</b>\n"
            f"{progress_table}\n\n"
            f"{capacity_explanation}\n\n"
            "Сейчас настраиваем:\n\n"
            f"{current_goal}\n"
            "——————\n"
            f"<b>→ Введите процент от {bounds}.</b>"
        )
    await message.answer(
        text,
        reply_markup=keyboard([[("✖️ Отмена", "goalmanage:allocation")]]),
    )


@router.message(GoalManagerStates.percentages)
async def save_percentage(message: Message, state: FSMContext):
    allocator = db.load_allocator(message.from_user.id)
    active = allocator.settings.active_goals
    data = await state.get_data()
    chosen = [Decimal(value) for value in data.get("goal_percentages", [])]
    index = len(chosen)
    minimum, maximum = goal_percentage_bounds(chosen, len(active) - index - 1)
    value = parse_percentage(message.text)
    if value is None or not minimum <= value <= maximum:
        await message.answer(
            f"Введите процент от {format_percentage(minimum)}% до "
            f"{format_percentage(maximum)}%. Можно использовать не больше двух знаков после запятой."
        )
        return
    chosen.append(value)
    await state.update_data(goal_percentages=[str(item) for item in chosen])
    await ask_percentage(message, message.from_user.id, state)


@router.callback_query(
    GoalManagerStates.percentage_review,
    F.data == "goalmanage:percent:save",
)
async def confirm_percentages(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    active = allocator.settings.active_goals
    data = await state.get_data()
    expected_uids = data.get("goal_percentage_uids", [])
    values = [Decimal(value) for value in data.get("goal_percentages_pending", [])]
    if [goal.uid for goal in active] != expected_uids or len(values) != len(active):
        await state.clear()
        await callback.message.answer("Состав активных позиций изменился. Настройте доли заново.")
        await show_allocation(callback.message, callback.from_user.id)
        return
    if (
        sum(values, Decimal("0")) != Decimal("100")
        or any(value < Decimal("1") for value in values)
        or any(value != value.quantize(Decimal("0.01")) for value in values)
    ):
        await callback.message.answer(
            "Доли не сохранены: каждой активной позиции нужен минимум 1%, "
            "сумма должна быть 100%, а точность — не больше двух знаков после запятой."
        )
        return
    for goal in allocator.settings.goals:
        if goal.status != "active":
            if goal.percentage > 0 and goal.previous_percentage is None:
                goal.previous_percentage = goal.percentage
            goal.percentage = Decimal("0")
            goal.is_auto_percentage = False
    for goal, value in zip(active, values):
        goal.percentage = value
        goal.is_auto_percentage = False
    active[-1].is_auto_percentage = True
    allocator.settings.allocation_needs_review = False
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer("✓ Доли распределения сохранены.")
    await show_allocation(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:recommend:apply:"))
async def apply_goal_percentage_recommendation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await callback.message.answer("Эта Цель уже изменена.")
        return
    _, goal = resolved
    recommendation = goal_percentage_recommendation(allocator, goal)
    if recommendation is None or recommendation["status"] != "recommended":
        await callback.message.answer("Рекомендация изменилась. Настройте общие доли заново.")
        await begin_percentage_setup(callback.message, callback.from_user.id, state)
        return
    percentage = Decimal(recommendation["conservative_percentage"])
    fallback = allocator.ensure_active_chest()
    available = max(Decimal("0"), Decimal(fallback.percentage) - Decimal("1"))
    if fallback is goal or percentage > available:
        await callback.message.answer(
            f"Для Цели рекомендуется {format_percentage(percentage)}%, но в постоянном "
            "Сундуке недостаточно свободной доли. Настройте общие доли."
        )
        await begin_percentage_setup(callback.message, callback.from_user.id, state)
        return
    fallback.percentage -= percentage
    goal.percentage = percentage
    goal.is_auto_percentage = False
    allocator.settings.allocation_needs_review = False
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"✓ Для Цели <b>{escape(display_name(goal))}</b> установлена доля "
        f"<b>{format_percentage(percentage)}%</b>."
    )
    await show_allocation(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:recommend:max:"))
async def apply_goal_maximum_percentage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await callback.message.answer("Эта Цель уже изменена.")
        return
    _, goal = resolved
    active = allocator.settings.active_goals
    if goal not in active:
        await callback.message.answer("Сначала разморозьте эту Цель.")
        return
    maximum = Decimal("100") - Decimal(len(active) - 1)
    for item in active:
        item.percentage = Decimal("1")
        item.is_auto_percentage = False
    goal.percentage = maximum
    active[-1].is_auto_percentage = True
    allocator.settings.allocation_needs_review = False
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"✓ Для Цели <b>{escape(display_name(goal))}</b> установлена максимально "
        f"доступная доля <b>{format_percentage(maximum)}%</b>."
    )
    await show_allocation(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:view:"))
async def view_position(callback: CallbackQuery):
    await callback.answer()
    await show_position_card(
        callback.message,
        callback.from_user.id,
        callback.data.rsplit(":", 1)[1],
    )


async def show_position_card(message: Message, telegram_id: int, token: str) -> None:
    allocator = db.load_allocator(telegram_id)
    resolved = goal_by_token(allocator, token)
    if resolved is None:
        await show_goals_manager(message, telegram_id)
        return
    _, goal = resolved
    uid = goal.uid
    current = Decimal(str(allocator.state.goal_balances.get(goal.name, goal.balance)))
    status_labels = {
        "active": "Активна" if goal.is_goal else "Активен",
        "paused": "Заморожена" if goal.is_goal else "Заморожен",
        "completed": "Выполнена",
        "archived": "В архиве",
    }
    details = [
        f"Накоплено — <b>{rub(current)}</b>"
        if goal.is_goal else f"Отложено Аллокатором — <b>{rub(current)}</b>"
    ]
    if goal.is_goal:
        target = goal.full_target_amount
        if target is None:
            details.append("Конечная сумма — <b>не задана</b>")
        else:
            details[0] += f" из <b>{rub(target)}</b>"
            progress = Decimal("100") if target <= 0 else min(
                Decimal("100"), current * Decimal("100") / target,
            )
            details.append(f"Готово — <b>{progress.quantize(Decimal('1'))}%</b>")
        deadline = date.fromisoformat(goal.deadline).strftime("%d.%m.%Y") if goal.deadline else "не задан"
        details.append(f"Срок — <b>{deadline}</b>")
        details.append(
            "Запас — <b>"
            + (f"{format_percentage(goal.buffer_percent)}%" if goal.buffer_enabled else "не задан")
            + "</b>"
        )
    if goal.status != "active":
        details.append(f"Статус — <b>{status_labels[goal.status]}</b>")
    if goal.status == "paused":
        details.append("\nДеньги сохраняются, но новые пополнения временно не поступают.")

    actions = [[("✎ Название", f"goalmanage:edit:name:{uid}")]]
    if goal.is_goal and goal.status not in {"completed", "archived"}:
        actions[0].append(("✎ Срок", f"goalmanage:edit:deadline:{uid}"))
        actions.append([
            ("✎ Баланс", f"goalmanage:edit:balance:{uid}"),
            ("✎ Целевая сумма", f"goalmanage:edit:target:{uid}"),
        ])
        actions.append([("✎ Запас", f"goalmanage:edit:buffer:{uid}")])
    else:
        actions[0].append(("✎ Баланс", f"goalmanage:edit:balance:{uid}"))
    if goal.is_goal and goal.status in {"active", "paused"}:
        actions.append([("✓ Отметить выполненной", f"goalmanage:complete:ask:{uid}")])
    is_only_active_chest = allocator.is_last_active_chest(goal)
    if is_only_active_chest:
        details.append(f"\n{LAST_ACTIVE_CHEST_TEXT}")
        # Its share remains editable on the allocation screen. The card only
        # keeps actions that can actually succeed for the last active chest.
        actions = [[("✎ Название", f"goalmanage:edit:name:{uid}")]]
    if goal.status in {"active", "paused"} and not is_only_active_chest:
        freeze_row = [
            (
                "☀️ Разморозить" if goal.status == "paused" else "❄️ Заморозить",
                f"goalmanage:toggle:{uid}",
            ),
        ]
        freeze_row.append(("🗑️ Удалить", f"goalmanage:delete:ask:{uid}"))
        actions.append([
            *freeze_row,
        ])
    elif goal.status == "completed":
        actions.append([
            ("Перенести в архив", f"goalmanage:archive:{uid}"),
            ("🗑️ Удалить", f"goalmanage:delete:ask:{uid}"),
        ])
    elif goal.status == "archived":
        actions.append([("Вернуть из архива", f"goalmanage:restore:{uid}")])
    actions.append([("← Назад", position_list_callback(goal))])
    await message.answer(
        f"<b>{status_icon(goal)} {escape(display_name(goal).upper())}</b>\n\n"
        + "\n".join(details),
        reply_markup=keyboard(actions),
    )


async def begin_edit(callback: CallbackQuery, state: FSMContext, field: str, state_value: State, prompt: str):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    await state.update_data(edit_goal_uid=goal.uid)
    await state.set_state(state_value)
    await callback.message.answer(
        prompt,
        reply_markup=keyboard([[('✖️ Отмена', f"goalmanage:view:{goal.uid}")]]),
    )


@router.callback_query(F.data.startswith("goalmanage:edit:name:"))
async def edit_name(callback: CallbackQuery, state: FSMContext):
    await begin_edit(callback, state, "name", GoalManagerStates.edit_name, "——————\n<b>→ Введите новое название.</b>")


@router.message(GoalManagerStates.edit_name)
async def save_edited_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    resolved = goal_by_token(
        allocator,
        data.get("edit_goal_uid", data.get("edit_goal_index", "")),
    )
    if resolved is None:
        await state.clear()
        await message.answer("Эта цель уже изменена.")
        await show_goals_manager(message, message.from_user.id)
        return
    index, goal = resolved
    if not name or len(name) > 60 or any(i != index and goal.name.casefold() == name.casefold() for i, goal in enumerate(allocator.settings.goals)):
        await message.answer("Введите уникальное название длиной до 60 символов.")
        return
    old_name = goal.name
    goal.name = name
    db.record_envelope_rename(
        message.from_user.id, allocator, 'Цели:', old_name, name, goal.uid,
    )
    if old_name in allocator.state.goal_balances:
        allocator.state.goal_balances[name] = (
            allocator.state.goal_balances.get(name, Decimal("0"))
            + allocator.state.goal_balances.pop(old_name)
        )
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_position_card(message, message.from_user.id, goal.uid)


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
    resolved = goal_by_token(
        allocator,
        data.get("edit_goal_uid", data.get("edit_goal_index", "")),
    )
    if resolved is None:
        await state.clear()
        await message.answer("Эта цель уже изменена.")
        await show_goals_manager(message, message.from_user.id)
        return
    _, goal = resolved
    goal.buffer_enabled = value > 0
    goal.buffer_percent = value
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await show_position_card(message, message.from_user.id, goal.uid)


@router.callback_query(F.data.startswith("goalmanage:edit:balance:"))
async def edit_balance(callback: CallbackQuery, state: FSMContext):
    await begin_edit(
        callback,
        state,
        "balance",
        GoalManagerStates.edit_balance,
        "——————\n<b>→ Введите текущий баланс.</b>",
    )


@router.message(GoalManagerStates.edit_balance)
async def save_edited_balance(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 и выше.")
        return
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    resolved = goal_by_token(allocator, data.get("edit_goal_uid", ""))
    if resolved is None:
        await state.clear()
        await message.answer("Эта позиция уже изменена.")
        await show_goals_manager(message, message.from_user.id)
        return
    _, goal = resolved
    previous = Decimal(str(allocator.state.goal_balances.get(goal.name, goal.balance)))
    allocator.state.goal_balances[goal.name] = value
    goal.balance = value
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    with db.transaction():
        db.save_operation(
            message.from_user.id,
            "envelope_balance_adjustment",
            {
                "envelope": f"Цели:{goal.name}",
                "entity_id": f"goal:{goal.uid}",
                "entity_kind": "chest" if goal.is_chest else "goal",
                "previous_balance": str(previous),
                "new_balance": str(value),
                "difference": str(value - previous),
            },
        )
        db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer("✓ Баланс скорректирован. История поступлений не изменена.")
    await show_position_card(message, message.from_user.id, goal.uid)


async def save_simple_goal_edit(message: Message, state: FSMContext, field: str, value):
    data = await state.get_data()
    allocator = db.load_allocator(message.from_user.id)
    resolved = goal_by_token(
        allocator,
        data.get("edit_goal_uid", data.get("edit_goal_index", "")),
    )
    if resolved is None:
        await state.clear()
        await message.answer("Эта цель уже изменена.")
        await show_goals_manager(message, message.from_user.id)
        return
    _, goal = resolved
    setattr(goal, field, value)
    if field == "target_amount":
        goal.currency_code = "RUB"
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    if (
        field in {"target_amount", "deadline"}
        and goal.status == "active"
        and await show_goal_percentage_recommendation(message, allocator, goal)
    ):
        return
    await show_position_card(message, message.from_user.id, goal.uid)


@router.callback_query(F.data.startswith("goalmanage:toggle:"))
async def toggle_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    if goal.status not in {"active", "paused"}:
        await callback.message.answer("Эту позицию сначала нужно вернуть из архива.")
        return
    if goal.status == "paused":
        try:
            allocator.activate_position(goal)
        except ValueError as error:
            await callback.message.answer(str(error))
            return
    else:
        if allocator.is_last_active_chest(goal):
            await callback.message.answer(LAST_ACTIVE_CHEST_TEXT)
            return
        remaining = [item for item in allocator.settings.active_goals if item is not goal]
        if Decimal(goal.percentage) <= 0 or len(remaining) == 1:
            try:
                allocator.pause_position_with_distribution(
                    goal, recipient=remaining[0] if remaining else None
                )
            except ValueError as error:
                await callback.message.answer(str(error))
                return
        else:
            await state.update_data(freeze_source_uid=goal.uid)
            capacity = allocator.allocation_setup_capacity_range()
            released = percentage_money_range(capacity, Decimal(goal.percentage))
            buttons = []
            recipient_buttons = [
                (f"{icon(item)} {freeze_button_name(item)}",
                 f"goalmanage:freeze:to:{item.uid}")
                for item in remaining
            ]
            buttons.extend([recipient_buttons[index:index + 2] for index in range(0, len(recipient_buttons), 2)])
            buttons.extend([
                [("Равномерно между всеми", "goalmanage:freeze:equal")],
                [("✖️ Отмена", f"goalmanage:view:{goal.uid}")],
            ])
            await callback.message.answer(
                f"Если заморозить {icon(goal)} <b>{escape(display_name(goal))}</b>, "
                f"освободится доля <b>{format_percentage(Decimal(goal.percentage))}%</b> — "
                f"это <b>{released} в месяц</b>.\n\n"
                "Куда направить эти деньги?",
                reply_markup=keyboard(buttons),
            )
            return
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    if goal.status == "active":
        active_goal_count = sum(item.is_goal for item in allocator.settings.active_goals)
        if goal.is_goal and active_goal_count > RECOMMENDED_ACTIVE_GOALS:
            await callback.message.answer(
                f"Сейчас активно {active_goal_count} Цели. Чем их больше, тем медленнее "
                "копится каждая."
            )
        await callback.message.answer(
            "Позиция разморожена с долей 0%. Настройте распределение, когда будете готовы."
        )
    else:
        await callback.message.answer(
            "Позиция заморожена: баланс и история сохранены, новые деньги не поступают."
        )
    if goal.is_goal:
        await show_goals_list(callback.message, callback.from_user.id)
    else:
        await show_chests_list(callback.message, callback.from_user.id)


async def finish_freeze_distribution(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    recipient_uid: str | None = None,
    evenly: bool = False,
):
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    source_resolved = goal_by_token(allocator, data.get("freeze_source_uid", ""))
    if source_resolved is None or source_resolved[1].status != "active":
        await state.clear()
        await callback.message.answer("Список активных позиций изменился. Откройте заморозку ещё раз.")
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, source = source_resolved
    recipient = None
    if not evenly:
        recipient_resolved = goal_by_token(allocator, recipient_uid or "")
        if recipient_resolved is None:
            await callback.message.answer("Эта позиция больше недоступна. Выберите другую.")
            return
        recipient = recipient_resolved[1]
    released = Decimal(source.percentage)
    try:
        allocator.pause_position_with_distribution(
            source, recipient=recipient, evenly=evenly
        )
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    source.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    if evenly:
        destination_text = "распределена поровну между всеми активными позициями"
    else:
        destination_text = f"направлена в {icon(recipient)} <b>{escape(display_name(recipient))}</b>"
    await callback.message.answer(
        f"Позиция ❄️ <b>{escape(display_name(source))}</b> заморожена. "
        f"Доля {format_percentage(released)}% {destination_text}."
    )
    if source.is_goal:
        await show_goals_list(callback.message, callback.from_user.id)
    else:
        await show_chests_list(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:freeze:to:"))
async def freeze_to_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await finish_freeze_distribution(
        callback, state, recipient_uid=callback.data.rsplit(":", 1)[1]
    )


@router.callback_query(F.data == "goalmanage:freeze:equal")
async def freeze_evenly(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await finish_freeze_distribution(callback, state, evenly=True)


@router.callback_query(F.data.startswith("goalmanage:complete:ask:"))
async def ask_complete_position(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    await callback.message.answer(
        f"Отметить ⭐️ <b>{escape(goal.name)}</b> выполненной? Она перестанет получать новые деньги.",
        reply_markup=keyboard([
            [("✔️ Цель выполнена", f"goalmanage:complete:yes:{goal.uid}")],
            [("✖️ Отмена", f"goalmanage:view:{goal.uid}")],
        ]),
    )


@router.callback_query(F.data.startswith("goalmanage:complete:yes:"))
async def complete_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    if not goal.is_goal:
        await callback.message.answer("Сундук не имеет конечной точки и не завершается автоматически.")
        return
    try:
        allocator.complete_goal(goal)
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"✔️ Цель <b>{escape(goal.name)}</b> выполнена. "
        "Её доля поровну распределена между оставшимися активными позициями."
    )
    await show_goals_list(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:archive:"))
async def archive_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    if goal.status != "completed":
        await callback.message.answer("В архив можно перенести завершённую Цель.")
        return
    goal.status = "archived"
    goal.archived_at = datetime.now(timezone.utc).isoformat()
    goal.updated_at = goal.archived_at
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await show_goals_list(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:restore:"))
async def restore_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    if goal.status != "archived":
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    limit_error = allocator.position_creation_error(goal.position_type)
    if limit_error:
        await callback.message.answer(
            limit_error,
            reply_markup=keyboard([[ ("← Назад", "goalmanage:archive:list") ]]),
        )
        return
    goal.status = "paused"
    goal.archived_at = None
    goal.completed_at = None
    goal.updated_at = datetime.now(timezone.utc).isoformat()
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        "Цель возвращена в замороженные. Разморозьте её, когда она снова должна получать деньги."
    )
    await show_goals_list(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("goalmanage:delete:ask:"))
async def ask_delete_position(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    _, goal = resolved
    if allocator.is_last_active_chest(goal):
        await callback.message.answer(
            LAST_ACTIVE_CHEST_TEXT,
            reply_markup=keyboard([[("← Назад", f"goalmanage:view:{goal.uid}")]]),
        )
        return
    destination = next(
        (item for item in allocator.settings.active_goals if item is not goal and item.is_chest),
        None,
    )
    if destination is None:
        destination = allocator.ensure_active_chest()
    balance = Decimal(str(allocator.state.goal_balances.get(goal.name, goal.balance)))
    await callback.message.answer(
        f"Удалить {icon(goal)} <b>{escape(display_name(goal))}</b>?\n\n"
        f"Сейчас здесь — <b>{rub(balance)}</b>. Деньги останутся в накоплениях "
        f"и будут перенесены в 🧳 <b>{escape(display_name(destination))}</b>.",
        reply_markup=keyboard([
            [("🗑️ Удалить", f"goalmanage:delete:yes:{goal.uid}")],
            [("← Назад", f"goalmanage:view:{goal.uid}"), ("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data.startswith("goalmanage:delete:yes:"))
async def delete_position(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    resolved = goal_by_token(allocator, callback.data.rsplit(":", 1)[1])
    if resolved is None:
        await show_goals_manager(callback.message, callback.from_user.id)
        return
    index, goal = resolved
    return_to = position_list_callback(goal)
    if allocator.is_last_active_chest(goal):
        await callback.message.answer(
            LAST_ACTIVE_CHEST_TEXT,
            reply_markup=keyboard([[("← Назад", f"goalmanage:view:{goal.uid}")]]),
        )
        return
    allocator.move_system_chest_role(goal)
    destination = allocator.ensure_active_chest()
    was_active = goal.status == "active"
    released = max(Decimal("0"), goal.percentage) if was_active else Decimal("0")
    moved = Decimal(str(allocator.state.goal_balances.pop(goal.name, goal.balance)))
    allocator.state.goal_balances[destination.name] = (
        Decimal(str(allocator.state.goal_balances.get(destination.name, destination.balance)))
        + moved
    )
    source_key = f"Цели:{goal.name}"
    destination_key = f"Цели:{destination.name}"
    period_moved = Decimal(str(allocator.state.period_allocations.pop(source_key, 0)))
    if period_moved:
        allocator.state.period_allocations[destination_key] = (
            Decimal(str(allocator.state.period_allocations.get(destination_key, 0)))
            + period_moved
        )
    allocator.settings.goals.pop(index)
    if was_active:
        allocator.settle_active_composition_change(released)
    with db.transaction():
        if moved:
            db.save_operation(
                callback.from_user.id,
                "envelope_transfer",
                {
                    "source": source_key,
                    "source_id": f"goal:{goal.uid}",
                    "destination": destination_key,
                    "destination_id": f"goal:{destination.uid}",
                    "source_kind": "chest" if goal.is_chest else "goal",
                    "destination_kind": "chest",
                    "amount": str(moved),
                    "reason": "goal_deleted",
                },
            )
        db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(
        f"{icon(goal)} <b>{escape(display_name(goal))}</b> удалён. "
        f"{rub(moved)} перенесено в 🧳 <b>{escape(display_name(destination))}</b>."
    )
    if return_to == "goalmanage:goals":
        await show_goals_list(callback.message, callback.from_user.id)
    else:
        await show_chests_list(callback.message, callback.from_user.id)
