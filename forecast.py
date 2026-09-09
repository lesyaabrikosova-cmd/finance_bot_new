from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from dashboard import send_text_with_image
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from financial_engine import FinancialAllocator, MODE_NAMES, fmt_money, goal_display_name
from storage import db
from ui import keyboard, main_menu_keyboard


router = Router()


class ForecastStates(StatesGroup):
    available_before_purchases = State()
    gap_months = State()


def parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    raw = text.replace("₽", "").replace(" ", "").replace("\u00a0", "")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        value = Decimal(raw)
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def rub(value: Decimal) -> str:
    return f"{fmt_money(value)} ₽"


@router.callback_query(F.data == "menu:forecast")
async def start_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте финансовый профиль.")
        return
    await state.clear()
    await state.set_state(ForecastStates.available_before_purchases)
    await state.update_data(forecast_user_id=callback.from_user.id)
    await send_text_with_image(
        callback.message,
        "<b>ПРОГНОЗ РАСПРЕДЕЛЕНИЯ</b>\n\n"
        "Хотите проверить, как Аллокатор распределил бы ваш будущий доход прямо сейчас?\n"
        "Экспериментируйте сколько угодно, балансы не изменятся.\n"
        "——————\n<b>→ Введите сумму после налога.</b>",
        Path(__file__).resolve().parent / "assets/menu/distribution_forecast.png",
        reply_markup=keyboard([[("Отмена", "forecast:cancel")]]),
    )


@router.message(ForecastStates.available_before_purchases)
async def save_available_forecast(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму.")
        return
    await state.update_data(forecast_available=str(value), forecast_user_id=message.from_user.id)
    allocator = db.load_allocator(message.from_user.id)
    if allocator is None:
        await message.answer("Сначала настройте финансовый профиль.")
        return
    if allocator.settings.income_rhythm != "cyclic":
        await render_forecast(message, state, None)
        return
    months = allocator.settings.income_gap_months
    await state.set_state(ForecastStates.gap_months)
    await message.answer(
        "<b>СКОЛЬКО МЕСЯЦЕВ НУЖНО БУДЕТ ЖИТЬ ДО СЛЕДУЮЩЕГО ДОХОДА?</b>\n\n"
        f"В профиле указано: <b>{months} мес.</b>",
        reply_markup=keyboard([
            [(f"Оставить {months} мес.", "forecastmonths:profile")],
            [("Указать другое значение", "forecastmonths:custom")],
            [("Отмена", "forecast:cancel")],
        ]),
    )


@router.callback_query(ForecastStates.gap_months, F.data.startswith("forecastmonths:"))
async def choose_forecast_months(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data.endswith(":custom"):
        await callback.message.answer("Введите количество месяцев от 1 до 24.")
        return
    allocator = db.load_allocator(callback.from_user.id)
    await render_forecast(callback.message, state, allocator.settings.income_gap_months)


@router.message(ForecastStates.gap_months)
async def save_custom_forecast_months(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value < 1 or value > 24:
        await message.answer("Введите количество месяцев от 1 до 24.")
        return
    await render_forecast(message, state, value)


def forecast_allocation_text(source, allocations):
    groups = [[], [], [], [], []]
    known = set()
    def add(group, key, label):
        known.add(key)
        amount = Decimal(allocations.get(key, 0))
        if amount > 0:
            groups[group].append(f"{label} — {rub(amount)}")
    for key, label in [("Подушка", "🛡️ Подушка"), ("Стабилизатор дохода", "🛟 Стабилизатор"),
                       ("Инвестиции", "📈 Инвестиции"), ("Фонд Зарплаты", "🏦 Фонд Зарплаты")]:
        add(0, key, label)
    for key in allocations:
        if key.startswith("КЖ:"):
            add(1, key, f"❤️ {escape(key[3:])}")
    add(2, "Бытовой резерв", "💚 Бытовой резерв")
    goals = {g.name: g for g in source.settings.goals}
    for key in allocations:
        if key.startswith("Цели:"):
            name = key[5:]
            chest = bool(goals.get(name) and goals[name].is_chest)
            add(3, key, ('🧳 ' if chest else '⭐️ ') + escape(goal_display_name(name, chest)))
    for key in allocations:
        if key not in known and not key.startswith("БР:"):
            add(4, key, '💳 ' + escape(key.replace(':', ' · ')))
    return "\n\n".join("\n".join(group) for group in groups if group) or "Нет свободной суммы для распределения"


async def render_forecast(message: Message, state: FSMContext, months: Decimal | None):
    data = await state.get_data()
    source = db.load_allocator(data.get("forecast_user_id", message.from_user.id))
    if source is None:
        return
    available = Decimal(data["forecast_available"])
    if source.settings.income_rhythm == "cyclic":
        simulated, result, obligations, distributable = simulate_cyclic_forecast(
            source, available, Decimal("0"), Decimal(months)
        )
    else:
        simulated, result, obligations, distributable = simulate_standard_forecast(
            source, available, Decimal("0")
        )
    lines = ["<b>ПРОГНОЗ РАСПРЕДЕЛЕНИЯ</b>", "",
             f"Ожидаемая сумма — <b>{rub(available)}</b>"]
    if source.profile_id == "cyclic":
        lines.extend([f"Обязательства на время контракта — <b>{rub(obligations)}</b>",
                      f"К распределению после возвращения — <b>{rub(distributable)}</b>",
                      f"Период без дохода — <b>{months} мес.</b>"])
    lines.extend(["", "<b>ПРЕДПОЛАГАЕМОЕ РАСПРЕДЕЛЕНИЕ</b>", "",
                  "<blockquote>" + forecast_allocation_text(source, result.allocations if result else {}) + "</blockquote>"])
    critical = max(Decimal("0"), simulated.settings.critical_life - simulated.state.life_balance)
    sustainable = max(Decimal("0"), simulated.settings.household_life - simulated.state.life_balance)
    lines.extend(["", f"До Критического Минимума — {rub(critical)}",
                  f"До Устойчивой Жизни — {rub(sustainable)}", "",
                  f"🛡️ Подушка — <b>{rub(simulated.state.pillow_balance)}</b> / {rub(simulated.settings.force_majeure_limit)}"])
    if simulated.settings.needs_stabilizer:
        lines.append(f"🛟 Стабилизатор — <b>{rub(simulated.state.stabilizer_balance)}</b> / {rub(simulated.settings.stabilizer_full_limit)}")
    if simulated.profile_id == "cyclic":
        lines.append(f"🏦 Фонд Зарплаты — <b>{rub(simulated.state.intercontract_reserve)}</b> / {rub(simulated.settings.intercontract_full_limit)}")
    lines.extend(["", "<b>ПРЕДПОЛАГАЕМЫЙ УРОВЕНЬ</b>",
                  f"{'🏆' * simulated.active_mode()} <b>{escape(simulated.mode_display_name())}</b>.",
                  escape(simulated.mode_title())])
    await state.clear()
    await message.answer(
        "\n".join(lines),
        reply_markup=keyboard([
            [("Повторить прогноз", "menu:forecast")],
            [("Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "forecast:cancel")
async def cancel_forecast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("Прогноз отменён.", reply_markup=main_menu_keyboard(callback.from_user.id))


def simulate_cyclic_forecast(
    source: FinancialAllocator,
    available: Decimal,
    purchases: Decimal,
    months: Decimal,
):
    """Рассчитать прогноз на копии профиля, не меняя реальные балансы."""
    available = Decimal(available)
    purchases = Decimal(purchases)
    months = Decimal(months)
    if source.settings.income_rhythm != "cyclic":
        raise ValueError("Прогноз доступен только для циклического профиля.")
    if available < 0 or purchases < 0 or purchases > available:
        raise ValueError("Некорректные суммы прогноза.")
    if months < 1 or months > 24:
        raise ValueError("Период прогноза должен быть от 1 до 24 месяцев.")

    obligations = source.settings.contract_obligations_total
    distributable = max(Decimal("0"), available - purchases - obligations)
    simulated = deepcopy(source)
    simulated.settings.income_gap_months = months

    # Доход ожидается к окончанию рабочей части. Текущий рабочий месяц не должен
    # повторно забирать деньги на российскую жизнь: её плановая нехватка уже
    # целиком представлена Фондом Зарплаты.
    simulated.state.life_balance = simulated.settings.household_life
    simulated.state.period_income = Decimal("0")
    simulated.state.period_allocations = {}
    simulated.state.period_life_topups = {}

    result = (
        simulated.process_income(distributable, "Прогноз", tax_override=Decimal("0"))
        if distributable > 0
        else None
    )
    return simulated, result, obligations, distributable


def simulate_standard_forecast(
    source: FinancialAllocator,
    available: Decimal,
    purchases: Decimal,
):
    """Прогноз обычного поступления для стабильного или сдельного профиля."""
    available = Decimal(available)
    purchases = Decimal(purchases)
    if source.settings.income_rhythm == "cyclic":
        raise ValueError("Для циклического профиля нужен прогноз с периодом перерыва.")
    if available < 0 or purchases < 0 or purchases > available:
        raise ValueError("Некорректные суммы прогноза.")
    distributable = available - purchases
    simulated = deepcopy(source)
    result = (
        simulated.process_income(distributable, "Прогноз", tax_override=Decimal("0"))
        if distributable > 0
        else None
    )
    return simulated, result, Decimal("0"), distributable
