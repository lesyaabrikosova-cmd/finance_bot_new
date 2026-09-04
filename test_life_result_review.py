import unittest
from unittest.mock import AsyncMock

from onboarding import br_override_save, km_override_save, life_result_keyboard


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


class LifeResultReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_result_keyboard_names_redistribution_and_keeps_all_actions(self):
        markup = life_result_keyboard()
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("↔️ Перераспределить КМ и БР", labels)
        self.assertEqual(
            callbacks(markup),
            [
                "kmfinal:continue",
                "lifeclassification:show",
                "lifeedit:list",
                "kmfinal:override",
                "lifeoverride:br",
            ],
        )

    async def test_km_override_returns_all_review_actions(self):
        message = AsyncMock()
        message.text = "95000"
        state = AsyncMock()
        state.get_data.return_value = {
            "critical_life_exact": "90000",
            "household_reserve": "20000",
            "combined_life_onboarding": True,
        }

        await km_override_save(message, state)

        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("lifeoverride:br", callbacks(markup))
        self.assertIn("kmfinal:override", callbacks(markup))

    async def test_br_override_returns_all_review_actions(self):
        message = AsyncMock()
        message.text = "25000"
        state = AsyncMock()
        state.get_data.return_value = {
            "household_reserve_exact": "20000",
            "critical_life": "90000",
            "combined_life_onboarding": True,
        }

        await br_override_save(message, state)

        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("lifeoverride:br", callbacks(markup))
        self.assertIn("kmfinal:override", callbacks(markup))


if __name__ == "__main__":
    unittest.main()
