import unittest
from decimal import Decimal

from financial_engine import (
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    UserSettings,
    money,
)


D = Decimal


class ModeTransitionTests(unittest.TestCase):
    def make_allocator(
        self,
        *,
        employment="Фрилансер",
        minimum="100",
        force="200",
        stabilizer="0",
        debt=None,
        life="2000",
    ):
        credits = []
        if debt is not None:
            credits.append(
                Credit(
                    name="Долг",
                    principal_balance=D(debt),
                    full_repayment_amount=None,
                    annual_rate=D("10"),
                    minimum_payment=D("10"),
                )
            )

        settings = UserSettings(
            has_debts=bool(credits),
            employment_type=employment,
            critical_life=D("1000"),
            household_reserve=D("1000"),
            average_income=D("1000"),
            minimum_reserve_months=D("0.1"),
            force_majeure_months=D("0.2"),
            bracket_a=D("20"),
            bracket_b=D("25"),
            bracket_c=D("30"),
            bracket_d=D("40"),
            bracket_e=D("40"),
            goals_share_c=D("50"),
            pillow_share_c=D("50"),
            goals=[Goal("Цель", D("100"))],
            credits=credits,
        )
        state = AllocatorState(
            life_balance=D(life),
            pillow_minimum=D(minimum),
            pillow_force_majeure=D(force),
            pillow_stabilizer=D(stabilizer),
            goal_balances={"Цель": D("0")},
        )
        return FinancialAllocator(settings, state)

    def assert_money(self, actual, expected):
        self.assertEqual(money(actual), D(expected))

    def test_mode_1_to_2_keeps_stage_c(self):
        allocator = self.make_allocator(
            minimum="0",
            force="0",
            debt="1000",
        )

        result = allocator.process_income(D("500"), "Тест")

        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Досрочное"], "400.00")

    def test_mode_2_can_continue_through_later_modes(self):
        allocator = self.make_allocator(
            force="0",
            debt="100",
        )

        result = allocator.process_income(D("2000"), "Тест")

        self.assert_money(result.allocations["Досрочное"], "100.00")
        self.assert_money(result.allocations["Подушка"], "1620.00")
        self.assert_money(result.allocations["Инвестиции"], "280.00")
        self.assertNotIn("Цели:Цель", result.allocations)
        self.assert_money(result.super_income_part, "1000.00")

    def test_mode_3_to_4_keeps_stage_c(self):
        allocator = self.make_allocator(force="100")

        result = allocator.process_income(D("1500"), "Тест")

        self.assert_money(result.allocations["Подушка"], "1340.00")
        self.assert_money(result.allocations["Инвестиции"], "160.00")
        self.assertNotIn("Цели:Цель", result.allocations)
        self.assert_money(result.super_income_part, "500.00")

    def test_average_income_splits_current_receipt_by_period_total(self):
        allocator = self.make_allocator(employment="Наёмный", life="2000")
        allocator.state.pillow_force_majeure = D("200")
        allocator.state.period_income = D("800")
        result = allocator.process_income(D("500"), "Тест")
        self.assert_money(result.regular_income_part, "200.00")
        self.assert_money(result.super_income_part, "300.00")
        self.assert_money(result.allocations["Инвестиции"], "180.00")
        self.assert_money(result.allocations["Цели:Цель"], "320.00")

    def test_cyclic_employee_gets_multi_month_stabilizer(self):
        allocator = self.make_allocator(employment="Наёмный")
        allocator.settings.income_rhythm = "cyclic"
        allocator.settings.income_gap_months = D("3")
        self.assertTrue(allocator.settings.needs_stabilizer)
        self.assertEqual(allocator.settings.stabilizer_life_limit, D("3000"))
        self.assertEqual(allocator.settings.stabilizer_full_limit, D("6000"))

    def test_mode_3_to_maximum_for_employee_keeps_stage_c(self):
        allocator = self.make_allocator(
            employment="Наёмный",
            force="100",
        )

        result = allocator.process_income(D("1000"), "Тест")

        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Инвестиции"], "270.00")
        self.assert_money(result.allocations["Цели:Цель"], "630.00")

    def test_mode_4_to_5_uses_30_35_35_for_remainder(self):
        allocator = self.make_allocator(stabilizer="900")

        result = allocator.process_income(D("1000"), "Тест")

        self.assert_money(result.allocations["Подушка"], "415.00")
        self.assert_money(result.allocations["Инвестиции"], "270.00")
        self.assert_money(result.allocations["Цели:Цель"], "315.00")

    def test_mode_5_to_maximum_keeps_stage_c(self):
        allocator = self.make_allocator(stabilizer="1900")

        result = allocator.process_income(D("1000"), "Тест")

        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Инвестиции"], "300.00")
        self.assert_money(result.allocations["Цели:Цель"], "600.00")

    def test_stage_a_is_repeated_after_mode_change(self):
        allocator = self.make_allocator(
            minimum="90",
            debt="1000",
            life="0",
        )
        allocations = {
            "Подушка": D("0"),
            "Инвестиции": D("0"),
            "Досрочное": D("0"),
            "Бытовой резерв": D("0"),
        }
        steps = []

        remainder = allocator.run_stage_with_mode_transitions(
            D("100"),
            lambda amount, mode: allocator.stage_a(
                amount,
                mode,
                steps,
                allocations,
            ),
        )

        self.assert_money(remainder, "0.00")
        self.assert_money(allocations["Подушка"], "10.00")
        self.assert_money(allocations["Досрочное"], "10.00")
        self.assert_money(
            allocator.state.life_balance
            + allocator.state.accumulated_minimum_payments,
            "80.00",
        )

    def test_stage_b_is_repeated_after_mode_change(self):
        allocator = self.make_allocator(
            stabilizer="990",
            life="1900",
        )
        allocations = {
            "Подушка": D("0"),
            "Инвестиции": D("0"),
            "Досрочное": D("0"),
            "Бытовой резерв": D("0"),
        }
        steps = []

        remainder = allocator.run_stage_with_mode_transitions(
            D("133.3333333333333333333333333333333333333"),
            lambda amount, mode: allocator.stage_b(
                amount,
                mode,
                steps,
                allocations,
            ),
        )

        self.assert_money(remainder, "0.00")
        self.assert_money(allocations["Подушка"], "10.00")
        self.assert_money(allocations["Бытовой резерв"], "100.00")
        self.assert_money(allocations["Инвестиции"], "23.33")

    def test_super_income_is_repeated_after_mode_change(self):
        allocator = self.make_allocator(stabilizer="900")
        allocations = {
            "Подушка": D("0"),
            "Инвестиции": D("0"),
            "Досрочное": D("0"),
            "Бытовой резерв": D("0"),
        }

        remainder = allocator.run_stage_with_mode_transitions(
            D("1000"),
            lambda amount, mode: allocator.super_income(
                amount,
                mode,
                allocations,
            ),
        )

        self.assert_money(remainder, "0.00")
        self.assert_money(allocations["Подушка"], "640.00")
        self.assert_money(allocations["Инвестиции"], "360.00")


if __name__ == "__main__":
    unittest.main()
