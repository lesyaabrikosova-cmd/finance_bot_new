import unittest
from decimal import Decimal
from unittest.mock import patch

from financial_engine import FinancialAllocator, PhaseLifeBudget, UserSettings
from ui import main_menu_keyboard


class PhaseLifeStageTwoTests(unittest.TestCase):
    def cyclic_allocator(self, budgets):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("40000"),
            household_reserve=Decimal("10000"),
            average_income=Decimal("70000"),
            income_rhythm="cyclic",
            profile_type="cyclic",
            income_work_months=Decimal("5"),
            income_gap_months=Decimal("7"),
            phase_life_budgets=budgets,
            force_majeure_months=Decimal("6"),
        )
        return FinancialAllocator(settings=settings)

    def button_texts(self, markup):
        return [button.text for row in markup.inline_keyboard for button in row]

    def test_missing_work_life_is_visible_in_main_menu(self):
        allocator = self.cyclic_allocator({
            "break": PhaseLifeBudget(critical_life="40000", completed=True),
        })
        with patch("ui.db.load_allocator", return_value=allocator):
            texts = self.button_texts(main_menu_keyboard(1))
        self.assertIn("⚠️ Заполнить рабочую жизнь", texts)

    def test_missing_life_button_disappears_when_both_are_complete(self):
        allocator = self.cyclic_allocator({
            "break": PhaseLifeBudget(critical_life="40000", completed=True),
            "work": PhaseLifeBudget(critical_life="25000", completed=True),
        })
        with patch("ui.db.load_allocator", return_value=allocator):
            texts = self.button_texts(main_menu_keyboard(1))
        self.assertNotIn("⚠️ Заполнить рабочую жизнь", texts)
        self.assertNotIn("⚠️ Заполнить жизнь в перерыве", texts)

    def test_foreign_phase_budget_keeps_native_values_and_rub_equivalent(self):
        budget = PhaseLifeBudget(
            critical_life="40000",
            household_reserve="7000",
            currency_code="INR",
            currency_symbol="₹",
            exchange_rate_to_rub="0.91",
            completed=True,
        )
        self.assertEqual(budget.critical_life, Decimal("40000"))
        self.assertEqual(budget.critical_life_rub, Decimal("36400.00"))
        self.assertEqual(budget.household_reserve_rub, Decimal("6370.00"))

    def test_first_distribution_moves_salary_fund_overflow_forward(self):
        allocator = self.cyclic_allocator({
            "break": PhaseLifeBudget(
                critical_life="40000", household_reserve="10000", completed=True
            ),
        })
        allocator.state.current_cycle_phase = "break"
        allocator.state.intercontract_break_active = True
        allocator.state.intercontract_months_remaining = Decimal("2")
        allocations = allocator.apply_first_distribution(Decimal("250000"))

        self.assertEqual(allocator.state.life_balance, Decimal("50000"))
        self.assertEqual(allocator.state.intercontract_reserve, Decimal("100000"))
        self.assertEqual(allocator.state.pillow_stabilizer, Decimal("100000"))
        self.assertEqual(allocator.state.pillow_force_majeure, Decimal("0"))
        self.assertEqual(allocations["Фонд Зарплаты"], Decimal("100000.00"))
        self.assertEqual(allocations["Стабилизатор дохода"], Decimal("100000.00"))

    def test_piecework_waterfall_fills_stabilizer_before_force_majeure(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("10000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("20000"),
            income_rhythm="irregular",
            profile_type="piecework",
            stabilizer_target_months=Decimal("1"),
            force_majeure_months=Decimal("4"),
        )
        allocator = FinancialAllocator(settings=settings)
        allocator.apply_first_distribution(Decimal("30000"))
        self.assertEqual(allocator.state.pillow_stabilizer, Decimal("10000"))
        self.assertEqual(allocator.state.pillow_force_majeure, Decimal("10000"))


if __name__ == "__main__":
    unittest.main()
