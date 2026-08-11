from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return keyboard([
        [("💰 Добавить доход", "menu:income")],
        [("📊 Моя финансовая картина", "menu:analytics"), ("🧭 Мой режим", "menu:state")],
        [("💳 Кредиты", "menu:credits"), ("⭐️ Цели", "menu:goals")],
        [("📅 Новый расчётный период", "period:new")],
        [("⚙️ Настройки", "settings:open")],
        [("✨ Почему это работает", "menu:about")],
        [("❓ Помощь", "menu:help")],
    ])
