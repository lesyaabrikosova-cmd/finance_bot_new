import os
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal as D
from unittest.mock import patch
_DATA = tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR'] = _DATA.name
from financial_engine import FinancialAllocator, UserSettings, Goal
from goals_manager import goal_income_preview, display_name

class GoalPreviewTests(unittest.TestCase):
    def test_preview_caps_goals_sends_overflow_to_chest_and_does_not_mutate(self):
        for profile, rhythm in [('piecework','irregular'), ('stable','monthly'), ('cyclic','cyclic')]:
            with self.subTest(profile=profile):
                a=FinancialAllocator(UserSettings(has_debts=False, employment_type='Фрилансер',
                    profile_type=profile, income_rhythm=rhythm, critical_life=D(100),
                    household_reserve=D(100), average_income=D(100000),
                    goals=[Goal('Отпуск',D(50),target_amount=D(100)),
                           Goal('Подарки',D(50),position_type='chest')]))
                original=deepcopy(a)
                with patch('goals_manager.refresh_planned_payment_targets') as payments, patch('goals_manager.refresh_planned_tax_targets') as taxes:
                    values,text=goal_income_preview(a,42)
                    self.assertFalse(payments.call_args.kwargs['persist'])
                    self.assertFalse(taxes.call_args.kwargs['persist'])
                self.assertEqual(values[display_name(a.settings.goals[0])],D(100))
                self.assertGreater(values[display_name(a.settings.goals[1])],D(100))
                self.assertEqual(a.state,original.state)
                self.assertEqual(a.settings,original.settings)
                self.assertIn('одного следующего поступления',text)
