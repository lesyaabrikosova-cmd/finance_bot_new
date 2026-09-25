import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_DATA = tempfile.TemporaryDirectory()
os.environ['ALLOCATOR_DATA_DIR'] = _DATA.name
from storage import Database
from financial_engine import FinancialAllocator, UserSettings
from settings_editor import confirm_erase_all, confirm_full_reset, start_reset_period


class ResetStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.db')
        self.tables = [r[0] for r in self.db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                       if r[0] not in ('sqlite_sequence', 'exchange_rates', 'users')]
        for uid in (101, 202):
            self.db.ensure_user(uid)
            for table in self.tables:
                columns = self.db.connection.execute(f'PRAGMA table_info({table})').fetchall()
                names, values = [], []
                for col in columns:
                    name = col['name']
                    if name == 'telegram_id':
                        names.append(name); values.append(uid)
                    elif col['notnull'] and col['dflt_value'] is None:
                        names.append(name); values.append(1 if col['type'] == 'INTEGER' else '0')
                self.db.connection.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", values)
        self.db.connection.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_delete_cascades_every_user_table_without_touching_other_user(self):
        self.db.delete_user(101)
        for table in self.tables:
            self.assertEqual(self.db.connection.execute(f'SELECT COUNT(*) FROM {table} WHERE telegram_id=101').fetchone()[0], 0, table)
            self.assertEqual(self.db.connection.execute(f'SELECT COUNT(*) FROM {table} WHERE telegram_id=202').fetchone()[0], 1, table)

    def test_accounting_reset_removes_sql_history_and_preserves_configuration(self):
        self.db.save_allocator(101, FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("1000"),
            household_reserve=D("100"),
            average_income=D("2000"),
            life_categories={"Жизнь": D("1000")},
        )))
        self.db.connection.execute("UPDATE tax_obligations SET opening_amount='300', saved_before='500' WHERE telegram_id=101")
        self.db.connection.commit()
        self.db.clear_accounting_history(101)
        self.assertEqual(self.db.operation_count(101), 0)
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM tax_payments WHERE telegram_id=101').fetchone()[0], 0)
        row = self.db.connection.execute('SELECT opening_amount,saved_before FROM tax_obligations WHERE telegram_id=101').fetchone()
        self.assertEqual(tuple(row), ('0','0'))
        self.assertTrue(self.db.user_exists(101))
        self.assertEqual(self.db.operation_count(202), 1)

    def test_reset_period_can_start_from_any_past_date(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("1000"),
            household_reserve=D("100"),
            average_income=D("2000"),
        ))

        start, end = start_reset_period(
            allocator, date(2026, 8, 28), date(2026, 9, 12),
        )

        self.assertEqual(start, date(2026, 8, 28))
        self.assertEqual(end, date(2026, 9, 27))
        self.assertEqual(allocator.state.period_status, "active")
        self.assertEqual(allocator.state.period_started_at[:10], "2026-08-28")

    def test_old_reset_start_extends_the_open_period_to_current_anchor(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("1000"),
            household_reserve=D("100"),
            average_income=D("2000"),
        ))

        _, end = start_reset_period(
            allocator, date(2026, 1, 28), date(2026, 9, 12),
        )

        self.assertEqual(end, date(2026, 9, 27))


class ResetGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_confirmation_without_pending_request_does_not_delete(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()), from_user=SimpleNamespace(id=101))
        state = AsyncMock()
        state.get_data.return_value = {}
        with patch('settings_editor.db') as db:
            db.load_allocator.return_value = SimpleNamespace(settings=SimpleNamespace(developer_mode=True))
            await confirm_erase_all(callback, state)
            db.delete_user.assert_not_called()

    async def test_accounting_reset_rolls_back_visible_balances_when_history_clear_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "atomic-reset.db")
            user = 303
            allocator = FinancialAllocator(UserSettings(
                has_debts=False,
                employment_type="Фрилансер",
                critical_life=D("1000"),
                household_reserve=D("100"),
                average_income=D("2000"),
                life_categories={"Жизнь": D("1000")},
            ))
            allocator.state.life_balance = D("777")
            database.save_allocator(user, allocator)
            callback = SimpleNamespace(
                answer=AsyncMock(),
                message=SimpleNamespace(answer=AsyncMock()),
                from_user=SimpleNamespace(id=user),
            )
            state = AsyncMock()
            original_clear = database.clear_accounting_history

            def failed_clear(_telegram_id):
                raise RuntimeError("simulated reset failure")

            try:
                with patch("settings_editor.db", database), patch.object(
                    database, "clear_accounting_history", side_effect=failed_clear,
                ):
                    await confirm_full_reset(callback, state)
                restored = database.load_allocator(user)
            finally:
                database.clear_accounting_history = original_clear
                database.close()

        self.assertEqual(restored.state.life_balance, D("777"))
        self.assertIn("данные сохранены", callback.message.answer.await_args.args[0])

    async def test_full_reset_waits_for_a_user_selected_period_start(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "reset-start.db")
            user = 304
            allocator = FinancialAllocator(UserSettings(
                has_debts=False,
                employment_type="Фрилансер",
                critical_life=D("1000"),
                household_reserve=D("100"),
                average_income=D("2000"),
            ))
            allocator.state.activate_budget_period(date(2026, 9, 12))
            database.save_allocator(user, allocator)
            callback = SimpleNamespace(
                answer=AsyncMock(),
                message=SimpleNamespace(answer=AsyncMock()),
                from_user=SimpleNamespace(id=user),
            )
            state = AsyncMock()
            try:
                with patch("settings_editor.db", database):
                    await confirm_full_reset(callback, state)
                reset = database.load_allocator(user)
            finally:
                database.close()

        self.assertEqual(reset.state.period_status, "not_started")
        self.assertIsNone(reset.state.period_started_at)
        state.set_state.assert_awaited_once()
        self.assertIn(
            "КОГДА НАЧАТЬ РАСЧЁТНЫЙ ПЕРИОД",
            callback.message.answer.await_args.args[0],
        )
