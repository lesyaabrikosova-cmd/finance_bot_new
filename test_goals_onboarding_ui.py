import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial_engine import FinancialAllocator, UserSettings
from mode_presentation import FIRE_EFFECT_ID
from onboarding import (
    GOAL_SUGGESTIONS,
    ask_goal_target,
    financial_opportunities_text,
    investment_unlock_level,
    show_goals_intro,
    show_goals_menu,
)


class GoalsOnboardingUiTests(unittest.IsolatedAsyncioTestCase):
    def test_investments_unlock_at_profile_specific_level(self):
        self.assertEqual(investment_unlock_level("stable"), 4)
        self.assertEqual(investment_unlock_level("piecework"), 5)
        self.assertEqual(investment_unlock_level("cyclic"), 7)

    def make_allocator(self, mode):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False, employment_type="Фрилансер", income_rhythm="irregular",
            critical_life=Decimal("100"), household_reserve=Decimal("100"),
            force_majeure_months=Decimal("4"), average_income=Decimal("1000"),
        ))
        allocator.active_mode = lambda: mode
        allocator.allocation_mode = lambda: mode
        return allocator

    def test_opportunities_name_both_piecework_unlock_levels(self):
        text = financial_opportunities_text(self.make_allocator(2), False)
        self.assertIn("Цели станут доступны с <b>3-го уровня</b>", text)
        self.assertIn("инвестиции — с <b>5-го</b>", text)

    def test_partial_goals_show_personal_balanced_and_protection_choices(self):
        text = financial_opportunities_text(self.make_allocator(3), True)
        self.assertIn("<b>65%</b> — ФМ-подушка", text)
        self.assertIn("<b>35%</b> — Цели и Сундуки", text)
        self.assertIn("<b>100%</b> — ФМ-подушка", text)
        self.assertIn("ИЛИ", text)

    def test_penultimate_level_shows_fixed_30_35_35_without_alternative(self):
        text = financial_opportunities_text(self.make_allocator(5), True)
        self.assertIn("<b>30%</b> — Стабилизатор-Полная", text)
        self.assertIn("<b>35%</b> — Инвестиции", text)
        self.assertIn("<b>35%</b> — Цели и Сундуки", text)
        self.assertNotIn("ИЛИ", text)

    async def test_intro_uses_image_fire_effect_and_continue_button(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            answer_photo=AsyncMock(),
        )
        state = AsyncMock()

        await show_goals_intro(message, state)

        message.answer_photo.assert_awaited_once()
        kwargs = message.answer_photo.await_args.kwargs
        self.assertEqual(kwargs["message_effect_id"], FIRE_EFFECT_ID)
        self.assertIn("ЦЕЛИ И СУНДУКИ", kwargs["caption"])
        self.assertIn("⭐️ <b>Цель</b>", kwargs["caption"])
        self.assertIn("🧳 <b>Сундук</b>", kwargs["caption"])
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.text, "Понятно →")
        self.assertEqual(button.callback_data, "goals:intro:continue")

    async def test_menu_has_only_approved_suggestions(self):
        self.assertEqual(
            [(item["name"], item["position_type"]) for item in GOAL_SUGGESTIONS],
            [("Отпуск", "goal"), ("Замена техники", "chest")],
        )
        message = SimpleNamespace(answer=AsyncMock())
        state = AsyncMock()
        state.get_data.return_value = {
            "goal_drafts": [],
            "historical_gifts_monthly": "0",
        }

        with patch("onboarding.goals_capacity_profile_text", return_value="Профильный текст"):
            await show_goals_menu(message, state)

        kwargs = message.answer.await_args.kwargs
        labels = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("Отпуск", labels)
        self.assertIn("Сундук Подарков", labels)
        self.assertIn("Сундук Техники", labels)
        self.assertIn("+ Своя Цель или Сундук", labels)
        self.assertIn("Мне пока не нужны", labels)
        self.assertNotIn("Сундук Хотелок", labels)
        self.assertNotIn("Образование", labels)
        self.assertNotIn("Дом и ремонт", labels)

    async def test_goal_amount_prompt_explains_when_to_choose_chest(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = AsyncMock()

        await ask_goal_target(message, state, {"name": "Хотелки", "position_type": "goal"})

        text = message.answer.await_args.args[0]
        self.assertIn("Если конечной суммы нет", text)
        self.assertIn("🧳 <b>Сундук</b>", text)


if __name__ == "__main__":
    unittest.main()
