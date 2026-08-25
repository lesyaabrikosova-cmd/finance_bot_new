from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from storage import db


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    def normalized(text: str) -> str:
        if text == "Отмена":
            return "✖️ Отмена"
        if text == "Другое":
            return "+ Другое"
        return text

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=normalized(text),
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

    rows = [
        [("Новый доход", "menu:income")],
        [("Балансы", "menu:analytics"), ("Анализ доходов", "menu:income_analysis")],
        [("Режим", "menu:state"), ("Настройки", "settings:open")],
        [("Налоги", "menu:taxes")],
    ]

    if has_active_debts:
        rows.append([("Кредиты", "menu:credits")])

    if allocator:
        rows.append([("Прогноз распределения дохода", "menu:forecast")])
        rows.append([("Распределить текущие деньги", "firstallocation:start")])

    if allocator and allocator.settings.income_rhythm == "cyclic":
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
        else:
            rows.append([("Начать рабочую часть", "intercontract:finish")])
        rows.append([("Как работает Фонд Зарплаты", "fundsalary:help")])

    rows.extend([
        [("Новый расчетный период", "period:new")],
    ])

    return keyboard(rows)
