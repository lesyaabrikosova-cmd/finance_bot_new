import unittest
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from debts import show_debt_card
from financial_engine import (
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    PhaseLifeBudget,
    UserSettings,
)
from goals_manager import show_position_card
from period import show_phase_life_menu
from settings_editor import (
    save_cycle_phase_setting,
    save_cycle_predictability_setting,
)
from storage import Database


def button_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


class PostOnboardingEditorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_switching_to_cyclic_normalizes_reserves_and_sets_current_phase(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("50000"),
            household_reserve=Decimal("10000"),
            average_income=Decimal("100000"),
            income_rhythm="monthly",
            force_majeure_months=Decimal("3"),
            minimum_reserve_months=Decimal("1"),
        )
        allocator = FinancialAllocator(settings, AllocatorState())
        state_data = {
            "settings_cycle_phase": "break",
            "settings_cycle_profile_pending": True,
            "settings_gap_months": "7",
            "settings_work_months": "5",
        }

        async def update_data(**values):
            state_data.update(values)

        state = SimpleNamespace(
            get_data=AsyncMock(side_effect=lambda: dict(state_data)),
            update_data=AsyncMock(side_effect=update_data),
            clear=AsyncMock(),
        )
        message = SimpleNamespace(
            text="3",
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        with (
            patch("settings_editor.db.load_allocator", return_value=allocator),
            patch("settings_editor.db.save_allocator") as save,
            patch("settings_editor.show_profile_income_settings", new=AsyncMock()),
        ):
            await save_cycle_phase_setting(message, state)
            callback = SimpleNamespace(
                data="settingscyclepredictability:known",
                from_user=SimpleNamespace(id=42),
                message=message,
                answer=AsyncMock(),
            )
            await save_cycle_predictability_setting(callback, state)

        self.assertEqual(allocator.profile_id, "cyclic")
        self.assertEqual(allocator.settings.force_majeure_months, Decimal("6"))
        self.assertEqual(allocator.settings.minimum_reserve_months, Decimal("2"))
        self.assertFalse(allocator.settings.cyclic_income_uncertain)
        self.assertEqual(allocator.state.current_cycle_phase, "break")
        self.assertEqual(allocator.state.intercontract_months_remaining, Decimal("3"))
        save.assert_called_once()

    async def test_debt_card_exposes_every_onboarding_field_and_manual_order(self):
        settings = UserSettings(
            has_debts=True,
            employment_type="Наёмный",
            critical_life=Decimal("50000"),
            household_reserve=Decimal("10000"),
            average_income=Decimal("100000"),
            minimum_reserve_months=Decimal("1"),
            debt_strategy="Ручной выбор",
            credits=[
                Credit(
                    "Кредитка",
                    Decimal("100000"),
                    Decimal("101500"),
                    Decimal("29.9"),
                    Decimal("7000"),
                    "Дифференцированный",
                    "Уменьшать платёж",
                ),
                Credit("Автокредит", 200000, None, 15, 12000),
            ],
        )
        allocator = FinancialAllocator(settings, AllocatorState())
        message = SimpleNamespace(answer=AsyncMock())
        with patch("debts.db.load_allocator", return_value=allocator):
            await show_debt_card(message, 42, 0)

        text = message.answer.await_args.args[0]
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("Полное погашение", text)
        self.assertIn("Дифференцированный", text)
        self.assertIn("Уменьшать платёж", text)
        self.assertIn("✎ Изменить данные кредита", button_texts(markup))
        self.assertIn("↓ Ниже в очереди", button_texts(markup))

    async def test_goal_card_shows_buffer_editor(self):
        goal = Goal(
            "Отпуск",
            Decimal("100"),
            target_amount=Decimal("100000"),
            buffer_enabled=True,
            buffer_percent=Decimal("15"),
        )
        allocator = FinancialAllocator(
            UserSettings(
                has_debts=False,
                employment_type="Наёмный",
                critical_life=Decimal("10000"),
                household_reserve=Decimal("1000"),
                average_income=Decimal("50000"),
                goals=[goal],
            ),
            AllocatorState(),
        )
        message = SimpleNamespace(answer=AsyncMock())
        with patch("goals_manager.db.load_allocator", return_value=allocator):
            await show_position_card(message, 42, goal.uid)

        text = message.answer.await_args.args[0]
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("Запас — <b>15%</b>", text)
        self.assertIn("✎ Запас", button_texts(markup))

    async def test_phase_menu_exposes_currency_and_work_obligations(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("40000"),
            household_reserve=Decimal("10000"),
            average_income=Decimal("70000"),
            income_rhythm="cyclic",
            profile_type="cyclic",
            phase_life_budgets={
                "work": PhaseLifeBudget(
                    critical_life="20000", currency_code="USD",
                    exchange_rate_to_rub="90", completed=True,
                ),
                "break": PhaseLifeBudget(
                    critical_life="40000", completed=True,
                ),
            },
        )
        allocator = FinancialAllocator(settings, AllocatorState())
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(
            answer=AsyncMock(),
            message=message,
            from_user=SimpleNamespace(id=42),
        )
        with patch("period.db.load_allocator", return_value=allocator):
            await show_phase_life_menu(callback)

        markup = message.answer.await_args.kwargs["reply_markup"]
        labels = button_texts(markup)
        self.assertIn("Валюта рабочая жизнь: USD", labels)
        self.assertIn("Обязательства на время работы", labels)


class TaxTypeStorageTests(unittest.TestCase):
    def test_tax_type_change_keeps_the_existing_obligation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "tax-type.db")
            try:
                obligation_id = database.add_tax_obligation(
                    42,
                    "Другой налог",
                    "Регулярный платёж",
                    Decimal("12000"),
                    Decimal("0"),
                    12,
                    Decimal("1000"),
                    "2027-12-01",
                )
                database.change_tax_obligation_type(
                    42,
                    obligation_id,
                    "Другой налог",
                    "Регулярный платёж",
                    "Налог на имущество",
                )
                item = database.load_tax_obligations(42)[0]
                self.assertEqual(item["id"], obligation_id)
                self.assertEqual(item["tax_type"], "Налог на имущество")
                self.assertEqual(item["object_name"], "Регулярный платёж")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
