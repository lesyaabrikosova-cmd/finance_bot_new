from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from financial_engine import FinancialAllocator, MODE_NAMES, fmt_money
from storage import db
from ui import keyboard, main_menu_keyboard


router = Router()


class ForecastStates(StatesGroup):
    available_before_purchases = State()
    planned_purchases = State()
    gap_months = State()


def parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    raw = text.replace("₽", "").replace(" ", "").replace("\u00a0", "")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
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
    if allocator.settings.income_rhythm == "cyclic":
        amount_question = (
            "<b>СКОЛЬКО ДЕНЕГ ОСТАНЕТСЯ ПОСЛЕ НАЛОГОВ И ОБЯЗАТЕЛЬНОЙ ЖИЗНИ ВО ВРЕМЯ КОНТРАКТА?</b>\n\n"
            "Укажите примерный рублёвый эквивалент. Фактически обменивать валюту сейчас не требуется."
        )
    else:
        amount_question = (
            "<b>КАКУЮ СУММУ ВЫ ХОТИТЕ ПРОВЕРИТЬ?</b>\n\n"
            "Укажите сумму после налога. Прогноз покажет, как Аллокатор распределил бы её прямо сейчас."
        )
    await callback.message.answer(
        "<b>ПРОГНОЗ РАСПРЕДЕЛЕНИЯ ДОХОДА</b>\n\n"
        "Это прогноз, а не совершённое распределение. Реальные балансы не изменятся.\n\n"
        f"{amount_question}\n\n"
        "——————\n<b>→ Введите сумму.</b>",
        reply_markup=keyboard([[("Отмена", "forecast:cancel")]]),
    )


@router.message(ForecastStates.available_before_purchases)
async def save_available_forecast(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        await message.answer("Введите положительную сумму.")
        return
    await state.update_data(forecast_available=str(value))
    await state.set_state(ForecastStates.planned_purchases)
    allocator = db.load_allocator(message.from_user.id)
    if allocator.settings.income_rhythm == "cyclic":
        prompt = (
            "<b>СКОЛЬКО ИЗ ЭТОЙ СУММЫ ВЫ ПЛАНИРУЕТЕ ПОТРАТИТЬ ДО ВОЗВРАЩЕНИЯ?</b>\n\n"
            "Например: техника, одежда, косметика, подарки, развлечения и другие необязательные покупки."
        )
    else:
        prompt = (
            "<b>СКОЛЬКО ВЫ ХОТИТЕ ОСТАВИТЬ ВНЕ РАСПРЕДЕЛЕНИЯ?</b>\n\n"
            "Аллокатор рекомендует сначала распределять весь доход. Но прогноз позволяет честно проверить "
            "последствия суммы, которую вы хотите потратить заранее."
        )
    await message.answer(f"{prompt}\n\nЕсли нисколько — отправьте <code>0</code>.")


@router.message(ForecastStates.planned_purchases)
async def save_purchases_forecast(message: Message, state: FSMContext):
    value = parse_decimal(message.text)
    data = await state.get_data()
    available = Decimal(data["forecast_available"])
    if value is None or value < 0 or value > available:
        await message.answer("Введите сумму от 0 до ожидаемого остатка.")
        return
    await state.update_data(forecast_purchases=str(value))
    allocator = db.load_allocator(message.from_user.id)
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


async def render_forecast(message: Message, state: FSMContext, months: Decimal | None):
    source = db.load_allocator(message.from_user.id)
    if source is None:
        return
    data = await state.get_data()
    available = Decimal(data["forecast_available"])
    purchases = Decimal(data["forecast_purchases"])
    if source.settings.income_rhythm == "cyclic":
        simulated, result, obligations, distributable = simulate_cyclic_forecast(
            source, available, purchases, Decimal(months)
        )
    else:
        simulated, result, obligations, distributable = simulate_standard_forecast(
            source, available, purchases
        )
    shortfall = max(Decimal("0"), purchases + obligations - available)

    allocations = result.allocations if result else {}
    lines = [
        "<b>ПРОГНОЗ РАСПРЕДЕЛЕНИЯ</b>",
        "",
        "Это прогноз, а не совершённое распределение. Балансы не изменены.",
        "",
        f"Ожидаемая сумма — <b>{rub(available)}</b>",
        f"Планируемые покупки — <b>{rub(purchases)}</b>",
    ]
    if source.settings.income_rhythm == "cyclic":
        lines.extend([
            f"Обязательства на время контракта — <b>{rub(obligations)}</b>",
            f"К распределению после возвращения — <b>{rub(distributable)}</b>",
            f"Период без дохода — <b>{months} мес.</b>",
        ])
    else:
        lines.append(f"К распределению — <b>{rub(distributable)}</b>")
    if shortfall > 0:
        lines.extend([
            "",
            f"⚠️ На покупки и обязательства не хватает <b>{rub(shortfall)}</b>. "
            "При таком сценарии Аллокатору нечего направить в Фонд Зарплаты, Подушку и другие конверты.",
        ])
    lines.extend(["", "<b>ПРЕДПОЛАГАЕМОЕ РАСПРЕДЕЛЕНИЕ</b>"])
    goal_icons = {
        goal.name: ("💼 " if goal.is_chest else "⭐️ ")
        for goal in source.settings.goals
    }
    for name, amount in allocations.items():
        if Decimal(amount) > 0:
            label = name.replace("КЖ:", "").replace("Цели:", "")
            icon = goal_icons.get(label, "⭐️ ") if name.startswith("Цели:") else ""
            lines.append(f"• {icon}{escape(label)} — {rub(Decimal(amount))}")
    if not any(Decimal(amount) > 0 for amount in allocations.values()):
        lines.append("• Нет свободной суммы для распределения")
    lines.extend([
        "",
        "",
    ])
    if simulated.settings.income_rhythm == "cyclic":
        lines.append(
            f"Доход текущего цикла после прогноза — <b>{rub(simulated.state.cycle_income)}</b> "
            f"/ {rub(simulated.settings.cycle_regular_income_limit)}"
        )
        lines.append(f"Фонд Зарплаты после прогноза — <b>{rub(simulated.state.intercontract_reserve)}</b> / {rub(simulated.settings.intercontract_full_limit)}")
        lines.append(
            f"Обязательства рабочей части после прогноза — "
            f"<b>{rub(simulated.state.contract_obligations_reserve)}</b> / "
            f"{rub(simulated.settings.contract_obligations_total)}"
        )
    lines.extend([
        f"ФМ-подушка после прогноза — <b>{rub(simulated.state.pillow_force_majeure)}</b> / {rub(simulated.settings.force_majeure_limit)}",
        f"Стабилизатор после прогноза — <b>{rub(simulated.state.pillow_stabilizer)}</b> / {rub(simulated.settings.stabilizer_full_limit)}" if simulated.settings.needs_stabilizer else "",
        "",
        f"Предполагаемый режим — <b>{simulated.mode_display_name()}</b>. "
        f"{simulated.mode_title()}",
    ])
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
