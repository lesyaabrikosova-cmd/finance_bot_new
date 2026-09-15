import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial_engine import (
    AllocatorState,
    FinancialAllocator,
    Goal,
    UserSettings,
    active_position_limit_error,
)
from goals_manager import (
    complete_position,
    delete_position,
    persist_new_position,
    toggle_position,
)
from onboarding import add_goal_draft, finish_goals_onboarding, finish_without_goals
from storage import Database


def allocator(goals):
    return FinancialAllocator(UserSettings(
        has_debts=False, employment_type="Фрилансер", income_rhythm="irregular",
        critical_life=D("100"), household_reserve=D("100"),
        average_income=D("1000"), goals=goals,
    ), AllocatorState(life_balance=D("200")))


class GoalOverflowTests(unittest.TestCase):
    def test_caps_target_and_routes_only_to_chests(self):
        a = allocator([Goal("Ноутбук", 60, target_amount=10000, balance=8000),
                       Goal("Поездка", 20, target_amount=100000),
                       Goal("Подарки", 5, position_type="chest"),
                       Goal("Хотелки", 15, position_type="chest")])
        result = {}
        a._allocate_goals(D("10000"), result)
        self.assertEqual(result, {"Цели:Ноутбук": D(2000), "Цели:Поездка": D(2000),
                                  "Цели:Подарки": D(1500), "Цели:Хотелки": D(4500)})
        self.assertEqual(a.state.goal_balances["Ноутбук"], D(10000))
        self.assertEqual(a.settings.goals[0].status, "completed")

    def test_three_goals_and_five_chests_are_allowed(self):
        existing = [Goal(f"Цель {index}", 10) for index in range(3)]
        existing += [
            Goal(f"Сундук {index}", 10, position_type="chest")
            for index in range(4)
        ]
        self.assertIsNone(active_position_limit_error(existing, "chest"))

    def test_five_goals_and_three_chests_are_allowed(self):
        existing = [Goal(f"Цель {index}", 10) for index in range(5)]
        existing += [
            Goal(f"Сундук {index}", 10, position_type="chest")
            for index in range(2)
        ]
        self.assertIsNone(active_position_limit_error(existing, "chest"))

    def test_sixth_active_goal_is_rejected(self):
        existing = [Goal(f"Цель {index}", 10) for index in range(5)]
        existing.append(Goal("Сундук", 50, position_type="chest"))
        self.assertIn("5 Целей", active_position_limit_error(existing, "goal"))

    def test_ninth_active_position_is_rejected(self):
        existing = [Goal(f"Цель {index}", 10) for index in range(5)]
        existing += [
            Goal(f"Сундук {index}", 10, position_type="chest")
            for index in range(3)
        ]
        self.assertIn("8", active_position_limit_error(existing, "chest"))

    def test_core_rejects_activating_sixth_goal(self):
        goals = [Goal(f"Цель {index}", 19, target_amount=1000) for index in range(5)]
        goals += [
            Goal("Сундук", 5, position_type="chest"),
            Goal("Шестая", 0, status="paused", target_amount=1000),
        ]
        a = allocator(goals)
        with self.assertRaisesRegex(ValueError, "5 Целей"):
            a.activate_position(goals[-1])
        self.assertEqual(goals[-1].status, "paused")

    def test_core_rejects_activating_ninth_position(self):
        goals = [Goal(f"Цель {index}", 10, target_amount=1000) for index in range(4)]
        goals += [
            Goal(f"Сундук {index}", 15, position_type="chest")
            for index in range(4)
        ]
        waiting = Goal("Ещё один", 0, position_type="chest", status="paused")
        goals.append(waiting)
        a = allocator(goals)
        with self.assertRaisesRegex(ValueError, "8"):
            a.activate_position(waiting)
        self.assertEqual(waiting.status, "paused")

    def test_pause_and_reactivation_preserve_balance(self):
        goal = Goal("Отпуск", 40, target_amount=1000, balance=250)
        chest = Goal("Подарки", 60, position_type="chest")
        a = allocator([goal, chest])
        original_balance = a.state.goal_balances[goal.name]
        a.pause_position(goal)
        self.assertEqual(a.state.goal_balances[goal.name], original_balance)
        a.activate_position(goal)
        self.assertEqual(a.state.goal_balances[goal.name], original_balance)

    def test_second_allocation_does_not_refill_full_goal(self):
        a = allocator([Goal("Цель", 100, target_amount=10)])
        a._allocate_goals(D(100), {})
        a._allocate_goals(D(100), {})
        self.assertEqual(a.state.goal_balances["Цель"], D(10))
        self.assertEqual(a.state.goal_balances["Будущие покупки"], D(190))

    def test_completed_goal_releases_an_active_slot(self):
        goals = [Goal("Готовая", 20, target_amount=10)]
        goals += [Goal(f"Цель {index}", 10, target_amount=1000) for index in range(4)]
        goals += [
            Goal("Сундук 0", 20, position_type="chest"),
            Goal("Сундук 1", 10, position_type="chest"),
            Goal("Сундук 2", 10, position_type="chest"),
        ]
        a = allocator(goals)
        a._allocate_goals(D(50), {})
        self.assertEqual(a.settings.goals[0].status, "completed")
        self.assertIsNone(active_position_limit_error(a.settings.goals, "goal"))
        self.assertEqual(
            sum((goal.percentage for goal in a.settings.active_goals), D(0)),
            D(100),
        )

    def test_paused_goal_forecast_has_zero_monthly_funding(self):
        a = allocator([
            Goal("Пауза", 50, status="paused", target_amount=1000),
            Goal("Сундук", 100, position_type="chest"),
        ])
        forecast = a.goal_forecast(a.settings.goals[0])
        self.assertEqual(forecast["monthly_minimum"], D(0))
        self.assertEqual(forecast["monthly_maximum"], D(0))

    def test_paused_chest_receives_zero(self):
        a = allocator([
            Goal("Активный", 100, position_type="chest"),
            Goal("Ожидающий", 40, position_type="chest", status="paused"),
        ])
        allocations = {}
        a._allocate_goals(D(100), allocations)
        self.assertEqual(allocations, {"Цели:Активный": D(100)})
        self.assertEqual(a.state.goal_balances["Ожидающий"], D(0))

    def test_legacy_over_limit_profile_is_preserved_for_user_choice(self):
        goals = [Goal(f"Цель {index}", 10, target_amount=1000) for index in range(5)]
        goals += [
            Goal("Сундук 0", 13, position_type="chest"),
            Goal("Сундук 1", 13, position_type="chest"),
            Goal("Сундук 2", 12, position_type="chest"),
            Goal("Сундук 3", 12, position_type="chest"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "legacy-limit.db")
            database.save_allocator(42, allocator(goals))
            restored = database.load_allocator(42)
            self.assertEqual(len(restored.settings.goals), 9)
            self.assertEqual(len(restored.settings.active_goals), 9)
            self.assertTrue(all(goal.status == "active" for goal in restored.settings.goals))
            database.close()

    def test_buffer_is_part_of_cap(self):
        a = allocator([Goal("Цель", 100, target_amount=100, buffer_enabled=True,
                            buffer_percent=10, balance=105)])
        a._allocate_goals(D(20), {})
        self.assertEqual(a.state.goal_balances["Цель"], D(110))
        self.assertEqual(a.state.goal_balances["Будущие покупки"], D(15))

    def test_zero_weight_chests_share_equally_and_preserve_fraction(self):
        a = allocator([Goal("Цель", 100, target_amount=0),
                       Goal("Первый", 0, position_type="chest"),
                       Goal("Второй", 0, position_type="chest"),
                       Goal("Пауза", 0, position_type="chest", status="paused")])
        split = a.split_goal_amount(D("100.01"))
        self.assertEqual(split, {"Первый": D("50.005"), "Второй": D("50.005")})
        self.assertEqual(sum(split.values()), D("100.01"))

    def test_lowering_target_does_not_move_existing_balance(self):
        a = allocator([Goal("Цель", 100, target_amount=50, balance=100)])
        a._allocate_goals(D(20), {})
        self.assertEqual(a.state.goal_balances["Цель"], D(100))
        self.assertEqual(a.state.goal_balances["Будущие покупки"], D(20))

    def test_default_chest_is_unique_and_migration_is_idempotent(self):
        a = allocator([Goal("Будущие покупки", 100, target_amount=10)])
        self.assertEqual(a.ensure_active_chest().name, "Будущие покупки 2")
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            db.save_allocator(42, a)
            restored = db.load_allocator(42)
            db.save_allocator(42, restored)
            restored = db.load_allocator(42)
            self.assertEqual(len(restored.settings.goals), 2)
            self.assertEqual(restored.settings.goals[0].percentage, D(100))
            self.assertEqual(restored.settings.goals[1].percentage, D(0))
            db.close()

    def test_empty_profile_has_real_persisted_destination(self):
        a = allocator([])
        result = {}
        a._allocate_goals(D(100), result)
        self.assertEqual(result, {"Цели:Будущие покупки": D(100)})
        self.assertEqual(a.state.goal_balances["Будущие покупки"], D(100))

    def test_foreign_target_never_compared_directly_to_rubles(self):
        a = allocator([Goal("Поездка", 100, target_amount=1000, currency_code="EUR")])
        a._allocate_goals(D(10000), {})
        self.assertEqual(a.state.goal_balances["Поездка"], D(0))
        self.assertEqual(a.state.goal_balances["Будущие покупки"], D(10000))

    def test_manual_target_overflow_rejected_without_mutation(self):
        a = allocator([Goal("Цель", 100, target_amount=100, balance=90)])
        before = dict(a.state.goal_balances)
        with self.assertRaises(ValueError):
            a.transfer_salary_remainder(D(20), "goal:0")
        self.assertEqual(a.state.goal_balances, before)

    def test_large_super_income_caps_goal_and_preserves_total(self):
        a = allocator([Goal("Цель", 100, target_amount=25)])
        result = a.process_income(D(10000), "Тест", tax_override=D(0))
        self.assertEqual(a.state.goal_balances["Цель"], D(25))
        self.assertEqual(sum(result.allocations.values()), D(10000))
        self.assertGreater(a.state.goal_balances["Будущие покупки"], 0)


class ChestLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_manager_creates_ninth_position_in_waiting(self):
        goals = [Goal(f"Цель {index}", 10, target_amount=1000) for index in range(4)]
        goals += [
            Goal(f"Сундук {index}", 15, position_type="chest")
            for index in range(4)
        ]
        a = allocator(goals)
        state = AsyncMock()
        state.get_data.return_value = {
            "goal_draft": {
                "name": "Девятая позиция",
                "position_type": "chest",
            }
        }
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        with (
            patch("goals_manager.db") as db,
            patch("goals_manager.show_goals_manager", new=AsyncMock()),
        ):
            db.load_allocator.return_value = a
            await persist_new_position(message, state)
            db.save_allocator.assert_called_once()
        created = a.settings.goals[-1]
        self.assertEqual(created.status, "paused")
        self.assertEqual(created.percentage, D(0))
        self.assertEqual(created.balance, D(0))

    async def test_ninth_onboarding_position_is_created_paused(self):
        class DraftState:
            def __init__(self):
                self.data = {
                    "goal_drafts": [
                        {
                            "name": f"Сундук {index}",
                            "position_type": "chest",
                            "status": "active",
                        }
                        for index in range(8)
                    ]
                }

            async def get_data(self):
                return self.data

            async def update_data(self, **values):
                self.data.update(values)

        state = DraftState()
        await add_goal_draft(state, {"name": "Девятый", "position_type": "chest"})
        self.assertEqual(len(state.data["goal_drafts"]), 9)
        self.assertEqual(state.data["goal_drafts"][-1]["status"], "paused")
        self.assertEqual(state.data["goal_drafts"][-1]["percentage"], "0")

    async def test_sixth_onboarding_goal_is_created_paused(self):
        class DraftState:
            def __init__(self):
                self.data = {
                    "goal_drafts": [
                        {
                            "name": f"Цель {index}",
                            "position_type": "goal",
                            "status": "active",
                        }
                        for index in range(5)
                    ]
                }

            async def get_data(self):
                return self.data

            async def update_data(self, **values):
                self.data.update(values)

        state = DraftState()
        await add_goal_draft(state, {"name": "Шестая", "position_type": "goal"})
        self.assertEqual(state.data["goal_drafts"][-1]["status"], "paused")

    async def test_onboarding_requires_chest_before_percentages(self):
        state = AsyncMock()
        state.get_data.return_value = {"goal_drafts": [{"name": "Ноутбук", "position_type": "goal"}]}
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch("onboarding.ask_next_goal_percentage", new=AsyncMock()) as next_step:
            await finish_goals_onboarding(callback, state)
            next_step.assert_not_called()
        self.assertIn("Сундук", callback.message.answer.call_args.args[0])

    async def test_skip_goals_creates_default_chest(self):
        state = AsyncMock()
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        with patch("onboarding.show_onboarding_time_forecast", new=AsyncMock()):
            await finish_without_goals(callback, state)
        goals = state.update_data.call_args.kwargs["goals"]
        self.assertEqual(goals[0]["position_type"], "chest")
        self.assertEqual(goals[0]["percentage"], "100")

    async def test_last_chest_cannot_be_paused_or_deleted(self):
        for handler, action in ((toggle_position, "toggle"), (delete_position, "delete:yes")):
            a = allocator([Goal("Подарки", 100, position_type="chest")])
            callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()),
                                       from_user=SimpleNamespace(id=42), data=f"goalmanage:{action}:0")
            with patch("goals_manager.db") as db:
                db.load_allocator.return_value = a
                await handler(callback, AsyncMock())
                db.save_allocator.assert_not_called()
            self.assertEqual(a.settings.goals[0].status, "active")
            self.assertEqual(len(a.settings.goals), 1)

    async def test_completing_goal_gives_share_only_to_chests(self):
        a = allocator([Goal("Готовая", 60, target_amount=100), Goal("Другая", 20, target_amount=200),
                       Goal("Подарки", 20, position_type="chest")])
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()),
                                   from_user=SimpleNamespace(id=42), data="goalmanage:complete:yes:0")
        with patch("goals_manager.db") as db, patch("goals_manager.show_goals_manager", new=AsyncMock()):
            db.load_allocator.return_value = a
            await complete_position(callback, AsyncMock())
        self.assertEqual([g.percentage for g in a.settings.goals], [D(0), D(20), D(80)])


if __name__ == "__main__":
    unittest.main()
