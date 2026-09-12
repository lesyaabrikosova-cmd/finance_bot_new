import os
import tempfile
import unittest
from decimal import Decimal as D
from types import SimpleNamespace

_data = tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR'] = _data.name
from storage import Database
from dashboard import period_balance_chart


class RenameTests(unittest.TestCase):
    def test_chart_uses_the_name_already_resolved_by_the_ledger(self):
        a = SimpleNamespace(state=SimpleNamespace(
            period_tax=D(0), period_life_topups={'Квартира': D(100)}))
        values, _ = period_balance_chart(a, {'КЖ:Квартира': D(100)})
        self.assertEqual(values, {'КМ · Квартира': D(100)})

    def test_chart_keeps_archived_and_current_identities_separate(self):
        current_goal = SimpleNamespace(
            name='Путешествие', uid='goal-1', is_chest=False,
        )
        settings = SimpleNamespace(
            life_categories={'Квартира': D(100)},
            life_category_ids={'Квартира': 'life-1'},
            goals=[current_goal],
        )
        state = SimpleNamespace(
            period_tax=D(0),
            period_life_topups={'Недвижимость': D(100), 'Квартира': D(50)},
        )
        allocator = SimpleNamespace(settings=settings, state=state)
        values, _ = period_balance_chart(
            allocator,
            {
                'КЖ:Недвижимость · прежний life-old': D(100),
                'КЖ:Квартира': D(50),
                'Цели:Отпуск · прежний goal-old': D(100),
                'Цели:Путешествие': D(50),
            },
        )
        self.assertEqual(values['КМ · Квартира'], D(50))
        self.assertEqual(values['КМ · Недвижимость · прежняя категория'], D(100))
        self.assertEqual(values['Цели и Сундуки · Путешествие'], D(50))
        self.assertEqual(values['Цели и Сундуки · Отпуск · прежняя позиция'], D(100))

    def test_chained_renames_preserve_period_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, 'test.db'))
            db.save_operation(42, 'income_distribution', {'allocations': {'Цели:А': '100'}})
            a = SimpleNamespace(state=SimpleNamespace(period_allocations={'Цели:А': D(100)}))
            db.record_envelope_rename(42, a, 'Цели:', 'А', 'Б')
            db.record_envelope_rename(42, a, 'Цели:', 'Б', 'В')
            # A stale write after the rename must still use the current name
            # when it is read for reports.
            db.save_operation(42, 'income_distribution', {'allocations': {'Цели:А': '50'}})
            self.assertEqual(a.state.period_allocations, {'Цели:В': D(100)})
            incomes = [o for o in db.load_operations(42) if o['type'] == 'income_distribution']
            self.assertEqual(incomes[0]['payload']['allocations'], {'Цели:В': '50'})
            self.assertEqual(incomes[1]['payload']['allocations'], {'Цели:В': '100'})
            db.connection.close()

    def test_recovery_link_merges_legacy_period_income(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, 'test.db'))
            db.save_operation(7, 'income_distribution', {
                'allocations': {'КЖ:Недвижимость': '25000'},
            })
            db.save_operation(7, 'income_distribution', {
                'allocations': {'КЖ:Квартира': '5000'},
            })
            allocator = SimpleNamespace(state=SimpleNamespace(
                period_allocations={
                    'КЖ:Недвижимость': D(25000),
                    'КЖ:Квартира': D(5000),
                },
            ))
            db.record_envelope_rename(
                7, allocator, 'КЖ:', 'Недвижимость', 'Квартира', 'life-1',
            )
            operations = db.load_operations(7)
            total = sum(
                D(operation['payload']['allocations'].get('КЖ:Квартира', 0))
                for operation in operations
                if operation['type'] == 'income_distribution'
            )
            self.assertEqual(total, D(30000))
            self.assertEqual(allocator.state.period_allocations, {'КЖ:Квартира': D(30000)})
            db.connection.close()

    def test_normalization_merges_old_and_new_state_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, 'test.db'))
            db.save_operation(42, 'envelope_rename', {'old': 'КЖ:Недвижимость', 'new': 'КЖ:Квартира'})
            db.save_operation(42, 'envelope_rename', {'old': 'Цели:Отпуск', 'new': 'Цели:Путешествие'})
            allocator = SimpleNamespace(state=SimpleNamespace(
                period_allocations={
                    'КЖ:Недвижимость': D(100),
                    'КЖ:Квартира': D(50),
                },
                period_life_topups={'Недвижимость': D(100), 'Квартира': D(50)},
                goal_balances={'Отпуск': D(200), 'Путешествие': D(30)},
            ))
            db.normalize_envelope_names(42, allocator)
            self.assertEqual(allocator.state.period_allocations, {'КЖ:Квартира': D(150)})
            self.assertEqual(allocator.state.period_life_topups, {'Квартира': D(150)})
            self.assertEqual(allocator.state.goal_balances, {'Путешествие': D(230)})
            db.connection.close()
