import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import income


class FakeState:
    def __init__(self, value):
        self.value = value

    async def get_state(self):
        return self.value

    async def set_state(self, value):
        self.value = value.state if hasattr(value, "state") else value

    async def clear(self):
        self.value = None


def callback(user_id=54321):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )


class IncomeConfirmationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        income._income_commit_locks.clear()

    async def test_two_simultaneous_confirmations_run_accounting_once(self):
        state = FakeState(income.IncomeStates.confirmation.state)
        first = callback()
        repeated = callback()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_commit(_callback, current_state):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            await current_state.clear()

        with patch.object(income, "_confirm_income_locked", side_effect=slow_commit):
            first_task = asyncio.create_task(income.confirm_income(first, state))
            await entered.wait()
            await income.confirm_income(repeated, state)
            release.set()
            await first_task

        self.assertEqual(calls, 1)
        response = repeated.message.answer.await_args
        self.assertIn("уже обрабатывается", response.args[0])
        markup = response.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].text, "← Главное меню")

    async def test_stale_confirmation_is_idempotent_and_has_exit(self):
        state = FakeState(None)
        stale = callback()

        with patch.object(income, "_confirm_income_locked", new=AsyncMock()) as commit:
            await income.confirm_income(stale, state)

        commit.assert_not_awaited()
        response = stale.message.answer.await_args
        self.assertIn("уже обработано", response.args[0])
        self.assertEqual(
            response.kwargs["reply_markup"].inline_keyboard[0][0].text,
            "← Главное меню",
        )


if __name__ == "__main__":
    unittest.main()
