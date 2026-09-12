import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto

import income
from financial_engine import AllocatorState, FinancialAllocator, UserSettings


class FakeState:
    def __init__(self, state=None, data=None):
        self.value = state
        self.data = dict(data or {})
        self.clear = AsyncMock()

    async def get_state(self):
        return self.value

    async def get_data(self):
        return dict(self.data)


class MutableFakeState(FakeState):
    async def set_state(self, value):
        self.value = getattr(value, "state", value)

    async def update_data(self, **values):
        self.data.update(values)


class IncomeInputTests(unittest.TestCase):
    def test_parser_accepts_russian_and_international_grouping(self):
        self.assertEqual(income.parse_decimal("1,234.56"), Decimal("1234.56"))
        self.assertEqual(income.parse_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(income.parse_decimal("125 000,50 ₽"), Decimal("125000.50"))

    def test_parser_rejects_non_finite_exponents_and_subkopecks(self):
        for raw in (
            "NaN", "Infinity", "-Infinity", "1e3", "1E999999", "0.001",
            "1,234,56", "12.34.56", "1000000000001",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(income.parse_decimal(raw))

    def test_financial_core_rejects_non_finite_income(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("100"),
        ))
        for value in (Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                allocator.process_income(value, "Доход")


class IncomeFlowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def button_rows(message):
        markup = message.answer.await_args.kwargs["reply_markup"]
        return [[button.text for button in row] for row in markup.inline_keyboard]

    async def test_income_type_picker_hides_legacy_tax_profile_records(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("100"),
            income_type_tax_rates={
                "Зарплата": Decimal("6"),
                "Самозанятость · Физики · 3%": Decimal("3"),
                "ИП · УСН · 6%": Decimal("6"),
                "Халтура": Decimal("0"),
            },
            income_tax_profiles={
                "НПД · ФЛ · 3%": {"rate": "3"},
                "ИП · УСН · 6%": {"rate": "6"},
            },
        )
        message = SimpleNamespace(answer=AsyncMock())
        state = MutableFakeState(data={"income_flow_id": "flow"})

        await income.show_income_types(message, state, settings)

        rows = self.button_rows(message)
        self.assertEqual(rows[:1], [["Зарплата", "Халтура"]])
        self.assertNotIn("Самозанятость · Физики · 3%", sum(rows, []))
        self.assertNotIn("ИП · УСН · 6%", sum(rows, []))
        self.assertEqual(state.data["available_income_types"], ["Зарплата", "Халтура"])

    async def test_one_off_tax_editor_uses_compact_nested_menus(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("100"),
            income_type_tax_rates={"Зарплата": Decimal("6")},
        ))
        message = SimpleNamespace(answer=AsyncMock())
        state = MutableFakeState(data={
            "income_flow_id": "flow",
            "income_amount": "1000",
            "income_type": "Зарплата",
        })
        with patch.object(income.db, "load_allocator", return_value=allocator):
            await income.show_income_tax_edit_menu(message, state, 42)
        self.assertEqual(self.button_rows(message), [
            ["Самозанятость", "ИП"],
            ["Без налога", "Ввести свой %"],
            ["Ввести сумму налога"],
            ["← Главное меню", "← Назад"],
        ])

        callback = SimpleNamespace(
            data="taxedit:subject:self|flow",
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=42),
            message=message,
        )
        await income.tax_edit_subject(callback, state)
        self.assertEqual(message.answer.await_args.args[0], (
            "<b>САМОЗАНЯТОСТЬ</b>\n\n"
            "<b>НПД</b> — налог на профессиональный доход;\n"
            "<b>ФЛ</b> — физлица,\n"
            "<b>ЮЛ</b> — юрлица."
        ))
        self.assertEqual(self.button_rows(message), [
            ["НПД · ФЛ · 4%", "НПД · ЮЛ · 6%"],
            ["НПД · ФЛ · 3%", "НПД · ЮЛ · 4%"],
            ["← Главное меню", "← Назад"],
        ])

        callback.data = "taxedit:subject:ip|flow"
        await income.tax_edit_subject(callback, state)
        self.assertEqual(self.button_rows(message), [
            ["УСН · 6%", "Своя ставка"],
            ["← Главное меню", "← Назад"],
        ])

    async def test_stale_cancel_cannot_clear_another_wizard(self):
        state = FakeState("Taxes:amount", {"income_flow_id": "current"})
        callback = SimpleNamespace(
            data="income:cancel|old",
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )

        accepted = await income.require_current_flow(callback, state)

        self.assertFalse(accepted)
        state.clear.assert_not_awaited()
        self.assertIn("другая операция", callback.message.answer.await_args.args[0])

    async def test_missing_income_image_still_sends_report_with_navigation(self):
        message = SimpleNamespace(answer=AsyncMock())
        markup = object()
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.png"
            await income.send_photo_with_sections(
                message, missing, ["Отчёт сохранён"], reply_markup=markup,
            )

        self.assertEqual(message.answer.await_args.args[0], "Отчёт сохранён")
        self.assertIs(message.answer.await_args.kwargs["reply_markup"], markup)

    async def test_telegram_photo_error_falls_back_to_text_and_navigation(self):
        message = SimpleNamespace(
            answer_photo=AsyncMock(side_effect=TelegramBadRequest(
                method=SendPhoto(chat_id=1, photo="x"), message="bad media",
            )),
            answer=AsyncMock(),
        )
        markup = object()
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "income.png"
            image.write_bytes(b"not important")
            await income.send_photo_with_sections(
                message, image, ["Доход распределён"], reply_markup=markup,
            )

        self.assertEqual(message.answer.await_args.args[0], "Доход распределён")
        self.assertIs(message.answer.await_args.kwargs["reply_markup"], markup)

    async def test_live_report_merges_taxes_and_shows_household_reserve(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="cyclic",
            income_rhythm="cyclic",
            critical_life=Decimal("100"),
            household_reserve=Decimal("50"),
            average_income=Decimal("1000"),
            life_categories={"Жизнь": Decimal("100")},
            household_reserve_categories={"Продукты": Decimal("30")},
            income_gap_months=Decimal("1"),
        ), AllocatorState(
            intercontract_reserve=Decimal("200"),
            pillow_force_majeure=Decimal("100"),
            pillow_stabilizer=Decimal("50"),
        ))
        result = SimpleNamespace(
            income=Decimal("100"),
            tax=Decimal("10"),
            allocations={
                "КЖ:Налоги": Decimal("20"),
                "Бытовой резерв": Decimal("30"),
                "Фонд Зарплаты": Decimal("5"),
                "Подушка": Decimal("4"),
                "Стабилизатор дохода": Decimal("3"),
                "Инвестиции": Decimal("2"),
            },
            super_stage_allocated=Decimal("0"),
            checks={"total": Decimal("100"), "income": Decimal("100"),
                    "difference": Decimal("0"), "ok": True},
            steps=[],
        )
        message = SimpleNamespace(from_user=SimpleNamespace(id=10))
        with (
            patch.object(income, "send_photo_with_sections", new=AsyncMock()) as send,
            patch.object(income, "main_menu_keyboard", return_value=object()),
        ):
            await income.send_distribution_report(
                message, allocator, result, "Контракт", date(2026, 9, 12),
            )

        text = "\n".join(send.await_args.args[2])
        self.assertEqual(text.count("🏛️ <b>Налоги</b>"), 1)
        self.assertIn("🏛️ <b>Налоги</b> — 30", text)
        self.assertNotIn("🏛️ <b>Налог</b>", text)
        self.assertIn("💚 <b>Бытовой резерв</b> — 30", text)
        self.assertLess(text.index("🏦 <b>Фонд Зарплаты</b>"), text.index("🛡️ <b>Подушка</b>"))


if __name__ == "__main__":
    unittest.main()
