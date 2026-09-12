"""Contracts for complete, navigable period and income reports."""

from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import dashboard
from financial_engine import Goal
from storage import Database


def income_operation(operation_id=1, *, amount="100", income_type="Работа"):
    return {
        "id": operation_id,
        "type": "income_distribution",
        "created_at": "2026-09-10T12:00:00",
        "payload": {
            "date": "2026-09-10",
            "income_type": income_type,
            "income_type_id": "income-1",
            "income": amount,
            "tax": "10",
            "allocations": {"КЖ:Квартира": "90"},
            "envelope_ids": {"КЖ:Квартира": "life:home"},
        },
    }


class PeriodLedgerTests(unittest.TestCase):
    def test_legacy_history_uses_period_start_to_hide_old_income_deletion(self):
        operations = [
            {
                "id": 2,
                "type": "income_distribution",
                "payload": {"date": "2026-09-10"},
            },
            {
                "id": 1,
                "type": "income_distribution",
                "payload": {"date": "2026-08-20"},
            },
        ]
        allocator = SimpleNamespace(
            state=SimpleNamespace(period_started_at="2026-09-01T00:00:00+03:00")
        )
        with patch("dashboard.db") as db:
            db.load_operations.return_value = operations
            db.load_allocator.return_value = allocator
            result = dashboard.current_period_income_ids(42)

        self.assertEqual(result, {2})

    def test_envelope_transfers_move_only_open_period_money(self):
        operations = [
            {
                "type": "envelope_transfer",
                "payload": {
                    "source": "Цели:Отпуск",
                    "source_id": "goal:old",
                    "destination": "Цели:Будущие покупки",
                    "destination_id": "goal:system",
                    # The bank balance includes older periods.
                    "amount": "500",
                },
            },
            {
                "type": "envelope_transfer",
                "payload": {
                    "source": "КЖ:Квартира",
                    "source_id": "life:home",
                    "destination": "КЖ:Зарплата",
                    "amount": "30",
                },
            },
            {
                "type": "income_distribution",
                "payload": {
                    "income": "140",
                    "tax": "10",
                    "allocations": {
                        "КЖ:Квартира": "30",
                        "Цели:Отпуск": "100",
                    },
                    "envelope_ids": {
                        "КЖ:Квартира": "life:home",
                        "Цели:Отпуск": "goal:old",
                    },
                },
            },
            {"type": "period_reset", "payload": {}},
        ]

        income, tax, allocations, has_income = dashboard.period_ledger_snapshot(operations)

        self.assertTrue(has_income)
        self.assertEqual(income, D("140"))
        self.assertEqual(tax, D("10"))
        self.assertNotIn("КЖ:Квартира", allocations)
        self.assertNotIn("Цели:Отпуск", allocations)
        self.assertEqual(allocations["КЖ:Зарплата"], D("30"))
        # Only this period's 100 is moved, not the all-time balance of 500.
        self.assertEqual(allocations["Цели:Будущие покупки"], D("100"))

    def test_period_reader_requests_the_whole_ledger(self):
        allocator = SimpleNamespace(
            state=SimpleNamespace(
                period_allocations={"Инвестиции": D("1")},
                period_income=D("999"),
                period_tax=D("999"),
            )
        )
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [income_operation()]
            totals = dashboard.get_period_allocations(allocator, 42)

        db.load_operations.assert_called_once_with(42, limit=-1)
        self.assertEqual(totals, {"КЖ:Квартира": D("90")})
        self.assertEqual(totals.period_income, D("100"))
        self.assertEqual(totals.period_tax, D("10"))

    def test_real_database_keeps_more_than_one_thousand_period_incomes(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "period-ledger.db")
            try:
                user = 42
                database.ensure_user(user)
                database.save_operation(user, "period_reset", {})
                with database.transaction():
                    for index in range(1001):
                        database.save_operation(user, "income_distribution", {
                            "type": "income_distribution",
                            "income": "1",
                            "tax": "0",
                            "allocations": {"Инвестиции": "1"},
                            "income_type": f"Источник {index}",
                        })
                allocator = SimpleNamespace(state=SimpleNamespace(
                    period_allocations={}, period_income=D("0"), period_tax=D("0"),
                ))
                with patch("dashboard.db", database):
                    totals = dashboard.get_period_allocations(allocator, user)
            finally:
                database.close()

        self.assertEqual(totals.period_income, D("1001"))
        self.assertEqual(totals["Инвестиции"], D("1001"))


class BalanceChartCompletenessTests(unittest.TestCase):
    def test_chart_tax_uses_the_same_ledger_snapshot_as_its_slices(self):
        allocator = SimpleNamespace(
            state=SimpleNamespace(period_tax=D("999"), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={},
                household_reserve_category_ids={},
                goals=[],
            ),
        )
        allocations = dashboard.PeriodAllocations(
            {"КЖ:Налоги": D("5"), "КЖ:Квартира": D("85")},
            period_income=D("100"),
            period_tax=D("10"),
            has_income_records=True,
        )

        values, _ = dashboard.period_balance_chart(allocator, allocations)

        self.assertEqual(values["Налог"], D("15"))

    def test_every_positive_family_and_archived_envelope_is_visible_in_order(self):
        allocator = SimpleNamespace(
            state=SimpleNamespace(period_tax=D("10"), period_life_topups={}),
            settings=SimpleNamespace(
                life_category_ids={"Квартира": "home"},
                household_reserve_category_ids={"Дети": "children"},
                goals=[Goal("Отпуск", D("20"), uid="vacation")],
            ),
        )
        allocations = dashboard.PeriodAllocations({
            "КЖ:Налоги": D("5"),
            "Фонд Зарплаты": D("1"),
            "Подушка": D("2"),
            "Стабилизатор дохода": D("3"),
            "Инвестиции": D("4"),
            "Мин. платеж": D("5"),
            "Досрочное": D("6"),
            "Рабочие обязательства:Карта:Связь": D("7"),
            "КЖ:Квартира": D("20"),
            "КЖ:Старая · прежний abc123": D("15"),
            "КЖ:Зарплата": D("10"),
            "БР:Дети": D("8"),
            "БР:Старый · прежний def456": D("7"),
            "Бытовой резерв": D("6"),
            "Цели:Отпуск": D("9"),
            "Цели:Хотелки · прежний ghi789": D("4"),
            "Разовое отчисление": D("2"),
        }, envelope_kinds={"Цели:Хотелки · прежний ghi789": "chest"})

        values, colors = dashboard.period_balance_chart(allocator, allocations)
        labels = list(values)

        self.assertEqual(labels[:7], [
            "Налог",
            "Фонд Зарплаты",
            "Подушка",
            "Стабилизатор",
            "Инвестиции",
            "Минимальные платежи по долгам",
            "Досрочное погашение",
        ])
        self.assertLess(labels.index("Рабочие обязательства · Карта · Связь"), labels.index("КМ · Квартира"))
        self.assertLess(labels.index("КМ · Квартира"), labels.index("КМ · Зарплата"))
        self.assertLess(labels.index("КМ · Зарплата"), labels.index("Бытовой резерв · Дети"))
        self.assertLess(labels.index("Бытовой резерв"), labels.index("Цели и Сундуки · Отпуск"))
        self.assertIn("КМ · Старая · прежняя категория", values)
        self.assertIn("Бытовой резерв · Старый · прежний конверт", values)
        self.assertIn("Цели и Сундуки · Сундук Хотелок · прежняя позиция", values)
        self.assertIn("Прочее · Разовое отчисление", values)
        self.assertEqual(values["Налог"], D("15"))
        self.assertEqual(
            sum(values.values(), D("0")),
            D("10") + sum(allocations.values(), D("0")),
        )
        self.assertEqual(colors["Бытовой резерв"], "#24734A")


class IncomeHistoryNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_period_income_does_not_offer_impossible_deletion(self):
        message = SimpleNamespace(answer=AsyncMock())
        operation = income_operation(17)
        operations = [
            {"id": 16, "type": "period_reset", "payload": {}},
            operation,
        ]
        with patch("dashboard.db") as db:
            db.load_operations.return_value = operations
            shown = await dashboard.send_income_history_detail(message, 42, 17)

        self.assertTrue(shown)
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        self.assertNotIn("🗑️ Удалить доход", buttons)
        self.assertIn("Показать распределение", buttons)
        self.assertIn("+ Заметка", buttons)

    async def test_closed_period_income_note_remains_editable(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            data="incomehistory:note:17",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        with patch("dashboard.find_income_history_operation", return_value=income_operation(17)):
            await dashboard.ask_history_income_note(callback, state)

        state.update_data.assert_awaited_once_with(history_note_operation_id=17)
        state.set_state.assert_awaited_once()
        self.assertIn(
            "ЗАМЕТКА К ПОСТУПЛЕНИЮ",
            callback.message.answer.await_args.args[0],
        )

    def test_history_distribution_merges_all_tax_routes_into_one_envelope(self):
        operation = income_operation()
        operation["payload"]["tax"] = "10"
        operation["payload"]["allocations"] = {
            "Налог": "1",
            "КЖ:Налоги": "4",
            "КЖ:Квартира": "85",
        }
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))

        text = dashboard.income_distribution_text(operation, allocator)

        self.assertEqual(text.count("🏛️"), 1)
        self.assertIn("🏛️ <b>Налоги</b> — 15", text)
        self.assertNotIn("<b>Налог</b>", text)

    def test_history_distribution_keeps_household_sub_envelopes(self):
        operation = income_operation()
        operation["payload"]["allocations"] = {
            "БР:Одежда": "20",
            "БР:Подарки": "30",
            "Бытовой резерв": "5",
        }
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))

        text = dashboard.income_distribution_text(operation, allocator)

        self.assertIn("💚 <b>Подарки</b> — 30", text)
        self.assertIn("💚 <b>Одежда</b> — 20", text)
        self.assertIn("💚 <b>Бытовой резерв</b> — 5", text)
        self.assertLess(text.index("Подарки"), text.index("Одежда"))

    def test_history_distribution_keeps_deleted_chest_icon_by_saved_kind(self):
        operation = income_operation()
        archived_key = "Цели:Хотелки · прежний abc123"
        operation["payload"]["allocations"] = {archived_key: "50"}
        operation["payload"]["envelope_kinds"] = {archived_key: "chest"}
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))

        text = dashboard.income_distribution_text(operation, allocator)

        self.assertIn("🧳 <b>Сундук Хотелок · прежняя позиция</b> — 50", text)
        self.assertNotIn("abc123", text)

    def test_history_button_keeps_the_year_and_fits(self):
        item = income_operation(
            income_type="Очень длинное название частного занятия с учеником",
        )

        label = dashboard.income_history_button_label(item)

        self.assertTrue(label.startswith("10.09.2026 · "))
        self.assertLessEqual(len(label), 64)
        self.assertTrue(label.endswith(" · 100"))

    def test_note_editor_has_delete_back_cancel_and_main_menu(self):
        markup = dashboard.income_note_edit_keyboard(17, has_note=True)
        buttons = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(buttons, [
            "🗑️ Удалить заметку",
            "← Назад",
            "✗ Отмена",
            "← Главное меню",
        ])

    async def test_delete_note_clears_only_note_and_returns_to_income(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            data="incomehistory:note_delete:17",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock())
        with (
            patch("dashboard.db") as db,
            patch("dashboard.send_income_history_detail", new=AsyncMock()) as detail,
        ):
            db.update_income_note.return_value = True
            await dashboard.delete_history_income_note(callback, state)

        db.update_income_note.assert_called_once_with(42, 17, "")
        state.clear.assert_awaited_once()
        detail.assert_awaited_once_with(callback.message, 42, 17)

    async def test_stale_income_card_always_offers_navigation(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("dashboard.find_income_history_operation", return_value=None):
            shown = await dashboard.send_income_history_detail(message, 42, 999)

        self.assertFalse(shown)
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(buttons, ["← К истории", "← Главное меню"])


class IncomeAnalysisDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_reads_all_rows_and_exposes_every_source(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={"Работа": "income-1", "Подарок": "income-2"},
            income_type_labels={"income-1": "Работа", "income-2": "Подарок"},
        ))
        operations = [
            income_operation(2, amount="20", income_type="Подарок"),
            income_operation(1, amount="80", income_type="Работа"),
        ]
        operations[0]["payload"]["income_type_id"] = "income-2"
        with (
            patch("dashboard.db") as db,
            patch("dashboard.send_chart_report", new=AsyncMock()) as report,
        ):
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = operations
            await dashboard.send_income_analysis(message, 42)

        db.load_operations.assert_called_once_with(42, limit=-1)
        args, kwargs = report.await_args
        self.assertEqual(args[1], {"Работа": D("80"), "Подарок": D("20")})
        self.assertTrue(kwargs["preserve_order"])
        self.assertIn("Работа", kwargs["fallback_text"])
        self.assertIn("Подарок", kwargs["fallback_text"])


if __name__ == "__main__":
    unittest.main()
