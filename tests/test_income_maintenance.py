import unittest
from datetime import date
from decimal import Decimal as D

from financial_engine import FinancialAllocator, UserSettings
from income_maintenance import (
    completed_cyclic_average,
    completed_piecework_average,
    stable_review_is_due,
)


class IncomeMaintenanceTests(unittest.TestCase):
    def make_piecework(self):
        return FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='piecework',
            employment_type='Фрилансер',
            income_rhythm='irregular',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
        ))

    def test_piecework_average_waits_for_six_full_months(self):
        source = self.make_piecework()
        self.assertIsNone(completed_piecework_average(
            source,
            started_on=date(2026, 4, 15),
            today=date(2026, 10, 1),
        ))

    def test_piecework_average_rolls_over_last_six_completed_months(self):
        source = self.make_piecework()
        source.state.distribution_history = [
            {
                'type': 'income_distribution',
                'date': f'2026-{month:02d}-01',
                'income': D(str(amount)),
            }
            for month, amount in zip(range(3, 9), (100, 200, 300, 400, 500, 600))
        ]
        average, completed_month = completed_piecework_average(
            source,
            started_on=date(2026, 3, 1),
            today=date(2026, 9, 25),
        )
        self.assertEqual(average, D('350'))
        self.assertEqual(completed_month, '2026-08')

    def test_stable_reminder_repeats_six_months_after_latest_anchor(self):
        self.assertTrue(stable_review_is_due(
            started_on=date(2026, 1, 10),
            today=date(2026, 7, 10),
        ))

    def test_predictable_cyclic_average_uses_whole_cycles_and_zero_break_months(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='cyclic',
            employment_type='Фрилансер',
            income_rhythm='cyclic',
            income_work_months=D('3'),
            income_gap_months=D('2'),
            cyclic_income_uncertain=False,
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('6'),
        ))
        history = [
            {
                'type': 'income_distribution',
                'date': f'2026-{month:02d}-01',
                'income': D('1000'),
            }
            for month in (1, 2, 3, 6, 7, 8)
        ]
        average, completed_month, window_months = completed_cyclic_average(
            source,
            started_on=date(2026, 1, 1),
            today=date(2026, 11, 15),
            history=history,
        )
        self.assertEqual(window_months, 10)
        self.assertEqual(average, D('600'))
        self.assertEqual(completed_month, '2026-10')

    def test_cyclic_pillow_recommendation_depends_on_predictability(self):
        common = dict(
            has_debts=False,
            profile_type='cyclic',
            employment_type='Фрилансер',
            income_rhythm='cyclic',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
        )
        predictable = UserSettings(**common, cyclic_income_uncertain=False)
        uncertain = UserSettings(**common, cyclic_income_uncertain=True)
        self.assertEqual(predictable.recommended_force_majeure_minimum, D('6'))
        self.assertEqual(uncertain.recommended_force_majeure_minimum, D('9'))
        self.assertFalse(predictable.needs_stabilizer)
        self.assertFalse(uncertain.needs_stabilizer)
        self.assertFalse(stable_review_is_due(
            started_on=date(2026, 1, 10),
            today=date(2026, 9, 25),
            last_reminded_on=date(2026, 7, 10),
        ))
        self.assertTrue(stable_review_is_due(
            started_on=date(2026, 1, 10),
            today=date(2027, 1, 10),
            last_reminded_on=date(2026, 7, 10),
        ))


if __name__ == '__main__':
    unittest.main()
