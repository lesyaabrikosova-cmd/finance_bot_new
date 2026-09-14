import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dashboard import (
    INCOME_COLOR_FAMILIES,
    MONTH_BUTTON_NAMES,
    MONTH_EMOJIS,
    income_distribution_text,
    income_analysis_chart_colors,
    income_analysis_totals,
    income_analysis_periods,
    income_history_distribution,
    income_history_operations,
    income_color_types,
    income_months_keyboard,
    income_operations_for_period,
    income_operation_card_text,
    next_income_color_shade,
    rebuild_period_analytics_from_history,
    send_income_period_analysis,
    send_income_history,
    send_income_history_detail,
)
from financial_engine import FinancialAllocator, UserSettings
from storage import (
    Database,
    deserialize_income_rhythm,
    remove_legacy_income_tax_pseudo_types,
    serialize_income_types,
)
from time_utils import moscow_today


def operation(operation_id=17):
    return {
        "id": operation_id,
        "type": "income_distribution",
        "created_at": "2026-09-10T12:00:00",
        "payload": {
            "date": "2026-09-09",
            "income_type": "Частник",
            "income": "3700",
            "tax": "222",
            "note": "Урок с Машей",
            "allocations": {
                "Инвестиции": "740",
                "КЖ:Квартира": "1400",
                "КЖ:Тройка": "60",
            },
        },
    }


class IncomeHistoryTests(unittest.IsolatedAsyncioTestCase):
    def test_income_color_palette_contains_nine_requested_heart_colors(self):
        self.assertEqual(
            [icon for _, icon, _ in INCOME_COLOR_FAMILIES],
            ["❤️", "🧡", "💛", "💚", "💙", "💜", "🩷", "🤎", "🩶"],
        )

    def test_ten_automatic_income_colours_are_perceptually_distinct(self):
        from income_colors import (
            MIN_INCOME_COLOR_DISTANCE,
            income_color_distance,
            select_income_color,
        )
        colors = []
        for _ in range(10):
            colors.append(select_income_color(colors))
        self.assertEqual(len(colors), len(set(colors)))
        for index, color in enumerate(colors):
            for other in colors[index + 1:]:
                self.assertGreaterEqual(
                    income_color_distance(color, other),
                    MIN_INCOME_COLOR_DISTANCE,
                )

    def test_history_is_ordered_by_income_date_not_the_later_ledger_id(self):
        newer = operation(7)
        newer["payload"]["date"] = "2026-09-10"
        forgotten_older_income = operation(8)
        forgotten_older_income["payload"]["date"] = "2026-09-02"
        with patch("dashboard.db") as db:
            # The later ledger ID simulates an older income entered today.
            db.load_operations.return_value = [forgotten_older_income, newer]
            operations = income_history_operations(42)
        self.assertEqual([item["id"] for item in operations], [7, 8])

    async def test_history_lists_saved_income_with_navigation(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [operation()]
            db.load_allocator.return_value = None
            await send_income_history(message, 42)
        self.assertIn(
            "ИСТОРИЯ ТЕКУЩЕГО ПЕРИОДА",
            message.answer.await_args.args[0],
        )
        self.assertIn(
            "Здесь показаны доходы с момента последнего закрытия расчётного периода.",
            message.answer.await_args.args[0],
        )
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("09.09 · Частник · 3 700", buttons)
        self.assertIn("← Назад", buttons)
        self.assertIn("← Главное меню", buttons)

    async def test_primary_history_only_shows_open_period_incomes(self):
        message = SimpleNamespace(answer=AsyncMock())
        current_income = operation(3)
        current_income["payload"]["date"] = "2026-09-10"
        older_income = operation(1)
        older_income["payload"]["date"] = "2026-08-10"
        period_reset = {"id": 2, "type": "period_reset", "payload": {}}
        with patch("dashboard.db") as db:
            # The operation log is read from newest to oldest.
            db.load_operations.return_value = [current_income, period_reset, older_income]
            await send_income_history(message, 42)
        buttons = [
            button.text
            for row in message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("10.09 · Частник · 3 700", buttons)
        self.assertNotIn("10.08.2026 · Частник · 3 700", buttons)

    async def test_month_history_filters_operations_and_returns_to_same_month(self):
        message = SimpleNamespace(answer=AsyncMock())
        march = operation(17)
        march["payload"]["date"] = "2025-03-09"
        april = operation(18)
        april["payload"]["date"] = "2025-04-10"
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [april, march]
            await send_income_history(message, 42, year=2025, month=3)
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertIn("09.03.2025 · Частник · 3 700", [button.text for button in buttons])
        self.assertNotIn("10.04.2025 · Частник · 3 700", [button.text for button in buttons])
        self.assertIn("incomeanalysis:month:2025:3", [button.callback_data for button in buttons])

    def test_card_and_distribution_keep_note_and_group_order(self):
        item = operation()
        self.assertIn("————————————\n📝 Урок с Машей\n————————————", income_operation_card_text(item))
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))
        text = income_distribution_text(item, allocator)
        self.assertIn("<blockquote>", text)
        self.assertLess(text.index("📈"), text.index("❤️ <b>Квартира</b>"))
        self.assertLess(text.index("Квартира"), text.index("Тройка"))

    async def test_month_detail_and_distribution_keep_selected_month(self):
        detail_message = SimpleNamespace(answer=AsyncMock())
        with (
            patch("dashboard.find_income_history_operation", return_value=operation()),
            patch("dashboard.current_period_income_ids", return_value=set()),
        ):
            await send_income_history_detail(
                detail_message, 42, 17, year=2025, month=3,
            )
        detail_callbacks = [
            button.callback_data
            for row in detail_message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("incomehistory:distribution:17:2025:3", detail_callbacks)
        self.assertIn("incomehistory:note:17:2025:3", detail_callbacks)
        self.assertIn("incomehistory:month:2025:3", detail_callbacks)

        callback = SimpleNamespace(
            answer=AsyncMock(),
            data="incomehistory:distribution:17:2025:3",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(goals=[]))
        with (
            patch("dashboard.find_income_history_operation", return_value=operation()),
            patch("dashboard.db") as db,
        ):
            db.load_allocator.return_value = allocator
            await income_history_distribution(callback, state)
        distribution_callbacks = [
            button.callback_data
            for row in callback.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("incomehistory:detail:17:2025:3", distribution_callbacks)
        self.assertIn("incomehistory:month:2025:3", distribution_callbacks)

    def test_recreated_income_type_name_does_not_break_analysis(self):
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={"Работа": "new-id"},
            income_type_labels={"new-id": "Работа", "old-id": "Работа"},
        ))
        current = operation(2)
        current["payload"].update(
            income="100", income_type="Работа", income_type_id="new-id",
        )
        previous = operation(1)
        previous["payload"].update(
            income="50", income_type="Работа", income_type_id="old-id",
        )

        self.assertEqual(
            income_analysis_totals(allocator, [current, previous]),
            {
                "Работа": Decimal("100"),
                "Работа · прежний тип": Decimal("50"),
            },
        )

    def test_deletion_rebuilds_chart_totals_from_remaining_ledger(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Квартира": Decimal("100")},
        ))
        remaining = operation()
        with patch("dashboard.db") as db:
            db.load_operations.return_value = [remaining, {"type": "period_reset"}]
            rebuild_period_analytics_from_history(allocator, 42)
        self.assertEqual(allocator.state.period_income, Decimal("3700"))
        self.assertEqual(allocator.state.period_tax, Decimal("222"))
        self.assertEqual(allocator.state.period_allocations["Инвестиции"], Decimal("740"))

    def test_latest_income_rollback_restores_the_exact_snapshot(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Жизнь": Decimal("100")},
        ))
        before = deepcopy(allocator.state)
        allocator.process_income(Decimal("1000"), "Подарок", tax_override=Decimal("0"))
        operation = allocator.state.operation_log[-1]
        allocator.rollback_income_operation(operation, restore_snapshot=True)
        self.assertEqual(allocator.state.period_income, before.period_income)
        self.assertEqual(allocator.state.period_tax, before.period_tax)
        self.assertEqual(allocator.state.life_balance, before.life_balance)
        self.assertEqual(allocator.state.pillow_balance, before.pillow_balance)
        self.assertEqual(allocator.state.investments, before.investments)

    def test_legacy_rollback_does_not_fail_on_a_renamed_period_key(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Жизнь": Decimal("100")},
        ))
        allocator.process_income(Decimal("1000"), "Подарок", tax_override=Decimal("0"))
        operation = deepcopy(allocator.state.operation_log[-1])
        operation.pop("state_before")
        operation.pop("credits_before")
        allocator.state.period_allocations = {}
        allocator.rollback_income_operation(operation)
        self.assertEqual(allocator.state.period_income, Decimal("0"))

    def test_calendar_period_filter_handles_month_year_and_empty_periods(self):
        march = operation(1)
        march["payload"]["date"] = "2025-03-09"
        april = operation(2)
        april["payload"]["date"] = "2025-04-10"
        operations = [march, april, {"type": "period_reset"}]
        self.assertEqual(income_operations_for_period(operations, "month", 2025, 3), [march])
        self.assertEqual(income_operations_for_period(operations, "year", 2025), [march, april])
        self.assertEqual(income_operations_for_period(operations, "year", 2024), [])

    async def test_empty_past_month_and_year_show_explicit_messages_without_chart(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={},
        ))
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "month", 2025, 3)
            await send_income_period_analysis(message, 42, "year", 2025)
        chart.assert_not_awaited()
        self.assertEqual(message.answer.await_count, 2)
        self.assertIn("<b>МАРТ 2025</b>", message.answer.await_args_list[0].args[0])
        self.assertIn("В этом месяце нет записанных доходов.", message.answer.await_args_list[0].args[0])
        self.assertEqual(
            message.answer.await_args_list[1].args[0],
            "<b>2025</b>\n\nВ этом году нет записанных доходов.",
        )

    async def test_empty_future_month_explains_that_it_has_not_started(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={}, income_type_colors={},
        ))
        today = moscow_today()
        future_year = today.year + (1 if today.month == 12 else 0)
        future_month = 1 if today.month == 12 else today.month + 1
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "month", future_year, future_month)
        chart.assert_not_awaited()
        self.assertIn("Этот месяц ещё не начался.", message.answer.await_args.args[0])

    async def test_period_analysis_groups_multiple_income_types_for_month_and_year(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={},
        ))
        march = operation(1)
        march["payload"].update(date="2025-03-09", income_type="Работа", income="1000")
        april = operation(2)
        april["payload"].update(date="2025-04-10", income_type="Подарок", income="2000")
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = [april, march]
            await send_income_period_analysis(message, 42, "month", 2025, 3)
            await send_income_period_analysis(message, 42, "year", 2025)
        self.assertEqual(chart.await_args_list[0].args[1], {"Работа": Decimal("1000")})
        self.assertEqual(chart.await_args_list[1].args[1], {"Подарок": Decimal("2000"), "Работа": Decimal("1000")})

    def test_income_type_color_is_matched_to_immutable_id_in_chart(self):
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={"Работа": "income-work"},
            income_type_labels={"income-work": "Работа"},
            income_type_colors={"income-work": "#9675E5"},
        ))
        item = operation()
        item["payload"].update(income_type="Работа", income_type_id="income-work")
        self.assertEqual(
            income_analysis_chart_colors(allocator, [item]),
            {"Работа": "#9675E5"},
        )

    def test_repeated_base_color_uses_next_free_shade(self):
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_colors={"income-one": "#9675E5"},
        ))
        next_color = next_income_color_shade(allocator, "income-two", 5)
        self.assertNotEqual(next_color, "#9675E5")
        from income_colors import MIN_INCOME_COLOR_DISTANCE, income_color_distance
        self.assertGreaterEqual(
            income_color_distance(next_color, "#9675E5"),
            MIN_INCOME_COLOR_DISTANCE,
        )

    def test_color_settings_exclude_legacy_tax_rules(self):
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={
                "Зарплата": "income-salary",
                "Самозанятость · Физики · 3%": "legacy-npd",
                "ИП · УСН · 6%": "legacy-usn",
            },
            income_type_tax_rates={
                "Зарплата": Decimal("0"),
                "Самозанятость · Физики · 3%": Decimal("3"),
                "ИП · УСН · 6%": Decimal("6"),
            },
            income_tax_profiles={
                "НПД · ФЛ · 3%": {"rate": "3"},
                "ИП · УСН · 6%": {"rate": "6"},
            },
        ))
        self.assertEqual(income_color_types(allocator), [("Зарплата", "income-salary")])

    def test_legacy_tax_pseudo_type_cleanup_preserves_history_and_balances(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("100"),
            average_income=Decimal("2000"),
            income_type_tax_rates={
                "Зарплата": Decimal("0"),
                "Самозанятость · Физики · 3%": Decimal("3"),
                "ИП · УСН «Доходы» · 6%": Decimal("6"),
            },
            income_type_ids={
                "Зарплата": "income-salary",
                "Самозанятость · Физики · 3%": "legacy-npd",
                "ИП · УСН «Доходы» · 6%": "legacy-usn",
            },
            income_type_colors={
                "income-salary": "#55B5DB",
                "legacy-npd": "#9675E5",
                "legacy-usn": "#B59AEC",
            },
            income_tax_profiles={
                "НПД · ФЛ · 3%": {"rate": "3"},
                "ИП · УСН · 6%": {"rate": "6"},
            },
        )
        allocator = FinancialAllocator(settings)
        allocator.state.period_income = Decimal("5000")
        allocator.state.life_balance = Decimal("3000")

        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cleanup.db")
            try:
                database.save_allocator(42, allocator)
                database.save_operation(42, "income_distribution", {
                    "type": "income_distribution",
                    "date": "2025-01-10",
                    "income": "1000",
                    "income_type": "Самозанятость · Физики · 3%",
                })

                loaded = database.load_allocator(42)
                self.assertEqual(loaded.settings.income_type_tax_rates, {"Зарплата": Decimal("0")})
                self.assertEqual(loaded.settings.income_type_ids, {"Зарплата": "income-salary"})
                self.assertEqual(loaded.settings.income_type_colors, {"income-salary": "#55B5DB"})
                self.assertEqual(len(loaded.settings.income_tax_profiles), 2)
                self.assertEqual(loaded.state.period_income, Decimal("5000"))
                self.assertEqual(loaded.state.life_balance, Decimal("3000"))
                self.assertEqual(database.operation_count(42), 1)
                self.assertEqual(
                    database.load_operations(42)[0]["payload"]["income_type"],
                    "Самозанятость · Физики · 3%",
                )

                persisted = database.load_settings(42)
                self.assertEqual(persisted.income_type_tax_rates, {"Зарплата": Decimal("0")})
            finally:
                database.close()

    def test_cleanup_helper_does_not_remove_real_income_types(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("100"),
            average_income=Decimal("2000"),
            income_type_tax_rates={"Частник": Decimal("6")},
            income_type_ids={"Частник": "income-client"},
            income_type_colors={"income-client": "#69BE98"},
        )
        self.assertEqual(remove_legacy_income_tax_pseudo_types(settings), set())
        self.assertEqual(settings.income_type_tax_rates, {"Частник": Decimal("6")})
        self.assertEqual(settings.income_type_colors, {"income-client": "#69BE98"})

    def test_income_type_color_survives_settings_serialization(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("100"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            income_type_tax_rates={"Работа": Decimal("0")},
            income_type_ids={"Работа": "income-work"},
            income_type_colors={"income-work": "#9675E5"},
        )
        restored = deserialize_income_rhythm(serialize_income_types(settings))
        self.assertEqual(restored["income_type_colors"], {"income-work": "#9675E5"})

    def test_month_navigation_keeps_selected_year_when_returning_from_march(self):
        markup = income_months_keyboard(2025)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("incomeanalysis:month:2025:3", callbacks)
        self.assertIn("incomeanalysis:months:2024", callbacks)
        self.assertIn("incomeanalysis:months:2026", callbacks)
        self.assertIn("< 2024", [button.text for button in markup.inline_keyboard[-2]])
        self.assertIn("2026 >", [button.text for button in markup.inline_keyboard[-2]])

    async def test_other_period_places_years_before_months(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock())
        await income_analysis_periods(callback, state)
        self.assertIn(
            "Здесь доходы собраны по календарным месяцам — по указанной вами дате. "
            "Календарный месяц может не совпадать с вашим расчётным периодом.",
            callback.message.answer.await_args.args[0],
        )
        markup = callback.message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual([button.text for button in markup.inline_keyboard[0]], ["По годам", "По месяцам"])

    async def test_history_page_uses_period_navigation_symbols(self):
        message = SimpleNamespace(answer=AsyncMock())
        operations = [operation(index) for index in range(1, 10)]
        for index, item in enumerate(operations):
            item["payload"]["date"] = f"2026-09-{index + 1:02d}"
        with patch("dashboard.db") as db:
            db.load_operations.return_value = operations
            db.load_allocator.return_value = None
            await send_income_history(message, 42)
            await send_income_history(message, 42, page=1)
        first_markup = message.answer.await_args_list[0].kwargs["reply_markup"]
        second_markup = message.answer.await_args_list[1].kwargs["reply_markup"]
        self.assertIn("< Предыдущие", [button.text for button in first_markup.inline_keyboard[-2]])
        self.assertIn("К последним >", [button.text for button in second_markup.inline_keyboard[-2]])

    def test_current_year_month_navigation_does_not_offer_a_future_year(self):
        current_year = moscow_today().year
        markup = income_months_keyboard(current_year)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(f"incomeanalysis:months:{current_year - 1}", callbacks)
        self.assertNotIn(f"incomeanalysis:months:{current_year + 1}", callbacks)

    def test_only_current_month_has_its_seasonal_emoji(self):
        today = moscow_today()
        current_markup = income_months_keyboard(today.year)
        current_buttons = [button.text for row in current_markup.inline_keyboard for button in row]
        self.assertIn(f"{MONTH_EMOJIS[today.month - 1]} {MONTH_BUTTON_NAMES[today.month - 1]}", current_buttons)
        self.assertEqual(
            sum(any(icon in button for icon in MONTH_EMOJIS) for button in current_buttons),
            1,
        )

        past_markup = income_months_keyboard(today.year - 1)
        past_buttons = [button.text for row in past_markup.inline_keyboard for button in row]
        self.assertFalse(any(icon in button for icon in MONTH_EMOJIS for button in past_buttons))

    async def test_current_year_analysis_does_not_offer_a_future_year(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        allocator = SimpleNamespace(settings=SimpleNamespace(
            income_type_ids={}, income_type_labels={}, income_type_colors={},
        ))
        current_year = moscow_today().year
        with patch("dashboard.db") as db, patch("dashboard.send_chart_report", new_callable=AsyncMock) as chart:
            db.load_allocator.return_value = allocator
            db.load_operations.return_value = []
            await send_income_period_analysis(message, 42, "year", current_year)
        chart.assert_not_awaited()
        markup = message.answer.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(f"incomeanalysis:year:{current_year - 1}", callbacks)
        self.assertNotIn(f"incomeanalysis:year:{current_year + 1}", callbacks)
