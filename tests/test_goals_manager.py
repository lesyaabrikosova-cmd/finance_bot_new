import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial_engine import AllocatorState, FinancialAllocator, Goal, UserSettings, normalize_active_goal_percentages
from goals_manager import (
    allocation_basis_text,
    allocation_variability_note,
    ask_percentage,
    format_percentage,
    funded_goal_overflow_note,
    freeze_button_name,
    goal_percentage_recommendation,
    goal_portfolio_requirement,
    goal_line,
    icon,
    parse_date,
    parse_decimal,
    parse_percentage,
    position_count_phrase,
    show_chests_list,
    show_goals_list,
    show_goals_manager,
    show_allocation,
    show_position_card,
    status_icon,
)
from calculators import start_vacation_calculator
from ui import main_menu_keyboard


class GoalsManagerTests(unittest.TestCase):
    def test_input_helpers_accept_russian_money_and_dates(self):
        self.assertEqual(parse_decimal("12 500,50 ₽"), Decimal("12500.50"))
        self.assertEqual(parse_date("01.08.2027").isoformat(), "2027-08-01")

    def test_percentage_parser_accepts_suffix_and_two_decimal_places(self):
        self.assertEqual(parse_percentage("50%"), Decimal("50"))
        self.assertEqual(parse_percentage(" 15,25 % "), Decimal("15.25"))
        self.assertIsNone(parse_percentage("5%0"))
        self.assertIsNone(parse_percentage("15,255%"))
        self.assertEqual(format_percentage(Decimal("15.20")), "15.2")

    def test_position_count_phrases_and_profile_copy(self):
        self.assertEqual(position_count_phrase(1), "1 цель или сундук")
        self.assertEqual(position_count_phrase(2), "2 цели и сундука")
        self.assertEqual(position_count_phrase(5), "5 целей и сундуков")
        self.assertIn("минимальном гарантированном", allocation_basis_text("stable"))
        self.assertIn("голодные", allocation_variability_note("piecework"))

    def test_goal_recommendation_uses_two_decimal_places_and_detects_portfolio_conflict(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="irregular",
            critical_life=Decimal("10000"),
            household_reserve=Decimal("5000"),
            average_income=Decimal("100000"),
            goals=[
                Goal("Первая", 0, target_amount=Decimal("1000000"), deadline="2027-09-01"),
                Goal("Вторая", 0, target_amount=Decimal("1000000"), deadline="2027-09-01"),
                Goal("Подарки", 100, position_type="chest", is_system_chest=True),
            ],
        )
        allocator = FinancialAllocator(settings, AllocatorState())
        recommendation = goal_percentage_recommendation(
            allocator,
            settings.goals[0],
            today=date(2026, 9, 1),
        )
        self.assertIsNotNone(recommendation)
        self.assertEqual(
            recommendation["conservative_percentage"],
            recommendation["conservative_percentage"].quantize(Decimal("0.01")),
        )
        portfolio = goal_portfolio_requirement(allocator, today=date(2026, 9, 1))
        self.assertTrue(portfolio["conflict"])
        self.assertEqual(portfolio["available"], Decimal("99"))

    def test_funded_goal_overflow_names_multiple_chests_without_picking_one(self):
        allocator = FinancialAllocator(
            UserSettings(
                has_debts=False,
                employment_type="Фрилансер",
                critical_life=Decimal("100"),
                household_reserve=Decimal("0"),
                average_income=Decimal("1000"),
                goals=[
                    Goal("Отпуск", 20, target_amount=Decimal("100"), balance=Decimal("90")),
                    Goal("Подарки", 40, position_type="chest", is_system_chest=True),
                    Goal("Техника", 40, position_type="chest"),
                ],
            ),
            AllocatorState(goal_balances={"Отпуск": Decimal("90")}),
        )
        note = funded_goal_overflow_note(
            allocator,
            allocator.settings.goals[0],
            {"minimum": Decimal("100"), "maximum": Decimal("100")},
            Decimal("20"),
        )
        self.assertIn("между активными Сундуками по их долям", note)

    def test_icons_follow_entity_type(self):
        self.assertEqual(icon(Goal("Отпуск", 50)), "⭐️")
        self.assertEqual(icon(Goal("Подарки", 50, position_type="chest")), "🧳")
        self.assertEqual(status_icon(Goal("Позже", 0, status="paused")), "❄️")
        self.assertEqual(status_icon(Goal("Готово", 0, status="completed")), "✅")

    def test_freeze_recipient_labels_only_shorten_chests(self):
        self.assertEqual(
            freeze_button_name(Goal("Замена техники", 50, position_type="chest")),
            "Техника",
        )
        self.assertEqual(
            freeze_button_name(Goal("Сундук мечты", 50)),
            "Сундук мечты",
        )

    def test_pausing_position_preserves_it_and_rebalances_active_ones(self):
        goals = [Goal("A", 30), Goal("B", 30), Goal("C", 40)]
        goals[1].previous_percentage = goals[1].percentage
        goals[1].status = "paused"
        normalize_active_goal_percentages(goals)
        self.assertEqual(goals[1].previous_percentage, Decimal("30"))
        self.assertEqual(goals[1].status, "paused")
        self.assertEqual(
            sum((goal.percentage for goal in goals if goal.status == "active"), Decimal("0")),
            Decimal("100"),
        )

    def test_legacy_goal_without_target_can_be_rendered(self):
        class State:
            goal_balances = {"Старая": Decimal("100")}

        class Allocator:
            state = State()

        text = goal_line(Allocator(), Goal("Старая", 100))
        self.assertIn("⭐️", text)
        self.assertIn("Старая", text)


class GoalsManagerUiTests(unittest.IsolatedAsyncioTestCase):
    def allocator(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            goals=[
                Goal("Отпуск", 0, target_amount=1000),
                Goal("Подарки", 100, position_type="chest", is_system_chest=True),
                Goal("Айфон", 0, status="paused", target_amount=1000),
            ],
            allocation_needs_review=True,
        )
        return FinancialAllocator(settings, AllocatorState())

    async def test_main_screen_has_only_navigation_and_dynamic_warning(self):
        allocator = self.allocator()
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        with (
            patch("goals_manager.db") as db,
            patch("goals_manager.render_goals_card", return_value=b"png"),
        ):
            db.load_allocator.return_value = allocator
            await show_goals_manager(message, 42)

        message.answer.assert_not_awaited()
        self.assertNotIn("caption", message.answer_photo.await_args.kwargs)
        buttons = [
            button.text
            for row in message.answer_photo.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(
            buttons,
            ["⭐️ Мои Цели", "🧳 Мои Сундуки", "⚠️ Настройте доли", "← Главное меню", "ℹ️ Помощь"],
        )

    async def test_main_menu_contains_calculators_entry(self):
        allocator = self.allocator()
        with patch("ui.db") as db:
            db.load_allocator.return_value = allocator
            db.initial_distribution_available.return_value = False
            markup = main_menu_keyboard(42)

        buttons = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("Калькуляторы", buttons)
        self.assertIn("Цели и Сундуки", buttons)

    async def test_main_screen_returns_to_regular_allocation_button(self):
        allocator = self.allocator()
        allocator.settings.allocation_needs_review = False
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        with (
            patch("goals_manager.db") as db,
            patch("goals_manager.render_goals_card", return_value=b"png"),
        ):
            db.load_allocator.return_value = allocator
            await show_goals_manager(message, 42)

        buttons = [
            button.text
            for row in message.answer_photo.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("✎ Доли распределения", buttons)
        self.assertNotIn("⚠️ Настройте доли", buttons)

    async def test_allocation_chart_uses_balance_colors_and_requested_titles(self):
        allocator = self.allocator()
        message = SimpleNamespace(answer=AsyncMock())
        with (
            patch("goals_manager.db") as db,
            patch("goals_manager.send_chart_report", new=AsyncMock()) as report,
        ):
            db.load_allocator.return_value = allocator
            await show_allocation(message, 42)

        self.assertEqual(report.await_args.args[2], "ЦЕЛИ И СУНДУКИ")
        self.assertEqual(report.await_args.kwargs["subtitle"], "Доли распределения")
        self.assertEqual(report.await_args.kwargs["colors"]["Сундук Подарков"], "#6a432f")
        self.assertEqual(report.await_args.kwargs["colors"]["Отпуск"], "#FDE047")
        self.assertEqual(report.await_args.kwargs["preserve_order"], True)
        self.assertIn("Проверьте доли", report.await_args.args[3])

    async def test_goal_and_chest_lists_are_separate(self):
        allocator = self.allocator()
        goal_message = SimpleNamespace(answer=AsyncMock())
        chest_message = SimpleNamespace(answer=AsyncMock())
        with patch("goals_manager.db") as db:
            db.load_allocator.return_value = allocator
            await show_goals_list(goal_message, 42)
            await show_chests_list(chest_message, 42)

        goal_buttons = [
            button.text
            for row in goal_message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        chest_buttons = [
            button.text
            for row in chest_message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("⭐️ Отпуск", goal_buttons)
        self.assertIn("❄️ Айфон", goal_buttons)
        self.assertNotIn("🧳 Сундук Подарков", goal_buttons)
        self.assertIn("🧳 Сундук Подарков", chest_buttons)
        self.assertNotIn("⭐️ Отпуск", chest_buttons)

    async def test_vacation_calculator_starts_even_when_goal_exists(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = AsyncMock()
        state.get_data.return_value = {"vacation_index": 0}

        await start_vacation_calculator(callback, state)

        state.set_state.assert_awaited()
        self.assertGreaterEqual(callback.message.answer.await_count, 2)

    async def test_goal_and_chest_cards_have_contextual_actions(self):
        allocator = self.allocator()
        goal, chest = allocator.settings.goals[:2]
        goal_message = SimpleNamespace(answer=AsyncMock())
        chest_message = SimpleNamespace(answer=AsyncMock())
        with patch("goals_manager.db") as db:
            db.load_allocator.return_value = allocator
            await show_position_card(goal_message, 42, goal.uid)
            await show_position_card(chest_message, 42, chest.uid)

        goal_text = goal_message.answer.await_args.args[0]
        chest_text = chest_message.answer.await_args.args[0]
        goal_buttons = [
            button.text
            for row in goal_message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        chest_buttons = [
            button.text
            for row in chest_message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("Готово", goal_text)
        self.assertIn("✎ Баланс", goal_buttons)
        self.assertIn("СУНДУК ПОДАРКОВ", chest_text)
        self.assertIn("Отложено Аллокатором", chest_text)
        self.assertNotIn("Накоплено", chest_text)
        self.assertIn("Этот сундук нельзя удалить или заморозить", chest_text)
        self.assertNotIn("✎ Баланс", chest_buttons)
        self.assertNotIn("🗑️ Удалить", chest_buttons)
        self.assertNotIn("❄️ Заморозить", chest_buttons)

    async def test_non_last_chest_keeps_balance_freeze_and_delete_actions(self):
        allocator = self.allocator()
        chest = allocator.settings.goals[1]
        allocator.settings.goals.append(
            Goal("Хотелки", 0, position_type="chest")
        )
        message = SimpleNamespace(answer=AsyncMock())
        with patch("goals_manager.db") as db:
            db.load_allocator.return_value = allocator
            await show_position_card(message, 42, chest.uid)

        buttons = [
            button.text
            for row in message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("✎ Баланс", buttons)
        self.assertIn("❄️ Заморозить", buttons)
        self.assertIn("🗑️ Удалить", buttons)

    async def test_percentage_wizard_uses_full_first_screen_and_dynamic_next_limit(self):
        allocator = self.allocator()
        allocator.settings.goals = [
            Goal("Отпуск", 0, target_amount=1000),
            Goal("Хотелки", 0, position_type="chest"),
            Goal("Продвижение", 0, position_type="chest"),
            Goal("Техника", 0, position_type="chest"),
            Goal("Подарки", 100, position_type="chest", is_system_chest=True),
        ]
        allocator._ensure_goal_balances()
        message = SimpleNamespace(answer=AsyncMock())
        state = AsyncMock()
        uids = [goal.uid for goal in allocator.settings.active_goals]
        state.get_data.return_value = {
            "goal_percentages": [],
            "goal_percentage_uids": uids,
        }
        with patch("goals_manager.db") as db:
            db.load_allocator.return_value = allocator
            await ask_percentage(message, 42, state)
        first = message.answer.await_args.args[0]
        self.assertIn("У вас сейчас <b>5 активных позиций</b>:", first)
        self.assertIn("Аллокатор сможет направлять на них примерно", first)
        self.assertIn("Чтобы было проще выбирать проценты", first)
        self.assertIn("Введите процент от 1% до 96%", first)

        message.answer.reset_mock()
        state.get_data.return_value = {
            "goal_percentages": ["5"],
            "goal_percentage_uids": uids,
        }
        with patch("goals_manager.db") as db:
            db.load_allocator.return_value = allocator
            await ask_percentage(message, 42, state)
        second = message.answer.await_args.args[0]
        self.assertIn("<pre>", second)
        self.assertIn("⭐️ Отпуск", second)
        self.assertIn("— 5% ≈", second)
        table = second.split("<pre>", 1)[1].split("</pre>", 1)[0]
        self.assertNotIn("Сундук Хотелок", table)
        self.assertIn("10% ≈", second)
        self.assertIn("Введите процент от 1% до 92%", second)


if __name__ == "__main__":
    unittest.main()
