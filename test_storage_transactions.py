import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from financial_engine import AllocatorState, FinancialAllocator, UserSettings

_MODULE_DATA = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _MODULE_DATA.name

from storage import Database


class StorageTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "transactions.db"
        self.db = Database(self.path)
        self.telegram_id = 701
        self.tax_id = self.db.add_tax_obligation(
            self.telegram_id,
            "Налог на имущество",
            "Квартира",
            Decimal("12000"),
            Decimal("0"),
            12,
            Decimal("1000"),
            "2026-12-01",
        )
        self.payment_id = self.db.add_planned_payment(
            self.telegram_id,
            "Образование",
            "Образование",
            "Курс",
            Decimal("6000"),
            Decimal("500"),
            "2026-12-01",
        )
        self.db.save_state(self.telegram_id, AllocatorState())

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_income_writes_commit_as_one_transaction(self):
        state = self.db.load_state(self.telegram_id)
        state.period_income = Decimal("5000")
        state.life_balance = Decimal("3000")
        operation = {
            "type": "income_distribution",
            "income": Decimal("5000"),
            "allocations": {"КЖ:Жильё": Decimal("3000")},
        }

        with self.db.transaction():
            self.db.update_tax_obligation_saved(
                self.telegram_id, self.tax_id, Decimal("400"), True,
            )
            self.db.update_planned_payment_saved(
                self.telegram_id, self.payment_id, Decimal("250"), True,
            )
            self.db.save_state(self.telegram_id, state)
            self.db.save_operation(
                self.telegram_id, "income_distribution", operation,
            )

        reopened = Database(self.path)
        try:
            persisted = reopened.load_state(self.telegram_id)
            self.assertEqual(persisted.period_income, Decimal("5000"))
            self.assertEqual(persisted.life_balance, Decimal("3000"))
            self.assertEqual(reopened.operation_count(self.telegram_id), 1)
            self.assertEqual(
                reopened.load_tax_obligations(self.telegram_id)[0]["saved_before"],
                Decimal("400"),
            )
            self.assertEqual(
                reopened.load_planned_payments(self.telegram_id)[0]["saved_amount"],
                Decimal("250"),
            )
        finally:
            reopened.close()

    def test_exception_rolls_back_state_operation_and_progress(self):
        state = self.db.load_state(self.telegram_id)
        state.period_income = Decimal("5000")

        with self.assertRaisesRegex(RuntimeError, "simulated failure"):
            with self.db.transaction():
                self.db.update_tax_obligation_saved(
                    self.telegram_id, self.tax_id, Decimal("400"), True,
                )
                self.db.update_planned_payment_saved(
                    self.telegram_id, self.payment_id, Decimal("250"), True,
                )
                self.db.save_state(self.telegram_id, state)
                self.db.save_operation(
                    self.telegram_id,
                    "income_distribution",
                    {"type": "income_distribution", "income": Decimal("5000")},
                )
                raise RuntimeError("simulated failure")

        persisted = self.db.load_state(self.telegram_id)
        self.assertEqual(persisted.period_income, Decimal("0"))
        self.assertEqual(self.db.operation_count(self.telegram_id), 0)
        self.assertEqual(
            self.db.load_tax_obligations(self.telegram_id)[0]["saved_before"],
            Decimal("0"),
        )
        self.assertEqual(
            self.db.load_planned_payments(self.telegram_id)[0]["saved_amount"],
            Decimal("0"),
        )

    def test_existing_save_methods_still_autocommit(self):
        self.db.save_operation(
            self.telegram_id,
            "income_distribution",
            {"type": "income_distribution", "income": Decimal("100")},
        )

        reopened = Database(self.path)
        try:
            self.assertEqual(reopened.operation_count(self.telegram_id), 1)
        finally:
            reopened.close()

    def test_save_allocator_joins_transaction_and_rolls_back(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("500"),
            average_income=Decimal("3000"),
        ))
        allocator.state.period_income = Decimal("700")
        allocator.state.operation_log.append({
            "type": "income_distribution",
            "income": Decimal("700"),
            "allocations": {},
        })

        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.save_allocator(self.telegram_id, allocator)
                raise RuntimeError("do not persist partial allocator")

        self.assertIsNone(self.db.load_settings(self.telegram_id))
        self.assertEqual(
            self.db.load_state(self.telegram_id).period_income,
            Decimal("0"),
        )
        self.assertEqual(self.db.operation_count(self.telegram_id), 0)

    def test_nested_failure_rolls_back_only_nested_savepoint(self):
        with self.db.transaction():
            self.db.save_operation(self.telegram_id, "outer_before", {})
            with self.assertRaises(RuntimeError):
                with self.db.transaction():
                    self.db.save_operation(self.telegram_id, "inner", {})
                    raise RuntimeError("nested failure")
            self.db.save_operation(self.telegram_id, "outer_after", {})

        types = [item["type"] for item in self.db.load_operations(self.telegram_id)]
        self.assertCountEqual(types, ["outer_before", "outer_after"])


if __name__ == "__main__":
    unittest.main()
