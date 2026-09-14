import os
import tempfile
import unittest
from decimal import Decimal as D
from types import SimpleNamespace
_DATA = tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR'] = _DATA.name
from dashboard import period_balance_chart, period_balance_legend_labels
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
        self.assertEqual(colors['Налог'], '#4B0082')
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

    def test_balance_chart_uses_approved_fixed_colours(self):
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
        self.assertEqual(colors['Налог'], '#4B0082')
        self.assertEqual(colors['Фонд Зарплаты'], '#393939')
        self.assertEqual(colors['Подушка'], '#008080')
        self.assertEqual(colors['Стабилизатор'], '#000080')
        self.assertEqual(colors['Инвестиции'], '#006400')
        self.assertEqual(colors['КМ · Квартира'], '#DC143C')
        self.assertEqual(colors['КМ · Еда'], '#CD5C5C')
        self.assertEqual(colors['Бытовой резерв · Продукты'], '#9ACD32')
        self.assertEqual(colors['Бытовой резерв'], '#9ACD32')
        self.assertEqual(colors['Цели и Сундуки · Отпуск'], '#FFB02E')
        self.assertEqual(colors['Цели и Сундуки · Сундук'], '#FFF44F')

    def test_eight_critical_life_categories_never_leave_red_family(self):
        names = ('Квартира', 'Здоровье', 'Кот', 'Тройка', 'Зарплата', 'Еда', 'Дети', 'Транспорт')
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
        self.assertEqual(
            selected,
            ['#DC143C', '#CD5C5C', '#FF0000', '#F4AFAF', '#800000',
             '#B22222', '#FA757F', '#750A2D'],
        )

    def test_large_critical_life_group_has_no_repeats_or_foreign_hues(self):
        names = tuple(f'Категория {index}' for index in range(20))
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
        self.assertEqual(selected[:8], [
            '#DC143C', '#CD5C5C', '#FF0000', '#F4AFAF', '#B22222',
            '#FA757F', '#750A2D', '#FA8072',
        ])

    def test_chests_stay_recognisably_brown(self):
        names = ('Хотелок', 'Продвижения', 'Техники', 'Подарков')
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={},
                goals=[],
            ),
        )
        allocations = {
            f'Цели:Сундук {name}': D(100 - index)
            for index, name in enumerate(names)
        }
        allocations = type(
            'Allocations', (dict,),
            {'envelope_kinds': {
                key: 'chest' for key in allocations
            }},
        )(allocations)

        _, colors = period_balance_chart(a, allocations)
        selected = [
            colors[f'Цели и Сундуки · Сундук {name}'] for name in names
        ]

        self.assertEqual(
            selected,
            ['#5B3A29', '#431804', '#8B4513', '#342018'],
        )

    def test_large_chest_group_has_no_repeats_or_foreign_hues(self):
        names = tuple(f'Сундук {index}' for index in range(20))
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={},
                goals=[],
            ),
        )
        allocations = {
            f'Цели:{name}': D(100 - index)
            for index, name in enumerate(names)
        }
        allocations = type(
            'Allocations', (dict,),
            {'envelope_kinds': {
                key: 'chest' for key in allocations
            }},
        )(allocations)

        _, colors = period_balance_chart(a, allocations)
        selected = [
            colors[f'Цели и Сундуки · {name}'] for name in names
        ]

        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(selected[:8], [
            '#5B3A29', '#431804', '#8B4513', '#342018', '#85592E',
            '#342822', '#685440', '#321414',
        ])

    def test_goals_and_system_chest_use_approved_colours(self):
        goal_names = tuple(f'Цель {index}' for index in range(9))
        system_chest = SimpleNamespace(
            name='Будущие покупки', uid='system-chest', is_chest=True,
            is_system_chest=True,
        )
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={},
                goals=[system_chest],
            ),
        )
        allocations = {
            f'Цели:{name}': D(100 - index)
            for index, name in enumerate(goal_names)
        }
        allocations['Цели:Будущие покупки'] = D(1)
        allocations = type(
            'Allocations', (dict,),
            {'envelope_kinds': {'Цели:Будущие покупки': 'chest'}},
        )(allocations)

        _, colors = period_balance_chart(a, allocations)

        self.assertEqual(
            [colors[f'Цели и Сундуки · {name}'] for name in goal_names],
            ['#FFB02E', '#FFF44F', '#D6AE01', '#FFFF99', '#E6CE2D',
             '#E28B00', '#FFEBB7', '#BAAA36', '#FFF5A5'],
        )
        self.assertEqual(
            colors['Цели и Сундуки · Сундук Будущие покупки'], '#3F2003',
        )

    def test_goals_and_chests_are_one_block_sorted_by_percentage(self):
        goals = [
            SimpleNamespace(name='Хотелок', uid='chest-1', percentage=D(30), order_index=0,
                            is_chest=True, is_system_chest=False),
            SimpleNamespace(name='Подарков', uid='chest-2', percentage=D(30), order_index=1,
                            is_chest=True, is_system_chest=False),
            SimpleNamespace(name='Отпуск', uid='goal-1', percentage=D(20), order_index=2,
                            is_chest=False, is_system_chest=False),
            SimpleNamespace(name='Техники', uid='chest-3', percentage=D(17), order_index=3,
                            is_chest=True, is_system_chest=False),
            SimpleNamespace(name='Курсы', uid='chest-4', percentage=D(3), order_index=4,
                            is_chest=True, is_system_chest=False),
        ]
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={}, goals=goals,
            ),
        )
        allocations = type(
            'Allocations', (dict,),
            {'envelope_kinds': {
                'Цели:Хотелок': 'chest', 'Цели:Подарков': 'chest',
                'Цели:Техники': 'chest', 'Цели:Курсы': 'chest',
            }},
        )({
            # Values deliberately disagree with shares: sorting must use the
            # configured percentage, not this period's contribution amount.
            'Цели:Хотелок': D(1), 'Цели:Подарков': D(99),
            'Цели:Отпуск': D(5), 'Цели:Техники': D(100), 'Цели:Курсы': D(50),
        })

        values, colors = period_balance_chart(a, allocations)
        labels = list(values)
        expected = [
            'Цели и Сундуки · Сундук Хотелок',
            'Цели и Сундуки · Сундук Подарков',
            'Цели и Сундуки · Отпуск',
            'Цели и Сундуки · Сундук Техники',
            'Цели и Сундуки · Сундук Курсы',
        ]
        self.assertEqual(labels[-5:], expected)
        self.assertEqual(
            [colors[label] for label in expected],
            ['#5B3A29', '#431804', '#FFB02E', '#8B4513', '#342018'],
        )

    def test_balance_legend_hides_only_goals_and_chests_group_prefix(self):
        labels = period_balance_legend_labels({
            'Налог': D(1),
            'Цели и Сундуки · Сундук Хотелок': D(2),
            'Цели и Сундуки · Отпуск': D(3),
        })
        self.assertEqual(labels, {
            'Цели и Сундуки · Сундук Хотелок': 'Сундук Хотелок',
            'Цели и Сундуки · Отпуск': 'Отпуск',
        })
