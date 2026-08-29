import unittest
from decimal import Decimal

from financial_engine import AllocatorState, Credit, FinancialAllocator, Goal, UserSettings
from onboarding import (
    build_contract_obligation_storage,
    build_contract_obligations,
    recalculate_cyclic_expense_rates,
)


class RevisedWaterfallTests(unittest.TestCase):
    def settings(self, profile="cyclic"):
        rhythm = "cyclic" if profile == "cyclic" else ("irregular" if profile == "piecework" else "monthly")
        return UserSettings(
            has_debts=False,
            employment_type="Наёмный" if profile == "stable" else "Фрилансер",
            profile_type=profile,
            income_rhythm=rhythm,
            income_work_months=Decimal("5"),
            income_gap_months=Decimal("7"),
            critical_life=Decimal("100"),
            household_reserve=Decimal("20"),
            average_income=Decimal("100"),
            minimum_reserve_months=Decimal("2" if profile != "stable" else "1"),
            force_majeure_months=Decimal("4"),
            stabilizer_target_months=Decimal("1"),
        )

    def test_existing_force_majeure_balance_counts_as_minimum_pillow_after_new_debt(self):
        settings = self.settings("cyclic")
        settings.credits.append(Credit("Новый долг", Decimal("100"), None, Decimal("0"), Decimal("0")))
        allocator = FinancialAllocator(settings, AllocatorState(pillow_force_majeure=Decimal("340")))
        self.assertEqual(allocator.active_mode(), 2)

    def test_cyclic_returns_to_force_majeure_mode_even_when_stabilizer_is_full(self):
        settings = self.settings("cyclic")
        state = AllocatorState(
            intercontract_reserve=Decimal("840"),
            pillow_force_majeure=Decimal("100"),
            pillow_stabilizer=Decimal("240"),
        )
        allocator = FinancialAllocator(settings, state)
        self.assertEqual(allocator.active_mode(), 5)
        state.pillow_force_majeure = Decimal("400")
        self.assertEqual(allocator.active_mode(), 8)

    def test_annual_subscription_is_split_once_between_cycle_parts(self):
        data = {
            "income_work_months": "5",
            "income_gap_months": "7",
            "contract_obligation_keys": ["br:0"],
            "km_items": [],
            "br_items": [{"name": "Яндекс Диск", "amount": "1990", "months": "12", "monthly": "165.83"}],
        }
        km_items, br_items = recalculate_cyclic_expense_rates(data)
        data["km_items"], data["br_items"] = km_items, br_items
        obligations, _, total = build_contract_obligations(data)
        self.assertEqual(br_items[0]["monthly"], "165.83")
        self.assertEqual(obligations["Яндекс Диск"], "829.17")
        self.assertEqual(total, Decimal("829.17"))

    def test_annual_subscription_only_at_home_is_funded_during_home_months(self):
        data = {
            "income_work_months": "5", "income_gap_months": "7",
            "contract_obligation_keys": [], "km_items": [],
            "br_items": [{"name": "Яндекс Диск", "amount": "1990", "months": "12", "monthly": "165.83"}],
        }
        _, br_items = recalculate_cyclic_expense_rates(data)
        self.assertEqual(br_items[0]["monthly"], "284.29")

    def test_obligation_storage_uses_native_envelopes_and_optional_fund(self):
        data = {
            "contract_obligation_keys": ["km:0", "km:1", "km:2", "br:0"],
            "km_items": [
                {"name": "ЖКХ"}, {"name": "ВК Музыка"}, {"name": "Мобильная связь"},
            ],
            "br_items": [{"name": "Яндекс Диск"}],
            "km_storage_items": [
                {"storage": "separate", "envelope_name": "Недвижимость"},
                {"storage": "salary"},
                {"storage": "salary"},
            ],
        }
        self.assertEqual(build_contract_obligation_storage(data, use_fund=True), {
            "ЖКХ": "Недвижимость",
            "ВК Музыка": "Фонд Обязательств",
            "Мобильная связь": "Фонд Обязательств",
            "Яндекс Диск": "Бытовой резерв",
        })

    def test_calendar_tax_is_not_duplicated_in_work_obligations(self):
        data = {
            "income_work_months": "5",
            "income_gap_months": "7",
            "contract_obligation_keys": ["km:0"],
            "km_items": [{
                "name": "Налог за квартиру",
                "subcategory": "property_tax",
                "amount": "900",
                "months": "2",
                "monthly": "450",
            }],
            "br_items": [],
        }
        km_items, _ = recalculate_cyclic_expense_rates(data)
        data["km_items"] = km_items
        obligations, _, total = build_contract_obligations(data)
        self.assertEqual(km_items[0]["monthly"], "450")
        self.assertEqual(obligations, {})
        self.assertEqual(total, Decimal("0.00"))

    def test_each_income_can_choose_balanced_or_full_force_majeure_funding(self):
        def run(strategy: str):
            settings = UserSettings(
                has_debts=False,
                employment_type="Наёмный",
                profile_type="stable",
                income_rhythm="monthly",
                critical_life=Decimal("100"),
                household_reserve=Decimal("0"),
                average_income=Decimal("100"),
                force_majeure_months=Decimal("10"),
                goals=[Goal("Цель", Decimal("100"))],
                protective_stage_c_strategy=strategy,
            )
            allocator = FinancialAllocator(settings, AllocatorState(life_balance=Decimal("100")))
            return allocator.process_income(Decimal("100"), "Зарплата", tax_override=Decimal("0"))

        balanced = run("balanced")
        protection = run("protection")
        self.assertEqual(balanced.allocations["Подушка"], Decimal("65.00"))
        self.assertEqual(balanced.allocations["Цели:Цель"], Decimal("35.00"))
        self.assertEqual(protection.allocations["Подушка"], Decimal("100"))
        self.assertNotIn("Цели:Цель", protection.allocations)


if __name__ == "__main__":
    unittest.main()
