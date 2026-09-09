from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from storage import db


def button_text(text: str) -> str:
    import re
    return re.sub(r'[\U0001F000-\U0001FAFF\u2300-\u27FF\u2B00-\u2BFF\uFE0F\u200D\u20E3]', '', text).strip() or 'Открыть'


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
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


def main_menu_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    allocator = db.load_allocator(telegram_id)
    has_active_debts = bool(
        allocator
        and any(
            credit.active
            for credit in allocator.settings.credits
        )
    )

    period_pending = bool(
        allocator
        and allocator.state.period_status in {"not_started", "scheduled"}
    )
    if period_pending:
        return keyboard([
            [("Начать первый период сейчас", "periodsetup:today")],
            [("Выбрать дату начала", "periodsetup:date")],
            [("Уровень", "menu:state"), ("⚙️ Настройки", "settings:open")],
        ])

    rows = [
        [("Новый доход", "menu:income")],
        [("Балансы", "menu:analytics"), ("Анализ доходов", "menu:income_analysis")],
        [("Налоги", "menu:taxes"), ("Долги", "menu:credits")],
        [("Прогноз", "menu:forecast"), ("Цели", "menu:goals")],
        [("Уровень", "menu:state"), ("Резервы", "menu:reserves")],
        [("⚙️ Настройки", "settings:open")],
    ]
    if allocator and db.initial_distribution_available(telegram_id):
        rows.append([("Распределить текущие деньги", "firstallocation:start")])

    if allocator and allocator.settings.income_rhythm == "cyclic":
        rows.append([("Валюты Фонда Зарплаты", "fundcurrency:menu")])
        rows.append([("Жизнь в рабочей части и перерыве", "phaselife:menu")])
        missing_phase = next(
            (
                phase for phase in ("work", "break")
                if not (
                    allocator.settings.phase_life(phase)
                    and allocator.settings.phase_life(phase).completed
                )
            ),
            None,
        )
        if missing_phase:
            label = (
                "⚠️ Заполнить рабочую жизнь"
                if missing_phase == "work"
                else "⚠️ Заполнить жизнь в перерыве"
            )
            rows.append([(label, f"phaselife:fill:{missing_phase}")])
        if not allocator.state.intercontract_break_active:
            rows.append([("Начать перерыв", "intercontract:start")])
        elif allocator.state.intercontract_months_remaining > 0:
            rows.append([("Заплатить себе из Фонда Зарплаты", "intercontract:salary")])
            rows.append([("Начать рабочую часть", "intercontract:finish")])
            rows.append([("Продлить перерыв", "intercontract:extend")])
        else:
            rows.append([("Начать рабочую часть", "intercontract:finish")])
            rows.append([("Продлить перерыв", "intercontract:extend")])
        rows.append([("Как работает Фонд Зарплаты", "fundsalary:help")])

    rows.extend([
        [("Новый расчетный период", "period:new")],
    ])

    return keyboard(rows)
