import unittest
from decimal import Decimal

from financial_engine import UserSettings
from settings_editor import set_user_critical_life


class CriticalLifeEditingTests(unittest.TestCase):
    def test_manual_edit_does_not_absorb_an_active_tax_norm(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("90450"),
            base_critical_life=Decimal("90000"),
            automatic_life_obligations={"tax:Дом": Decimal("450")},
            household_reserve=Decimal("0"),
            average_income=Decimal("100000"),
        )

        displayed = set_user_critical_life(settings, Decimal("90000"))

        self.assertEqual(settings.base_critical_life, Decimal("90000"))
        self.assertEqual(displayed, Decimal("90450.00"))
        settings.remove_automatic_life_obligation("tax:Дом")
        self.assertEqual(settings.critical_life, Decimal("90000.00"))

    def test_manual_edit_changes_the_permanent_cost_of_life(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("90000"),
            base_critical_life=Decimal("90000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("100000"),
        )

        self.assertEqual(
            set_user_critical_life(settings, Decimal("95000")),
            Decimal("95000.00"),
        )
        self.assertEqual(settings.base_critical_life, Decimal("95000"))


if __name__ == "__main__":
    unittest.main()
