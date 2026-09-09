import unittest
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial_engine import FinancialAllocator, UserSettings
from income import send_distribution_report
from onboarding import start_first_allocation, first_allocation_preview_text, financial_opportunities_text
from settings_editor import show_settings_menu
from period import show_new_period_confirmation


def make_allocator(profile, developer=False):
    a = FinancialAllocator(UserSettings(
        has_debts=False, profile_type=profile,
        employment_type="Наёмный" if profile == "stable" else "Фрилансер",
        income_rhythm={"stable": "monthly", "piecework": "irregular", "cyclic": "cyclic"}[profile],
        critical_life=D(100), household_reserve=D(100), average_income=D(1000),
        force_majeure_months=D(4), stabilizer_target_months=D(2), developer_mode=developer,
    ))
    a.state.activate_budget_period(date.today())
    return a


def message():
    return SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())


def button_texts(markup):
    return " ".join(b.text for row in markup.inline_keyboard for b in row)


class ProfileTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_allocation_entry_text_and_buttons_follow_profile(self):
        for profile in ("stable", "piecework", "cyclic"):
            a, msg = make_allocator(profile), message()
            callback = SimpleNamespace(answer=AsyncMock(), message=msg, from_user=msg.from_user)
            with patch("onboarding.db") as db:
                db.load_allocator.return_value = a
                await start_first_allocation(callback, AsyncMock())
                db.save_allocator.assert_not_called()
            call = msg.answer.await_args
            combined = call.args[0] + button_texts(call.kwargs["reply_markup"])
            self.assertEqual("Фонд" in combined, profile == "cyclic")

    def test_first_preview_has_no_zero_foreign_entities(self):
        for profile in ("stable", "piecework"):
            a = make_allocator(profile)
            text = first_allocation_preview_text(a, D(10000))
            self.assertNotIn("Фонд Зарплаты", text)
            if profile == "stable":
                self.assertNotIn("Стабилизатор", text)
            self.assertEqual(a.state.life_balance, D(0))

    async def test_settings_only_offer_applicable_reserves(self):
        for profile in ("stable", "piecework", "cyclic"):
            msg = message()
            with patch("settings_editor.db") as db:
                db.load_allocator.return_value = make_allocator(profile)
                await show_settings_menu(msg, 42)
            call = msg.answer.await_args
            combined = call.args[0] + button_texts(call.kwargs["reply_markup"])
            self.assertEqual("Фонд Зарплаты" in combined, profile == "cyclic")
            self.assertEqual("Стабилизатор" in combined, profile != "stable")

    async def test_income_report_including_developer_mode_filters_reserves(self):
        for profile in ("stable", "piecework", "cyclic"):
            for developer in (False, True):
                a, msg = make_allocator(profile, developer), message()
                result = a.process_income(D(1000), "Тест", tax_override=D(0))
                with patch("income.main_menu_keyboard", return_value=None), \
                     patch("income.send_photo_with_sections", new=AsyncMock()) as photo, \
                     patch("income.send_long_message", new=AsyncMock()):
                    await send_distribution_report(msg, a, result, "Тест", date.today())
                text = "\n".join(photo.await_args.args[2])
                self.assertEqual("Фонд Зарплаты" in text, profile == "cyclic")
                if profile == "stable":
                    self.assertNotIn("Стабилизатор", text)

    async def test_period_confirmation_filters_cyclic_explanation(self):
        for profile in ("stable", "piecework", "cyclic"):
            msg = message()
            with patch("period.db") as db:
                db.load_allocator.return_value = make_allocator(profile)
                await show_new_period_confirmation(msg, 42)
            self.assertEqual("Фонд Зарплаты" in msg.answer.await_args.args[0], profile == "cyclic")

    def test_custom_rates_and_actual_priority_drive_explanation(self):
        a = make_allocator("piecework")
        a.settings.set_brackets(10, 10, 15, 20)
        a.state.pillow_force_majeure = D(400)
        a.state.pillow_stabilizer = D(200)
        text = financial_opportunities_text(a, True)
        self.assertIn("<b>15%</b> — Стабилизатор-Полная", text)
        self.assertIn("<b>42.5%</b> — Цели и Сундуки", text)
        self.assertNotIn("ИЛИ", text)
        # High total capital does not imply that the actual priority is funded.
        a.state.pillow_force_majeure = D(0)
        a.state.pillow_stabilizer = D(10000)
        self.assertEqual(a.active_mode(), 6)
        text = financial_opportunities_text(a, True)
        self.assertIn("ФМ-подушка", text)
        self.assertIn("<b>57.5%</b>", text)

    def test_cyclic_fund_has_no_unavailable_goal_choice(self):
        a = make_allocator("cyclic")
        text = financial_opportunities_text(a, True)
        self.assertIn("<b>100%</b> — Фонд Зарплаты", text)
        self.assertNotIn("ИЛИ", text)


if __name__ == "__main__":
    unittest.main()
