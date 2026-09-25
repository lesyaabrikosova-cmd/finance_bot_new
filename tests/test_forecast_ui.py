import os
import tempfile
import unittest
import importlib.util
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
_DATA=tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR']=_DATA.name
from forecast import (
    _process_average_forecast_month,
    debt_forecast_text,
    financial_path_text,
    forecast_allocation_text,
    goal_completion_calendar_text,
    goal_forecast_text,
    human_path_duration,
    historical_income_tax_rate,
    investment_forecast_text,
    choose_investment_income_growth,
    last_income_distribution_strategy,
    level_forecast_text,
    parse_decimal,
    render_forecast,
    save_available_forecast,
    show_forecast_menu,
    show_financial_path,
    simulate_debt_forecast,
    simulate_financial_path,
    simulate_goal_forecast,
    simulate_investment_forecast,
    simulate_level_forecast,
    simulate_standard_forecast,
)
from financial_engine import AllocatorState, Credit, FinancialAllocator, Goal, UserSettings, goal_display_name
from financial_path_card import financial_path_items, render_financial_path_card
from storage import db

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

    def test_forecast_positions_are_sorted_like_new_income_report(self):
        goals = [
            Goal('Маленькая цель', D('20')),
            Goal('Большой сундук', D('30'), position_type='chest'),
            Goal('Средняя цель', D('50')),
        ]
        source = SimpleNamespace(
            profile_id='stable',
            settings=SimpleNamespace(
                goals=goals,
                active_goals=goals,
                needs_stabilizer=False,
                life_categories={'Связь': D('1'), 'Квартира': D('1')},
            ),
        )
        allocations = {
            'Цели:Маленькая цель': D('100'),
            'Цели:Большой сундук': D('900'),
            'Цели:Средняя цель': D('500'),
            'КЖ:Связь': D('200'),
            'КЖ:Квартира': D('800'),
            'КЖ:Зарплата': D('1200'),
            'Мин. платеж': D('300'),
            'Досрочное': D('400'),
            'Бытовой резерв': D('250'),
        }
        text = forecast_allocation_text(source, allocations, plain=True)
        self.assertLess(text.index('Большой сундук'), text.index('Средняя цель'))
        self.assertLess(text.index('Средняя цель'), text.index('Маленькая цель'))
        self.assertLess(text.index('Квартира'), text.index('Связь'))
        self.assertLess(text.index('Связь'), text.index('Зарплата'))
        self.assertLess(text.index('Минимальные платежи'), text.index('Квартира'))
        self.assertLess(text.index('Досрочное погашение'), text.index('Квартира'))

    def test_forecast_uses_same_work_obligation_label_as_new_income(self):
        source = SimpleNamespace(
            profile_id='cyclic',
            settings=SimpleNamespace(
                goals=[],
                active_goals=[],
                needs_stabilizer=False,
                life_categories={},
            ),
        )
        text = forecast_allocation_text(source, {
            'Рабочие обязательства:Рабочий конверт:ЖКХ': D('500'),
        }, plain=True)
        self.assertIn('💳 ЖКХ → Рабочий конверт — 500', text)

    def test_forecast_uses_latest_explicit_income_strategy(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
        ))
        source.state.distribution_history = [
            {
                'type': 'income_distribution',
                'distribution_strategy': 'balanced',
            },
            {
                'type': 'income_distribution',
                'distribution_strategy': 'balanced',
                'distribution_strategy_selected': True,
            },
        ]
        self.assertEqual(
            last_income_distribution_strategy(source),
            ('balanced', True),
        )
        source.state.distribution_history[-1][
            'distribution_strategy'
        ] = 'protection'
        self.assertEqual(
            last_income_distribution_strategy(source),
            ('protection', True),
        )
        source.state.distribution_history = source.state.distribution_history[:1]
        self.assertEqual(
            last_income_distribution_strategy(source),
            ('protection', False),
        )

    def test_distribution_math_applies_the_remembered_strategy(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            profile_type='stable',
            income_rhythm='monthly',
            critical_life=D('100'),
            household_reserve=D('0'),
            average_income=D('100'),
            force_majeure_months=D('10'),
            goals=[Goal('Цель', D('100'))],
        ), AllocatorState(life_balance=D('100')))
        source.state.distribution_history = [{
            'type': 'income_distribution',
            'distribution_strategy': 'balanced',
            'distribution_strategy_selected': True,
        }]
        _, balanced, _, _ = simulate_standard_forecast(
            source, D('100'), D('0'),
        )
        self.assertEqual(balanced.allocations['Подушка'], D('65.00'))
        self.assertEqual(balanced.allocations['Цели:Цель'], D('35.00'))

        source.state.distribution_history = []
        _, protection, _, _ = simulate_standard_forecast(
            source, D('100'), D('0'),
        )
        self.assertEqual(protection.allocations['Подушка'], D('100'))
        self.assertNotIn('Цели:Цель', protection.allocations)

    def test_distribution_forecast_refreshes_dated_payment_and_tax_targets(self):
        telegram_id = 908172639
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            life_categories={'Жизнь': D('9000'), 'Страхование': D('1000')},
        ))
        db.save_allocator(telegram_id, source)
        payment_id = db.add_planned_payment(
            telegram_id,
            'insurance',
            'Страхование',
            'ОСАГО',
            D('6000'),
            D('1000'),
            '2026-12-25',
        )
        db.add_tax_obligation(
            telegram_id,
            'Плановый налог',
            'Объект',
            D('3000'),
            D('0'),
            3,
            D('1000'),
            due_date='2026-12-25',
            annual_monthly_amount=D('0'),
            applied_annual_monthly_amount=D('0'),
        )
        try:
            loaded = db.load_allocator(telegram_id)
            simulated, result, _, _ = simulate_standard_forecast(
                loaded,
                D('0'),
                D('0'),
                today=date(2026, 11, 25),
            )
            self.assertIsNone(result)
            self.assertEqual(
                simulated.settings.automatic_life_obligations[
                    f'payment:{payment_id}'
                ],
                D('6000.00'),
            )
            self.assertEqual(
                simulated.settings.life_categories['Страхование'],
                D('6000.00'),
            )
            self.assertEqual(
                simulated.settings.tax_catchups[
                    'Плановый налог · Объект'
                ],
                D('3000.00'),
            )
            self.assertEqual(
                loaded.settings.automatic_life_obligations[
                    f'payment:{payment_id}'
                ],
                D('1000'),
            )
        finally:
            db.delete_user(telegram_id)

    def test_path_duration_uses_month_range_instead_of_false_weeks(self):
        self.assertEqual(human_path_duration(D('1.5')), '1–2 месяца')
        self.assertEqual(human_path_duration(D('0.5')), '1 месяц')
        self.assertEqual(human_path_duration(D('14')), '1 год и 2 месяца')
        self.assertEqual(human_path_duration(D('14.5')), '1 год и 2–3 месяца')

    def test_goal_completion_calendar_uses_month_or_month_range(self):
        self.assertEqual(
            goal_completion_calendar_text(date(2026, 9, 24), D('4')),
            'январь 2027',
        )
        self.assertEqual(
            goal_completion_calendar_text(date(2026, 9, 24), D('4.2')),
            'январь–февраль 2027',
        )
        self.assertEqual(
            goal_completion_calendar_text(date(2026, 9, 24), D('2.5')),
            'ноябрь–декабрь 2026',
        )

    def test_income_tax_forecast_uses_progressive_monthly_history(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            income_type_tax_rates={'Зарплата': D('13'), 'Подработка': D('20')},
        ))
        source.state.distribution_history = [
            {
                'type': 'income_distribution',
                'date': f'2026-{month:02d}-15',
                'income': D('100000'),
                'tax': D(str(rate * 1000)),
            }
            for month, rate in zip(range(3, 9), range(1, 7))
        ]
        rate, months = historical_income_tax_rate(source, today=date(2026, 9, 25))
        self.assertEqual(months, 6)
        self.assertEqual(rate, D('3.5'))

        source.state.distribution_history = []
        fallback, months = historical_income_tax_rate(source, today=date(2026, 9, 25))
        self.assertEqual(months, 0)
        self.assertEqual(fallback, D('20'))

    def test_income_tax_history_survives_allocator_reload(self):
        telegram_id = 908172635
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            income_type_tax_rates={'Зарплата': D('13')},
        ))
        db.save_allocator(telegram_id, source)
        db.save_operation(telegram_id, 'income_distribution', {
            'type': 'income_distribution',
            'date': '2026-08-15',
            'income': D('100000'),
            'tax': D('13000'),
            'allocations': {},
        })
        try:
            loaded = db.load_allocator(telegram_id)
            rate, months = historical_income_tax_rate(
                loaded,
                today=date(2026, 9, 25),
            )
            self.assertEqual(months, 1)
            self.assertEqual(rate, D('13'))
        finally:
            db.delete_user(telegram_id)

    def test_underfunded_minimum_payments_stop_the_path(self):
        source = FinancialAllocator(UserSettings(
            has_debts=True,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('3000'),
            minimum_reserve_months=D('1'),
            force_majeure_months=D('2'),
            credits=[Credit('Долг', D('100000'), None, D('10'), D('5000'))],
        ))
        result = simulate_financial_path(source, max_months=12)
        self.assertIsNotNone(result['debt_payment_shortfall'])
        self.assertGreater(result['debt_payment_shortfall']['amount'], D('0'))
        items = financial_path_items(source, result)
        self.assertTrue(any(item.kind == 'blocker' for item in items))

    def test_planned_payment_appears_on_path_and_uses_saved_balance(self):
        telegram_id = 908172636
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
            life_categories={'Жизнь': D('10000'), 'Страхование': D('2000')},
        ))
        db.save_allocator(telegram_id, source)
        payment_id = db.add_planned_payment(
            telegram_id,
            'insurance',
            'Страхование',
            'ОСАГО',
            D('6000'),
            D('2000'),
            '2026-10-25',
        )
        db.update_planned_payment_saved(telegram_id, payment_id, D('4000'), True)
        try:
            loaded = db.load_allocator(telegram_id)
            result = simulate_financial_path(
                loaded,
                max_months=12,
                today=date(2026, 9, 25),
            )
            milestone = next(
                item for item in result['milestones']
                if item.key == f'payment:{payment_id}'
            )
            self.assertEqual(milestone.label, 'Платёж «ОСАГО»')
            self.assertEqual(milestone.amount, D('6000'))
            self.assertEqual(milestone.due_date, date(2026, 10, 25))
            self.assertEqual(milestone.months, D('1'))
            self.assertFalse(result['obligation_shortfalls'])
            card_item = next(
                item for item in financial_path_items(loaded, result)
                if item.key == f'payment:{payment_id}'
            )
            self.assertEqual(card_item.kind, 'payment')
        finally:
            db.delete_user(telegram_id)

    def test_unfunded_planned_payment_stops_path_at_deadline(self):
        telegram_id = 908172637
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('1000'),
            force_majeure_months=D('2'),
            life_categories={'Жизнь': D('10000'), 'Страхование': D('6000')},
        ))
        db.save_allocator(telegram_id, source)
        payment_id = db.add_planned_payment(
            telegram_id,
            'insurance',
            'Страхование',
            'Страховка',
            D('6000'),
            D('6000'),
            '2026-10-25',
        )
        try:
            loaded = db.load_allocator(telegram_id)
            result = simulate_financial_path(
                loaded,
                max_months=12,
                today=date(2026, 9, 25),
            )
            self.assertEqual(result['obligation_shortfalls'][0]['key'], f'payment:{payment_id}')
            items = financial_path_items(loaded, result)
            self.assertTrue(any(item.kind == 'payment_blocker' for item in items))
        finally:
            db.delete_user(telegram_id)

    def test_dated_tax_obligation_appears_on_path(self):
        telegram_id = 908172638
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
            life_categories={'Жизнь': D('10000')},
        ))
        db.save_allocator(telegram_id, source)
        tax_id = db.add_tax_obligation(
            telegram_id,
            'Транспортный налог',
            'Автомобиль',
            D('3000'),
            D('0'),
            3,
            D('1000'),
            due_date='2026-12-25',
            annual_monthly_amount=D('0'),
            applied_annual_monthly_amount=D('0'),
        )
        try:
            loaded = db.load_allocator(telegram_id)
            result = simulate_financial_path(
                loaded,
                max_months=12,
                today=date(2026, 9, 25),
            )
            milestone = next(
                item for item in result['milestones']
                if item.key == f'tax:{tax_id}'
            )
            self.assertEqual(milestone.kind, 'tax')
            self.assertEqual(milestone.amount, D('3000'))
        finally:
            db.delete_user(telegram_id)

    def test_cyclic_path_runs_work_and_break_months(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='cyclic',
            employment_type='Фрилансер',
            income_rhythm='cyclic',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('100000'),
            income_work_months=D('2'),
            income_gap_months=D('1'),
            reliable_gap_income=D('0'),
            force_majeure_months=D('2'),
            stabilizer_target_months=D('2'),
        ))
        first = _process_average_forecast_month(source, 1, tax_rate=D('0'))
        self.assertEqual(first['minimum_payment_shortfall'], D('0'))
        self.assertEqual(source.state.current_cycle_phase, 'work')
        _process_average_forecast_month(source, 2, tax_rate=D('0'))
        self.assertEqual(source.state.current_cycle_phase, 'break')
        fund_before_break = source.state.intercontract_reserve
        _process_average_forecast_month(source, 3, tax_rate=D('0'))
        self.assertEqual(source.state.current_cycle_phase, 'work')
        self.assertLess(source.state.intercontract_reserve, fund_before_break)

    def test_pillow_has_no_minimum_or_full_layer_in_path(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='piecework',
            employment_type='Фрилансер',
            income_rhythm='irregular',
            critical_life=D('30000'),
            household_reserve=D('10000'),
            average_income=D('100000'),
            force_majeure_months=D('3'),
            stabilizer_target_months=D('2'),
        ))
        result = simulate_financial_path(source, max_months=24)
        text = financial_path_text(source, result)
        self.assertIn('🛡 <b>Подушка</b>', text)
        self.assertNotIn('Подушка: минимальный слой', text)
        self.assertNotIn('Подушка: полный размер', text)
        self.assertEqual(text.count('Минимальный слой:'), 1)
        self.assertEqual(text.count('Полный размер:'), 1)

    def test_financial_path_does_not_change_source(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
            goals=[Goal('Ноутбук', D('100'), target_amount=D('10000'))],
        ))
        before = deepcopy(source)
        result = simulate_financial_path(source, max_months=24)
        self.assertEqual(source.settings, before.settings)
        self.assertEqual(source.state, before.state)
        self.assertTrue(any(item.key == 'pillow' for item in result['milestones']))
        self.assertTrue(any(item.key.startswith('goal:') for item in result['milestones']))

    def test_financial_path_uses_minimum_pillow_and_one_final_debt_card(self):
        source = FinancialAllocator(UserSettings(
            has_debts=True,
            profile_type='stable',
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            minimum_reserve_months=D('1'),
            force_majeure_months=D('3'),
            credits=[Credit('Кредит', D('20000'), None, D('0'), D('10000'))],
        ))
        result = simulate_financial_path(source, max_months=24)
        self.assertTrue(any(item.key == 'pillow:min' for item in result['milestones']))
        debts = [item for item in result['milestones'] if item.kind == 'debt']
        self.assertEqual([(item.key, item.label) for item in debts], [
            ('debts:all', 'Долгов нет'),
        ])

    def test_financial_path_names_only_nonfinal_debts(self):
        source = FinancialAllocator(UserSettings(
            has_debts=True,
            profile_type='stable',
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('70000'),
            minimum_reserve_months=D('1'),
            force_majeure_months=D('3'),
            credits=[
                Credit('Кредитка', D('10000'), None, D('0'), D('10000')),
                Credit('Рассрочка', D('30000'), None, D('0'), D('10000')),
            ],
        ))
        result = simulate_financial_path(source, max_months=24)
        debts = [item for item in result['milestones'] if item.kind == 'debt']
        self.assertEqual(len(debts), 2)
        self.assertEqual(debts[0].label, 'Долг «Кредитка» погашен')
        self.assertEqual(debts[-1].label, 'Долгов нет')

    def test_goal_is_drawn_after_third_level_even_for_legacy_early_eta(self):
        goal = Goal('Отпуск', D('100'), target_amount=D('250000'))
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='stable',
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('3'),
            goals=[goal],
        ))
        source.settings.credits = [Credit('Долг', D('10000'), None, D('0'), D('5000'))]
        result = {
            'starting_mode': 1,
            'average_income': D('50000'),
            'milestones': [
                SimpleNamespace(key=f'goal:{goal.uid}', months=D('1'), kind='goal', label=''),
                SimpleNamespace(key='level:3', months=D('5'), kind='level', label=''),
            ],
        }
        items = financial_path_items(source, result)
        self.assertLess(
            next(index for index, item in enumerate(items) if item.key == 'level:3'),
            next(index for index, item in enumerate(items) if item.key.startswith('goal:')),
        )

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is not installed')
    def test_financial_path_renderer_returns_png(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='stable',
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        result = simulate_financial_path(source, max_months=24)
        image = render_financial_path_card(source, result)
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_investment_forecast_uses_current_capital_without_mutating_profile(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        source.state.investments = D('25000')
        before = deepcopy(source)
        result = simulate_investment_forecast(source, D('10'), horizons=(5, 10))
        self.assertEqual(source.settings, before.settings)
        self.assertEqual(source.state, before.state)
        self.assertEqual(result['starting_capital'], D('25000'))
        self.assertEqual([item.years for item in result['projections']], [5, 10])
        self.assertGreater(result['projections'][0].profit, D('0'))
        self.assertGreaterEqual(
            result['projections'][0].own_funds,
            result['starting_capital'],
        )

    def test_zero_investment_rate_has_no_market_profit(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        result = simulate_investment_forecast(source, D('0'), horizons=(5,))
        self.assertEqual(result['projections'][0].profit, D('0'))

    def test_investment_text_contains_all_requested_horizons(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        result = simulate_investment_forecast(source, D('12.5'))
        text = investment_forecast_text(source, result)
        for years in (5, 10, 15, 20, 30):
            self.assertIn(f'ЧЕРЕЗ {years} ', text)
        self.assertIn('12,5% годовых', text)
        self.assertIn('Инвестиционная прибыль', text)

    def test_investment_forecast_applies_inflation_and_income_indexation(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        without_growth = simulate_investment_forecast(
            source,
            D('10'),
            inflation_rate=D('5'),
            income_growth_rate=D('0'),
            horizons=(10,),
        )
        with_growth = simulate_investment_forecast(
            source,
            D('10'),
            inflation_rate=D('5'),
            income_growth_rate=D('5'),
            horizons=(10,),
        )
        flat_projection = without_growth['projections'][0]
        growing_projection = with_growth['projections'][0]
        self.assertLess(flat_projection.real_capital, flat_projection.capital)
        self.assertGreater(growing_projection.capital, flat_projection.capital)
        self.assertGreater(
            growing_projection.monthly_contribution,
            flat_projection.monthly_contribution,
        )

    def test_investment_text_shows_nominal_and_today_money(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        result = simulate_investment_forecast(
            source,
            D('10'),
            inflation_rate=D('5'),
            income_growth_rate=D('3'),
            horizons=(5,),
        )
        text = investment_forecast_text(source, result)
        self.assertIn('Инфляция — <b>5% в год</b>', text)
        self.assertIn('Индексация дохода — <b>3% в год</b>', text)
        self.assertIn('В сегодняшних деньгах', text)
        self.assertIn('Среднее пополнение', text)

    def test_debt_forecast_tracks_each_debt_and_all_debts_without_mutation(self):
        source = FinancialAllocator(UserSettings(
            has_debts=True,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('100000'),
            minimum_reserve_months=D('1'),
            force_majeure_months=D('2'),
            credits=[
                Credit('Кредитка', D('20000'), None, D('20'), D('5000')),
                Credit('Рассрочка', D('10000'), None, D('0'), D('2500')),
            ],
        ))
        before = deepcopy(source)
        result = simulate_debt_forecast(source, max_months=60)
        self.assertEqual(source.settings, before.settings)
        self.assertEqual(source.state, before.state)
        self.assertEqual(len(result['projections']), 2)
        self.assertEqual(result['starting_balance_total'], D('30000'))
        self.assertTrue(all(item.payoff_months is not None for item in result['projections']))
        self.assertIsNotNone(result['overall_months'])
        text = debt_forecast_text(source, result)
        self.assertIn('Кредитка', text)
        self.assertIn('Рассрочка', text)
        self.assertIn('<b>ВСЕ ДОЛГИ</b>', text)
        self.assertIn('Ожидаемые проценты', text)

    def test_unresolved_debt_forecast_shows_remaining_balance(self):
        source = FinancialAllocator(UserSettings(
            has_debts=True,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            minimum_reserve_months=D('1'),
            force_majeure_months=D('2'),
            credits=[Credit('Долг', D('100000'), None, D('10'), D('5000'))],
        ))
        source.settings.average_income = D('0')
        result = simulate_debt_forecast(source, max_months=12)
        self.assertIsNone(result['overall_months'])
        self.assertEqual(result['remaining_balance_total'], D('100000'))
        text = debt_forecast_text(source, result)
        self.assertIn('не удалось рассчитать в пределах 1 года', text)
        self.assertIn('Остаток после прогноза — 100 000 ₽', text)

    def test_goal_forecast_uses_saved_balance_and_excludes_chests(self):
        goals = [
            Goal(
                'Ноутбук', D('60'), balance=D('5000'),
                target_amount=D('20000'), deadline='2027-12-31',
            ),
            Goal('Отпуск', D('30'), balance=D('2000'), target_amount=D('15000')),
            Goal('Хотелки', D('10'), position_type='chest'),
        ]
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
            goals=goals,
        ))
        before = deepcopy(source)
        result = simulate_goal_forecast(
            source,
            max_months=60,
            today=date(2026, 9, 24),
        )
        self.assertEqual(source.settings, before.settings)
        self.assertEqual(source.state, before.state)
        self.assertEqual({item.name for item in result['projections']}, {'Ноутбук', 'Отпуск'})
        notebook = next(item for item in result['projections'] if item.name == 'Ноутбук')
        self.assertEqual(notebook.current, D('5000'))
        self.assertEqual(notebook.remaining, D('15000'))
        self.assertIsNotNone(notebook.completion_months)
        text = goal_forecast_text(result)
        self.assertIn('Накоплено — 5 000 ₽ из 20 000 ₽', text)
        self.assertIn('Запланированный срок — 31.12.2027', text)
        self.assertNotIn('Ориентировочная дата', text)
        self.assertRegex(text, r'Ориентировочно — [а-яё]+(?:–[а-яё]+)? 20\d{2}')
        self.assertNotIn('Хотелки', text)

    def test_level_forecast_keeps_pillow_single_and_stabilizer_layered(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            profile_type='piecework',
            employment_type='Фрилансер',
            income_rhythm='irregular',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
            stabilizer_target_months=D('2'),
        ))
        result = simulate_level_forecast(source, max_months=60)
        self.assertEqual(
            [item.level for item in result['projections']],
            [4, 5, 6],
        )
        self.assertTrue(all(item.months is not None for item in result['projections']))
        text = level_forecast_text(result)
        self.assertIn('Условие — Сформировать Подушку', text)
        self.assertNotIn('слой Подушки', text)
        self.assertIn('минимальный слой Стабилизатора', text)
        self.assertIn('полный размер Стабилизатора', text)

class ForecastFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_investment_growth_continues_after_expired_callback(self):
        callback = SimpleNamespace(
            data='forecastinvest:growth:3',
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(side_effect=TelegramBadRequest(
                method=object(), message='query is too old',
            )),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={
            'investment_rate': '10',
            'investment_inflation': '5',
        }))
        with patch(
            'forecast.render_investment_forecast', new=AsyncMock(),
        ) as render:
            await choose_investment_income_growth(callback, state)

        callback.message.answer.assert_awaited_once_with(
            'Рассчитываю. Ожидайте, симуляция может занять некоторое время'
        )
        render.assert_awaited_once_with(
            callback.message, state, 42, D('10'), D('5'), D('3'),
        )

    async def test_financial_path_sends_generated_image(self):
        source = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type='Наёмный',
            income_rhythm='monthly',
            critical_life=D('10000'),
            household_reserve=D('2000'),
            average_income=D('50000'),
            force_majeure_months=D('2'),
        ))
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock())
        result = simulate_financial_path(source, max_months=24)
        with (
            patch('forecast.db') as db,
            patch('forecast.simulate_financial_path', return_value=result),
            patch('forecast.render_financial_path_card', return_value=b'png'),
        ):
            db.load_allocator.return_value = source
            await show_financial_path(callback, state)
        callback.message.answer_photo.assert_awaited_once()
        self.assertEqual(
            callback.message.answer_photo.await_args.kwargs['photo'].filename,
            'financial-path.png',
        )

    async def test_forecast_menu_has_requested_button_order_without_emoji(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock())
        with patch('forecast.db') as db:
            db.load_allocator.return_value = object()
            await show_forecast_menu(callback, state)
        markup = callback.message.answer.await_args.kwargs['reply_markup']
        labels = [row[0].text for row in markup.inline_keyboard[:3]]
        self.assertEqual(labels, [
            'Распределить будущую сумму',
            'Мой финансовый путь',
            'Инвестиции',
        ])

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
                self.assertIn("<pre>", text)
                self.assertIn(
                    f"/ {source.settings.force_majeure_limit}"
                    .replace('.00', ''), text,
                )
                self.assertEqual('🛟 Стабилизатор' in text, profile == 'piecework')
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
