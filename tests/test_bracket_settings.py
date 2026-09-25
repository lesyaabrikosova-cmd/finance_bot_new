import tempfile
import os
import unittest
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from brackets import (overview_text, bracket_rows, simulate, preview_text, rates_of,
                      receive_rates, receive_amount, save_brackets, open_brackets,
                      show_bracket_card, show_preview)
from financial_engine import FinancialAllocator, UserSettings, Goal
from planned_payments import refresh_planned_payment_targets
from taxes import refresh_planned_tax_targets
from storage import Database


def allocator(profile="piecework"):
    a = FinancialAllocator(UserSettings(
        has_debts=False, profile_type=profile, employment_type="Фрилансер",
        income_rhythm={"piecework": "irregular", "stable": "monthly", "cyclic": "cyclic"}[profile],
        critical_life=D(100), household_reserve=D(100), average_income=D(1000),
        force_majeure_months=D(4), stabilizer_target_months=D(2),
        income_type_tax_rates={"Заказ": D(10), "Подарок": D(0)},
        goals=[Goal("Цель", D(100), target_amount=D(10))],
    ))
    return a


class State:
    def __init__(self, **data):
        self.data = data
        self.value = None

    async def get_data(self):
        return self.data

    async def update_data(self, **data):
        self.data.update(data)

    async def set_state(self, value):
        self.value = value

    async def clear(self):
        self.data = {}
        self.value = None


def message(text=""):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=42), answer=AsyncMock(), answer_photo=AsyncMock())


def callback():
    msg = message()
    return SimpleNamespace(message=msg, from_user=msg.from_user, answer=AsyncMock())


class BracketPresentationTests(unittest.TestCase):
    def test_rows_follow_profiles_and_custom_rates(self):
        for profile in ("stable", "piecework", "cyclic"):
            a = allocator(profile)
            a.settings.set_brackets(10, 10, 15, 20)
            text = overview_text(a)
            self.assertEqual("Фонд Зарплаты" in text, profile == "cyclic")
            self.assertEqual([r["rate"] for r in bracket_rows(a)], [D(10), D(10), D(15), D(20)])
            self.assertLess(len(text), 4096)

    def test_preview_matches_engine_and_does_not_mutate_source(self):
        a = allocator()
        before = deepcopy(a)
        simulated, result = simulate(a, D(10000), "Заказ", [10, 10, 15, 20])
        expected = deepcopy(a)
        expected.settings.set_brackets(10, 10, 15, 20)
        expected.settings.protective_stage_c_strategy = "balanced"
        expected_result = expected.process_income(D(10000), "Заказ", income_date=date.today())
        self.assertEqual(result.allocations, expected_result.allocations)
        self.assertEqual(result.tax, D(1000))
        self.assertEqual(simulated.state.goal_balances["Цель"], D(10))
        self.assertEqual(a.state, before.state)
        self.assertEqual(a.settings, before.settings)
        self.assertLess(len(preview_text(a, simulated, result, "Заказ", "balanced", True)), 4096)

    def test_deficit_warning_is_about_remaining_km(self):
        a = allocator()
        simulated, result = simulate(a, D(50), "Подарок")
        text = preview_text(a, simulated, result, "Подарок", "balanced", False)
        self.assertIn("Критического минимума", text)
        self.assertIn("60", text)
        self.assertEqual(rates_of(a), ["20", "25", "30", "35"])

    def test_refresh_functions_allow_read_only_preview(self):
        a = allocator()
        with patch("planned_payments.db") as db:
            db.load_planned_payments.return_value = [{"id": 1, "envelope_name": "Связь", "monthly_amount": D(5),
                                                      "target_amount": D(100), "saved_amount": D(0), "due_date": "2026-09-10"}]
            refresh_planned_payment_targets(42, a, date(2026, 9, 9), persist=False)
            db.update_planned_payment_monthly.assert_not_called()
            self.assertEqual(a.settings.life_categories["Связь"], D(95))
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = [{"id": 1, "due_date": "2026-09-10", "tax_type": "Налог на имущество",
                                                     "object_name": "Дом", "target_amount": D(100),
                                                     "saved_before": D(0), "monthly_amount": D(5)}]
            refresh_planned_tax_targets(42, a, date(2026, 9, 9), persist=False)
            db.update_tax_obligation_monthly.assert_not_called()
            self.assertNotIn("Налог на имущество · Дом", a.settings.planned_taxes)
            self.assertEqual(a.settings.tax_catchups["Налог на имущество · Дом"], D(50))


class BracketSettingsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_developer_mode_shows_text_with_bracket_card(self):
        a = allocator()
        a.settings.developer_mode = True
        msg = message()

        with patch("brackets.render_bracket_card", return_value=b"card"):
            await show_bracket_card(msg, a)

        msg.answer_photo.assert_awaited_once()
        self.assertEqual(msg.answer.await_count, 1)
        text = msg.answer.await_args.args[0]
        self.assertIn("ТЕКСТОВАЯ ВЕРСИЯ ДЛЯ ПРОВЕРКИ", text)
        self.assertIn("<b>БРАКЕТЫ</b>", text)

    async def test_invalid_rates_do_not_write_or_change_pending(self):
        for text in ("20/10/30/35", "100/100/100/100", "10/10/NaN/20", "10/10/15", "1/2/3/Infinity"):
            state = State(bracket_base_rates=rates_of(allocator()))
            with patch("brackets.db") as db:
                a = allocator()
                db.load_allocator.return_value = a
                await receive_rates(message(text), state)
                db.save_allocator.assert_not_called()
                self.assertNotIn("pending_brackets", state.data)
                self.assertEqual(rates_of(a), ["20", "25", "30", "35"])

    async def test_valid_input_only_prepares_review(self):
        state = State(bracket_base_rates=["20", "25", "30", "35"])
        with patch("brackets.db") as db, patch("brackets.show_preview", new=AsyncMock()) as preview:
            db.load_allocator.return_value = allocator()
            await receive_rates(message("10 / 10 / 15 / 20"), state)
            db.save_allocator.assert_not_called()
            preview.assert_awaited_once()
            self.assertEqual(state.data["pending_brackets"], ["10", "10", "15", "20"])

    async def test_save_roundtrip_changes_only_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "test.db")
            a = allocator()
            a.process_income(D(500), "Подарок")
            database.save_allocator(42, a)
            before = deepcopy(database.load_allocator(42).state)
            state = State(bracket_base_rates=rates_of(a), pending_brackets=["10", "10", "15", "20"])
            with patch("brackets.db", database):
                await save_brackets(callback(), state)
            restored = database.load_allocator(42)
            self.assertEqual(rates_of(restored), ["10", "10", "15", "20"])
            self.assertEqual(restored.state, before)
            self.assertEqual(state.data, {})
            database.close()

    async def test_stale_save_is_rejected(self):
        a = allocator()
        state = State(bracket_base_rates=["1", "2", "3", "4"], pending_brackets=["10", "10", "15", "20"])
        with patch("brackets.db") as db:
            db.load_allocator.return_value = a
            await save_brackets(callback(), state)
            db.save_allocator.assert_not_called()
        self.assertEqual(rates_of(a), ["20", "25", "30", "35"])

    async def test_open_discards_unsaved_edit(self):
        state = State(pending_brackets=["10", "10", "15", "20"])
        with patch("brackets.db") as db:
            db.load_allocator.return_value = allocator()
            await open_brackets(callback(), state)
            db.save_allocator.assert_not_called()
        self.assertEqual(state.data, {})

    async def test_amount_requires_finite_positive_money(self):
        for value in ("NaN", "Infinity", "0", "-1", "1.001", "1000000000001"):
            state = State()
            with patch("brackets.show_preview", new=AsyncMock()) as preview:
                await receive_amount(message(value), state)
                preview.assert_not_called()
                self.assertNotIn("bracket_amount", state.data)

    async def test_render_preview_refreshes_only_copy(self):
        a = allocator()
        before = deepcopy(a)
        state = State(bracket_amount="500", bracket_income_type="Заказ")
        with patch("brackets.db") as db, patch("brackets.refresh_planned_payment_targets") as payment, patch("brackets.refresh_planned_tax_targets") as tax:
            db.load_allocator.return_value = a
            await show_preview(message(), state, 42)
            db.save_allocator.assert_not_called()
            self.assertFalse(payment.call_args.kwargs["persist"])
            self.assertFalse(tax.call_args.kwargs["persist"])
            self.assertIsNot(payment.call_args.args[1], a)
        self.assertEqual(a.state, before.state)
        self.assertEqual(a.settings, before.settings)


if __name__ == "__main__":
    unittest.main()
