import os
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal


_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from financial_engine import Credit, FinancialAllocator, UserSettings  # noqa: E402
from forecast import simulate_cyclic_forecast, simulate_standard_forecast  # noqa: E402
from onboarding import (  # noqa: E402
    apply_budget_optimizer_plan,
    budget_optimizer_candidate,
    forecast_duration_range,
    forecast_snapshot_text,
    human_duration,
    simulate_priority_months,
)


class CyclicForecastTests(unittest.TestCase):
    def make_allocator(self):
        return FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("39000"),
            household_reserve=Decimal("7000"),
            average_income=Decimal("100000"),
            income_rhythm="cyclic",
            income_work_months=Decimal("5"),
            income_gap_months=Decimal("7"),
            reliable_gap_income=Decimal("0"),
            stabilizer_target_months=Decimal("2"),
            contract_obligations={"ЖКХ": Decimal("19308.35")},
            force_majeure_months=Decimal("6"),
            life_categories={"Жизнь": Decimal("46000")},
        ))

    def test_forecast_does_not_change_source(self):
        source = self.make_allocator()
        before = deepcopy(source)
        simulated, result, obligations, distributable = simulate_cyclic_forecast(
            source, Decimal("600000"), Decimal("100000"), Decimal("7")
        )
        self.assertEqual(source.state, before.state)
        self.assertEqual(source.settings, before.settings)
        self.assertIsNot(source, simulated)
        self.assertIsNotNone(result)
        self.assertEqual(obligations, Decimal("19308.35"))
        self.assertEqual(distributable, Decimal("480691.65"))

    def test_forecast_changes_gap_target_only_in_copy(self):
        source = self.make_allocator()
        simulated, _, _, _ = simulate_cyclic_forecast(
            source, Decimal("500000"), Decimal("0"), Decimal("5")
        )
        self.assertEqual(source.settings.income_gap_months, Decimal("7"))
        self.assertEqual(simulated.settings.income_gap_months, Decimal("5"))
        self.assertEqual(simulated.settings.intercontract_full_limit, Decimal("230000"))

    def test_forecast_rejects_purchases_above_available(self):
        with self.assertRaises(ValueError):
            simulate_cyclic_forecast(
                self.make_allocator(), Decimal("1000"), Decimal("1001"), Decimal("7")
            )

    def test_obligations_can_leave_nothing_for_allocation(self):
        source = self.make_allocator()
        simulated, result, obligations, distributable = simulate_cyclic_forecast(
            source, Decimal("10000"), Decimal("0"), Decimal("7")
        )
        self.assertEqual(obligations, Decimal("19308.35"))
        self.assertEqual(distributable, Decimal("0"))
        self.assertIsNone(result)
        self.assertEqual(source.state.intercontract_reserve, Decimal("0"))
        self.assertEqual(simulated.state.intercontract_reserve, Decimal("0"))

    def test_standard_profile_can_use_forecast_without_mutation(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("30000"),
            household_reserve=Decimal("5000"),
            average_income=Decimal("60000"),
            income_rhythm="monthly",
            force_majeure_months=Decimal("3"),
            life_categories={"Жизнь": Decimal("30000")},
        )
        source = FinancialAllocator(settings)
        before = deepcopy(source)
        simulated, result, obligations, distributable = simulate_standard_forecast(
            source, Decimal("60000"), Decimal("10000")
        )
        self.assertEqual(source.state, before.state)
        self.assertEqual(obligations, Decimal("0"))
        self.assertEqual(distributable, Decimal("50000"))
        self.assertIsNotNone(result)
        self.assertGreater(simulated.state.pillow_balance, Decimal("0"))

    def test_onboarding_monthly_forecast_does_not_change_source(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("30000"),
            household_reserve=Decimal("5000"),
            average_income=Decimal("80000"),
            income_rhythm="monthly",
            force_majeure_months=Decimal("3"),
            life_categories={"Жизнь": Decimal("30000")},
        ))
        before = deepcopy(source)
        months = simulate_priority_months(source, Decimal("80000"))
        self.assertIsNotNone(months)
        self.assertEqual(source.state, before.state)
        self.assertEqual(source.settings, before.settings)

    def test_debt_forecast_runs_through_minimum_pillow_to_full_repayment(self):
        source = FinancialAllocator(UserSettings(
            has_debts=True,
            employment_type="Наёмный",
            critical_life=Decimal("30000"),
            household_reserve=Decimal("5000"),
            average_income=Decimal("90000"),
            minimum_reserve_months=Decimal("1"),
            force_majeure_months=Decimal("3"),
            credits=[Credit("Кредит", Decimal("100000"), None, Decimal("10"), Decimal("10000"))],
        ))
        months = simulate_priority_months(source, Decimal("90000"), max_months=120)
        self.assertIsNotNone(months)
        self.assertGreater(months, 1)

    def test_forecast_duration_is_human_readable(self):
        self.assertEqual(human_duration(18), "1 год 6 мес.")
        self.assertEqual(forecast_duration_range(18, 20), "примерно через 1 год 6 мес. — 1 год 8 мес.")

    def test_budget_optimizer_only_selects_flexible_reserve_expenses(self):
        self.assertTrue(budget_optimizer_candidate({"category": "food", "subcategory": "delivery"}))
        self.assertTrue(budget_optimizer_candidate({"category": "transport", "subcategory": "optional_taxi"}))
        self.assertTrue(budget_optimizer_candidate({"category": "clothes"}))
        self.assertFalse(budget_optimizer_candidate({"category": "food", "subcategory": "supermarket"}))
        self.assertFalse(budget_optimizer_candidate({"category": "transport", "subcategory": "required_taxi"}))
        self.assertFalse(budget_optimizer_candidate({"category": "gifts"}))
        self.assertFalse(budget_optimizer_candidate({"category": "education", "future_goal": True}))

    def test_budget_optimizer_overwrites_plan_and_preserves_period(self):
        item = {"name": "Доставка", "amount": "12000", "monthly": "2000", "months": "6"}
        updated = apply_budget_optimizer_plan(item, Decimal("1600"))
        self.assertEqual(updated["monthly"], "1600.00")
        self.assertEqual(updated["amount"], "9600.00")
        self.assertEqual(item["monthly"], "2000")

    def test_budget_optimizer_forecast_summary_is_human_readable(self):
        self.assertEqual(
            forecast_snapshot_text({"fast": 18, "slow": 20}),
            "примерно через 1 год 6 мес. — 1 год 8 мес.",
        )


if __name__ == "__main__":
    unittest.main()
