import os
import tempfile
import unittest
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_DATA = tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR'] = _DATA.name
from charts import make_chart, chart_items, send_chart_report
from taxes import collect_tax_statistics, TAX_COLORS, tax_chart_values_and_colors
from financial_engine import FinancialAllocator, UserSettings
from PIL import Image


class ReportCharts(unittest.TestCase):
    def test_tax_history_preserves_two_categories_and_chart_colors(self):
        operation = {'payload': {'type': 'income_distribution', 'date': '2026-09-09',
                     'tax': '600', 'income_type': 'Работа',
                     'allocations': {'КЖ:Налоги': '400'},
                     'planned_tax_details': {'Налог на имущество · Квартира': '400'}}}
        with patch('taxes.db') as db:
            db.load_allocator.return_value = SimpleNamespace(settings=SimpleNamespace(planned_taxes={}))
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = [operation]
            groups, total, _ = collect_tax_statistics(1, 2026)
        self.assertEqual(total, D(1000))
        self.assertEqual(groups['Налог на имущество']['total'], D(400))
        data = make_chart({k: v['total'] for k,v in groups.items()}, 'НАЛОГИ', colors=TAX_COLORS)
        colors = {rgb for count, rgb in Image.open(BytesIO(data)).getcolors(2_000_000)}
        self.assertIn((118, 86, 216), colors)
        self.assertIn((226, 185, 59), colors)

    def test_income_tax_profiles_get_separate_purple_sectors(self):
        groups = {
            'Налог на доход': {
                'total': D('700'),
                'details': {'ИП · УСН «Доходы» · 6%': D('600'), 'Самозанятость · Физики · 3%': D('100')},
            },
            'Налог на имущество': {'total': D('50'), 'details': {}},
        }
        values, colors = tax_chart_values_and_colors(groups)
        self.assertEqual(values['ИП · УСН «Доходы» · 6%'], D('600'))
        self.assertEqual(values['Самозанятость · Физики · 3%'], D('100'))
        self.assertNotEqual(colors['ИП · УСН «Доходы» · 6%'], colors['Самозанятость · Физики · 3%'])
        self.assertEqual(colors['Налог на имущество'], TAX_COLORS['Налог на имущество'])

    def test_property_tax_objects_get_separate_yellow_sectors(self):
        groups = {
            'Налог на имущество': {
                'total': D('700'),
                'details': {'Квартира': D('400'), 'Дом': D('300')},
            },
        }
        values, colors = tax_chart_values_and_colors(groups)
        apartment = 'Налог на имущество · Квартира'
        house = 'Налог на имущество · Дом'
        self.assertEqual(values[apartment], D('400'))
        self.assertEqual(values[house], D('300'))
        self.assertNotEqual(colors[apartment], colors[house])

    def test_empty_and_many_categories(self):
        self.assertIsNone(make_chart({'Нет': 0}, 'БАЛАНСЫ'))
        values = {f'Источник {i}': D(i+1) for i in range(20)}
        items = chart_items(values)
        self.assertEqual(len(items), 8)
        self.assertEqual(sum(v for _,v in items), sum(values.values()))

    def test_actual_super_stage_excludes_money_consumed_by_required_life(self):
        def allocator():
            return FinancialAllocator(UserSettings(has_debts=False, employment_type='Фрилансер',
                profile_type='piecework', income_rhythm='irregular', critical_life=D(1000),
                household_reserve=D(1000), average_income=D(10)))
        small = allocator().process_income(D(100), 'Работа', tax_override=D(0))
        self.assertGreater(small.super_income_part, 0)
        self.assertEqual(small.super_stage_allocated, 0)
        large = allocator().process_income(D(100000), 'Работа', tax_override=D(0))
        self.assertGreater(large.super_stage_allocated, 0)
        self.assertEqual(large.total_allocated_after_tax(), D(100000))


class ReportDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_long_report_keeps_chart_and_all_text(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        text = 'Подробный отчёт ' * 100
        await send_chart_report(message, {'Работа': D(10)}, 'ДОХОДЫ', text)
        message.answer_photo.assert_awaited_once()
        self.assertEqual(message.answer.await_args.args[0], text)
