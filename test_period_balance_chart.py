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
        self.assertIn(colors['Налог'], {'#4D2A91', '#9675E5', '#D0C1F4'})
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

    def test_semantic_balance_families_use_visually_distinct_shades(self):
        from income_colors import MIN_INCOME_COLOR_DISTANCE, income_color_distance
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(10), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={}, goals=[],
            ),
        )
        _, colors = period_balance_chart(a, {
            'Фонд Зарплаты': D(10), 'Подушка': D(10),
            'Стабилизатор дохода': D(10), 'Инвестиции': D(10),
            'Мин. платеж': D(10), 'Досрочное': D(10),
            'КЖ:Квартира': D(10), 'КЖ:Еда': D(10),
            'БР:Продукты': D(10), 'Бытовой резерв': D(10),
            'Цели:Отпуск': D(10), 'Цели:Сундук': D(10),
        })
        self.assertIn(colors['Налог'], {'#4D2A91', '#9675E5', '#D0C1F4'})
        self.assertIn(colors['Фонд Зарплаты'], {'#4B515C', '#8B92A1', '#D0D4DA'})
        groups = (
            ('Подушка', 'Стабилизатор', 'Инвестиции'),
            ('Минимальные платежи по долгам', 'Досрочное погашение'),
            ('КМ · Квартира', 'КМ · Еда'),
            ('Бытовой резерв · Продукты', 'Бытовой резерв'),
        )
        for labels in groups:
            selected = [colors[label] for label in labels]
            self.assertEqual(len(selected), len(set(selected)))
            for index, color in enumerate(selected):
                for other in selected[index + 1:]:
                    self.assertGreaterEqual(
                        income_color_distance(color, other),
                        MIN_INCOME_COLOR_DISTANCE,
                    )

    def test_eight_critical_life_categories_never_leave_red_family(self):
        from income_colors import (
            MIN_INCOME_COLOR_DISTANCE,
            income_color_distance,
            oklch_family_palette,
        )
        names = ('Квартира', 'Здоровье', 'Кот', 'Тройка', 'Зарплата', 'Еда', 'Дети', 'Транспорт')
        red_family = set(oklch_family_palette(
            ('#AD7575', '#72111D', '#A02364', '#B5452F', '#E76348', '#E1A6A4', '#856374'),
            (*range(335, 360, 5), *range(0, 31, 5)),
        ))
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={name: name for name in names},
                household_reserve_category_ids={}, goals=[],
            ),
        )
        _, colors = period_balance_chart(
            a, {f'КЖ:{name}': D(10) for name in names},
        )
        selected = [colors[f'КМ · {name}'] for name in names]
        self.assertTrue(set(selected).issubset(red_family))
        self.assertEqual(len(selected), len(set(selected)))
        for index, color in enumerate(selected):
            for other in selected[index + 1:]:
                self.assertGreaterEqual(
                    income_color_distance(color, other),
                    MIN_INCOME_COLOR_DISTANCE,
                )

    def test_large_critical_life_group_has_no_repeats_or_foreign_hues(self):
        from income_colors import oklch_family_palette
        names = tuple(f'Категория {index}' for index in range(20))
        red_family = set(oklch_family_palette(
            ('#AD7575', '#72111D', '#A02364', '#B5452F', '#E76348', '#E1A6A4', '#856374'),
            (*range(335, 360, 5), *range(0, 31, 5)),
        ))
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={name: name for name in names},
                household_reserve_category_ids={}, goals=[],
            ),
        )
        _, colors = period_balance_chart(
            a, {f'КЖ:{name}': D(10) for name in names},
        )
        selected = [colors[f'КМ · {name}'] for name in names]
        self.assertEqual(len(selected), len(set(selected)))
        self.assertTrue(set(selected).issubset(red_family))
