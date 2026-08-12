from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return keyboard([
        [("Новый доход", "menu:income")],
        [("Балансы", "menu:analytics"), ("Режим", "menu:state")],
        [("Анализ доходов", "menu:income_analysis")],
        [("История", "menu:history")],
        [("Кредиты", "menu:credits"), ("Цели", "menu:goals")],
        [("Новый расчетный период", "period:new")],
        [("Настройки", "settings:open")],
        [("От разработчика", "menu:about")],
        [("Помощь", "menu:help")],
    ])
