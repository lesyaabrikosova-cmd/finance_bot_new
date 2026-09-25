import unittest
from decimal import Decimal as D

from financial_engine import AllocatorState, FinancialAllocator, Goal, UserSettings


class BracketPolicyTests(unittest.TestCase):
    def allocator(self, profile="piecework", **overrides):
        values = dict(
            has_debts=False, profile_type=profile,
            employment_type="Наёмный" if profile == "stable" else "Фрилансер",
            income_rhythm={"stable": "monthly", "piecework": "irregular", "cyclic": "cyclic"}[profile],
            critical_life=D("100"), household_reserve=D("100"),
            average_income=D("1000000"), force_majeure_months=D("10"),
            stabilizer_target_months=D("2"), income_gap_months=D("6"),
            goals=[Goal("Планы", D("100"))],
        )
        values.update(overrides)
        return FinancialAllocator(UserSettings(**values), AllocatorState(life_balance=D("200")))

    def test_protective_remainder_depends_on_bracket_not_legacy_share(self):
        for mode, target in ((3, "Подушка"), (4, "Стабилизатор дохода")):
            a = self.allocator(bracket_c=D("20"), protective_stage_c_goals_share=D("1"))
            allocations = {}
            a.stage_c(D("100"), mode, [], allocations)
            self.assertEqual(allocations[target], D("60"))
            self.assertEqual(allocations["Цели:Планы"], D("40"))

    def test_penultimate_forecast_matches_distribution_even_with_protection_choice(self):
        a = self.allocator(protective_stage_c_strategy="protection", goals_share_c=D("10"), pillow_share_c=D("90"))
        a.state.pillow_force_majeure = D("1000")
        a.state.pillow_stabilizer = D("200")
        a.settings.set_brackets(10, 10, 15, 20)
        self.assertEqual(a.allocation_mode(), 5)
        self.assertEqual(a.current_stage_c_goal_share(), D("0.425"))
        allocations = {}
        a.stage_c(D("100"), 5, [], allocations)
        self.assertEqual(allocations["Стабилизатор дохода"], D("15"))
        self.assertEqual(allocations["Инвестиции"], D("42.5"))
        self.assertEqual(allocations["Цели:Планы"], D("42.5"))

    def test_maximum_uses_fourth_rate_in_every_profile_even_with_old_fifth_rate(self):
        for profile in ("stable", "piecework", "cyclic"):
            a = self.allocator(profile, bracket_e=D("40"))
            allocations = {}
            a.super_income(D("1000"), 6, allocations)
            self.assertEqual(allocations["Инвестиции"], D("350"))
            self.assertEqual(allocations["Цели:Планы"], D("650"))

    def test_same_destination_keeps_separate_bracket_and_remainder(self):
        a = self.allocator(protective_stage_c_strategy="protection")
        policy = a.bracket_policy("C", 3)
        self.assertEqual(policy.split(D("1000")), (D("300"), D("700")))
        self.assertEqual(policy.allocations(D("1000")), {"ФМ": D("1000")})

    def test_large_income_fills_multiple_reserves_without_losing_money(self):
        a = self.allocator()
        a.settings.set_brackets(10, 10, 15, 20)
        result = a.process_income(D("10000"), "Тест", tax_override=D("0"))
        self.assertEqual(a.active_mode(), 6)
        self.assertEqual(a.pillow_total_balance, D("1000"))
        self.assertEqual(a.state.pillow_stabilizer, D("400"))
        self.assertAlmostEqual(sum(result.allocations.values()), D("10000"), places=20)

    def test_zero_rates_do_not_stall_distribution(self):
        a = self.allocator()
        a.settings.set_brackets(0, 0, 0, 0)
        a.state.life_balance = D("0")
        result = a.process_income(D("10000"), "Тест", tax_override=D("0"))
        self.assertEqual(a.state.life_balance, D("200"))
        self.assertAlmostEqual(sum(result.allocations.values()), D("10000"), places=20)

    def test_invalid_edits_are_atomic(self):
        a = self.allocator()
        for values in ((20, 10, 30, 35), (100, 100, 100, 100), (-1, 10, 20, 30),
                       (10, 10, 15, 101), (10, 10, "NaN", 35), (10, 10, "abc", 35),
                       (10, 10, "15.5", 35)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                a.settings.set_brackets(*values)
            self.assertEqual(a.settings.bracket_a, D("20"))
            self.assertEqual(a.settings.bracket_d, D("35"))
        a.settings.set_brackets(10, 10, 15, 20)
        self.assertEqual(a.settings.bracket_e, D("20"))

    def test_unsafe_legacy_rate_rejected_before_division(self):
        with self.assertRaises(ValueError):
            self.allocator(bracket_a=D("100"))


if __name__ == "__main__":
    unittest.main()
