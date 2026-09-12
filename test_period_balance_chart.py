import os
import tempfile
import unittest
from decimal import Decimal as D
from types import SimpleNamespace
_DATA = tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR'] = _DATA.name
from dashboard import period_balance_chart
from charts import chart_items

class PeriodChartTests(unittest.TestCase):
    def test_only_period_flows_with_tax_and_individual_envelopes(self):
        state = SimpleNamespace(period_tax=D('3839.89'),
            period_life_topups={'Недвижимость':D('31277.19'),'Проездной':D('1388.88'),
                'Здоровье':D('4849.18'),'Питомцы':D('3636.88'),'Зарплата':D('24311.75')},
            pillow_balance=D(360560), stabilizer_balance=D(110150),goal_balances={'Отпуск':D(99999)})
        a=SimpleNamespace(state=state)
        flows={'Инвестиции':D('16365.97'),'Подушка':D(0),'Стабилизатор дохода':D(0),
               'Цели:Отпуск':D(0), 'КЖ:Недвижимость':D('31277.19'),
               'КЖ:Проездной':D('1388.88'), 'КЖ:Здоровье':D('4849.18'),
               'КЖ:Питомцы':D('3636.88'), 'КЖ:Зарплата':D('24311.75')}
        values,colors=period_balance_chart(a,flows)
        self.assertEqual(len(values),7)
        self.assertNotIn('Подушка',values)
        self.assertEqual(sum(values.values()),D('85669.74'))
        self.assertEqual(colors['Налог'],'#7656D8')
        self.assertEqual(len({colors[k] for k in values if k.startswith('КМ')}),5)

    def test_keeps_all_goals_and_every_household_sub_envelope(self):
        a=SimpleNamespace(state=SimpleNamespace(period_tax=D(0),period_life_topups={}))
        flows={f'Цели:Цель {i}':D(10) for i in range(12)}
        flows.update({'Бытовой резерв':D(100),'БР:Продукты':D(100)})
        values,colors=period_balance_chart(a,flows)
        self.assertEqual(len(chart_items(values,preserve_order=True)),14)
        self.assertEqual(sum(values.values()),D(320))
        self.assertEqual(values['Бытовой резерв'], D(100))
        self.assertEqual(values['Бытовой резерв · Продукты'], D(100))
