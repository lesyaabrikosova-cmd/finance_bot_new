import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mode_presentation import FIRE_EFFECT_ID
from onboarding import GOAL_SUGGESTIONS, show_goals_intro, show_goals_menu


class GoalsOnboardingUiTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIn("⭐️ Отпуск", labels)
        self.assertIn("🧳 Сундук Подарков", labels)
        self.assertIn("🧳 Сундук Техники", labels)
        self.assertIn("+ Своя Цель или Сундук", labels)
        self.assertIn("Мне пока не нужны", labels)
        self.assertNotIn("🧳 Сундук Хотелок", labels)
        self.assertNotIn("⭐️ Образование", labels)
        self.assertNotIn("⭐️ Дом и ремонт", labels)


if __name__ == "__main__":
    unittest.main()
