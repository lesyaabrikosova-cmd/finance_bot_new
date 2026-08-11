from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from storage import db
from ui import keyboard, main_menu_keyboard

router = Router()

class EditSettingsStates(StatesGroup):
    pillow = State()
    critical_life = State()
    household_reserve = State()
    average_income = State()
    tax_rate = State()
    life_categories = State()
    goal_percentages = State()
    c_split = State()

def parse_decimal(text: str) -> Decimal | None:
    if not text:
        return None
    value = text.strip().replace("₽", "").replace("%", "").replace("\u00a0", "").replace(" ", "")
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    elif "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None

def rub(value: Decimal) -> str:
    formatted = f"{Decimal(value):,.2f}"
    return formatted.replace(",", " ").replace(".", ",") + " ₽"

def distribute_existing_pillow(allocator, total: Decimal) -> None:
    s = allocator.settings
    st = allocator.state

    st.pillow_minimum = Decimal("0")
    st.pillow_force_majeure = Decimal("0")
    st.pillow_stabilizer = Decimal("0")

    remaining = total

    if any(credit.active for credit in s.credits):
        part = min(remaining, s.minimum_reserve_limit)
        st.pillow_minimum = part
        remaining -= part

    part = min(remaining, s.force_majeure_limit)
    st.pillow_force_majeure = part
    remaining -= part

    if remaining > 0 and s.employment_type == "Фрилансер":
        normal = min(remaining, s.stabilizer_full_limit)
        st.pillow_stabilizer = normal
        remaining -= normal

    st.pillow_stabilizer += remaining

async def show_settings_menu(message: Message, telegram_id: int):
    allocator = db.load_allocator(telegram_id)
    if allocator is None:
        await message.answer("Сначала настройте профиль через /start.")
        return

    s = allocator.settings
    st = allocator.state

    goals = ", ".join(f"{g.name} {g.percentage}%" for g in s.goals) if s.goals else "без отдельных категорий"
    categories = ", ".join(f"{name} {rub(amount)}" for name, amount in s.life_categories.items()) if s.life_categories else "нет отдельных категорий"

    await message.answer(
        "⚙️ <b>РЕДАКТИРОВАНИЕ НАСТРОЕК</b>\n\n"
        f"🔴 Обязательная жизнь: <b>{rub(s.critical_life)}</b>\n"
        f"💚 Бытовой резерв: <b>{rub(s.household_reserve)}</b>\n"
        f"💰 Средний доход: <b>{rub(s.average_income)}</b>\n"
        f"🏛 Налог: <b>{s.tax_rate}%</b>\n"
        f"🛟 Подушка сейчас: <b>{rub(st.pillow_balance)}</b>\n\n"
        f"❤️ Категории КЖ: {escape(categories)}\n"
        f"⭐️ Цели: {escape(goals)}\n\n"
        f"Распределение этапа C: ⭐️ цели {s.goals_share_c}% / 🛟 подушка {s.pillow_share_c}%",
        reply_markup=keyboard([
            [("🛟 Изменить Подушку", "settings:pillow")],
            [("🔴 Изменить КЖ", "settings:critical"), ("💚 Изменить Быт. резерв", "settings:household")],
            [("💰 Средний доход", "settings:income")],
            [("🏛 Налог", "settings:tax")],
            [("❤️ Категории КЖ", "settings:life_categories")],
            [("⭐️ Проценты целей", "settings:goals")],
            [("⚖️ Цели / Подушка этапа C", "settings:c_split")],
            [("🔄 Пройти настройку заново", "setup:restart")],
            [("⬅️ Главное меню", "menu:back")],
        ]),
    )

@router.callback_query(F.data.in_({"settings:open", "menu:settings"}))
async def open_settings(callback: CallbackQuery):
    await callback.answer()
    await show_settings_menu(callback.message, callback.from_user.id)

@router.callback_query(F.data == "settings:pillow")
async def edit_pillow(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.pillow)
    await callback.message.answer(
        "🛟 <b>ТЕКУЩИЙ БАЛАНС ПОДУШКИ</b>\n\n"
        f"Сейчас в Аллокаторе: <b>{rub(allocator.state.pillow_balance)}</b>\n\n"
        "Введите фактическую сумму, которая сейчас находится в вашей Подушке.\n"
        "Например: <code>175000</code>"
    )

@router.message(EditSettingsStates.pillow)
async def save_pillow(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    distribute_existing_pillow(allocator, value)
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Подушка обновлена: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard())

@router.callback_query(F.data == "settings:critical")
async def edit_critical(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.critical_life)
    await callback.message.answer(
        "🔴 <b>ОБЯЗАТЕЛЬНАЯ ЖИЗНЬ</b>\n\n"
        f"Сейчас: <b>{rub(allocator.settings.critical_life)}</b>\n\n"
        "Введите новую месячную сумму обязательных расходов.\n"
        "Кредитные минимальные платежи сюда не добавляйте — они учитываются отдельно."
    )

@router.message(EditSettingsStates.critical_life)
async def save_critical(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите сумму больше 0.")
        return
    allocator = db.load_allocator(message.from_user.id)
    explicit = sum(allocator.settings.life_categories.values(), Decimal("0"))
    if explicit > value:
        await message.answer(
            "Новая КЖ меньше суммы ваших отдельных категорий КЖ.\n\n"
            f"Категории сейчас составляют {rub(explicit)}.\n"
            "Сначала уменьшите категории либо введите КЖ не меньше этой суммы."
        )
        return
    allocator.settings.critical_life = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Обязательная жизнь обновлена: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard())

@router.callback_query(F.data == "settings:household")
async def edit_household(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.household_reserve)
    await callback.message.answer(
        "💚 <b>БЫТОВОЙ РЕЗЕРВ</b>\n\n"
        f"Сейчас: <b>{rub(allocator.settings.household_reserve)}</b>\n\n"
        "Введите новую месячную сумму нерегулярных бытовых расходов."
    )

@router.message(EditSettingsStates.household_reserve)
async def save_household(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.household_reserve = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Бытовой резерв обновлён: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard())

@router.callback_query(F.data == "settings:income")
async def edit_average_income(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.average_income)
    await callback.message.answer(
        "💰 <b>СРЕДНЕМЕСЯЧНЫЙ ДОХОД</b>\n\n"
        f"Сейчас: <b>{rub(allocator.settings.average_income)}</b>\n\n"
        "Введите новую среднюю сумму."
    )

@router.message(EditSettingsStates.average_income)
async def save_average_income(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0:
        await message.answer("Введите сумму от 0 ₽ и выше.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.average_income = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(f"✅ Средний доход обновлён: <b>{rub(value)}</b>", reply_markup=main_menu_keyboard())

@router.callback_query(F.data == "settings:tax")
async def edit_tax(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.tax_rate)
    await callback.message.answer(
        "🏛 <b>СТАВКА НАЛОГА</b>\n\n"
        f"Сейчас: <b>{allocator.settings.tax_rate}%</b>\n\n"
        "Введите новую ставку от 0 до 100."
    )

@router.message(EditSettingsStates.tax_rate)
async def save_tax(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 0 or value > 100:
        await message.answer("Введите число от 0 до 100.")
        return
    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.tax_rate = value
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer(
        f"✅ Ставка налога обновлена: <b>{value}%</b>\n\n"
        "Список типов дохода, с которых удерживается налог, остаётся прежним.",
        reply_markup=main_menu_keyboard(),
    )

@router.callback_query(F.data == "settings:life_categories")
async def edit_life_categories(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.life_categories)
    current = "\n".join(
        f"• {escape(name)} = {rub(amount)}"
        for name, amount in allocator.settings.life_categories.items()
    ) or "Отдельных категорий сейчас нет."
    await callback.message.answer(
        "❤️ <b>КАТЕГОРИИ ОБЯЗАТЕЛЬНОЙ ЖИЗНИ</b>\n\n"
        f"{current}\n\n"
        "Отправьте весь новый список одним сообщением в формате:\n"
        "<code>Квартира=43000, Транспорт=5000, Питомец=4000</code>\n\n"
        "До 4 категорий. Остаток КЖ бот автоматически оставит в «Зарплате».\n"
        "Чтобы удалить все отдельные категории, отправьте: <code>нет</code>"
    )

@router.message(EditSettingsStates.life_categories)
async def save_life_categories(message: Message, state: FSMContext):
    text = message.text.strip()
    allocator = db.load_allocator(message.from_user.id)

    if text.lower() in {"нет", "none", "0"}:
        allocator.settings.life_categories = {}
    else:
        new_categories = {}
        try:
            for raw_item in text.split(","):
                name, raw_value = raw_item.split("=", 1)
                name = name.strip()
                value = parse_decimal(raw_value)
                if not name or value is None or value <= 0:
                    raise ValueError
                new_categories[name] = value
        except ValueError:
            await message.answer(
                "Не удалось разобрать список.\n\n"
                "Используйте формат:\n"
                "<code>Квартира=43000, Транспорт=5000</code>"
            )
            return

        if len(new_categories) > 4:
            await message.answer("Можно указать не больше 4 отдельных категорий.")
            return

        total = sum(new_categories.values(), Decimal("0"))
        if total > allocator.settings.critical_life:
            await message.answer(
                f"Категории дают {rub(total)}, а ваша КЖ — {rub(allocator.settings.critical_life)}.\n"
                "Сумма категорий не может быть больше КЖ."
            )
            return

        allocator.settings.life_categories = new_categories

    valid = set(allocator.settings.life_categories) | {"Зарплата"}
    allocator.state.period_life_topups = {
        name: amount
        for name, amount in allocator.state.period_life_topups.items()
        if name in valid
    }
    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer("✅ Категории обязательной жизни обновлены.", reply_markup=main_menu_keyboard())

@router.callback_query(F.data == "settings:goals")
async def edit_goal_percentages(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if not allocator.settings.goals:
        await callback.message.answer(
            "У вас пока нет отдельных категорий целей. Чтобы создать их, проще пройти настройку заново.",
            reply_markup=main_menu_keyboard(),
        )
        return

    current = "\n".join(f"• {escape(goal.name)} = {goal.percentage}%" for goal in allocator.settings.goals)
    await state.set_state(EditSettingsStates.goal_percentages)
    await callback.message.answer(
        "⭐️ <b>ПРОЦЕНТЫ ЦЕЛЕЙ</b>\n\n"
        f"{current}\n\n"
        "Отправьте новый список процентов для всех существующих целей:\n"
        "<code>Отпуск=50, Техника=30, Подарки=20</code>\n\n"
        "Сумма должна быть ровно 100%."
    )

@router.message(EditSettingsStates.goal_percentages)
async def save_goal_percentages(message: Message, state: FSMContext):
    allocator = db.load_allocator(message.from_user.id)
    try:
        entered = {}
        for raw_item in message.text.split(","):
            name, raw_value = raw_item.split("=", 1)
            name = name.strip()
            value = parse_decimal(raw_value)
            if not name or value is None or value <= 0:
                raise ValueError
            entered[name.lower()] = value
    except ValueError:
        await message.answer(
            "Не удалось разобрать проценты.\n"
            "Пример: <code>Отпуск=50, Техника=30, Подарки=20</code>"
        )
        return

    existing_names = {goal.name.lower() for goal in allocator.settings.goals}
    if set(entered) != existing_names:
        await message.answer(
            "Нужно указать все существующие цели и не добавлять новые названия."
        )
        return

    total = sum(entered.values(), Decimal("0"))
    if abs(total - Decimal("100")) > Decimal("0.0001"):
        await message.answer(f"Сейчас сумма процентов = {total}%. Нужно ровно 100%.")
        return

    for goal in allocator.settings.goals:
        goal.percentage = entered[goal.name.lower()]

    db.save_allocator(message.from_user.id, allocator)
    await state.clear()
    await message.answer("✅ Проценты целей обновлены.", reply_markup=main_menu_keyboard())

@router.callback_query(F.data == "settings:c_split")
async def edit_c_split(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    await state.set_state(EditSettingsStates.c_split)
    await callback.message.answer(
        "⚖️ <b>ЭТАП C: ЦЕЛИ / ПОДУШКА</b>\n\n"
        f"Сейчас: ⭐️ цели {allocator.settings.goals_share_c}% / 🛟 подушка {allocator.settings.pillow_share_c}%\n\n"
        "Введите два числа через запятую. Например: <code>60,40</code>\n"
        "Первое — цели, второе — Подушка. В сумме должно быть 100%."
    )

@router.message(EditSettingsStates.c_split)
async def save_c_split(message: Message, state: FSMContext):
    parts = [part.strip() for part in message.text.split(",")]
    if len(parts) != 2:
        await message.answer("Введите два процента через запятую, например: 60,40")
        return

    first = parse_decimal(parts[0])
    second = parse_decimal(parts[1])

    if (
        first is None or second is None
        or first < 0 or second < 0
        or abs(first + second - Decimal("100")) > Decimal("0.0001")
    ):
        await message.answer("Проценты должны быть неотрицательными и в сумме давать 100%.")
        return

    allocator = db.load_allocator(message.from_user.id)
    allocator.settings.goals_share_c = first
    allocator.settings.pillow_share_c = second
    db.save_allocator(message.from_user.id, allocator)

    await state.clear()
    await message.answer(f"✅ Этап C обновлён: ⭐️ {first}% / 🛟 {second}%", reply_markup=main_menu_keyboard())
