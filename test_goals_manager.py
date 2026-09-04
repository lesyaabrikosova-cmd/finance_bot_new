import unittest
from decimal import Decimal

from financial_engine import Goal, normalize_active_goal_percentages
from goals_manager import goal_line, icon, parse_date, parse_decimal


class GoalsManagerTests(unittest.TestCase):
    def test_input_helpers_accept_russian_money_and_dates(self):
        self.assertEqual(parse_decimal("12 500,50 ₽"), Decimal("12500.50"))
        self.assertEqual(parse_date("01.08.2027").isoformat(), "2027-08-01")

    def test_icons_follow_entity_type(self):
        self.assertEqual(icon(Goal("Отпуск", 50)), "⭐️")
        self.assertEqual(icon(Goal("Подарки", 50, position_type="chest")), "🪎")

    def test_pausing_position_preserves_it_and_rebalances_active_ones(self):
        goals = [Goal("A", 30), Goal("B", 30), Goal("C", 40)]
        goals[1].previous_percentage = goals[1].percentage
        goals[1].status = "paused"
        normalize_active_goal_percentages(goals)
        self.assertEqual(goals[1].previous_percentage, Decimal("30"))
        self.assertEqual(goals[1].status, "paused")
        self.assertEqual(
            sum((goal.percentage for goal in goals if goal.status == "active"), Decimal("0")),
            Decimal("100"),
        )

    def test_legacy_goal_without_target_can_be_rendered(self):
        class State:
            goal_balances = {"Старая": Decimal("100")}

        class Allocator:
            state = State()

        text = goal_line(Allocator(), Goal("Старая", 100))
        self.assertIn("⭐️", text)
        self.assertIn("Старая", text)


if __name__ == "__main__":
    unittest.main()
