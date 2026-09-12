"""Настройки бракетов и примеры без изменения реальных балансов."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
import re
import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from bracket_card import render_bracket_card

from financial_engine import FinancialAllocator, fmt_money
from planned_payments import refresh_planned_payment_targets
from taxes import (
    compact_income_tax_profile,
    is_income_tax_profile_label,
    refresh_planned_tax_targets,
)
from storage import db
from ui import keyboard

router = Router()
ZERO = Decimal("0")
PROFILE_NAMES = {"stable": "Стабильный", "piecework": "Сдельный", "cyclic": "Циклический"}
logger = logging.getLogger(__name__)


class BracketStates(StatesGroup):
    rates = State()
    amount = State()
    review = State()


def rates_of(allocator):
    s = allocator.settings
    return [str(x) for x in (s.bracket_a, s.bracket_b, s.bracket_c, s.bracket_d)]


def rate_text(values):
    return " / ".join(format(Decimal(x).normalize(), "f") for x in values)


def rub(value):
    return fmt_money(value) + " ₽"


def bracket_rows(allocator):
    """Данные для текста и будущей картинки; проценты вычисляет ядро."""
    mode = allocator.allocation_mode()
    labels = {"МП": "Минимальная подушка", "ФМ": "ФМ-подушка", "МР": "Фонд Зарплаты",
              "СтабД": "Стабилизатор-КМ" if mode == 4 else "Полный Стабилизатор",
              "Досрочное": "Досрочное погашение", "Инвест": "Инвестиции",
              "Цели": "Цели и Сундуки", "КМ": "Критический минимум", "БР": "Бытовой резерв"}
    titles = ("Собираем на необходимое", "Наполняем Бытовой резерв",
              "Текущая жизнь обеспечена", "Распределяем сверхдоход")
    rows = []
    for stage, title in zip("ABCD", titles):
        policy = allocator.bracket_policy(stage)
        rows.append({"stage": stage, "title": title, "rate": policy.rate,
                     "target": labels[policy.bracket_target],
                     "remainder": [labels[x] for x in policy.remainder_targets],
                     "example": [(labels[x], value) for x, value in policy.allocations(Decimal(1000)).items() if value > 0]})
    return rows


def overview_text(allocator):
    rows = bracket_rows(allocator)
    lines = ["<b>БРАКЕТЫ</b>",
             f"{PROFILE_NAMES[allocator.profile_id]} · {allocator.mode_display_name()}", "",
             "Бракет — доля, которую вы откладываете первой. Сейчас ваш приоритет — "
             f"<b>{rows[0]['target']}</b>.", ""]
    for row in rows:
        remainder = "поровну: " + " и ".join(row["remainder"]) if len(row["remainder"]) > 1 else row["remainder"][0]
        lines += [f"<b>{row['stage']} · {row['title']}</b>",
                  f"{row['rate']}% → {row['target']}", f"Остаток → {remainder}", ""]
    example = rows[0]["example"]
    lines += ["<b>Например, из 1 000 ₽ на первом шаге:</b>",
              *[f"• {rub(amount)} → {label}" for label, amount in example], "",
              "Проценты не складываются: каждый относится к своей части денег. "
              "Заполненные шаги пропускаются, а после наполнения резерва правила меняются.",
              "Пример ниже покажет, как распределится целое поступление именно у вас."]
    if allocator.allocation_mode() in {3, 4} and not (allocator.profile_id == "cyclic" and allocator.state.intercontract_reserve < allocator.intercontract_current_limit):
        lines += ["", "Для свободной части можно выбрать защиту вместе с планами или всё в защиту. "
                  "В расчёте на своей сумме можно сравнить оба варианта."]
    return "\n".join(lines)


def card_has_choice(allocator):
    return allocator.allocation_mode() in {3, 4} and not (
        allocator.profile_id == "cyclic"
        and allocator.state.intercontract_reserve < allocator.intercontract_current_limit
    )


def card_caption(allocator):
    row = bracket_rows(allocator)[2]
    lines = ["<b>Бракет — доля, которую вы откладываете первой.</b>", "",
             "Когда текущая жизнь обеспечена, по показанной схеме из 1 000 ₽ свободного обычного дохода:",
             *[f"• <b>{rub(amount)}</b> → {label}" for label, amount in row["example"]], "",
             "Пример показывает текущее правило. Если резерв заполнится, оставшиеся деньги пойдут дальше. "
             "Расчёт целого поступления — кнопка «Показать на моей сумме»."]
    if card_has_choice(allocator):
        lines += ["", "Переключение схемы здесь не меняет настройки."]
    return "\n".join(lines)


def card_keyboard(allocator):
    return keyboard([
        [("Изменить проценты", "brackets:edit")],
        [("Показать на моей сумме", "brackets:example")],
        *([[("Показать другой вариант", "brackets:card-toggle")]] if card_has_choice(allocator) else []),
        [("Текстовая версия", "brackets:text"), ("Сверхдоход — что это?", "brackets:help")],
        [("← Настройки", "brackets:cancel")],
    ])


async def show_bracket_card(message, allocator):
    variant = ""
    if card_has_choice(allocator):
        variant = ("Вариант: защита и свои планы" if allocator.settings.protective_stage_c_strategy == "balanced"
                   else "Вариант: свободная часть — в защиту")
    markup = card_keyboard(allocator)
    try:
        data = await asyncio.to_thread(render_bracket_card, PROFILE_NAMES[allocator.profile_id],
                                       allocator.mode_display_name(), bracket_rows(allocator), variant)
        await message.answer_photo(photo=BufferedInputFile(data, filename="brackets.png"),
                                   caption=card_caption(allocator), reply_markup=markup)
        if allocator.settings.developer_mode:
            await message.answer(
                "<b>🛠 ТЕКСТОВАЯ ВЕРСИЯ ДЛЯ ПРОВЕРКИ</b>\n\n"
                + overview_text(allocator),
            )
    except (OSError, ValueError, ImportError, TelegramBadRequest, TelegramNetworkError):
        logger.warning("Bracket card unavailable; using text version", exc_info=True)
        await message.answer(overview_text(allocator), reply_markup=markup)


def simulate(allocator, amount, income_type, rates=None, strategy="balanced"):
    simulated = deepcopy(allocator)
    if rates is not None:
        simulated.settings.set_brackets(*rates)
    simulated.settings.protective_stage_c_strategy = strategy
    result = simulated.process_income(amount, income_type, income_date=date.today())
    return simulated, result


def preview_text(source, simulated, result, income_type, strategy, pending):
    groups = {}
    for key, value in result.allocations.items():
        label = ("Критический минимум" if key.startswith("КЖ:") else
                 "Бытовой резерв" if key.startswith("БР:") else
                 "Цели и Сундуки" if key.startswith("Цели:") else
                 "Обязательства рабочей части" if key.startswith("Рабочие обязательства:") else key)
        groups[label] = groups.get(label, ZERO) + value
    lines = ["<b>ПРИМЕР РАСПРЕДЕЛЕНИЯ</b>",
             f"Бракеты: <b>{rate_text(rates_of(simulated))}</b>",
             f"Поступление: <b>{rub(result.income)}</b> · {escape(income_type)}",
             f"Налог по этому типу дохода: <b>{rub(result.tax)}</b>", "",
             *[f"• {escape(label)} — <b>{rub(value)}</b>" for label, value in groups.items() if value > 0], "",
             f"Уровень: {result.mode_before} → {result.mode_after}",
             "Свободная часть: " + ("защита и планы" if strategy == "balanced" else "приоритет защите") + "."]
    missing = max(
        ZERO,
        simulated.settings.total_critical_life
        - simulated.critical_life_progress
        - simulated.state.accumulated_minimum_payments,
    )
    if missing > 0:
        lines += ["", f"⚠️ После этого поступления до Критического минимума остаётся <b>{rub(missing)}</b>.",
                  "Можно уменьшить первый бракет или учесть другие ожидаемые поступления. Ставки сами не меняются."]
    if pending:
        lines += ["", f"Было: {rate_text(rates_of(source))}. Новые ставки ещё не сохранены."]
    lines += ["", "Это расчёт на сегодня, с текущими остатками и обязательствами. Деньги и история не изменены."]
    return "\n".join(lines)


def navigation(pending=False, alternative=False):
    return keyboard([
        *([[("Сохранить бракеты", "brackets:save")]] if pending else []),
        [("Изменить проценты", "brackets:edit")],
        [("Показать на моей сумме", "brackets:example")],
        *([[("Сравнить другой вариант", "brackets:toggle")]] if alternative else []),
        [("Что такое сверхдоход?", "brackets:help")],
        [("← В раздел Бракеты", "brackets:open")],
        [("← Настройки", "brackets:cancel")],
    ])


async def show_preview(message, state, user_id):
    source = db.load_allocator(user_id)
    if source is None:
        await state.clear()
        await message.answer("Сначала настройте профиль через /start.")
        return
    data = await state.get_data()
    prepared = deepcopy(source)
    refresh_planned_payment_targets(user_id, prepared, date.today(), persist=False)
    refresh_planned_tax_targets(user_id, prepared, date.today(), persist=False)
    income_type = data.get("bracket_income_type")
    types = prepared.settings.income_type_tax_rates
    if income_type is None or (types and income_type not in types):
        income_type = max(types, key=types.get) if types else "Доход"
    amount = Decimal(data.get("bracket_amount") or str(source.settings.average_income or Decimal(10000)))
    strategy = data.get("bracket_strategy", source.settings.protective_stage_c_strategy)
    rates = data.get("pending_brackets")
    try:
        simulated, result = simulate(prepared, amount, income_type, rates, strategy)
        other = "protection" if strategy == "balanced" else "balanced"
        _, alternative = simulate(prepared, amount, income_type, rates, other)
    except ValueError as error:
        await message.answer(escape(str(error)), reply_markup=navigation(bool(rates)))
        return
    await state.update_data(bracket_amount=str(amount), bracket_income_type=income_type, bracket_strategy=strategy)
    await state.set_state(BracketStates.review)
    await message.answer(preview_text(source, simulated, result, income_type, strategy, bool(rates)),
                         reply_markup=navigation(bool(rates), result.allocations != alternative.allocations))


@router.callback_query(F.data == "brackets:open")
async def open_brackets(callback, state: FSMContext):
    await callback.answer()
    await state.clear()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала настройте профиль через /start.")
        return
    await show_bracket_card(callback.message, allocator)


@router.callback_query(F.data == "brackets:card-toggle")
async def toggle_card(callback, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    data = await state.get_data()
    selected = data.get("bracket_card_strategy", allocator.settings.protective_stage_c_strategy)
    selected = "protection" if selected == "balanced" else "balanced"
    await state.update_data(bracket_card_strategy=selected)
    displayed = deepcopy(allocator)
    displayed.settings.protective_stage_c_strategy = selected
    await show_bracket_card(callback.message, displayed)


@router.callback_query(F.data == "brackets:text")
async def show_bracket_text(callback, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    data = await state.get_data()
    displayed = deepcopy(allocator)
    displayed.settings.protective_stage_c_strategy = data.get("bracket_card_strategy", allocator.settings.protective_stage_c_strategy)
    await callback.message.answer(overview_text(displayed), reply_markup=card_keyboard(displayed))


@router.callback_query(F.data == "brackets:cancel")
async def cancel_brackets(callback, state: FSMContext):
    from settings_editor import show_settings_menu
    await callback.answer()
    await state.clear()
    await show_settings_menu(callback.message, callback.from_user.id)


@router.callback_query(F.data == "brackets:edit")
async def edit_brackets(callback, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    await state.update_data(bracket_base_rates=rates_of(allocator), pending_brackets=None)
    await state.set_state(BracketStates.rates)
    await callback.message.answer(
        "<b>ВАШИ ЧЕТЫРЕ БРАКЕТА</b>\n\n"
        f"Сейчас: <b>{rate_text(rates_of(allocator))}</b>.\n\n"
        "Введите четыре целых процента по порядку: A / B / C / сверхдоход.\n"
        "Например: <code>10 / 10 / 15 / 20</code>.\n\n"
        "Соседние проценты могут совпадать. Следующий должен быть равен предыдущему или выше. "
        "Можно начать с 0%. Первые два — меньше 100%, чтобы оставались деньги на жизнь.\n\n"
        "Сначала покажем расчёт. Сохранённые ставки применятся только к будущим распределениям.",
        reply_markup=keyboard([[("Отмена", "brackets:open")]]))


@router.message(BracketStates.rates)
async def receive_rates(message, state: FSMContext):
    values = re.split(r"[\s/,;]+", (message.text or "").strip())
    allocator = db.load_allocator(message.from_user.id)
    if allocator is None:
        await state.clear()
        return
    try:
        if len(values) != 4:
            raise ValueError("Нужны четыре процента. Например: 10 / 10 / 15 / 20.")
        candidate = deepcopy(allocator)
        candidate.settings.set_brackets(*values)
    except ValueError as error:
        await message.answer(escape(str(error)))
        return
    await state.update_data(pending_brackets=rates_of(candidate))
    await show_preview(message, state, message.from_user.id)


@router.callback_query(F.data == "brackets:example")
async def choose_example_type(callback, state: FSMContext):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    known_profiles = {
        compact_income_tax_profile(name)
        for name in allocator.settings.income_tax_profiles
    }
    names = [
        name for name in allocator.settings.income_type_tax_rates
        if not is_income_tax_profile_label(name, known_profiles)
    ] or ["Доход"]
    await state.update_data(bracket_type_choices=names)
    await callback.message.answer("<b>КАКОЕ ПОСТУПЛЕНИЕ ПОСЧИТАТЬ?</b>\nТип дохода определяет налог в примере.",
                                  reply_markup=keyboard([[(name, f"brackets:type:{i}")] for i, name in enumerate(names)] + [[("Отмена", "brackets:open")]]))


@router.callback_query(F.data.startswith("brackets:type:"))
async def select_example_type(callback, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    try:
        index = int(callback.data.rsplit(":", 1)[1])
        if index < 0:
            raise IndexError
        name = data["bracket_type_choices"][index]
    except (KeyError, IndexError, ValueError):
        await callback.message.answer("Этот выбор устарел. Откройте пример заново.")
        return
    await state.update_data(bracket_income_type=name)
    await state.set_state(BracketStates.amount)
    await callback.message.answer("Введите сумму поступления <b>до налога</b> в рублях. Например: <code>50000</code>.",
                                  reply_markup=keyboard([[("Отмена", "brackets:open")]]))


@router.message(BracketStates.amount)
async def receive_amount(message, state: FSMContext):
    try:
        value = Decimal((message.text or "").replace(" ", "").replace("\u00a0", "").replace(",", "."))
        if not value.is_finite() or not ZERO < value <= Decimal("1000000000000") or value != value.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите положительную сумму до 1 трлн ₽, не больше двух знаков после запятой.")
        return
    await state.update_data(bracket_amount=str(value))
    await show_preview(message, state, message.from_user.id)


@router.callback_query(BracketStates.review, F.data == "brackets:toggle")
async def toggle_example(callback, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(bracket_strategy="protection" if data.get("bracket_strategy", "balanced") == "balanced" else "balanced")
    await show_preview(callback.message, state, callback.from_user.id)


@router.callback_query(BracketStates.review, F.data == "brackets:save")
async def save_brackets(callback, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or not data.get("pending_brackets"):
        await callback.message.answer("Нет новых ставок для сохранения.")
        return
    if rates_of(allocator) != data.get("bracket_base_rates"):
        await state.clear()
        await callback.message.answer("Ставки уже изменились. Откройте раздел и проверьте их заново.", reply_markup=navigation())
        return
    allocator.settings.set_brackets(*data["pending_brackets"])
    db.save_allocator(callback.from_user.id, allocator)
    await state.clear()
    await callback.message.answer(f"<b>Бракеты сохранены: {rate_text(rates_of(allocator))}</b>\n"
                                  "Они применятся к следующим распределениям. Уже распределённые деньги не изменились.")
    await show_bracket_card(callback.message, allocator)


@router.callback_query(F.data == "brackets:help")
async def explain_super_income(callback, state: FSMContext):
    await callback.answer()
    a = db.load_allocator(callback.from_user.id)
    if a is None:
        return
    cyclic = a.profile_id == "cyclic"
    limit = a.settings.cycle_regular_income_limit if cyclic else a.settings.average_income
    used = a.state.cycle_income if cyclic else a.state.period_income
    text = ("<b>ЧТО СЧИТАЕТСЯ СВЕРХДОХОДОМ?</b>\n\n"
            f"Обычная база за {'полный цикл' if cyclic else 'расчётный период'} — <b>{rub(limit)}</b>.\n"
            f"Уже учтено поступлений — <b>{rub(used)}</b>.\n"
            f"До превышения базы — <b>{rub(max(ZERO, limit - used))}</b>.\n\n"
            "Сравниваются суммы до налога. Превышение — сверхдоход. Но если на текущую жизнь ещё не хватает, "
            "он тоже участвует в её обеспечении. Оставшаяся часть распределяется по четвёртому бракету.")
    if limit <= 0:
        text = "<b>ОБЫЧНАЯ БАЗА ПОКА НЕ ЗАДАНА</b>\n\nУкажите средний доход в настройках. Пока поступления считаются обычным доходом."
    await callback.message.answer(text, reply_markup=keyboard([[("← Назад", "brackets:back")]]))


@router.callback_query(F.data == "brackets:back")
async def back_from_help(callback, state: FSMContext):
    data = await state.get_data()
    if data.get("bracket_amount"):
        await callback.answer()
        await show_preview(callback.message, state, callback.from_user.id)
    else:
        await open_brackets(callback, state)
