from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from storage import db


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
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
    ]

    if has_active_debts:
        rows.append([("Кредиты", "menu:credits")])

    rows.extend([
        [("Новый расчетный период", "period:new")],
    ])

    return keyboard(rows)
