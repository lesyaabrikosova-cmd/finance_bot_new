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
        self.assertEqual(colors['Налог'], '#7C3AED')
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
                life_category_ids={'Квартира': 'home', 'Еда': 'food'},
                household_reserve_category_ids={},
                goals=[
                    SimpleNamespace(name='Отпуск', uid='goal-1', percentage=D(20), order_index=0,
                                    is_chest=False, is_system_chest=False),
                    SimpleNamespace(name='Хотелок', uid='chest-1', percentage=D(10), order_index=1,
                                    is_chest=True, is_system_chest=False),
                ],
            ),
        )
        _, colors = period_balance_chart(a, {
            'Фонд Зарплаты': D(10), 'Подушка': D(10),
            'Стабилизатор дохода': D(10), 'Инвестиции': D(10),
            'Мин. платеж': D(10), 'Досрочное': D(10),
            'КЖ:Квартира': D(10), 'КЖ:Еда': D(10),
            'БР:Продукты': D(10), 'Бытовой резерв': D(10),
            'Цели:Отпуск': D(10), 'Цели:Хотелок': D(10),
        })
        self.assertEqual(colors['Налог'], '#7C3AED')
        self.assertEqual(colors['Минимальные платежи по долгам'], '#A91E68')
        self.assertEqual(colors['Досрочное погашение'], '#E65C9C')
        self.assertEqual(colors['Фонд Зарплаты'], '#4B5563')
        self.assertEqual(colors['Подушка'], '#176B87')
        self.assertEqual(colors['Стабилизатор'], '#3E6FD8')
        self.assertEqual(colors['Инвестиции'], '#72B7D7')
        self.assertEqual(colors['КМ · Квартира'], '#a61e2e')
        self.assertEqual(colors['КМ · Еда'], '#d64550')
        self.assertEqual(colors['Бытовой резерв · Продукты'], '#45A86B')
        self.assertEqual(colors['Бытовой резерв'], '#45A86B')
        self.assertEqual(colors['Цели и Сундуки · Сундук Хотелок'], '#6a432f')
        self.assertEqual(colors['Цели и Сундуки · Отпуск'], '#FDE047')

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
            ['#a61e2e', '#d64550', '#c73a4a', '#f0787f', '#8e2832',
             '#e46878', '#f3a0a6', '#c55c6d'],
        )
        self.assertEqual(colors['КМ · Зарплата'], '#8e2832')
        self.assertEqual(len(selected), len(set(selected)))
        critical_labels = [label for label in period_balance_chart(
            a, {f'КЖ:{name}': D(10) for name in names},
        )[0] if label.startswith('КМ ·')]
        self.assertEqual(critical_labels[-1], 'КМ · Зарплата')

    def test_critical_life_is_sorted_by_period_topups_with_salary_last(self):
        names = ('Квартира', 'Еда', 'Транспорт', 'Зарплата')
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={name: name for name in names},
                household_reserve_category_ids={}, goals=[],
            ),
        )

        values, _ = period_balance_chart(a, {
            'КЖ:Квартира': D(100),
            'КЖ:Еда': D(500),
            'КЖ:Транспорт': D(200),
            'КЖ:Зарплата': D(1000),
        })

        self.assertEqual(
            [label for label in values if label.startswith('КМ ·')],
            ['КМ · Еда', 'КМ · Транспорт', 'КМ · Квартира', 'КМ · Зарплата'],
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
        self.assertEqual(len(set(selected)), 10)
        self.assertEqual(selected[:10], [
            '#a61e2e', '#d64550', '#c73a4a', '#f0787f', '#e46878',
            '#f3a0a6', '#c55c6d', '#e88c98', '#f6c1c8', '#8e2832',
        ])
        self.assertEqual(selected[10:], selected[:10])

    def test_chests_stay_recognisably_brown(self):
        names = ('Хотелок', 'Продвижения', 'Техники', 'Подарков')
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={},
                goals=[
                    SimpleNamespace(name=f'Сундук {name}', uid=f'chest-{index}',
                                    percentage=D(100 - index), order_index=index,
                                    is_chest=True, is_system_chest=False)
                    for index, name in enumerate(names)
                ],
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
            ['#6a432f', '#a66f45', '#7b523a', '#be8760'],
        )

    def test_large_chest_group_has_no_repeats_or_foreign_hues(self):
        names = tuple(f'Сундук {index}' for index in range(20))
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={},
                goals=[
                    SimpleNamespace(name=name, uid=f'chest-{index}', percentage=D(100 - index),
                                    order_index=index, is_chest=True, is_system_chest=False)
                    for index, name in enumerate(names)
                ],
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

        self.assertEqual(len(set(selected)), 8)
        self.assertEqual(selected[:8], [
            '#6a432f', '#a66f45', '#7b523a', '#be8760',
            '#5c392B', '#915c47', '#b47452', '#845744',
        ])
        self.assertEqual(selected[8:16], selected[:8])

    def test_goals_and_system_chest_use_approved_colours(self):
        goal_names = tuple(f'Цель {index}' for index in range(9))
        goal_settings = [
            SimpleNamespace(name=name, uid=f'goal-{index}', percentage=D(100 - index),
                            order_index=index, is_chest=False, is_system_chest=False)
            for index, name in enumerate(goal_names)
        ]
        system_chest = SimpleNamespace(
            name='Будущие покупки', uid='system-chest', is_chest=True,
            is_system_chest=True, percentage=D(1), order_index=20,
        )
        a = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={},
                goals=[*goal_settings, system_chest],
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
            ['#FDE047', '#F7C948', '#F0A929', '#E08B00', '#C77D00',
             '#FDE047', '#F7C948', '#F0A929', '#E08B00'],
        )
        self.assertEqual(
            colors['Цели и Сундуки · Сундук Будущие покупки'], '#6a432f',
        )

    def test_chests_and_goals_are_separate_blocks_sorted_by_percentage(self):
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
            'Цели и Сундуки · Сундук Техники',
            'Цели и Сундуки · Сундук Курсы',
            'Цели и Сундуки · Отпуск',
        ]
        self.assertEqual(labels[-5:], expected)
        self.assertEqual(
            [colors[label] for label in expected],
            ['#6a432f', '#a66f45', '#7b523a', '#be8760', '#FDE047'],
        )

    def test_critical_colour_stays_with_saved_identifier_when_values_change(self):
        settings = SimpleNamespace(
            life_category_ids={'А': 'id-a', 'Б': 'id-b', 'В': 'id-c'},
            household_reserve_category_ids={}, goals=[],
        )
        allocator = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=settings,
        )
        _, first = period_balance_chart(allocator, {'КЖ:А': D(1), 'КЖ:В': D(100)})
        _, second = period_balance_chart(allocator, {'КЖ:Б': D(500), 'КЖ:В': D(2)})
        self.assertEqual(first['КМ · В'], '#c73a4a')
        self.assertEqual(second['КМ · В'], '#c73a4a')

    def test_goal_colour_does_not_shift_when_an_earlier_goal_is_absent(self):
        goals = [
            SimpleNamespace(name='Первая', uid='goal-a', percentage=D(60), order_index=0,
                            is_chest=False, is_system_chest=False),
            SimpleNamespace(name='Вторая', uid='goal-b', percentage=D(40), order_index=1,
                            is_chest=False, is_system_chest=False),
        ]
        allocator = SimpleNamespace(
            state=SimpleNamespace(period_tax=D(0), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={}, household_reserve_category_ids={}, goals=goals,
            ),
        )
        _, together = period_balance_chart(
            allocator, {'Цели:Первая': D(10), 'Цели:Вторая': D(20)},
        )
        _, alone = period_balance_chart(allocator, {'Цели:Вторая': D(999)})
        self.assertEqual(together['Цели и Сундуки · Вторая'], '#F7C948')
        self.assertEqual(alone['Цели и Сундуки · Вторая'], '#F7C948')

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
