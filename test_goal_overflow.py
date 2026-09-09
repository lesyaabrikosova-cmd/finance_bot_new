import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial_engine import AllocatorState, FinancialAllocator, Goal, UserSettings
from goals_manager import toggle_position, delete_position, complete_position
from onboarding import finish_goals_onboarding, finish_without_goals
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
        self.assertEqual(a.settings.goals[0].status, "active")

    def test_second_allocation_does_not_refill_full_goal(self):
        a = allocator([Goal("Цель", 100, target_amount=10)])
        a._allocate_goals(D(100), {})
        a._allocate_goals(D(100), {})
        self.assertEqual(a.state.goal_balances["Цель"], D(10))
        self.assertEqual(a.state.goal_balances["Будущие покупки"], D(190))

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
