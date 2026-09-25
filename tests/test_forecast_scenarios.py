import os
import tempfile
import unittest
import importlib.util
from datetime import date
from decimal import Decimal as D


_DATA = tempfile.TemporaryDirectory()
os.environ.setdefault("ALLOCATOR_DATA_DIR", _DATA.name)

from financial_engine import (  # noqa: E402
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    UserSettings,
)
from financial_path_card import financial_path_items, render_financial_path_card  # noqa: E402
from forecast import simulate_financial_path, simulate_goal_forecast  # noqa: E402
from storage import db  # noqa: E402


TODAY = date(2026, 9, 25)


def settings_for(
    profile: str,
    *,
    debts: list[Credit] | None = None,
    goals: list[Goal] | None = None,
    average_income: D = D("150000"),
) -> UserSettings:
    rhythm = {
        "stable": "monthly",
        "piecework": "irregular",
        "cyclic": "cyclic",
    }[profile]
    return UserSettings(
        has_debts=bool(debts),
        profile_type=profile,
        employment_type="Наёмный" if profile == "stable" else "Фрилансер",
        income_rhythm=rhythm,
        critical_life=D("20000"),
        household_reserve=D("5000"),
        average_income=average_income,
        minimum_reserve_months=D("1"),
        force_majeure_months=D("3"),
        stabilizer_target_months=D("2"),
        income_work_months=D("3"),
        income_gap_months=D("2"),
        reliable_gap_income=D("0"),
        life_categories={"Жизнь": D("20000")},
        credits=list(debts or ()),
        goals=list(goals or ()),
    )


def allocator_at_level(profile: str, level: int) -> FinancialAllocator:
    debts = (
        [Credit("Проверочный долг", D("20000"), None, D("0"), D("5000"))]
        if level <= 2
        else []
    )
    allocator = FinancialAllocator(settings_for(profile, debts=debts), AllocatorState())
    if level == 2:
        allocator.state.pillow_minimum = allocator.settings.minimum_reserve_limit
    elif level >= 4:
        if profile == "stable":
            allocator.state.pillow_force_majeure = allocator.settings.force_majeure_limit
        elif profile == "piecework":
            allocator.state.pillow_force_majeure = allocator.settings.force_majeure_limit
            if level >= 5:
                allocator.state.pillow_stabilizer = allocator.settings.stabilizer_life_limit
            if level >= 6:
                allocator.state.pillow_stabilizer = allocator.settings.stabilizer_full_limit
        else:
            allocator.state.intercontract_reserve = (
                allocator.intercontract_current_life_limit
                if level == 4
                else allocator.intercontract_current_limit
            )
            if level >= 6:
                allocator.state.pillow_force_majeure = allocator.settings.force_majeure_limit
    return allocator


class ForecastScenarioAcceptanceTests(unittest.TestCase):
    def test_all_profiles_recognize_every_starting_level(self):
        expected_levels = {
            "stable": range(1, 5),
            "piecework": range(1, 7),
            "cyclic": range(1, 7),
        }
        for profile, levels in expected_levels.items():
            for level in levels:
                with self.subTest(profile=profile, level=level):
                    allocator = allocator_at_level(profile, level)
                    self.assertEqual(allocator.active_mode(), level)
                    result = simulate_financial_path(
                        allocator,
                        max_months=120,
                        today=TODAY,
                    )
                    self.assertEqual(result["starting_mode"], level)
                    visible_levels = [
                        item.level
                        for item in financial_path_items(allocator, result)
                        if item.kind == "level"
                    ]
                    self.assertTrue(
                        all(item_level > level for item_level in visible_levels)
                    )

    def test_multiple_debts_and_goals_follow_the_route_order(self):
        for profile in ("stable", "piecework", "cyclic"):
            with self.subTest(profile=profile):
                debts = [
                    Credit("Кредитка", D("10000"), None, D("0"), D("5000")),
                    Credit("Рассрочка", D("20000"), None, D("0"), D("7000")),
                    Credit("Кредит", D("30000"), None, D("0"), D("9000")),
                ]
                goals = [
                    Goal("Отпуск", D("30"), target_amount=D("30000")),
                    Goal("Ноутбук", D("30"), target_amount=D("50000")),
                    Goal("Машина", D("40"), target_amount=D("90000")),
                ]
                allocator = FinancialAllocator(settings_for(profile, debts=debts, goals=goals))
                result = simulate_financial_path(allocator, max_months=240, today=TODAY)
                items = financial_path_items(allocator, result)

                debt_items = [item for item in items if item.kind == "debt"]
                self.assertEqual(len(debt_items), 3)
                self.assertEqual(debt_items[-1].title, "Долгов нет")
                self.assertEqual(
                    {item.title for item in debt_items[:-1]},
                    {"Долг «Кредитка» погашен", "Долг «Рассрочка» погашен"},
                )
                goal_items = [item for item in items if item.kind == "goal"]
                self.assertEqual(
                    {item.title for item in goal_items},
                    {"Цель «Отпуск»", "Цель «Ноутбук»", "Цель «Машина»"},
                )
                level_three_index = next(
                    index for index, item in enumerate(items) if item.key == "level:3"
                )
                self.assertTrue(
                    all(items.index(item) > level_three_index for item in goal_items)
                )

    def test_tax_and_planned_payment_are_forecast_together(self):
        for index, profile in enumerate(("stable", "piecework", "cyclic"), 1):
            with self.subTest(profile=profile):
                telegram_id = 991700040 + index
                allocator = FinancialAllocator(settings_for(profile))
                allocator.settings.life_categories["Страхование"] = D("2000")
                db.save_allocator(telegram_id, allocator)
                payment_id = db.add_planned_payment(
                    telegram_id,
                    "insurance",
                    "Страхование",
                    "ОСАГО",
                    D("6000"),
                    D("2000"),
                    "2026-12-25",
                )
                tax_id = db.add_tax_obligation(
                    telegram_id,
                    "Транспортный налог",
                    "Автомобиль",
                    D("3000"),
                    D("0"),
                    3,
                    D("1000"),
                    due_date="2026-12-25",
                    annual_monthly_amount=D("0"),
                    applied_annual_monthly_amount=D("0"),
                )
                try:
                    loaded = db.load_allocator(telegram_id)
                    result = simulate_financial_path(loaded, max_months=24, today=TODAY)
                    milestone_keys = {item.key for item in result["milestones"]}
                    self.assertIn(f"payment:{payment_id}", milestone_keys)
                    self.assertIn(f"tax:{tax_id}", milestone_keys)
                    self.assertFalse(result["obligation_shortfalls"])
                finally:
                    db.delete_user(telegram_id)

    def test_goal_deadlines_cover_reachable_and_unreachable_cases(self):
        maximum_levels = {"stable": 4, "piecework": 6, "cyclic": 6}
        for profile, maximum in maximum_levels.items():
            with self.subTest(profile=profile):
                goals = [
                    Goal(
                        "Достижимая",
                        D("50"),
                        target_amount=D("20000"),
                        deadline="2027-09-25",
                    ),
                    Goal(
                        "Недостижимая",
                        D("50"),
                        target_amount=D("5000000"),
                        deadline="2026-10-25",
                    ),
                ]
                allocator = allocator_at_level(profile, maximum)
                allocator.settings.goals = goals
                attainable = allocator.goal_forecast(goals[0], today=TODAY)
                unattainable = allocator.goal_forecast(goals[1], today=TODAY)
                self.assertEqual(attainable["status"], "on_track")
                self.assertEqual(unattainable["status"], "unreachable")

                detailed = simulate_goal_forecast(allocator, max_months=120, today=TODAY)
                self.assertEqual(
                    {projection.name for projection in detailed["projections"]},
                    {"Достижимая", "Недостижимая"},
                )

    def test_cyclic_level_cups_follow_their_own_reserve_entities(self):
        allocator = FinancialAllocator(settings_for("cyclic"))
        result = simulate_financial_path(allocator, max_months=120, today=TODAY)
        months = {item.key: item.months for item in result["milestones"]}
        self.assertGreaterEqual(months["level:4"], months["salary:min"])
        self.assertGreaterEqual(months["level:5"], months["salary:full"])
        self.assertGreaterEqual(months["level:6"], months["pillow"])

    def test_maximum_level_shows_new_goal_but_not_completed_history(self):
        for profile, maximum in (("stable", 4), ("piecework", 6), ("cyclic", 6)):
            with self.subTest(profile=profile):
                allocator = allocator_at_level(profile, maximum)
                old_goal = Goal(
                    "Старая цель",
                    D("0"),
                    balance=D("30000"),
                    target_amount=D("30000"),
                    status="completed",
                )
                new_goal = Goal(
                    "Новая цель",
                    D("100"),
                    target_amount=D("50000"),
                )
                allocator.settings.goals = [old_goal, new_goal]
                allocator.state.goal_balances = {
                    old_goal.name: D("30000"),
                    new_goal.name: D("0"),
                }
                result = simulate_financial_path(
                    allocator,
                    max_months=120,
                    today=TODAY,
                )
                items = financial_path_items(allocator, result)
                titles = {item.title for item in items if item.kind == "goal"}
                self.assertEqual(titles, {"Цель «Новая цель»"})
                self.assertFalse(any(item.kind == "level" for item in items))

                if importlib.util.find_spec("PIL") is not None:
                    image = render_financial_path_card(allocator, result)
                    self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
