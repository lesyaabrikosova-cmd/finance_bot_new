import tempfile
import unittest
import sqlite3
from decimal import Decimal
from pathlib import Path

from financial_engine import AllocatorState, FinancialAllocator, Goal, UserSettings
from storage import Database
from onboarding import build_settings_from_data, goal_draft_summary


def settings_with(goals):
    return UserSettings(
        has_debts=False,
        employment_type="Фрилансер",
        income_rhythm="irregular",
        critical_life=Decimal("90000"),
        household_reserve=Decimal("20000"),
        average_income=Decimal("180000"),
        goals=goals,
    )


class GoalFoundationTests(unittest.TestCase):
    def test_chest_has_no_finish_line_or_buffer(self):
        chest = Goal(
            name="Хотелки",
            percentage=Decimal("30"),
            position_type="chest",
            target_amount=Decimal("100000"),
            deadline="2027-06-01",
            buffer_enabled=True,
            buffer_percent=Decimal("10"),
        )

        self.assertTrue(chest.is_chest)
        self.assertIsNone(chest.target_amount)
        self.assertIsNone(chest.deadline)
        self.assertFalse(chest.buffer_enabled)
        self.assertEqual(chest.buffer_percent, Decimal("0"))

    def test_goal_target_includes_optional_buffer(self):
        goal = Goal(
            name="Отпуск",
            percentage=Decimal("40"),
            target_amount=Decimal("150000"),
            buffer_enabled=True,
            buffer_percent=Decimal("10"),
        )

        self.assertEqual(goal.full_target_amount, Decimal("165000"))

    def test_database_round_trip_preserves_goal_and_chest_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "goals.db")
            goals = [
                Goal(
                    name="Отпуск",
                    percentage=Decimal("70"),
                    position_type="goal",
                    target_amount=Decimal("150000"),
                    deadline="2027-08-01",
                    buffer_enabled=True,
                    buffer_percent=Decimal("10"),
                    currency_code="EUR",
                    status="active",
                ),
                Goal(
                    name="Подарки",
                    percentage=Decimal("30"),
                    position_type="chest",
                    is_auto_percentage=True,
                    previous_percentage=Decimal("25"),
                ),
            ]
            database.save_allocator(
                991100,
                FinancialAllocator(settings_with(goals), AllocatorState()),
            )

            restored = database.load_allocator(991100)
            self.assertIsNotNone(restored)
            vacation, gifts = restored.settings.goals
            self.assertEqual(vacation.target_amount, Decimal("150000"))
            self.assertEqual(vacation.full_target_amount, Decimal("165000"))
            self.assertEqual(vacation.deadline, "2027-08-01")
            self.assertEqual(vacation.currency_code, "EUR")
            self.assertTrue(gifts.is_chest)
            self.assertTrue(gifts.is_auto_percentage)
            self.assertEqual(gifts.previous_percentage, Decimal("25"))
            database.close()

    def test_legacy_goals_table_is_migrated_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    percentage TEXT NOT NULL,
                    balance TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO goals (telegram_id, name, percentage, balance) "
                "VALUES (1, 'Старая цель', '100', '5000')"
            )
            connection.commit()
            connection.close()

            database = Database(path)
            row = database.connection.execute(
                "SELECT * FROM goals WHERE telegram_id = 1"
            ).fetchone()
            self.assertEqual(row["name"], "Старая цель")
            self.assertEqual(row["position_type"], "chest")
            self.assertEqual(row["currency_code"], "RUB")
            self.assertEqual(row["status"], "active")
            database.close()

    def test_onboarding_builds_finite_goal_and_balance(self):
        settings = build_settings_from_data({
            "has_debts": False,
            "employment_type": "Фрилансер",
            "critical_life": "90000",
            "household_reserve": "20000",
            "average_income": "180000",
            "tax_rate": "0",
            "force_majeure_months": "4",
            "goals": [{
                "name": "Отпуск",
                "position_type": "goal",
                "percentage": "100",
                "is_auto_percentage": True,
                "target_amount": "150000",
                "balance": "25000",
                "deadline": "2027-08-01",
                "buffer_enabled": True,
                "buffer_percent": "10",
            }],
        })
        goal = settings.goals[0]
        self.assertEqual(goal.balance, Decimal("25000"))
        self.assertEqual(goal.full_target_amount, Decimal("165000"))
        self.assertTrue(goal.is_auto_percentage)

    def test_onboarding_summary_distinguishes_goal_and_chest(self):
        summary = goal_draft_summary([
            {"name": "Отпуск", "position_type": "goal"},
            {"name": "Подарки", "position_type": "chest"},
        ])
        self.assertIn("⭐️ <b>Отпуск</b>", summary)
        self.assertIn("🪎 <b>Подарки</b>", summary)

    def test_goal_lifecycle_survives_storage_and_only_active_positions_allocate(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "lifecycle.db")
            goals = [
                Goal("Отпуск", 40, target_amount=Decimal("165000"), status="completed"),
                Goal("Подарки", 100, position_type="chest", status="active"),
                Goal("Техника", 20, position_type="chest", status="paused"),
            ]
            database.save_allocator(
                991101,
                FinancialAllocator(settings_with(goals), AllocatorState()),
            )
            restored = database.load_allocator(991101)
            self.assertEqual(
                [goal.status for goal in restored.settings.goals],
                ["completed", "active", "paused"],
            )
            self.assertEqual(
                restored.split_goal_amount(Decimal("10000")),
                {"Подарки": Decimal("10000")},
            )
            database.close()


if __name__ == "__main__":
    unittest.main()
