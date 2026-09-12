"""Contract tests for deleting an income from the persistent ledger.

Desired accounting semantics
============================

An income deletion is a historical correction, so the allocator must rebuild
the user's timeline as though that income had never existed:

* a later manual balance reconciliation is authoritative and must survive;
* a reset closes the period and blocks deletion of its incomes;
* every later income keeps the distribution the user actually carried out;
* money credited to a planned payment is reversed, including reactivating a
  payment that had been completed by the deleted income;
* duplicate confirmation callbacks are idempotent and leave navigation back
  to income history and the main menu.

These tests deliberately exercise the persistent SQLite database and the real
Telegram callback handler.  They describe the required end state; no
production implementation is duplicated here.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import dashboard
import planned_payments
from financial_engine import FinancialAllocator, UserSettings
from income_deletion import (
    attach_income_rollback_context,
    capture_income_rollback_context,
)
from storage import Database


ZERO = Decimal("0")


class IncomeIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "income-integrity.db")
        self.telegram_id = 991001

    def tearDown(self):
        self.database.close()
        self.temp_dir.cleanup()

    @staticmethod
    def make_allocator() -> FinancialAllocator:
        return FinancialAllocator(
            UserSettings(
                has_debts=False,
                employment_type="Фрилансер",
                profile_type="piecework",
                income_rhythm="irregular",
                critical_life=Decimal("1000"),
                household_reserve=Decimal("300"),
                average_income=Decimal("1000"),
                life_categories={"Жизнь": Decimal("1000")},
                force_majeure_months=Decimal("1"),
                stabilizer_target_months=Decimal("1"),
            )
        )

    def persist_income(
        self,
        allocator: FinancialAllocator,
        amount: str,
        income_type: str,
        *,
        income_date: date = date(2026, 9, 10),
    ) -> tuple[int, object]:
        rollback_context = capture_income_rollback_context(
            self.database, self.telegram_id, allocator,
        )
        result = allocator.process_income(
            Decimal(amount),
            income_type,
            tax_override=ZERO,
            income_date=income_date,
        )
        with (
            patch.object(planned_payments, "db", self.database),
            self.database.transaction(),
        ):
            for envelope in {
                item["envelope_name"]
                for item in self.database.load_planned_payments(self.telegram_id)
            }:
                planned_payments.apply_planned_payment_allocation(
                    self.telegram_id,
                    allocator,
                    envelope,
                    result.allocations.get(f"КЖ:{envelope}", ZERO),
                )
            attach_income_rollback_context(
                self.database,
                self.telegram_id,
                allocator,
                rollback_context,
            )
            self.database.save_allocator(self.telegram_id, allocator)
        operation = next(
            item
            for item in self.database.load_operations(self.telegram_id)
            if (item.get("payload") or {}).get("income_type") == income_type
        )
        return int(operation["id"]), result

    async def confirm_delete(self, operation_id: int):
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(
            data=f"incomehistory:delete_confirm:{operation_id}",
            from_user=SimpleNamespace(id=self.telegram_id),
            message=message,
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock())
        # This suite has no tax obligations.  Isolating the tax reconciler
        # prevents it from consulting the module-level production database.
        with (
            patch.object(dashboard, "db", self.database),
            patch.object(
                dashboard,
                "reconcile_tax_obligation_balances",
                return_value=None,
            ),
        ):
            await dashboard.confirm_delete_income_history(callback, state)
        return callback, state

    @staticmethod
    def accounting_state(allocator: FinancialAllocator) -> dict:
        state = allocator.state
        return {
            "life_balance": state.life_balance,
            "pillow_minimum": state.pillow_minimum,
            "pillow_force_majeure": state.pillow_force_majeure,
            "pillow_stabilizer": state.pillow_stabilizer,
            "investments": state.investments,
            "goal_balances": deepcopy(state.goal_balances),
            "period_income": state.period_income,
            "period_tax": state.period_tax,
            "period_allocations": deepcopy(state.period_allocations),
            "period_life_topups": deepcopy(state.period_life_topups),
        }

    async def test_deleting_income_preserves_later_manual_balance_reconciliation(self):
        allocator = self.make_allocator()
        self.database.save_allocator(self.telegram_id, allocator)
        operation_id, _ = self.persist_income(allocator, "1000", "Заказ")

        # The reserve editor asks for the actual bank balance and overwrites
        # it.  This later fact must win when the earlier income is removed.
        reconciled = self.database.load_allocator(self.telegram_id)
        reconciled.state.pillow_minimum = ZERO
        reconciled.state.pillow_force_majeure = Decimal("777")
        self.database.save_allocator(self.telegram_id, reconciled)

        await self.confirm_delete(operation_id)

        restored = self.database.load_allocator(self.telegram_id)
        self.assertEqual(restored.state.pillow_balance, Decimal("777"))
        self.assertEqual(restored.state.period_income, ZERO)
        self.assertFalse(
            any(
                item["id"] == operation_id
                for item in self.database.load_operations(self.telegram_id)
            )
        )

    async def test_deleting_income_from_closed_period_is_blocked(self):
        allocator = self.make_allocator()
        allocator.state.activate_budget_period(date(2026, 8, 1))
        self.database.save_allocator(self.telegram_id, allocator)
        operation_id, _ = self.persist_income(
            allocator,
            "100",
            "Августовский заказ",
            income_date=date(2026, 8, 10),
        )

        current = self.database.load_allocator(self.telegram_id)
        current.reset_period()
        current.state.activate_budget_period(date(2026, 9, 1))
        current_period_start = current.state.period_started_at
        current_period_end = current.state.period_ends_at
        self.database.save_allocator(self.telegram_id, current)
        self.database.save_operation(
            self.telegram_id,
            "period_reset",
            {
                "started_at": current_period_start,
                "message": "Начат новый расчётный период",
            },
        )

        callback, _ = await self.confirm_delete(operation_id)

        restored = self.database.load_allocator(self.telegram_id)
        self.assertEqual(restored.state.period_started_at, current_period_start)
        self.assertEqual(restored.state.period_ends_at, current_period_end)
        self.assertEqual(restored.state.period_status, "active")
        self.assertEqual(restored.state.period_income, ZERO)
        self.assertTrue(
            any(
                item["id"] == operation_id
                for item in self.database.load_operations(self.telegram_id)
            )
        )
        self.assertIn(
            "закрытому расчётному периоду",
            callback.message.answer.await_args.args[0],
        )

    async def test_deleting_older_income_recalculates_later_distribution(self):
        allocator = self.make_allocator()
        clean = self.make_allocator()
        self.database.save_allocator(self.telegram_id, allocator)
        older_id, _ = self.persist_income(allocator, "100", "Первый заказ")
        _, second_result = self.persist_income(allocator, "1500", "Второй заказ")
        clean_result = clean.process_income(
            Decimal("1500"), "Второй заказ", tax_override=ZERO,
            income_date=date(2026, 9, 10),
        )

        await self.confirm_delete(older_id)

        restored = self.database.load_allocator(self.telegram_id)
        self.assertEqual(restored.state.period_income, Decimal("1500"))
        surviving = next(
            item
            for item in self.database.load_operations(self.telegram_id)
            if (item.get("payload") or {}).get("income_type") == "Второй заказ"
        )
        self.assertEqual(
            surviving["payload"]["allocations"],
            clean_result.allocations,
        )
        self.assertNotEqual(second_result.allocations, clean_result.allocations)

    async def test_deleting_income_rolls_back_completed_planned_payment(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="piecework",
            income_rhythm="irregular",
            critical_life=Decimal("1000"),
            household_reserve=ZERO,
            average_income=Decimal("2000"),
            life_categories={
                "Жизнь": Decimal("900"),
                "Образование": Decimal("100"),
            },
            force_majeure_months=Decimal("1"),
            stabilizer_target_months=Decimal("1"),
        )
        allocator = FinancialAllocator(settings)
        self.database.save_allocator(self.telegram_id, allocator)
        payment_id = self.database.add_planned_payment(
            self.telegram_id,
            "Образование",
            "Образование",
            "Семестр",
            Decimal("100"),
            Decimal("100"),
            "2026-10-01",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        operation_id, result = self.persist_income(
            allocator,
            "2000",
            "Оплата проекта",
        )
        completed = self.database.load_planned_payments(
            self.telegram_id,
            active_only=False,
        )[0]
        self.assertFalse(completed["active"])
        self.assertEqual(completed["saved_amount"], Decimal("100"))

        await self.confirm_delete(operation_id)

        restored_payment = self.database.load_planned_payments(
            self.telegram_id,
            active_only=False,
        )[0]
        restored = self.database.load_allocator(self.telegram_id)
        self.assertEqual(restored_payment["id"], payment_id)
        self.assertTrue(restored_payment["active"])
        self.assertEqual(restored_payment["saved_amount"], ZERO)
        self.assertEqual(
            restored.settings.automatic_life_obligations[f"payment:{payment_id}"],
            Decimal("100"),
        )
        self.assertEqual(
            restored.settings.life_categories["Образование"],
            Decimal("100"),
        )
        self.assertEqual(restored.settings.critical_life, Decimal("1100"))

    async def test_duplicate_confirmation_is_idempotent_and_navigable(self):
        allocator = self.make_allocator()
        self.database.save_allocator(self.telegram_id, allocator)
        operation_id, _ = self.persist_income(allocator, "1000", "Заказ")

        await self.confirm_delete(operation_id)
        after_first = self.accounting_state(
            self.database.load_allocator(self.telegram_id)
        )
        callback, _ = await self.confirm_delete(operation_id)
        after_second = self.accounting_state(
            self.database.load_allocator(self.telegram_id)
        )

        self.assertEqual(after_second, after_first)
        self.assertFalse(
            any(
                item["id"] == operation_id
                for item in self.database.load_operations(self.telegram_id)
            )
        )
        last_answer = callback.message.answer.await_args
        self.assertIn("уже недоступно", last_answer.args[0])
        markup = last_answer.kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        button_texts = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("← К истории", button_texts)
        self.assertIn("← Главное меню", button_texts)


if __name__ == "__main__":
    unittest.main()
