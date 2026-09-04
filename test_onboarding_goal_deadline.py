import unittest
from unittest.mock import AsyncMock

from onboarding import SetupStates, save_goal_deadline


class OnboardingGoalDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_goal_deadline_is_saved_and_flow_continues(self):
        message = AsyncMock()
        message.text = "30.05.2027"

        state = AsyncMock()
        state.get_data.return_value = {
            "pending_goal": {
                "name": "Отпуск",
                "position_type": "goal",
                "target_amount": "150000",
            }
        }

        await save_goal_deadline(message, state)

        state.update_data.assert_any_await(
            pending_goal={
                "name": "Отпуск",
                "position_type": "goal",
                "target_amount": "150000",
                "deadline": "2027-05-30",
            }
        )
        state.set_state.assert_awaited_with(SetupStates.goal_buffer_percent)
        self.assertIn(
            "ДОБАВИТЬ ФИНАНСОВЫЙ ЗАПАС",
            message.answer.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
