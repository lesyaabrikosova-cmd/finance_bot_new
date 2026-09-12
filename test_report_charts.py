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
from taxes import collect_tax_statistics, TAX_COLORS
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
