"""Contracts for immutable envelope and income-type identities."""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import dashboard
from financial_engine import FinancialAllocator, Goal, UserSettings
from storage import Database


D = Decimal


class ImmutableEntityIdTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "immutable-ids.db")
        self.user_id = 77001

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def allocator(
        self,
        *,
        life=None,
        life_ids=None,
        reserve=None,
        reserve_ids=None,
        goals=None,
        income_types=None,
        income_type_ids=None,
        income_type_labels=None,
    ):
        return FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("1000"),
            household_reserve=D("100"),
            average_income=D("2000"),
            life_categories=life or {},
            life_category_ids=life_ids or {},
            household_reserve_categories=reserve or {},
            household_reserve_category_ids=reserve_ids or {},
            goals=goals or [],
            income_type_tax_rates=income_types or {},
            income_type_ids=income_type_ids or {},
            income_type_labels=income_type_labels or {},
        ))

    def save_income(self, *, allocations=None, envelope_ids=None,
                    income_type="Заказ", income_type_id="", income="100"):
        payload = {
            "type": "income_distribution",
            "date": "2026-09-10",
            "income": income,
            "tax": "0",
            "income_type": income_type,
            "allocations": allocations or {},
        }
        if envelope_ids is not None:
            payload["envelope_ids"] = envelope_ids
        if income_type_id:
            payload["income_type_id"] = income_type_id
        self.db.save_operation(self.user_id, "income_distribution", payload)

    def test_life_rename_aggregates_history_by_id_in_period_report(self):
        allocator = self.allocator(
            life={"Недвижимость": D("1000")},
            life_ids={"Недвижимость": "life-1"},
        )
        self.db.save_allocator(self.user_id, allocator)
        self.save_income(
            allocations={"КЖ:Недвижимость": "25000"},
            envelope_ids={"КЖ:Недвижимость": "life:life-1"},
        )
        allocator.settings.life_categories = {"Квартира": D("1000")}
        allocator.settings.life_category_ids = {"Квартира": "life-1"}
        self.db.record_envelope_rename(
            self.user_id, allocator, "КЖ:", "Недвижимость", "Квартира", "life-1",
        )
        self.db.save_allocator(self.user_id, allocator)
        self.save_income(
            allocations={"КЖ:Квартира": "5000"},
            envelope_ids={"КЖ:Квартира": "life:life-1"},
        )

        with patch.object(dashboard, "db", self.db):
            totals = dashboard.get_period_allocations(allocator, self.user_id)
        self.assertEqual(totals, {"КЖ:Квартира": D("30000")})

    def test_reused_old_life_name_with_new_id_is_not_merged(self):
        allocator = self.allocator(
            life={"Квартира": D("700"), "Недвижимость": D("300")},
            life_ids={"Квартира": "life-1", "Недвижимость": "life-2"},
        )
        self.db.save_allocator(self.user_id, allocator)
        self.db.save_operation(self.user_id, "envelope_rename", {
            "old": "КЖ:Недвижимость",
            "new": "КЖ:Квартира",
            "entity_id": "life-1",
        })
        self.save_income(
            allocations={"КЖ:Недвижимость": "25000"},
            envelope_ids={"КЖ:Недвижимость": "life:life-1"},
        )
        self.save_income(
            allocations={"КЖ:Недвижимость": "5000"},
            envelope_ids={"КЖ:Недвижимость": "life:life-2"},
        )

        operations = [
            item for item in self.db.load_operations(self.user_id)
            if item["type"] == "income_distribution"
        ]
        by_id = {
            next(iter(item["payload"]["envelope_ids"].values())):
                item["payload"]["allocations"]
            for item in operations
        }
        self.assertEqual(by_id["life:life-1"], {"КЖ:Квартира": "25000"})
        self.assertEqual(by_id["life:life-2"], {"КЖ:Недвижимость": "5000"})

    def test_goal_rename_resolves_old_allocation_by_uid(self):
        goal = Goal("Отпуск", D("100"), uid="goal-1")
        allocator = self.allocator(goals=[goal])
        self.db.save_allocator(self.user_id, allocator)
        self.save_income(
            allocations={"Цели:Отпуск": "1000"},
            envelope_ids={"Цели:Отпуск": "goal:goal-1"},
        )
        goal.name = "Путешествие"
        self.db.record_envelope_rename(
            self.user_id, allocator, "Цели:", "Отпуск", "Путешествие", "goal-1",
        )
        self.db.save_allocator(self.user_id, allocator)

        operation = next(
            item for item in self.db.load_operations(self.user_id)
            if item["type"] == "income_distribution"
        )
        self.assertEqual(operation["payload"]["allocations"], {
            "Цели:Путешествие": "1000",
        })
        self.assertEqual(operation["payload"]["envelope_ids"], {
            "Цели:Путешествие": "goal:goal-1",
        })

    def test_household_reserve_rename_resolves_by_id(self):
        allocator = self.allocator(
            reserve={"Дети": D("100")},
            reserve_ids={"Дети": "reserve-1"},
        )
        self.db.save_allocator(self.user_id, allocator)
        self.save_income(
            allocations={"БР:Дети": "700"},
            envelope_ids={"БР:Дети": "household:reserve-1"},
        )
        allocator.settings.household_reserve_categories = {"Ребёнок": D("100")}
        allocator.settings.household_reserve_category_ids = {
            "Ребёнок": "reserve-1",
        }
        self.db.record_envelope_rename(
            self.user_id, allocator, "БР:", "Дети", "Ребёнок", "reserve-1",
        )
        self.db.save_allocator(self.user_id, allocator)

        operation = next(
            item for item in self.db.load_operations(self.user_id)
            if item["type"] == "income_distribution"
        )
        self.assertEqual(operation["payload"]["allocations"], {
            "БР:Ребёнок": "700",
        })

    def test_income_type_rename_keeps_one_analysis_sector(self):
        allocator = self.allocator(
            income_types={"Халтура": D("0")},
            income_type_ids={"Халтура": "income-1"},
        )
        self.db.save_allocator(self.user_id, allocator)
        self.save_income(income_type="Халтура", income_type_id="income-1", income="300")
        allocator.settings.income_type_tax_rates = {"Подработка": D("0")}
        allocator.settings.income_type_ids = {"Подработка": "income-1"}
        allocator.settings.income_type_labels["income-1"] = "Подработка"
        self.db.record_income_type_rename(
            self.user_id, "Халтура", "Подработка", "income-1",
        )
        self.db.save_allocator(self.user_id, allocator)
        self.save_income(income_type="Подработка", income_type_id="income-1", income="200")

        operations = self.db.load_operations(self.user_id)
        totals = dashboard.income_analysis_totals(allocator, operations)
        self.assertEqual(totals, {"Подработка": D("500")})

    def test_reused_income_type_name_with_new_id_stays_separate(self):
        allocator = self.allocator(
            income_types={"Подработка": D("0"), "Халтура": D("0")},
            income_type_ids={"Подработка": "income-1", "Халтура": "income-2"},
            income_type_labels={"income-1": "Подработка", "income-2": "Халтура"},
        )
        self.db.save_allocator(self.user_id, allocator)
        self.db.save_operation(self.user_id, "income_type_rename", {
            "old": "Халтура", "new": "Подработка", "entity_id": "income-1",
        })
        self.save_income(income_type="Халтура", income_type_id="income-1", income="300")
        self.save_income(income_type="Халтура", income_type_id="income-2", income="200")

        totals = dashboard.income_analysis_totals(
            allocator, self.db.load_operations(self.user_id),
        )
        self.assertEqual(sorted(totals.values()), [D("200"), D("300")])
        self.assertEqual(len(totals), 2)
        self.assertIn("Халтура", totals)


if __name__ == "__main__":
    unittest.main()
