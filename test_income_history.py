import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dashboard import (
    income_distribution_text,
    income_operation_card_text,
    send_income_history,
)


def operation(operation_id=17):
    return {
        "id": operation_id,
        "type": "income_distribution",
        "created_at": "2026-09-10T12:00:00",
        "payload": {
            "date": "2026-09-09",
            "income_type": "Частник",
            "income": "3700",
            "tax": "222",
            "note": "Урок с Машей",
            "allocations": {
                "Инвестиции": "740",
                "КЖ:Квартира": "1400",
                "КЖ:Тройка": "60",
            },
        },
    }


class IncomeHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_lists_saved_income_with_navigation(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [operation()]
            await send_income_history(message, 42)
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("09.09 · Частник · 3 700", buttons)
        self.assertIn("← К анализу доходов", buttons)
        self.assertIn("← В главное меню", buttons)

    def test_card_and_distribution_keep_note_and_group_order(self):
        item = operation()
        self.assertIn("📝 Заметка — Урок с Машей", income_operation_card_text(item))
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))
        text = income_distribution_text(item, allocator)
        self.assertIn("<blockquote>", text)
        self.assertLess(text.index("📈"), text.index("❤️ <b>Квартира</b>"))
        self.assertLess(text.index("Квартира"), text.index("Тройка"))

