"""Target and characterization tests for closing user envelopes.

These tests lock the accounting invariants for closing an envelope: labels may
disappear, but tracked money and the identity of historical allocations must not.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


# Importing the handlers also imports the process-wide ``storage.db``.  Keep
# that import away from the developer database even when this file is run by
# itself.
_data_dir = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _data_dir.name

from financial_engine import AllocatorState, FinancialAllocator, Goal, UserSettings
from goals_manager import delete_position, toggle_position
from settings_editor import delete_life_category


def make_allocator(
    *,
    life_categories: dict[str, D] | None = None,
    life_category_ids: dict[str, str] | None = None,
    life_balance: D = D("0"),
    period_life_topups: dict[str, D] | None = None,
    goals: list[Goal] | None = None,
    goal_balances: dict[str, D] | None = None,
) -> FinancialAllocator:
    settings = UserSettings(
        has_debts=False,
        employment_type="Фрилансер",
        income_rhythm="irregular",
        critical_life=D("1000"),
        household_reserve=D("200"),
        average_income=D("3000"),
        life_categories=life_categories or {},
        life_category_ids=life_category_ids or {},
        goals=goals or [],
    )
    state = AllocatorState(
        life_balance=life_balance,
        period_life_topups=period_life_topups or {},
        goal_balances=goal_balances or {},
    )
    return FinancialAllocator(settings, state)


def callback(data: str, telegram_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
        from_user=SimpleNamespace(id=telegram_id),
        data=data,
    )


class LifeCategoryDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def _delete(self, allocator: FinancialAllocator, name: str) -> None:
        cb = callback(f"settings:life_delete:{name}")
        state = AsyncMock()
        with (
            patch("settings_editor.db") as db,
            patch("settings_editor.main_menu_keyboard", return_value="menu"),
        ):
            db.load_allocator.return_value = allocator
            await delete_life_category(cb, state)

    async def test_zero_balance_category_can_close_without_changing_life_total(self):
        """Closing an empty bank envelope must only change future routing."""
        allocator = make_allocator(
            life_categories={"Квартира": D("300")},
            life_balance=D("100"),
            period_life_topups={"Квартира": D("0"), "Зарплата": D("100")},
        )

        await self._delete(allocator, "Квартира")

        self.assertNotIn("Квартира", allocator.settings.life_categories)
        self.assertEqual(allocator.state.life_balance, D("100"))
        self.assertEqual(allocator.state.period_life_topups["Зарплата"], D("100"))
        # Its future monthly allowance becomes part of the automatic Salary
        # remainder while Critical minimum itself stays unchanged.
        self.assertEqual(allocator.life_category_targets()["Зарплата"], D("1000"))

    async def test_funded_category_moves_known_period_money_to_salary(self):
        """Removing a label must not erase money already assigned this period."""
        allocator = make_allocator(
            life_categories={"Квартира": D("300")},
            life_balance=D("350"),
            period_life_topups={"Квартира": D("250"), "Зарплата": D("100")},
        )

        await self._delete(allocator, "Квартира")

        self.assertNotIn("Квартира", allocator.state.period_life_topups)
        self.assertEqual(allocator.state.period_life_topups["Зарплата"], D("350"))
        self.assertEqual(allocator.state.life_balance, D("350"))
        self.assertEqual(
            sum(allocator.state.period_life_topups.values(), D("0")),
            allocator.state.life_balance,
        )
        self.assertEqual(allocator.state.period_income, D("0"))

    async def test_closed_category_releases_its_active_identity(self):
        """A later same-name envelope is a new bank envelope, not a rename."""
        allocator = make_allocator(
            life_categories={"Квартира": D("300")},
            life_category_ids={"Квартира": "life-old"},
        )

        await self._delete(allocator, "Квартира")

        self.assertNotIn("Квартира", allocator.settings.life_category_ids)


class GoalAndChestDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def _delete(self, allocator: FinancialAllocator, index: int) -> None:
        cb = callback(f"goalmanage:delete:yes:{index}")
        state = AsyncMock()
        with (
            patch("goals_manager.db") as db,
            patch("goals_manager.show_goals_manager", new=AsyncMock()),
        ):
            db.load_allocator.return_value = allocator
            await delete_position(cb, state)

    @staticmethod
    def _system_chest(percentage: str = "50") -> Goal:
        return Goal(
            "Будущие покупки",
            D(percentage),
            position_type="chest",
            is_system_chest=True,
            uid="system-chest",
        )

    async def test_empty_goal_can_close_and_remaining_share_is_normalized(self):
        allocator = make_allocator(
            goals=[
                Goal("Отпуск", D("50"), target_amount=D("1000"), uid="vacation"),
                self._system_chest(),
            ],
            goal_balances={"Отпуск": D("0"), "Будущие покупки": D("100")},
        )

        await self._delete(allocator, 0)

        self.assertEqual([goal.name for goal in allocator.settings.goals], ["Будущие покупки"])
        self.assertEqual(allocator.settings.goals[0].percentage, D("100"))
        self.assertEqual(allocator.state.goal_balances, {"Будущие покупки": D("100")})

    async def test_funded_goal_moves_balance_to_permanent_chest(self):
        """Default close destination is the always-available system chest."""
        allocator = make_allocator(
            goals=[
                Goal("Отпуск", D("50"), target_amount=D("1000"), uid="vacation"),
                self._system_chest(),
            ],
            goal_balances={"Отпуск": D("500"), "Будущие покупки": D("100")},
        )
        tracked_before = sum(allocator.state.goal_balances.values(), D("0"))

        await self._delete(allocator, 0)

        self.assertEqual(allocator.state.goal_balances["Будущие покупки"], D("600"))
        self.assertEqual(sum(allocator.state.goal_balances.values(), D("0")), tracked_before)

    async def test_funded_user_chest_moves_balance_to_permanent_chest(self):
        allocator = make_allocator(
            goals=[
                Goal("Подарки", D("50"), position_type="chest", uid="gifts"),
                self._system_chest(),
            ],
            goal_balances={"Подарки": D("275"), "Будущие покупки": D("25")},
        )
        tracked_before = sum(allocator.state.goal_balances.values(), D("0"))

        await self._delete(allocator, 0)

        self.assertEqual(allocator.state.goal_balances["Будущие покупки"], D("300"))
        self.assertEqual(sum(allocator.state.goal_balances.values(), D("0")), tracked_before)

    async def test_permanent_chest_cannot_be_deleted_even_if_another_chest_exists(self):
        allocator = make_allocator(
            goals=[
                self._system_chest(),
                Goal("Подарки", D("50"), position_type="chest", uid="gifts"),
            ],
            goal_balances={"Будущие покупки": D("80"), "Подарки": D("20")},
        )
        before = dict(allocator.state.goal_balances)

        await self._delete(allocator, 0)

        self.assertTrue(any(goal.is_system_chest for goal in allocator.settings.goals))
        self.assertEqual(allocator.state.goal_balances, before)

    async def test_migration_leaves_exactly_one_permanent_chest(self):
        """Corrupt/legacy duplicate flags must not create two undeletable chests."""
        allocator = make_allocator(
            goals=[
                self._system_chest(),
                Goal(
                    "Запасной",
                    D("50"),
                    position_type="chest",
                    is_system_chest=True,
                    uid="second-system-chest",
                ),
            ],
            goal_balances={"Будущие покупки": D("80"), "Запасной": D("20")},
        )

        self.assertEqual(
            sum(goal.is_system_chest for goal in allocator.settings.goals),
            1,
        )
        self.assertEqual(sum(allocator.state.goal_balances.values(), D("0")), D("100"))

    async def test_permanent_chest_cannot_be_paused_when_another_chest_exists(self):
        """A permanent fallback must be active, not reactivated only on reload."""
        allocator = make_allocator(
            goals=[
                self._system_chest(),
                Goal("Подарки", D("50"), position_type="chest", uid="gifts"),
            ],
        )
        cb = callback("goalmanage:toggle:0")
        state = AsyncMock()
        with (
            patch("goals_manager.db") as db,
            patch("goals_manager.show_goals_manager", new=AsyncMock()),
        ):
            db.load_allocator.return_value = allocator
            await toggle_position(cb, state)

        self.assertEqual(allocator.settings.goals[0].status, "active")
        db.save_allocator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
