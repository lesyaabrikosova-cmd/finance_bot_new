import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from dashboard import rebuild_period_analytics_from_history
from financial_engine import AllocatorState, Credit, FinancialAllocator, UserSettings
from income_deletion import (
    IncomeDeletionError,
    attach_income_rollback_context,
    capture_income_rollback_context,
    delete_income_safely,
)
from planned_payments import (
    apply_planned_payment_allocation,
    refresh_planned_payment_targets,
)
from storage import Database
from taxes import (
    apply_planned_tax_allocation,
    reconcile_tax_obligation_balances,
    refresh_planned_tax_targets,
)


class SafeIncomeDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "allocator.db")
        self.telegram_id = 712345
        self.patches = [
            patch("dashboard.db", self.database),
            patch("planned_payments.db", self.database),
            patch("taxes.db", self.database),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.database.connection.close()
        self.temp_dir.cleanup()

    def allocator(self, **overrides):
        values = {
            "has_debts": False,
            "employment_type": "Фрилансер",
            "critical_life": Decimal("100"),
            "household_reserve": Decimal("0"),
            "average_income": Decimal("1000"),
            "life_categories": {"Жизнь": Decimal("100")},
        }
        values.update(overrides)
        return FinancialAllocator(UserSettings(**values))

    def save_income(
        self, allocator, amount, *, apply_payments=False, apply_taxes=False,
        income_date=date(2026, 9, 10),
    ):
        if apply_payments:
            refresh_planned_payment_targets(
                self.telegram_id, allocator, income_date,
            )
        if apply_taxes:
            refresh_planned_tax_targets(
                self.telegram_id, allocator, income_date,
            )
        context = capture_income_rollback_context(
            self.database, self.telegram_id, allocator,
        )
        result = allocator.process_income(
            Decimal(str(amount)),
            "Заказ",
            income_date=income_date,
            tax_override=Decimal("0"),
        )
        with self.database.transaction():
            if apply_taxes:
                apply_planned_tax_allocation(
                    self.telegram_id,
                    allocator,
                    result.allocations.get("КЖ:Налоги", Decimal("0")),
                )
            if apply_payments:
                for envelope in {
                    item["envelope_name"]
                    for item in self.database.load_planned_payments(self.telegram_id)
                }:
                    apply_planned_payment_allocation(
                        self.telegram_id,
                        allocator,
                        envelope,
                        result.allocations.get(f"КЖ:{envelope}", Decimal("0")),
                    )
            attach_income_rollback_context(
                self.database,
                self.telegram_id,
                allocator,
                context,
            )
            self.database.save_allocator(self.telegram_id, allocator)
        return result, self.database.load_operations(self.telegram_id)[0]

    def delete(self, operation_id):
        return delete_income_safely(
            self.database,
            self.telegram_id,
            operation_id,
            reconcile_taxes=reconcile_tax_obligation_balances,
            rebuild_period_analytics=rebuild_period_analytics_from_history,
            today=date(2026, 9, 10),
        )

    def test_middle_income_is_removed_without_erasing_later_income(self):
        allocator = self.allocator()
        clean = self.allocator()
        first_result, first = self.save_income(allocator, "1000")
        second_result, second = self.save_income(allocator, "500")
        clean_result = clean.process_income(
            Decimal("500"), "Заказ", income_date=date(2026, 9, 10),
            tax_override=Decimal("0"),
        )

        restored = self.delete(first["id"])

        remaining = self.database.load_operations(self.telegram_id)
        self.assertEqual([item["id"] for item in remaining], [second["id"]])
        self.assertEqual(restored.state.period_income, Decimal("500"))
        self.assertEqual(
            restored.state.investments,
            clean_result.allocations["Инвестиции"],
        )
        self.assertEqual(
            restored.state.goal_balances["Будущие покупки"],
            clean_result.allocations["Цели:Будущие покупки"],
        )
        replayed_payload = remaining[0]["payload"]
        self.assertEqual(replayed_payload["allocations"], clean_result.allocations)
        self.assertNotEqual(second_result.allocations, clean_result.allocations)
        self.assertGreater(first_result.allocations["Стабилизатор дохода"], Decimal("0"))
        self.assertEqual(
            restored.state.pillow_stabilizer,
            clean.state.pillow_stabilizer,
        )

    def test_income_before_latest_period_reset_is_never_deleted(self):
        allocator = self.allocator()
        _, income = self.save_income(allocator, "1000")
        self.database.save_operation(
            self.telegram_id,
            "period_reset",
            {"started_at": "2026-10-01T00:00:00"},
        )

        with self.assertRaisesRegex(IncomeDeletionError, "закрытому расчётному периоду"):
            self.delete(income["id"])

        self.assertTrue(any(
            item.get("id") == income["id"]
            for item in self.database.load_operations(self.telegram_id)
        ))

    def test_income_before_active_period_is_blocked_without_reset_row(self):
        allocator = self.allocator()
        allocator.state.activate_budget_period(date(2026, 9, 1))
        self.database.save_allocator(self.telegram_id, allocator)
        _, income = self.save_income(
            allocator, "100", income_date=date(2026, 8, 31),
        )

        with self.assertRaisesRegex(IncomeDeletionError, "закрытому расчётному периоду"):
            self.delete(income["id"])

        self.assertTrue(any(
            item["id"] == income["id"]
            for item in self.database.load_operations(self.telegram_id, limit=-1)
        ))

    def test_later_manual_reserve_reconciliation_is_authoritative(self):
        allocator = self.allocator()
        _, income = self.save_income(allocator, "1000")
        reconciled = self.database.load_allocator(self.telegram_id)
        reconciled.state.pillow_stabilizer = Decimal("777")
        self.database.save_allocator(self.telegram_id, reconciled)

        restored = self.delete(income["id"])

        self.assertEqual(restored.state.pillow_stabilizer, Decimal("777"))
        self.assertEqual(restored.state.period_income, Decimal("0"))

    def test_later_manual_goal_reconciliation_is_authoritative(self):
        allocator = self.allocator()
        _, income = self.save_income(allocator, "1000")
        reconciled = self.database.load_allocator(self.telegram_id)
        reconciled.state.goal_balances["Будущие покупки"] = Decimal("999")
        self.database.save_allocator(self.telegram_id, reconciled)

        restored = self.delete(income["id"])

        self.assertEqual(restored.state.goal_balances["Будущие покупки"], Decimal("999"))
        self.assertEqual(restored.state.period_income, Decimal("0"))

    def test_completed_planned_payment_is_reactivated_and_reconciled(self):
        allocator = self.allocator(
            critical_life=Decimal("1000"),
            average_income=Decimal("5000"),
            life_categories={"Образование": Decimal("1000")},
        )
        self.database.save_allocator(self.telegram_id, allocator)
        payment_id = self.database.add_planned_payment(
            self.telegram_id,
            "Образование",
            "Образование",
            "Семестр",
            Decimal("1000"),
            Decimal("1000"),
            "2026-10-01",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        _, income = self.save_income(allocator, "3000", apply_payments=True)
        completed = self.database.load_planned_payments(
            self.telegram_id, active_only=False,
        )[0]
        self.assertFalse(completed["active"])
        self.assertEqual(completed["saved_amount"], Decimal("1000"))

        restored = self.delete(income["id"])

        payment = self.database.load_planned_payments(
            self.telegram_id, active_only=False,
        )[0]
        self.assertEqual(payment["id"], payment_id)
        self.assertTrue(payment["active"])
        self.assertEqual(payment["saved_amount"], Decimal("0"))
        self.assertEqual(payment["monthly_amount"], Decimal("1000.00"))
        self.assertEqual(
            restored.settings.automatic_life_obligations[f"payment:{payment_id}"],
            Decimal("1000.00"),
        )
        self.assertEqual(restored.settings.life_categories["Образование"], Decimal("1000.00"))

    def test_external_planned_payment_balance_reconciliation_is_preserved(self):
        allocator = self.allocator(
            critical_life=Decimal("1000"),
            average_income=Decimal("5000"),
            life_categories={"Образование": Decimal("1000")},
        )
        self.database.save_allocator(self.telegram_id, allocator)
        payment_id = self.database.add_planned_payment(
            self.telegram_id, "Образование", "Образование", "Семестр",
            Decimal("2000"), Decimal("1000"), "2026-11-01",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        _, income = self.save_income(allocator, "3000", apply_payments=True)
        self.database.update_planned_payment_saved(
            self.telegram_id, payment_id, Decimal("1200"), True,
        )

        self.delete(income["id"])

        self.assertEqual(
            self.database.load_planned_payments(self.telegram_id)[0]["saved_amount"],
            Decimal("1200"),
        )

    def test_replay_rebuilds_planned_payment_progress_from_surviving_income(self):
        allocator = self.allocator(
            critical_life=Decimal("1000"), average_income=Decimal("5000"),
            life_categories={"Образование": Decimal("1000")},
        )
        self.database.save_allocator(self.telegram_id, allocator)
        payment_id = self.database.add_planned_payment(
            self.telegram_id, "Образование", "Образование", "Семестр",
            Decimal("1000"), Decimal("1000"), "2026-12-01",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        _, first = self.save_income(allocator, "500", apply_payments=True)
        _, second = self.save_income(allocator, "500", apply_payments=True)
        original_saved = self.database.load_planned_payments(
            self.telegram_id,
        )[0]["saved_amount"]
        self.assertGreater(original_saved, Decimal("0"))

        self.delete(first["id"])

        payment = self.database.load_planned_payments(self.telegram_id)[0]
        surviving = next(
            item for item in self.database.load_operations(self.telegram_id, limit=-1)
            if item["id"] == second["id"]
        )
        self.assertEqual(payment["id"], payment_id)
        self.assertGreater(payment["saved_amount"], Decimal("0"))
        self.assertLess(payment["saved_amount"], original_saved)
        self.assertGreater(
            surviving["payload"]["allocations"]["КЖ:Образование"], Decimal("0"),
        )

    def test_replay_rebuilds_current_month_tax_quota(self):
        allocator = self.allocator(
            critical_life=Decimal("900"), average_income=Decimal("5000"),
            life_categories={"Жизнь": Decimal("900")},
        )
        self.database.save_allocator(self.telegram_id, allocator)
        tax_id = self.database.add_tax_obligation(
            self.telegram_id,
            "Налог на имущество", "Квартира", Decimal("1200"), Decimal("0"),
            12, Decimal("100"), "2027-12-01", Decimal("100"),
            monthly_period="2026-09",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        _, first = self.save_income(allocator, "2000", apply_taxes=True)
        _, second = self.save_income(allocator, "2000", apply_taxes=True)
        original_second = next(
            item for item in self.database.load_operations(self.telegram_id, limit=-1)
            if item["id"] == second["id"]
        )
        self.assertEqual(
            original_second["payload"]["allocations"].get("КЖ:Налоги", Decimal("0")),
            Decimal("0"),
        )

        self.delete(first["id"])

        tax = self.database.load_tax_obligations(self.telegram_id)[0]
        replayed_second = next(
            item for item in self.database.load_operations(self.telegram_id, limit=-1)
            if item["id"] == second["id"]
        )
        self.assertEqual(tax["id"], tax_id)
        self.assertEqual(tax["saved_before"], Decimal("100"))
        self.assertEqual(tax["monthly_amount"], Decimal("0"))
        self.assertEqual(
            replayed_second["payload"]["allocations"]["КЖ:Налоги"],
            Decimal("100"),
        )

    def test_legacy_income_touching_a_plan_is_blocked_without_mutation(self):
        allocator = self.allocator()
        self.database.save_allocator(self.telegram_id, allocator)
        self.database.add_planned_payment(
            self.telegram_id, "Жизнь", "Жизнь", "Платёж",
            Decimal("1000"), Decimal("100"), "2026-12-01",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        allocator.state.life_balance = Decimal("100")
        allocator.state.period_income = Decimal("100")
        allocator.state.operation_log.append({
            "type": "income_distribution",
            "income": Decimal("100"),
            "tax": Decimal("0"),
            "allocations": {"КЖ:Жизнь": Decimal("100")},
        })
        self.database.save_allocator(self.telegram_id, allocator)
        operation = self.database.load_operations(self.telegram_id)[0]

        with self.assertRaisesRegex(IncomeDeletionError, "защищённого снимка"):
            self.delete(operation["id"])

        self.assertEqual(self.database.load_state(self.telegram_id).life_balance, Decimal("100"))
        self.assertEqual(len(self.database.load_operations(self.telegram_id)), 1)

    def test_transaction_restores_ledger_and_payment_when_final_save_fails(self):
        allocator = self.allocator(
            critical_life=Decimal("1000"),
            average_income=Decimal("5000"),
            life_categories={"Образование": Decimal("1000")},
        )
        self.database.save_allocator(self.telegram_id, allocator)
        self.database.add_planned_payment(
            self.telegram_id, "Образование", "Образование", "Семестр",
            Decimal("2000"), Decimal("1000"), "2026-11-01",
        )
        allocator = self.database.load_allocator(self.telegram_id)
        _, income = self.save_income(allocator, "3000", apply_payments=True)

        def fail_rebuild(_allocator, _telegram_id):
            raise RuntimeError("simulated write failure")

        with self.assertRaisesRegex(RuntimeError, "simulated write failure"):
            delete_income_safely(
                self.database,
                self.telegram_id,
                income["id"],
                reconcile_taxes=reconcile_tax_obligation_balances,
                rebuild_period_analytics=fail_rebuild,
                today=date(2026, 9, 10),
            )

        self.assertTrue(any(
            item.get("id") == income["id"]
            for item in self.database.load_operations(self.telegram_id)
        ))
        self.assertEqual(
            self.database.load_planned_payments(self.telegram_id)[0]["saved_amount"],
            Decimal("1000"),
        )

    def test_protected_delta_keeps_later_money_in_the_correct_pillow_layer(self):
        allocator = self.allocator()
        allocator.state = AllocatorState(
            pillow_minimum=Decimal("20"),
            pillow_force_majeure=Decimal("30"),
            period_income=Decimal("25"),
        )
        before = allocator._income_rollback_snapshot()
        before.update({
            "pillow_minimum": Decimal("10"),
            "pillow_force_majeure": Decimal("0"),
            "period_income": Decimal("0"),
        })
        after = dict(before)
        after.update({
            "pillow_minimum": Decimal("20"),
            "pillow_force_majeure": Decimal("5"),
            "period_income": Decimal("15"),
        })
        allocator.rollback_income_operation({
            "type": "income_distribution",
            "income": Decimal("15"),
            "tax": Decimal("0"),
            "allocations": {"Подушка": Decimal("15")},
            "state_before": before,
            "credits_before": {},
            "rollback_context": {"state_after": after, "credits_after": {}},
        })
        self.assertEqual(allocator.state.pillow_minimum, Decimal("10"))
        self.assertEqual(allocator.state.pillow_force_majeure, Decimal("25"))
        self.assertEqual(allocator.state.period_income, Decimal("10"))

    def test_protected_delta_restores_early_repayment_without_erasing_later_one(self):
        credit = Credit(
            name="Кредит",
            principal_balance=Decimal("30"),
            full_repayment_amount=None,
            annual_rate=Decimal("10"),
            minimum_payment=Decimal("5"),
            status="Активный",
        )
        allocator = self.allocator(has_debts=True, credits=[credit])
        allocator.state.early_repayment = Decimal("70")
        allocator.state.period_income = Decimal("70")
        before = allocator._income_rollback_snapshot()
        before.update({"early_repayment": Decimal("0"), "period_income": Decimal("0")})
        after = dict(before)
        after.update({"early_repayment": Decimal("40"), "period_income": Decimal("40")})
        allocator.rollback_income_operation({
            "type": "income_distribution",
            "income": Decimal("40"),
            "tax": Decimal("0"),
            "allocations": {"Досрочное": Decimal("40")},
            "state_before": before,
            "credits_before": {
                "Кредит": {"principal_balance": Decimal("100"), "status": "Активный"},
            },
            "rollback_context": {
                "state_after": after,
                "credits_after": {
                    "Кредит": {"principal_balance": Decimal("60"), "status": "Активный"},
                },
            },
        })
        self.assertEqual(credit.principal_balance, Decimal("70"))
        self.assertEqual(allocator.state.early_repayment, Decimal("30"))
        self.assertEqual(allocator.state.period_income, Decimal("30"))


if __name__ == "__main__":
    unittest.main()
