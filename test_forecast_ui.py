import os
import tempfile
import unittest
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
_DATA=tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR']=_DATA.name
from forecast import save_available_forecast, forecast_allocation_text, parse_decimal
from financial_engine import Goal, goal_display_name

class ForecastTextTests(unittest.TestCase):
    def test_groups_omit_zero_and_household_details(self):
        source=SimpleNamespace(settings=SimpleNamespace(goals=[Goal('Продвижение',D(100),position_type='chest')]))
        text=forecast_allocation_text(source,{'Цели:Продвижение':D(20),'КЖ:Дом':D(30),'БР:Еда':D(10),
            'Бытовой резерв':D(10),'Инвестиции':D(40),'Подушка':D(0)})
        self.assertEqual(text,'📈 Инвестиции — 40,00 ₽\n\n❤️ Дом — 30,00 ₽\n\n💚 Бытовой резерв — 10,00 ₽\n\n🧳 Сундук Продвижения — 20,00 ₽')
    def test_chest_names_are_idempotent_and_goal_name_unchanged(self):
        for name in ['Продвижение','Сундук Продвижение','Сундук Продвижения']:
            self.assertEqual(goal_display_name(name,True),'Сундук Продвижения')
        self.assertEqual(goal_display_name('Продвижение',False),'Продвижение')
        self.assertEqual(goal_display_name('Страхование',True),'Сундук Страхования')
        self.assertIsNone(parse_decimal('NaN'))

class ForecastFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_entering_amount_goes_straight_to_result(self):
        message=SimpleNamespace(text='100 000',from_user=SimpleNamespace(id=42),answer=AsyncMock())
        state=AsyncMock()
        with patch('forecast.db') as db,patch('forecast.render_forecast',new=AsyncMock()) as render:
            db.load_allocator.return_value=SimpleNamespace(settings=SimpleNamespace(income_rhythm='irregular'))
            await save_available_forecast(message,state)
            render.assert_awaited_once_with(message,state,None)
            message.answer.assert_not_awaited()
            db.save_allocator.assert_not_called()
