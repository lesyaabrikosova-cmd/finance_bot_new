import unittest
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dashboard import (
    income_distribution_text,
    income_analysis_chart_colors,
    income_history_operations,
    income_months_keyboard,
    income_operations_for_period,
    income_operation_card_text,
    next_income_color_shade,
    rebuild_period_analytics_from_history,
    send_income_period_analysis,
    send_income_history,
)
from financial_engine import FinancialAllocator, UserSettings
from storage import deserialize_income_rhythm, serialize_income_types
from time_utils import moscow_today


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
    def test_history_is_ordered_by_income_date_not_the_later_ledger_id(self):
        newer = operation(7)
        newer["payload"]["date"] = "2026-09-10"
        forgotten_older_income = operation(8)
        forgotten_older_income["payload"]["date"] = "2026-09-02"
        with patch("dashboard.db") as db:
            # The later ledger ID simulates an older income entered today.
            db.load_operations.return_value = [forgotten_older_income, newer]
            operations = income_history_operations(42)
        self.assertEqual([item["id"] for item in operations], [7, 8])

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

    async def test_empty_past_month_and_year_show_explicit_messages_without_chart(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={},
        ))
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "month", 2025, 3)
            await send_income_period_analysis(message, 42, "year", 2025)
        chart.assert_not_awaited()
        self.assertEqual(message.answer.await_count, 2)
        self.assertIn("<b>МАРТ 2025</b>", message.answer.await_args_list[0].args[0])
        self.assertIn("В этом месяце нет записанных доходов.", message.answer.await_args_list[0].args[0])
        self.assertEqual(
            message.answer.await_args_list[1].args[0],
            "<b>2025</b>\n\nВ этом году нет записанных доходов.",
        )

    async def test_empty_future_month_explains_that_it_has_not_started(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={}, income_type_colors={},
        ))
        today = moscow_today()
        future_year = today.year + (1 if today.month == 12 else 0)
        future_month = 1 if today.month == 12 else today.month + 1
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "month", future_year, future_month)
        chart.assert_not_awaited()
        self.assertIn("Этот месяц ещё не начался.", message.answer.await_args.args[0])

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

    def test_income_type_color_is_matched_to_immutable_id_in_chart(self):
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={"Работа": "income-work"},
            income_type_labels={"income-work": "Работа"},
            income_type_colors={"income-work": "#9675E5"},
        ))
        item = operation()
        item["payload"].update(income_type="Работа", income_type_id="income-work")
        self.assertEqual(
            income_analysis_chart_colors(allocator, [item]),
            {"Работа": "#9675E5"},
        )

    def test_repeated_base_color_uses_next_free_shade(self):
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_colors={"income-one": "#9675E5"},
        ))
        self.assertEqual(
            next_income_color_shade(allocator, "income-two", 0),
            "#B59AEC",
        )

    def test_income_type_color_survives_settings_serialization(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            income_type_tax_rates={"Работа": Decimal("0")},
            income_type_ids={"Работа": "income-work"},
            income_type_colors={"income-work": "#9675E5"},
        )
        restored = deserialize_income_rhythm(serialize_income_types(settings))
        self.assertEqual(restored["income_type_colors"], {"income-work": "#9675E5"})

    def test_month_navigation_keeps_selected_year_when_returning_from_march(self):
        markup = income_months_keyboard(2025)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("incomeanalysis:month:2025:3", callbacks)
        self.assertIn("incomeanalysis:months:2024", callbacks)
        self.assertIn("incomeanalysis:months:2026", callbacks)

    def test_current_year_month_navigation_does_not_offer_a_future_year(self):
        current_year = moscow_today().year
        markup = income_months_keyboard(current_year)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(f"incomeanalysis:months:{current_year - 1}", callbacks)
        self.assertNotIn(f"incomeanalysis:months:{current_year + 1}", callbacks)

    async def test_current_year_analysis_does_not_offer_a_future_year(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={}, income_type_colors={},
        ))
        current_year = moscow_today().year
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "year", current_year)
        chart.assert_not_awaited()
        markup = message.answer.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(f"incomeanalysis:year:{current_year - 1}", callbacks)
        self.assertNotIn(f"incomeanalysis:year:{current_year + 1}", callbacks)
