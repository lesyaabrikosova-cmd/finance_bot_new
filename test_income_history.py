import unittest
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dashboard import (
    income_distribution_text,
    income_months_keyboard,
    income_operations_for_period,
    income_operation_card_text,
    rebuild_period_analytics_from_history,
    send_income_period_analysis,
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
        self.assertIn("09.09.2026 · Частник · 3 700", buttons)
        self.assertIn("← Назад", buttons)
        self.assertIn("← Главное меню", buttons)

    async def test_month_history_filters_operations_and_returns_to_same_month(self):
        message = SimpleNamespace(answer=AsyncMock())
        march = operation(17)
        march["payload"]["date"] = "2025-03-09"
        april = operation(18)
        april["payload"]["date"] = "2025-04-10"
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [april, march]
            await send_income_history(message, 42, year=2025, month=3)
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertIn("09.03.2025 · Частник · 3 700", [button.text for button in buttons])
        self.assertNotIn("10.04.2025 · Частник · 3 700", [button.text for button in buttons])
        self.assertIn("incomeanalysis:month:2025:3", [button.callback_data for button in buttons])

    def test_card_and_distribution_keep_note_and_group_order(self):
        item = operation()
        self.assertIn("————————————\n📝 Урок с Машей\n————————————", income_operation_card_text(item))
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))
        text = income_distribution_text(item, allocator)
        self.assertIn("<blockquote>", text)
        self.assertLess(text.index("📈"), text.index("❤️ <b>Квартира</b>"))
        self.assertLess(text.index("Квартира"), text.index("Тройка"))

    def test_deletion_rebuilds_chart_totals_from_remaining_ledger(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Квартира": Decimal("100")},
        ))
        remaining = operation()
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [remaining, {"type": "period_reset"}]
            rebuild_period_analytics_from_history(allocator, 42)
        self.assertEqual(allocator.state.period_income, Decimal("3700"))
        self.assertEqual(allocator.state.period_tax, Decimal("222"))
        self.assertEqual(allocator.state.period_allocations["Инвестиции"], Decimal("740"))

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

    def test_calendar_period_filter_handles_month_year_and_empty_periods(self):
        march = operation(1)
        march["payload"]["date"] = "2025-03-09"
        april = operation(2)
        april["payload"]["date"] = "2025-04-10"
        operations = [march, april, {"type": "period_reset"}]
        self.assertEqual(income_operations_for_period(operations, "month", 2025, 3), [march])
        self.assertEqual(income_operations_for_period(operations, "year", 2025), [march, april])
        self.assertEqual(income_operations_for_period(operations, "year", 2024), [])

    async def test_period_analysis_uses_existing_chart_for_empty_month_and_year(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={},
        ))
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "month", 2025, 3)
            await send_income_period_analysis(message, 42, "year", 2025)
        self.assertEqual(chart.await_count, 2)
        self.assertEqual(chart.await_args_list[0].args[1], {})
        self.assertEqual(chart.await_args_list[0].kwargs["subtitle"], "Источники дохода · Март 2025")
        self.assertEqual(chart.await_args_list[1].kwargs["subtitle"], "Источники дохода · 2025")

    async def test_period_analysis_groups_multiple_income_types_for_month_and_year(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={},
        ))
        march = operation(1)
        march["payload"].update(date="2025-03-09", income_type="Работа", income="1000")
        april = operation(2)
        april["payload"].update(date="2025-04-10", income_type="Подарок", income="2000")
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = [april, march]
            await send_income_period_analysis(message, 42, "month", 2025, 3)
            await send_income_period_analysis(message, 42, "year", 2025)
        self.assertEqual(chart.await_args_list[0].args[1], {"Работа": Decimal("1000")})
        self.assertEqual(chart.await_args_list[1].args[1], {"Подарок": Decimal("2000"), "Работа": Decimal("1000")})

    def test_month_navigation_keeps_selected_year_when_returning_from_march(self):
        markup = income_months_keyboard(2025)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("incomeanalysis:month:2025:3", callbacks)
        self.assertIn("incomeanalysis:months:2024", callbacks)
        self.assertIn("incomeanalysis:months:2026", callbacks)
