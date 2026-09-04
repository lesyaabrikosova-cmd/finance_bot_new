import unittest
from datetime import date
from decimal import Decimal

from financial_engine import (
    AllocatorState,
    FinancialAllocator,
    Goal,
    UserSettings,
    goal_percentage_bounds,
    normalize_active_goal_percentages,
    sequential_goal_percentages,
    update_goal_percentage,
    vacation_budget,
)


def allocator_with(goals, tax_rate=Decimal("0")):
    settings = UserSettings(
        has_debts=False,
        employment_type="Фрилансер",
        income_rhythm="irregular",
        critical_life=Decimal("80000"),
        household_reserve=Decimal("15000"),
        average_income=Decimal("180000"),
        tax_rate=tax_rate,
        goals=goals,
    )
    return FinancialAllocator(settings, AllocatorState())


class GoalMathTests(unittest.TestCase):
    def test_sequential_limits_match_five_position_example(self):
        self.assertEqual(goal_percentage_bounds([], 4), (Decimal("1"), Decimal("96")))
        self.assertEqual(goal_percentage_bounds([30], 3), (Decimal("1"), Decimal("67")))
        self.assertEqual(goal_percentage_bounds([30, 30], 2), (Decimal("1"), Decimal("38")))

    def test_last_percentage_is_automatic_remainder(self):
        self.assertEqual(
            sequential_goal_percentages([30, 30, 20, 17], 5),
            [Decimal("30"), Decimal("30"), Decimal("20"), Decimal("17"), Decimal("3")],
        )

    def test_each_position_keeps_at_least_one_percent(self):
        with self.assertRaises(ValueError):
            sequential_goal_percentages([97, 1, 1, 1], 5)
        with self.assertRaises(ValueError):
            sequential_goal_percentages([Decimal("30.5")], 2)

    def test_editing_one_share_changes_only_automatic_remainder(self):
        goals = [
            Goal("Хотелки", 30),
            Goal("Отпуск", 30),
            Goal("Подарки", 40, is_auto_percentage=True),
        ]
        update_goal_percentage(goals, 0, Decimal("27"))
        self.assertEqual([goal.percentage for goal in goals], [Decimal("27"), Decimal("30"), Decimal("43")])

    def test_pause_rebalances_remaining_positions_to_integer_hundred(self):
        goals = [
            Goal("Хотелки", 30),
            Goal("Отпуск", 30, status="paused"),
            Goal("Подарки", 40, is_auto_percentage=True),
        ]
        normalize_active_goal_percentages(goals)
        active = [goal for goal in goals if goal.status == "active"]
        self.assertEqual(sum((goal.percentage for goal in active), Decimal("0")), Decimal("100"))
        self.assertTrue(active[-1].is_auto_percentage)
        self.assertEqual(goals[1].percentage, Decimal("30"))

    def test_capacity_range_uses_zero_and_full_known_tax(self):
        allocator = allocator_with([Goal("Отпуск", 100)], tax_rate=Decimal("10"))
        capacity = allocator.estimated_goals_capacity_range()
        self.assertLess(capacity["minimum"], capacity["maximum"])
        self.assertTrue(capacity["tax_changes_range"])

    def test_goal_forecast_detects_unreachable_deadline(self):
        goal = Goal(
            "Отпуск",
            100,
            target_amount=Decimal("500000"),
            deadline="2027-01-01",
        )
        allocator = allocator_with([goal])
        forecast = allocator.goal_forecast(goal, today=date(2026, 9, 4))
        self.assertEqual(forecast["months_left"], 4)
        self.assertEqual(forecast["required_monthly"], Decimal("125000.00"))
        self.assertEqual(forecast["status"], "unreachable")

    def test_paused_position_does_not_receive_allocations(self):
        active = Goal("Отпуск", 100)
        paused = Goal("Техника", 50, status="paused")
        allocator = allocator_with([active, paused])
        self.assertEqual(allocator.settings.total_goals_percentage, Decimal("100"))
        allocations = {}
        allocator._allocate_goals(Decimal("1000"), allocations)
        self.assertEqual(allocations, {"Цели:Отпуск": Decimal("1000.00")})

    def test_money_split_never_loses_rounding_remainder(self):
        goals = [Goal("Первая", 33), Goal("Вторая", 33), Goal("Третья", 34)]
        allocator = allocator_with(goals)
        split = allocator.split_goal_amount(Decimal("100.01"))
        self.assertEqual(sum(split.values(), Decimal("0")), Decimal("100.01"))
        self.assertEqual(split["Третья"], Decimal("34.0034"))

    def test_manual_transfer_rejects_paused_position(self):
        allocator = allocator_with([
            Goal("Активная", 100),
            Goal("Пауза", 20, status="paused"),
        ])
        with self.assertRaisesRegex(ValueError, "не активна"):
            allocator.transfer_salary_remainder(Decimal("1000"), "goal:1")

    def test_vacation_calculator_adds_ten_percent_once(self):
        result = vacation_budget({
            "tickets": Decimal("50000"),
            "accommodation": Decimal("100000"),
            "unknown": Decimal("999999"),
        })
        self.assertEqual(result["subtotal"], Decimal("150000.00"))
        self.assertEqual(result["buffer"], Decimal("15000.00"))
        self.assertEqual(result["total"], Decimal("165000.00"))

    def test_completed_goal_no_longer_receives_money(self):
        active = Goal("Подарки", 100, position_type="chest")
        completed = Goal("Отпуск", 50, status="completed", target_amount=100000)
        allocator = allocator_with([active, completed])
        self.assertEqual(allocator.split_goal_amount(Decimal("1000")), {"Подарки": Decimal("1000")})


if __name__ == "__main__":
    unittest.main()
