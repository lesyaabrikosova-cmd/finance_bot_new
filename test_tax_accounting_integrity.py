import asyncio
import tempfile
import unittest
from collections import defaultdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import taxes
from financial_engine import FinancialAllocator, UserSettings
from storage import Database


def allocator_with_taxes(planned=None):
    planned = planned or {}
    settings = UserSettings(
        has_debts=False,
        employment_type="Наёмный",
        critical_life=Decimal("10000"),
        base_critical_life=Decimal("10000"),
        household_reserve=Decimal("1000"),
        average_income=Decimal("20000"),
        planned_taxes=dict(planned),
    )
    for key, amount in planned.items():
        settings.set_automatic_life_obligation(f"tax:{key}", amount)
    return FinancialAllocator(settings)


class TaxAccountingIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(f"{self.tempdir.name}/taxes.db")

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_shared_account_payment_consumes_other_virtual_buckets_once(self):
        user = 101
        first = self.db.add_tax_obligation(
            user, "Налог на имущество", "Квартира", Decimal("100"),
            Decimal("0"), 1, Decimal("100"), "2026-12-01",
        )
        self.db.add_tax_obligation(
            user, "Транспортный налог", "Автомобиль", Decimal("900"),
            Decimal("0"), 1, Decimal("900"), "2026-12-01",
        )
        self.db.save_operation(user, "income_distribution", {
            "type": "income_distribution",
            "planned_tax_details": {
                "Налог на имущество · Квартира": "100",
                "Транспортный налог · Автомобиль": "900",
            },
            "allocations": {"КЖ:Налоги": "1000"},
            "tax": "0",
        })
        self.db.save_tax_payment(
            user, "Налог на имущество · Квартира", Decimal("500"),
            obligation_id=first, tax_due_year=2026,
        )
        with patch.object(taxes, "db", self.db):
            groups, current, contributed = taxes.collect_tax_statistics(user, 2026)
            first_balance = taxes.virtual_tax_balance(
                user, "Налог на имущество · Квартира", first,
            )
        self.assertEqual(contributed, Decimal("1000"))
        self.assertEqual(current, Decimal("500"))
        self.assertEqual(first_balance, Decimal("0"))
        self.assertEqual(groups["Транспортный налог"]["total"], Decimal("500"))

    def test_deleted_and_readded_same_name_have_separate_lifecycles(self):
        user = 102
        key = "Земельный налог · Дача"
        old_id = self.db.add_tax_obligation(
            user, "Земельный налог", "Дача", Decimal("1000"),
            Decimal("0"), 1, Decimal("1000"), "2026-12-01",
        )
        self.db.save_operation(user, "income_distribution", {
            "type": "income_distribution", "planned_tax_details": {key: "500"},
            "allocations": {"КЖ:Налоги": "500"}, "tax": "0",
        })
        self.db.deactivate_tax_obligation(user, old_id, reason="deleted")
        new_id = self.db.add_tax_obligation(
            user, "Земельный налог", "Дача", Decimal("800"),
            Decimal("0"), 1, Decimal("800"), "2026-12-01",
        )
        self.db.save_operation(user, "income_distribution", {
            "type": "income_distribution", "planned_tax_details": {key: "100"},
            "allocations": {"КЖ:Налоги": "100"}, "tax": "0",
        })
        with patch.object(taxes, "db", self.db):
            self.assertEqual(taxes.virtual_tax_balance(user, key, old_id), Decimal("0"))
            self.assertEqual(taxes.virtual_tax_balance(user, key, new_id), Decimal("600"))
            self.assertEqual(taxes.virtual_tax_balance(user, key), Decimal("600"))

    def test_readded_tax_reuses_shared_account_money_in_new_plan(self):
        user = 108
        key = "Земельный налог · Дача"
        old_id = self.db.add_tax_obligation(
            user, "Земельный налог", "Дача", Decimal("1000"),
            Decimal("0"), 2, Decimal("500"), "2026-12-01",
        )
        self.db.save_operation(user, "income_distribution", {
            "type": "income_distribution", "planned_tax_details": {key: "500"},
            "allocations": {"КЖ:Налоги": "500"}, "tax": "0",
        })
        self.db.deactivate_tax_obligation(user, old_id, reason="deleted")
        with patch.object(taxes, "db", self.db):
            funded, opening = taxes.new_tax_plan_funding(
                user, key, Decimal("1000"), Decimal("0"),
            )
        self.assertEqual(funded, Decimal("500"))
        self.assertEqual(opening, Decimal("0"))
        new_id = self.db.add_tax_obligation(
            user, "Земельный налог", "Дача", Decimal("1000"),
            funded, 2, Decimal("250"), "2026-12-01",
            opening_amount=opening,
        )
        row = next(item for item in self.db.load_tax_obligations(user) if item["id"] == new_id)
        self.assertEqual(row["saved_before"], Decimal("500"))
        self.assertEqual(row["monthly_amount"], Decimal("250"))

    def test_stale_paid_cycle_uses_this_year_when_december_is_still_ahead(self):
        user = 103
        item = {
            "id": self.db.add_tax_obligation(
                user, "Транспортный налог", "Автомобиль", Decimal("12000"),
                Decimal("0"), 1, Decimal("0"), "2024-12-01", Decimal("1000"),
            ),
            "tax_type": "Транспортный налог", "object_name": "Автомобиль",
            "target_amount": Decimal("12000"), "annual_monthly_amount": Decimal("1000"),
            "due_date": "2024-12-01",
        }
        allocator = allocator_with_taxes({"Транспортный налог · Автомобиль": Decimal("1000")})
        with patch.object(taxes, "db", self.db):
            result = taxes.start_next_annual_tax_cycle(
                user, item, allocator, date(2026, 9, 12),
            )
        self.assertEqual(result["due_date"], date(2026, 12, 1))
        child = next(row for row in self.db.load_tax_obligations(user) if row["id"] == result["id"])
        self.assertEqual(child["source_obligation_id"], item["id"])
        self.assertEqual(taxes.tax_report_year(child), 2025)

    def test_global_november_reminder_exists_without_any_tax_plan_once_per_year(self):
        user = 104
        self.db.save_allocator(user, allocator_with_taxes())
        self.assertEqual(self.db.due_global_tax_notice_reminders("2026-10-31"), [])
        self.assertEqual(self.db.due_global_tax_notice_reminders("2026-11-01"), [user])
        self.db.mark_global_tax_notice_reminder_sent(user, 2026, "2026-11-01")
        self.assertEqual(self.db.due_global_tax_notice_reminders("2026-11-20"), [])
        self.assertEqual(self.db.due_global_tax_notice_reminders("2027-11-01"), [user])

    def test_arbitrary_deadline_reminders_and_snooze_are_per_obligation(self):
        user = 105
        first = self.db.add_tax_obligation(
            user, "Патент", "Кофейня — 1-й платёж", Decimal("60000"),
            Decimal("0"), 2, Decimal("30000"), "2026-10-31",
        )
        second = self.db.add_tax_obligation(
            user, "Другой налог", "Страховой взнос", Decimal("10000"),
            Decimal("0"), 2, Decimal("5000"), "2026-10-31",
        )
        rows = self.db.due_tax_payment_reminders("2026-10-01")
        self.assertEqual({row["id"] for row in rows}, {first, second})
        self.db.mark_tax_payment_reminder_sent(user, first, "2026-10-01")
        self.db.mark_tax_payment_reminder_sent(user, second, "2026-10-01")
        self.db.snooze_tax_payment_reminder(
            user, first, days=3, from_date=date(2026, 10, 24),
        )
        rows = self.db.due_tax_payment_reminders("2026-10-24")
        self.assertNotIn(first, {row["id"] for row in rows})
        self.assertIn(second, {row["id"] for row in rows})
        rows = self.db.due_tax_payment_reminders("2026-10-27")
        self.assertIn(first, {row["id"] for row in rows})

    def test_full_payment_can_be_undone_with_its_generated_cycle(self):
        user = 106
        key = "Налог на имущество · Квартира"
        allocator = allocator_with_taxes({key: Decimal("1000")})
        self.db.save_allocator(user, allocator)
        old_id = self.db.add_tax_obligation(
            user, "Налог на имущество", "Квартира", Decimal("12000"),
            Decimal("12000"), 1, Decimal("0"), "2026-12-01", Decimal("1000"),
        )
        data = {
            "tax_payment_name": key,
            "tax_payment_amount": "12000",
            "tax_payment_obligation_id": old_id,
            "tax_payment_type": "Налог на имущество",
            "tax_payment_object": "Квартира",
            "tax_payment_adjust_target": False,
        }
        with patch.object(taxes, "db", self.db), patch.object(
            taxes, "moscow_today", return_value=date(2026, 12, 1),
        ):
            payment_id, _, next_cycle = taxes._apply_tax_payment(user, data)
            payment = self.db.load_tax_payment(user, payment_id)
            self.assertEqual(payment["next_obligation_id"], next_cycle["id"])
            taxes._undo_tax_payment(user, payment_id)
        self.assertIsNone(self.db.load_tax_payment(user, payment_id))
        old = next(row for row in self.db.load_tax_obligations(user, False) if row["id"] == old_id)
        child = next(
            row for row in self.db.load_tax_obligations(user, False)
            if row["id"] == next_cycle["id"]
        )
        self.assertTrue(old["active"])
        self.assertFalse(child["active"])

    def test_payment_spill_reconciles_other_virtual_tax_plans(self):
        user = 112
        transport_key = "Транспортный налог · Автомобиль"
        property_key = "Налог на имущество · Квартира"
        allocator = allocator_with_taxes({
            transport_key: Decimal("1000"),
            property_key: Decimal("500"),
        })
        self.db.save_allocator(user, allocator)
        transport_id = self.db.add_tax_obligation(
            user, "Транспортный налог", "Автомобиль", Decimal("12000"),
            Decimal("8000"), 1, Decimal("4000"), "2026-12-01", Decimal("1000"),
        )
        property_id = self.db.add_tax_obligation(
            user, "Налог на имущество", "Квартира", Decimal("6000"),
            Decimal("5000"), 1, Decimal("1000"), "2026-12-01", Decimal("500"),
        )
        data = {
            "tax_payment_name": transport_key,
            "tax_payment_amount": "12000",
            "tax_payment_obligation_id": transport_id,
            "tax_payment_adjust_target": False,
        }
        with patch.object(taxes, "db", self.db), patch.object(
            taxes, "moscow_today", return_value=date(2026, 12, 1),
        ):
            payment_id, _, _ = taxes._apply_tax_payment(user, data)
            property_row = next(
                row for row in self.db.load_tax_obligations(user)
                if row["id"] == property_id
            )
            self.assertEqual(taxes.virtual_tax_balance(user, property_key, property_id), Decimal("1000"))
            self.assertEqual(property_row["saved_before"], Decimal("1000"))
            self.assertEqual(property_row["monthly_amount"], Decimal("5000.00"))
            taxes._undo_tax_payment(user, payment_id)
            restored = next(
                row for row in self.db.load_tax_obligations(user)
                if row["id"] == property_id
            )
            self.assertEqual(taxes.virtual_tax_balance(user, property_key, property_id), Decimal("5000"))
            self.assertEqual(restored["saved_before"], Decimal("5000"))

    def test_full_reset_recalculates_tax_targets_from_zero(self):
        user = 107
        key = "Налог на имущество · Квартира"
        self.db.save_allocator(user, allocator_with_taxes({key: Decimal("1000")}))
        annual_id = self.db.add_tax_obligation(
            user, "Налог на имущество", "Квартира", Decimal("12000"),
            Decimal("4000"), 2, Decimal("4000"), "2026-12-01", Decimal("1000"),
        )
        patent_id = self.db.add_tax_obligation(
            user, "Патент", "Кофейня", Decimal("60000"),
            Decimal("10000"), 2, Decimal("25000"), "2026-11-11", Decimal("0"),
        )
        with patch("storage.moscow_today", return_value=date(2026, 9, 1)):
            self.db.clear_accounting_history(user)
        rows = {row["id"]: row for row in self.db.load_tax_obligations(user)}
        self.assertEqual(rows[annual_id]["saved_before"], Decimal("0"))
        self.assertEqual(rows[annual_id]["monthly_amount"], Decimal("6000.00"))
        self.assertEqual(rows[patent_id]["monthly_amount"], Decimal("30000.00"))
        loaded = self.db.load_allocator(user)
        self.assertEqual(loaded.settings.planned_taxes[key], Decimal("1000.00"))
        self.assertEqual(loaded.settings.tax_catchups[key], Decimal("6000.00"))

    def test_monthly_quota_is_not_charged_twice_when_second_tax_is_added(self):
        user = 109
        first_key = "Налог на имущество · Квартира"
        second_key = "Земельный налог · Дача"
        allocator = allocator_with_taxes()
        self.db.save_allocator(user, allocator)
        first_id = self.db.add_tax_obligation(
            user, "Налог на имущество", "Квартира", Decimal("1200"),
            Decimal("0"), 12, Decimal("100"), "2027-12-01", Decimal("100"),
            monthly_period="2026-09",
        )
        with patch.object(taxes, "db", self.db), patch.object(
            taxes, "moscow_today", return_value=date(2026, 9, 12),
        ):
            taxes.refresh_planned_tax_targets(user, allocator, date(2026, 9, 12))
            first_allocations = {}
            allocator._allocate_to_life(Decimal("100"), first_allocations)
            taxes.apply_planned_tax_allocation(
                user, allocator, first_allocations["КЖ:Налоги"],
            )
            self.db.add_tax_obligation(
                user, "Земельный налог", "Дача", Decimal("2400"),
                Decimal("0"), 12, Decimal("200"), "2027-12-01", Decimal("200"),
                monthly_period="2026-09",
            )
            taxes.refresh_planned_tax_targets(user, allocator, date(2026, 9, 12))
            self.assertEqual(allocator.settings.tax_catchups[first_key], Decimal("0"))
            self.assertEqual(allocator.settings.tax_catchups[second_key], Decimal("200"))
            second_allocations = {}
            allocator._allocate_to_life(Decimal("200"), second_allocations)
            self.assertEqual(second_allocations["КЖ:Налоги"], Decimal("200"))
            taxes.apply_planned_tax_allocation(
                user, allocator, second_allocations["КЖ:Налоги"],
            )
        rows = {row["id"]: row for row in self.db.load_tax_obligations(user)}
        self.assertEqual(rows[first_id]["saved_before"], Decimal("100"))
        self.assertEqual(rows[first_id]["monthly_amount"], Decimal("0"))
        self.assertEqual(sum((row["saved_before"] for row in rows.values()), Decimal("0")), Decimal("300"))

    def test_tax_quota_runs_even_when_aggregate_life_balance_is_full(self):
        key = "Налог на имущество · Квартира"
        allocator = allocator_with_taxes({key: Decimal("100")})
        allocator.settings.tax_catchups = {key: Decimal("100")}
        allocator.state.life_balance = Decimal("50000")
        allocations = defaultdict(lambda: Decimal("0"))
        allocator.stage_a(
            Decimal("1000"), 6, [], allocations,
        )
        self.assertEqual(allocations["КЖ:Налоги"], Decimal("100"))

    def test_other_tax_has_its_own_group_and_input_rejects_non_finite_values(self):
        self.assertEqual(taxes.tax_group("Другой налог · Страховой взнос"), "Другой налог")
        self.assertEqual(taxes.tax_group("Патент · Кофейня"), "Другой налог")
        self.assertEqual(taxes.tax_group("Налог на доход · Зарплата"), "Налог на доход")
        for value in ("NaN", "Infinity", "-Infinity", "1000000000001", "1.001"):
            self.assertIsNone(taxes.parse_amount(value), value)

    def test_tax_card_never_promises_a_deadline_that_has_already_passed(self):
        item = {
            "tax_type": "Налог на имущество", "object_name": "Квартира",
            "target_amount": Decimal("12000"), "saved_before": Decimal("0"),
            "monthly_amount": Decimal("12000"),
            "annual_monthly_amount": Decimal("1000"),
            "due_date": "2026-12-01",
        }
        with patch.object(taxes, "moscow_today", return_value=date(2026, 11, 15)):
            text = taxes.tax_obligation_card_text(item, Decimal("0"))
        self.assertIn("Предварительный срок уже прошёл", text)
        self.assertNotIn("До 1 ноября предварительная сумма будет собрана", text)
        with patch.object(taxes, "moscow_today", return_value=date(2026, 12, 2)):
            overdue = taxes.tax_obligation_card_text(item, Decimal("0"))
        self.assertIn("Срок оплаты 1 декабря уже прошёл", overdue)
        self.assertNotIn("налог должен быть оплачен", overdue)

    def test_snapshot_restore_preserves_tax_and_planned_payment_state(self):
        user = 110
        obligation_id = self.db.add_tax_obligation(
            user, "Патент", "Кофейня", Decimal("60000"),
            Decimal("10000"), 2, Decimal("25000"), "2026-11-11",
            notice_received=True, monthly_period="2026-09",
        )
        original = next(row for row in self.db.load_tax_obligations(user, False) if row["id"] == obligation_id)
        payment_id = self.db.add_planned_payment(
            user, "Бытовой резерв", "Бытовой резерв", "Страховка",
            Decimal("12000"), Decimal("1000"), "2027-01-01",
        )
        original_payment = next(row for row in self.db.load_planned_payments(user, False) if row["id"] == payment_id)
        with self.db.transaction():
            self.db.update_tax_obligation_plan(
                user, obligation_id, target_amount=Decimal("1"), months=1,
                monthly_amount=Decimal("1"), annual_monthly_amount=Decimal("0"),
            )
            self.db.update_planned_payment_saved(user, payment_id, Decimal("999"), False)
            self.assertTrue(self.db.restore_tax_obligation_snapshot(user, original))
            self.assertTrue(self.db.restore_planned_payment_snapshot(user, original_payment))
        restored = next(row for row in self.db.load_tax_obligations(user, False) if row["id"] == obligation_id)
        restored_payment = next(row for row in self.db.load_planned_payments(user, False) if row["id"] == payment_id)
        self.assertEqual(restored, original)
        self.assertEqual(restored_payment, original_payment)


class _MemoryState:
    def __init__(self, data: dict):
        self.data = dict(data)
        self.current = taxes.TaxStates.payment_review.state

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **values):
        self.data.update(values)

    async def get_state(self):
        return self.current

    async def set_state(self, value):
        self.current = getattr(value, "state", value)

    async def clear(self):
        self.data.clear()
        self.current = None


class TaxPaymentConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_tax_rolls_back_if_allocator_save_fails(self):
        tempdir = tempfile.TemporaryDirectory()
        database = Database(f"{tempdir.name}/taxes.db")
        user = 113
        database.save_allocator(user, allocator_with_taxes())
        state = _MemoryState({
            "tax_goal_amount": "12000",
            "tax_goal_saved": "0",
            "tax_goal_type": "Налог на имущество",
            "tax_goal_name": "Квартира",
        })
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=user), answer=AsyncMock(),
        )
        original_save = database.save_allocator
        try:
            with patch.object(taxes, "db", database), patch.object(
                database, "save_allocator", side_effect=RuntimeError("disk full"),
            ):
                with self.assertRaisesRegex(RuntimeError, "disk full"):
                    await taxes.save_tax_obligation(
                        message, state, user, 2, "2026-12-01",
                    )
            self.assertEqual(database.load_tax_obligations(user), [])
        finally:
            database.save_allocator = original_save
            database.close()
            tempdir.cleanup()

    async def test_double_tap_payment_confirmation_is_idempotent(self):
        tempdir = tempfile.TemporaryDirectory()
        database = Database(f"{tempdir.name}/taxes.db")
        taxes._PAYMENT_CONFIRM_LOCKS.clear()
        state = _MemoryState({
            "tax_payment_name": "Другой налог",
            "tax_payment_amount": "100",
            "tax_payment_flow_token": "same-token",
        })
        message = SimpleNamespace(answer=AsyncMock())
        first = SimpleNamespace(
            data="taxpayment:confirm:same-token",
            from_user=SimpleNamespace(id=111), message=message, answer=AsyncMock(),
        )
        second = SimpleNamespace(
            data="taxpayment:confirm:same-token",
            from_user=SimpleNamespace(id=111), message=message, answer=AsyncMock(),
        )
        try:
            with patch.object(taxes, "db", database), patch.object(
                taxes, "_apply_tax_payment", return_value=(1, None, None),
            ) as apply_payment:
                await asyncio.gather(
                    taxes.tax_payment_confirm(first, state),
                    taxes.tax_payment_confirm(second, state),
                )
            self.assertEqual(apply_payment.call_count, 1)
            rendered = "\n".join(
                str(call.args[0]) for call in message.answer.await_args_list if call.args
            )
            self.assertIn("уже неактуален", rendered)
        finally:
            database.close()
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
