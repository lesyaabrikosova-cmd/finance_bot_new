import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
_DATA=tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR']=_DATA.name
from forecast import render_forecast, save_available_forecast, forecast_allocation_text, parse_decimal
from financial_engine import FinancialAllocator, Goal, UserSettings, goal_display_name

class ForecastTextTests(unittest.TestCase):
    def test_groups_omit_zero_and_household_details(self):
        source=SimpleNamespace(settings=SimpleNamespace(goals=[Goal('Продвижение',D(100),position_type='chest')]))
        text=forecast_allocation_text(source,{'Цели:Продвижение':D(20),'КЖ:Дом':D(30),'БР:Еда':D(10),
            'Бытовой резерв':D(10),'Инвестиции':D(40),'Подушка':D(0)})
        self.assertEqual(text,'📈 Инвестиции — 40 ₽\n\n❤️ Дом — 30 ₽\n\n💚 Бытовой резерв — 10 ₽\n\n🧳 Сундук Продвижения — 20 ₽')
    def test_chest_names_are_idempotent_and_goal_name_unchanged(self):
        for name in ['Продвижение','Сундук Продвижение','Сундук Продвижения']:
            self.assertEqual(goal_display_name(name,True),'Сундук Продвижения')
        self.assertEqual(goal_display_name('Продвижение',False),'Продвижение')
        self.assertEqual(goal_display_name('Страхование',True),'Сундук Страхования')
        self.assertIsNone(parse_decimal('NaN'))

class ForecastFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_shows_applicable_reserve_limits_for_every_profile(self):
        for profile, rhythm in (
            ('stable', 'monthly'), ('piecework', 'irregular'), ('cyclic', 'cyclic'),
        ):
            with self.subTest(profile=profile):
                source = FinancialAllocator(UserSettings(
                    has_debts=False,
                    profile_type=profile,
                    employment_type='Наёмный' if profile == 'stable' else 'Фрилансер',
                    income_rhythm=rhythm,
                    critical_life=D('100'),
                    household_reserve=D('50'),
                    average_income=D('1000'),
                    force_majeure_months=D('4'),
                    stabilizer_target_months=D('2'),
                    income_gap_months=D('1'),
                ))
                source.state.activate_budget_period(date.today())
                message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
                state = SimpleNamespace(
                    get_data=AsyncMock(return_value={
                        'forecast_user_id': 42, 'forecast_available': '1000',
                    }),
                    clear=AsyncMock(),
                )
                with patch('forecast.db') as db:
                    db.load_allocator.return_value = source
                    await render_forecast(message, state, D('1') if profile == 'cyclic' else None)
                text = message.answer.await_args.args[0]
                self.assertIn(
                    f"/ {source.settings.force_majeure_limit}"
                    .replace('.00', ''), text,
                )
                self.assertEqual('🛟 Стабилизатор' in text, profile != 'stable')
                self.assertEqual('🏦 Фонд Зарплаты' in text, profile == 'cyclic')

    async def test_entering_amount_goes_straight_to_result(self):
        message=SimpleNamespace(text='100 000',from_user=SimpleNamespace(id=42),answer=AsyncMock())
        state=AsyncMock()
        with patch('forecast.db') as db,patch('forecast.render_forecast',new=AsyncMock()) as render:
            db.load_allocator.return_value=SimpleNamespace(settings=SimpleNamespace(income_rhythm='irregular'))
            await save_available_forecast(message,state)
            render.assert_awaited_once_with(message,state,None)
            message.answer.assert_not_awaited()
            db.save_allocator.assert_not_called()
