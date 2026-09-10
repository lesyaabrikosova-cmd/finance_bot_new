import unittest
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dashboard import (
    income_distribution_text,
    income_operation_card_text,
    send_income_history,
)
from financial_engine import FinancialAllocator, UserSettings


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
        self.assertIn("————————————\n📝 Урок с Машей\n————————————", income_operation_card_text(item))
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))
        text = income_distribution_text(item, allocator)
        self.assertIn("<blockquote>", text)
        self.assertLess(text.index("📈"), text.index("❤️ <b>Квартира</b>"))
        self.assertLess(text.index("Квартира"), text.index("Тройка"))

    def test_latest_income_rollback_restores_the_exact_snapshot(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Жизнь": Decimal("100")},
        ))
        before = deepcopy(allocator.state)
        allocator.process_income(Decimal("1000"), "Подарок", tax_override=Decimal("0"))
        operation = allocator.state.operation_log[-1]
        allocator.rollback_income_operation(operation, restore_snapshot=True)
        self.assertEqual(allocator.state.period_income, before.period_income)
        self.assertEqual(allocator.state.period_tax, before.period_tax)
        self.assertEqual(allocator.state.life_balance, before.life_balance)
        self.assertEqual(allocator.state.pillow_balance, before.pillow_balance)
        self.assertEqual(allocator.state.investments, before.investments)

    def test_legacy_rollback_does_not_fail_on_a_renamed_period_key(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Жизнь": Decimal("100")},
        ))
        allocator.process_income(Decimal("1000"), "Подарок", tax_override=Decimal("0"))
        operation = deepcopy(allocator.state.operation_log[-1])
        operation.pop("state_before")
        operation.pop("credits_before")
        allocator.state.period_allocations = {}
        allocator.rollback_income_operation(operation)
        self.assertEqual(allocator.state.period_income, Decimal("0"))
