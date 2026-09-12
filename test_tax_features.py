import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal


_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from onboarding import (  # noqa: E402
    add_calendar_months,
    build_contract_obligations,
    build_default_km_storage,
    build_state_from_data,
    br_group_totals,
    category_added_entries,
    category_added_totals,
    communication_item_name,
    contract_obligation_entries,
    cyclic_gift_history_monthly,
    default_km_storage,
    force_majeure_minimum_for_rhythm,
    gift_history_monthly,
    housing_item_name,
    input_period_label,
    is_future_goal_expense,
    keyboard,
    km_item_display_name,
    km_item_totals_by_name,
    km_group_totals,
    life_classification_reason,
    life_categories_from_storage,
    life_expense_summary,
    matching_housing_total,
    matching_communication_total,
    months_until_due_date,
    months_until_tax_ready,
    next_annual_tax_due_date,
    normalized_onboarding_goals,
    normalize_pass_months,
    pass_monthly_saving,
    parse_tax_due_date,
    planned_taxes_from_storage,
    reserve_progress_block,
    save_pass_accumulated,
    should_auto_route_to_reserve,
)
from planned_payments import apply_planned_payment_allocation, refresh_planned_payment_targets  # noqa: E402
from taxes import (  # noqa: E402
    annual_tax_due_date,
    annual_tax_monthly_norm,
    apply_planned_tax_allocation,
    calculate_notice_plan,
    calculate_payment_progress,
    collect_tax_statistics,
    make_pie_chart,
    parse_tax_object_name,
    reconcile_tax_obligation_balances,
    refresh_planned_tax_targets,
    report_text,
    start_next_annual_tax_cycle,
    TAX_COLORS,
    tax_funding_date,
    tax_months_remaining,
    tax_notice_months_remaining,
    tax_obligation_card_text,
    tax_obligations_overview,
    virtual_tax_balance,
)
from financial_engine import AllocatorState, FinancialAllocator, Goal, PhaseLifeBudget, UserSettings  # noqa: E402
from storage import (  # noqa: E402
    Database,
    db,
    deserialize_income_rhythm,
    deserialize_income_types,
    serialize_income_types,
    serialize_json,
)


class TaxFeatureTests(unittest.TestCase):
    def test_tax_object_name_rejects_amounts_and_normalizes_valid_names(self):
        self.assertIsNone(parse_tax_object_name("1000"))
        self.assertIsNone(parse_tax_object_name("1 000 ₽"))
        self.assertIsNone(parse_tax_object_name("01.12.2026"))
        self.assertIsNone(parse_tax_object_name("/menu"))
        self.assertIsNone(parse_tax_object_name("—"))
        self.assertEqual(parse_tax_object_name("  Дача   2 "), "Дача 2")
        self.assertEqual(parse_tax_object_name("Патент № 1"), "Патент № 1")

    def test_property_tax_card_explains_only_accelerated_saving(self):
        item = {
            "tax_type": "Налог на имущество",
            "object_name": "Хата",
            "target_amount": Decimal("12000"),
            "monthly_amount": Decimal("6000"),
            "annual_monthly_amount": Decimal("1000"),
            "notice_received": True,
            "due_date": "2026-12-01",
        }
        text = tax_obligation_card_text(item, Decimal("0"))
        self.assertIn("Сейчас откладываем — 6 000 ₽/мес", text)
        self.assertIn("Если бы копили весь год", text)
        self.assertIn("До 1 ноября предварительная сумма будет собрана.", text)
        self.assertIn("До 1 декабря — налог должен быть оплачен.", text)

        item["monthly_amount"] = Decimal("1000")
        self.assertNotIn(
            "Если бы копили весь год",
            tax_obligation_card_text(item, Decimal("0")),
        )

    def test_patent_is_a_dated_payment_without_annual_reserve_norm(self):
        item = {
            "tax_type": "Патент",
            "object_name": "Ветеринарка",
            "target_amount": Decimal("60000"),
            "monthly_amount": Decimal("30000"),
            "annual_monthly_amount": Decimal("30000"),
            "due_date": "2026-11-11",
        }
        self.assertEqual(annual_tax_monthly_norm(item), Decimal("0"))
        text = tax_obligation_card_text(item, Decimal("0"), show_changes=True)
        self.assertIn("До 11 ноября — патент должен быть оплачен.", text)
        self.assertIn("Критический минимум и резервы не изменились.", text)
        self.assertNotIn("предварительная сумма", text)
        self.assertNotIn("Годовая норма", text)

    def test_tax_overview_lists_active_obligations_or_empty_state(self):
        telegram_id = 990001
        self.assertEqual(
            tax_obligations_overview([]),
            "<b>НАЛОГИ В АЛЛОКАТОРЕ</b>\n\nПока нет накопленных или плановых налогов.",
        )
        obligation_id = db.add_tax_obligation(
            telegram_id, "Налог на имущество", "Квартира", Decimal("12000"),
            Decimal("1000"), 10, Decimal("1100"), "2026-12-01", Decimal("1000"),
        )
        obligation = next(
            item for item in db.load_tax_obligations(telegram_id)
            if item["id"] == obligation_id
        )
        self.assertIn(
            "• Налог на имущество · Квартира",
            tax_obligations_overview([obligation]),
        )

    def test_legacy_patent_no_longer_inflates_critical_life_or_reserves(self):
        telegram_id = 990002
        key = "Патент · Ветеринарка"
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="irregular",
            critical_life=Decimal("120000"),
            average_income=Decimal("180000"),
            base_critical_life=Decimal("90000"),
            automatic_life_obligations={f"tax:{key}": Decimal("30000")},
            planned_taxes={key: Decimal("30000")},
            household_reserve=Decimal("20000"),
            force_majeure_months=Decimal("4"),
            stabilizer_target_months=Decimal("1"),
        )
        db.save_allocator(telegram_id, FinancialAllocator(settings))
        obligation_id = db.add_tax_obligation(
            telegram_id, "Патент", "Ветеринарка", Decimal("60000"),
            Decimal("0"), 2, Decimal("30000"), "2026-11-11", Decimal("30000"),
        )

        allocator = db.load_allocator(telegram_id)
        self.assertEqual(allocator.settings.critical_life, Decimal("90000.00"))
        self.assertEqual(allocator.settings.household_life, Decimal("110000.00"))
        self.assertEqual(allocator.settings.force_majeure_limit, Decimal("360000.00"))
        self.assertNotIn(key, allocator.settings.planned_taxes)
        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
        self.assertEqual(item["annual_monthly_amount"], Decimal("0"))

    def test_dated_tax_catchup_is_allocated_without_permanent_life_category(self):
        key = "Патент · Ветеринарка"
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("10000"),
            average_income=Decimal("20000"),
            household_reserve=Decimal("0"),
            tax_catchups={key: Decimal("1000")},
        ))
        allocations = {}
        allocator._allocate_to_life(Decimal("2000"), allocations)
        self.assertEqual(allocations["КЖ:Налоги"], Decimal("1000"))
        self.assertEqual(allocations["КЖ:Зарплата"], Decimal("1000"))

    def test_legacy_tax_tables_receive_cycle_and_reminder_columns(self):
        with tempfile.TemporaryDirectory() as data_dir:
            database_path = os.path.join(data_dir, "legacy.db")
            connection = sqlite3.connect(database_path)
            connection.execute(
                "CREATE TABLE tax_payments (id INTEGER PRIMARY KEY, telegram_id INTEGER, "
                "tax_name TEXT, amount TEXT, paid_at TEXT)"
            )
            connection.execute(
                "CREATE TABLE tax_obligations (id INTEGER PRIMARY KEY, telegram_id INTEGER, "
                "tax_type TEXT, object_name TEXT, target_amount TEXT, opening_amount TEXT, "
                "saved_before TEXT, months INTEGER, monthly_amount TEXT, "
                "annual_monthly_amount TEXT, notice_received INTEGER, "
                "ready_reminder_sent_at TEXT, due_date TEXT, active INTEGER)"
            )
            connection.commit()
            connection.close()

            migrated = Database(database_path)
            payment_columns = {
                row["name"] for row in migrated.connection.execute(
                    "PRAGMA table_info(tax_payments)"
                ).fetchall()
            }
            obligation_columns = {
                row["name"] for row in migrated.connection.execute(
                    "PRAGMA table_info(tax_obligations)"
                ).fetchall()
            }
            migrated.close()
            self.assertIn("obligation_id", payment_columns)
            self.assertIn("tax_due_year", payment_columns)
            self.assertIn("last_notice_reminder_at", obligation_columns)
            self.assertIn("last_payment_reminder_at", obligation_columns)
            self.assertIn("next_payment_reminder_date", obligation_columns)
            self.assertIn("applied_annual_monthly_amount", obligation_columns)

    def test_onboarding_goal_monthly_amounts_become_exact_percentages(self):
        goals = normalized_onboarding_goals([
            {"name": "Подарки", "monthly": "3500"},
            {"name": "Отпуск", "monthly": "6500"},
        ])
        self.assertEqual(goals[0], {"name": "Подарки", "percentage": "35.0000"})
        self.assertEqual(goals[1], {"name": "Отпуск", "percentage": "65.0000"})
        self.assertEqual(
            sum(Decimal(item["percentage"]) for item in goals),
            Decimal("100"),
        )

    def test_onboarding_goal_percentages_ignore_empty_amounts(self):
        goals = normalized_onboarding_goals([
            {"name": "Подарки", "monthly": "0"},
            {"name": "Замена техники", "monthly": "5000"},
        ])
        self.assertEqual(
            goals,
            [{"name": "Замена техники", "percentage": "100"}],
        )

    def test_onboarding_goals_survive_allocator_storage_round_trip(self):
        telegram_id = 880020
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="irregular",
            critical_life=Decimal("90000"),
            household_reserve=Decimal("20000"),
            average_income=Decimal("180000"),
            goals=[
                Goal("Подарки", Decimal("35")),
                Goal("Отпуск", Decimal("65")),
            ],
        )
        allocator = FinancialAllocator(
            settings,
            AllocatorState(goal_balances={
                "Подарки": Decimal("3500"),
                "Отпуск": Decimal("6500"),
            }),
        )
        db.save_allocator(telegram_id, allocator)
        restored = db.load_allocator(telegram_id)
        self.assertIsNotNone(restored)
        self.assertEqual(
            [(goal.name, goal.percentage) for goal in restored.settings.goals],
            [("Подарки", Decimal("35")), ("Отпуск", Decimal("65")), ("Будущие покупки", Decimal("0"))],
        )
        self.assertEqual(restored.state.goal_balances["Подарки"], Decimal("3500"))
        self.assertEqual(restored.state.goal_balances["Отпуск"], Decimal("6500"))

    def test_onboarding_keyboard_removes_duplicate_callbacks(self):
        markup = keyboard([
            [("Аксессуары", "kmquick:pets:accessories"), ("+ Другое", "kmquick:pets:other")],
            [("+ Другое", "kmquick:pets:other"), ("✔️ Готово", "km:cancel")],
        ])
        buttons = [button for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for button in buttons]
        self.assertEqual(callbacks.count("kmquick:pets:other"), 1)
        self.assertEqual(len(callbacks), len(set(callbacks)))

    def test_onboarding_keyboard_normalizes_service_button_labels(self):
        markup = keyboard([
            [("Назад", "back"), ("Сохранить", "save")],
            [("Отмена", "cancel"), ("Готово", "done")],
        ])
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(
            labels,
            ["Назад", "Сохранить", "Отмена", "Готово"],
        )

    def test_category_summary_collects_all_items_and_sums_duplicates(self):
        data = {
            "km_items": [
                {"category": "habits", "name": "Вейп", "monthly": "2000"},
                {"category": "habits", "name": "Сигареты", "monthly": "5000"},
            ],
            "br_items": [
                {"category": "habits", "name": "Вейп", "monthly": "351.29"},
            ],
        }

        totals = category_added_totals(data, "habits")

        self.assertEqual(totals["Вейп"], Decimal("2351.29"))
        self.assertEqual(totals["Сигареты"], Decimal("5000"))

    def test_category_input_summary_keeps_original_amount_and_period(self):
        data = {
            "km_items": [
                {"category": "health", "name": "Стоматолог", "amount": "12000", "months": "12", "monthly": "1000"},
                {"category": "health", "name": "Стоматолог", "amount": "8000", "months": "12", "monthly": "666.67"},
                {"category": "health", "name": "Стоматолог", "amount": "5000", "months": "6", "monthly": "833.33"},
            ],
            "br_items": [],
        }

        entries = category_added_entries(data, "health")

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["amount"], Decimal("20000"))
        self.assertEqual(input_period_label(entries[0]["months"]), "в год")
        self.assertEqual(entries[1]["amount"], Decimal("5000"))
        self.assertEqual(input_period_label(entries[1]["months"]), "за 6 мес.")

    def test_category_input_summary_displays_due_date(self):
        entries = category_added_entries({
            "km_items": [{
                "category": "housing",
                "name": "Налог на имущество · Квартира",
                "amount": "900",
                "months": "4",
                "monthly": "225",
                "due_date": "2026-12-01",
            }],
            "br_items": [],
        }, "housing")

        self.assertEqual(
            input_period_label(entries[0]["months"], entries[0]["due_date"]),
            "к 01.12.2026",
        )

    def test_cyclic_force_majeure_minimum_is_always_six_months(self):
        self.assertEqual(force_majeure_minimum_for_rhythm("cyclic"), 6)

    def test_life_summary_groups_raw_amounts_by_category_and_period(self):
        text = life_expense_summary([
            {
                "category": "health",
                "subcategory": "medical",
                "name": "Медицинские услуги",
                "amount": "32970",
                "months": "12",
                "monthly": "2747.50",
            },
            {
                "category": "clothes",
                "name": "Одежда",
                "amount": "55452",
                "months": "7",
                "monthly": "7921.71",
            },
        ])
        self.assertIn("<b><u>ЗДОРОВЬЕ</u></b>", text)
        self.assertIn("<b>Медицинские услуги</b> — 32 970 ₽ / год", text)
        self.assertIn("<b><u>ОДЕЖДА</u></b>", text)
        self.assertIn("<b>Одежда</b> — 55 452 ₽ / 7 мес.", text)
        self.assertNotIn("Обязательная жизнь", text)

    def test_life_summary_displays_week_instead_of_internal_month_fraction(self):
        weekly_months = Decimal("12") / Decimal("52")
        text = life_expense_summary([{
            "category": "food",
            "name": "Питьевая вода",
            "amount": "105",
            "months": str(weekly_months),
            "monthly": "455",
        }])

        self.assertIn("<b>Питьевая вода</b> — 105 ₽ / неделю", text)
        self.assertNotIn("0.230769", text)

    def test_reserve_progress_block_marks_only_reached_target(self):
        self.assertEqual(
            reserve_progress_block("Подушка", Decimal("340000"), Decimal("360000")),
            "<b><u>ПОДУШКА</u></b>\n340 000 из 360 000 ₽",
        )
        self.assertEqual(
            reserve_progress_block("Стабилизатор", Decimal("110000"), Decimal("110000")),
            "<b><u>СТАБИЛИЗАТОР</u></b> ✔️\n110 000 из 110 000 ₽",
        )

    def test_cyclic_break_uses_remaining_months_for_salary_fund_target(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("39000"),
            household_reserve=Decimal("7000"),
            average_income=Decimal("57000"),
            income_rhythm="cyclic",
            profile_type="cyclic",
            income_work_months=Decimal("5"),
            income_gap_months=Decimal("7"),
        )
        state = build_state_from_data(
            {
                "current_pillow": "0",
                "current_stabilizer": "0",
                "current_intercontract": "92000",
                "current_life_balance": "0",
                "current_cycle_phase": "break",
                "current_cycle_gap_remaining": "2",
            },
            settings,
        )
        allocator = FinancialAllocator(settings, state)
        self.assertTrue(state.intercontract_break_active)
        self.assertEqual(state.intercontract_months_remaining, Decimal("2"))
        self.assertEqual(state.intercontract_reserve, Decimal("92000"))
        self.assertEqual(allocator.intercontract_current_limit, Decimal("92000"))

    def test_cyclic_work_phase_targets_the_full_future_break(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("39000"),
            household_reserve=Decimal("7000"),
            average_income=Decimal("57000"),
            income_rhythm="cyclic",
            profile_type="cyclic",
            income_work_months=Decimal("5"),
            income_gap_months=Decimal("7"),
        )
        state = build_state_from_data(
            {
                "current_pillow": "0",
                "current_stabilizer": "0",
                "current_intercontract": "0",
                "current_life_balance": "0",
                "current_cycle_phase": "work",
            },
            settings,
        )
        allocator = FinancialAllocator(settings, state)
        self.assertFalse(state.intercontract_break_active)
        self.assertEqual(allocator.intercontract_current_limit, Decimal("322000"))

    def test_life_classification_period_drops_trailing_zeroes(self):
        item = {"category": "subscriptions", "months": "12.00"}
        self.assertEqual(
            life_classification_reason(item, "br"),
            "оплата происходит раз в 12 мес.",
        )

    def test_children_reserve_category_survives_settings_serialization(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("100"),
            household_reserve=Decimal("50"),
            household_reserve_categories={"Дети": Decimal("20")},
            average_income=Decimal("100"),
        )
        restored = deserialize_income_rhythm(serialize_income_types(settings))
        self.assertEqual(restored["household_reserve_categories"], {"Дети": Decimal("20")})

    def test_gifts_are_saved_as_history_but_excluded_from_household_reserve(self):
        items = [
            {
                "category": "gifts",
                "category_label": "Подарки",
                "name": "Подарки семье",
                "monthly": "5000",
            },
            {
                "category": "repairs",
                "category_label": "Быт",
                "name": "Бытовые мелочи",
                "monthly": "2000",
            },
            {
                "category": "children",
                "subcategory": "gifts",
                "category_label": "Дети",
                "name": "Подарки ребёнку",
                "monthly": "1000",
            },
        ]

        self.assertEqual(br_group_totals(items), {"Быт": Decimal("2000.00")})
        self.assertEqual(gift_history_monthly(items), Decimal("6000.00"))

    def test_gifts_never_appear_as_cyclic_work_obligations(self):
        entries = contract_obligation_entries({
            "km_items": [],
            "br_items": [{
                "category": "gifts",
                "subcategory": "family",
                "name": "Подарки семье",
                "monthly": "5000",
            }],
        })
        self.assertEqual(entries, [])

    def test_future_education_goal_is_excluded_from_household_reserve(self):
        items = [{
            "category": "education",
            "category_label": "Образование",
            "name": "Иностранный язык",
            "monthly": "3000",
            "future_goal": True,
        }]
        self.assertTrue(is_future_goal_expense(items[0]))
        self.assertEqual(br_group_totals(items), {})

    def test_multiple_required_education_expenses_share_one_envelope(self):
        items = [
            {"category": "education", "category_label": "Образование", "name": "Курс", "monthly": "5000", "months": "1"},
            {"category": "education", "category_label": "Образование", "name": "Репетитор", "monthly": "4000", "months": "1"},
        ]
        storage = build_default_km_storage(items)
        self.assertEqual([item["storage"] for item in storage], ["separate", "separate"])
        self.assertEqual([item["envelope_name"] for item in storage], ["Образование", "Образование"])

    def test_cyclic_gift_history_is_weighted_across_both_phases(self):
        result = cyclic_gift_history_monthly(
            {
                "work": {
                    "historical_gifts_monthly": "120",
                    "exchange_rate_to_rub": "80",
                    "completed": True,
                },
                "break": {
                    "historical_gifts_monthly": "3000",
                    "exchange_rate_to_rub": "1",
                    "completed": True,
                },
            },
            Decimal("5"),
            Decimal("7"),
        )
        self.assertEqual(result, Decimal("5750.00"))

    def test_gift_history_survives_settings_serialization(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="cyclic",
            critical_life=Decimal("100"),
            household_reserve=Decimal("50"),
            average_income=Decimal("100"),
            historical_gifts_monthly=Decimal("5000"),
            protective_stage_c_goals_share=Decimal("27"),
            gift_guideline_min=Decimal("2"),
            gift_guideline_max=Decimal("6"),
            gift_warning_limit=Decimal("9"),
            phase_life_budgets={
                "break": PhaseLifeBudget(
                    historical_gifts_monthly=Decimal("3000"),
                    completed=True,
                ),
            },
        )
        restored = deserialize_income_rhythm(serialize_income_types(settings))
        self.assertEqual(restored["historical_gifts_monthly"], Decimal("5000"))
        self.assertEqual(restored["protective_stage_c_goals_share"], Decimal("27"))
        self.assertEqual(restored["gift_guideline_min"], Decimal("2"))
        self.assertEqual(restored["gift_guideline_max"], Decimal("6"))
        self.assertEqual(restored["gift_warning_limit"], Decimal("9"))
        self.assertEqual(
            restored["phase_life_budgets"]["break"].historical_gifts_monthly,
            Decimal("3000"),
        )

    def test_contract_obligation_storage_survives_settings_serialization(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="cyclic",
            critical_life=Decimal("100"),
            household_reserve=Decimal("50"),
            average_income=Decimal("100"),
            contract_obligations={"ЖКХ": Decimal("500")},
            contract_obligation_storage={"ЖКХ": "Недвижимость"},
        )
        restored = deserialize_income_rhythm(serialize_income_types(settings))
        self.assertEqual(restored["contract_obligation_storage"], {"ЖКХ": "Недвижимость"})

    def test_combined_onboarding_routes_nonmonthly_ambiguous_expenses_to_reserve(self):
        for category in ("communication", "fees"):
            self.assertTrue(
                should_auto_route_to_reserve(category, Decimal("3"), True)
            )
        self.assertFalse(
            should_auto_route_to_reserve("habits", Decimal("3"), True)
        )

    def test_combined_onboarding_keeps_monthly_and_obvious_expenses_in_critical_life(self):
        self.assertFalse(
            should_auto_route_to_reserve("communication", Decimal("1"), True)
        )
        self.assertFalse(
            should_auto_route_to_reserve("health", Decimal("12"), True)
        )
        self.assertFalse(
            should_auto_route_to_reserve("fees", Decimal("12"), False)
        )

    def test_pass_deadline_uses_complete_months_conservatively(self):
        self.assertEqual(normalize_pass_months(Decimal("1.5")), Decimal("1"))
        self.assertEqual(normalize_pass_months(Decimal("9")), Decimal("9"))
        self.assertEqual(normalize_pass_months(Decimal("0.5")), Decimal("1"))

    def test_pass_saving_subtracts_amount_already_accumulated(self):
        self.assertEqual(
            pass_monthly_saving(Decimal("12000"), Decimal("3000"), Decimal("3")),
            Decimal("3000.00"),
        )
        self.assertEqual(
            pass_monthly_saving(Decimal("12000"), Decimal("12000"), Decimal("3")),
            Decimal("0.00"),
        )

    def test_full_pass_onboarding_path_persists_remaining_amount(self):
        class FakeState:
            def __init__(self):
                self.data = {
                    "pending_km_item_amount": "24900",
                    "pending_km_payment_months": "9",
                    "pending_km_category": "transport",
                    "pending_km_category_label": "Транспорт",
                    "pending_km_item_name": "Безлимитный проездной",
                    "pending_km_subcategory": "pass",
                    "combined_life_onboarding": True,
                    "km_items": [],
                    "br_items": [],
                }

            async def get_data(self):
                return dict(self.data)

            async def update_data(self, **values):
                self.data.update(values)

            async def set_state(self, _state):
                return None

        class FakeMessage:
            text = "7815"

            async def answer(self, *_args, **_kwargs):
                return None

        state = FakeState()
        asyncio.run(save_pass_accumulated(FakeMessage(), state))
        item = state.data["km_items"][0]
        self.assertEqual(item["amount"], "24900")
        self.assertEqual(item["accumulated"], "7815")
        self.assertEqual(item["calculation_amount"], "17085")
        self.assertEqual(item["monthly"], "1898.33")

    def test_car_subcategory_uses_automobile_envelope(self):
        result = default_km_storage(
            {
                "category": "transport",
                "category_label": "Транспорт",
                "subcategory": "car_fuel",
                "name": "Бензин",
                "amount": "5000",
                "months": "1",
                "monthly": "5000",
            }
        )
        self.assertEqual(result["storage"], "separate")
        self.assertEqual(result["envelope_name"], "Автомобиль")

    def test_communication_item_names_do_not_require_phone_number(self):
        self.assertEqual(
            communication_item_name("mobile", "Рабочий"),
            "Мобильная связь · Рабочий",
        )
        self.assertEqual(
            communication_item_name("subscription", "Облако"),
            "Подписки · Облако",
        )
        self.assertEqual(
            communication_item_name("tv", "Дом"),
            "ТВ · Дом",
        )

    def test_matching_communication_total_sums_same_label(self):
        items = [
            {
                "category": "communication",
                "subcategory": "mobile",
                "name": "Мобильная связь · Рабочий",
                "monthly": "500",
            },
            {
                "category": "communication",
                "subcategory": "mobile",
                "name": " мобильная связь · рабочий ",
                "monthly": "250",
            },
            {
                "category": "communication",
                "subcategory": "internet",
                "name": "Домашний интернет · Рабочий",
                "monthly": "700",
            },
        ]
        self.assertEqual(
            matching_communication_total(
                items, "mobile", "Мобильная связь · Рабочий"
            ),
            Decimal("750.00"),
        )

    def test_housing_item_name_keeps_tax_object_separate(self):
        self.assertEqual(
            housing_item_name("utilities", "ЖКХ", "Квартира"),
            "ЖКХ · Квартира",
        )
        self.assertEqual(
            housing_item_name("property_tax", "Налог на имущество", "Квартира"),
            "Квартира",
        )

    def test_matching_housing_total_sums_repeated_combination(self):
        items = [
            {
                "category": "housing",
                "subcategory": "utilities",
                "name": "ЖКХ · Квартира",
                "monthly": "300",
            },
            {
                "category": "housing",
                "subcategory": "utilities",
                "name": "  жкх · квартира ",
                "monthly": "200",
            },
            {
                "category": "housing",
                "subcategory": "rent",
                "name": "Аренда · Квартира",
                "monthly": "1000",
            },
        ]
        self.assertEqual(
            matching_housing_total(items, "utilities", "ЖКХ · Квартира"),
            Decimal("500.00"),
        )

    def test_same_km_names_are_summed_for_display(self):
        items = [
            {
                "category": "housing",
                "subcategory": "other",
                "name": "Квартплата",
                "monthly": "300",
            },
            {
                "category": "housing",
                "subcategory": "other",
                "name": "  квартплата  ",
                "monthly": "200",
            },
        ]
        self.assertEqual(
            km_item_totals_by_name(items),
            [("Квартплата", Decimal("500.00"))],
        )

    def test_same_km_names_with_different_tax_dates_stay_separate(self):
        items = [
            {
                "category": "housing",
                "subcategory": "property_tax",
                "name": "Квартира",
                "due_date": "2026-12-01",
                "monthly": "300",
            },
            {
                "category": "housing",
                "subcategory": "property_tax",
                "name": "Квартира",
                "due_date": "2027-12-01",
                "monthly": "200",
            },
        ]
        self.assertEqual(
            km_item_totals_by_name(items),
            [
                ("Налог на имущество · Квартира", Decimal("300.00")),
                ("Налог на имущество · Квартира", Decimal("200.00")),
            ],
        )

    def test_tax_item_display_name_includes_tax_type(self):
        self.assertEqual(
            km_item_display_name(
                {"name": "квартира", "subcategory": "property_tax"}
            ),
            "Налог на имущество · квартира",
        )
        self.assertEqual(
            km_item_display_name(
                {"name": "Дача", "subcategory": "land_tax"}
            ),
            "Земельный налог · Дача",
        )
        self.assertEqual(
            km_item_display_name(
                {"name": "Лада", "subcategory": "tax"}
            ),
            "Транспортный налог · Лада",
        )

    def test_calendar_months_preserve_valid_day(self):
        self.assertEqual(add_calendar_months(date(2026, 8, 31), 1), date(2026, 9, 30))
        self.assertEqual(add_calendar_months(date(2026, 10, 31), 5), date(2027, 3, 31))

    def test_completed_planned_payment_stops_monthly_target(self):
        telegram_id = 880003
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("2000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("2000"),
            life_categories={"Образование": Decimal("1000")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        db.add_planned_payment(
            telegram_id,
            "Образование",
            "Образование",
            "Первый семестр",
            Decimal("1000"),
            Decimal("1000"),
            "2026-10-01",
        )
        apply_planned_payment_allocation(telegram_id, allocator, "Образование", Decimal("1000"))
        self.assertNotIn("Образование", allocator.settings.life_categories)
        self.assertEqual(allocator.settings.critical_life, Decimal("1000"))
        self.assertEqual(db.load_planned_payments(telegram_id), [])

    def test_planned_payment_uses_only_its_share_of_shared_envelope(self):
        telegram_id = 880004
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("2500"),
            household_reserve=Decimal("0"),
            average_income=Decimal("2500"),
            life_categories={"Образование": Decimal("1500")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        db.add_planned_payment(
            telegram_id,
            "Образование",
            "Образование",
            "Семестр",
            Decimal("2000"),
            Decimal("1000"),
            "2026-10-01",
        )
        apply_planned_payment_allocation(telegram_id, allocator, "Образование", Decimal("1500"))
        payment = db.load_planned_payments(telegram_id)[0]
        self.assertEqual(payment["saved_amount"], Decimal("1000"))

    def test_planned_payment_overflow_moves_to_other_goal_in_same_envelope(self):
        telegram_id = 880005
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Образование": Decimal("1000")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        first = db.add_planned_payment(
            telegram_id, "Образование", "Образование", "Первый",
            Decimal("100"), Decimal("500"), "2026-10-01",
        )
        second = db.add_planned_payment(
            telegram_id, "Образование", "Образование", "Второй",
            Decimal("2000"), Decimal("500"), "2027-03-01",
        )
        apply_planned_payment_allocation(telegram_id, allocator, "Образование", Decimal("1000"))
        items = {item["id"]: item for item in db.load_planned_payments(telegram_id, active_only=False)}
        self.assertEqual(items[first]["saved_amount"], Decimal("100"))
        self.assertEqual(items[second]["saved_amount"], Decimal("900"))

    def test_income_types_can_have_different_tax_rates(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            income_type_tax_rates={
                "Зарплата": Decimal("0"),
                "Заказ ФЛ": Decimal("4"),
                "Заказ ЮЛ": Decimal("6"),
            },
        )
        allocator = FinancialAllocator(settings)
        self.assertEqual(allocator.calculate_tax(Decimal("10000"), "Зарплата"), Decimal("0"))
        self.assertEqual(allocator.calculate_tax(Decimal("10000"), "Заказ ФЛ"), Decimal("400"))
        self.assertEqual(allocator.calculate_tax(Decimal("10000"), "Заказ ЮЛ"), Decimal("600"))

    def test_income_type_rates_survive_storage_round_trip(self):
        telegram_id = 880002
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            income_type_tax_rates={"Заказ ФЛ": Decimal("4"), "Подарок": Decimal("0")},
            income_type_tax_profiles={"Заказ ФЛ": "НПД · ФЛ · 4%"},
        )
        db.save_allocator(telegram_id, FinancialAllocator(settings))
        loaded = db.load_allocator(telegram_id)
        self.assertEqual(
            loaded.settings.income_type_tax_rates,
            {"Заказ ФЛ": Decimal("4"), "Подарок": Decimal("0")},
        )
        self.assertEqual(
            loaded.settings.income_type_tax_profiles,
            {"Заказ ФЛ": "НПД · ФЛ · 4%"},
        )

    def test_income_rhythm_survives_storage_round_trip(self):
        telegram_id = 880006
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("500"),
            average_income=Decimal("3000"),
            income_rhythm="cyclic",
            income_gap_months=Decimal("6"),
            income_work_months=Decimal("5"),
            reliable_gap_income=Decimal("12000"),
            stabilizer_target_months=Decimal("3"),
            contract_obligations={"ЖКХ": Decimal("5000")},
            income_type_tax_rates={"Вахта": Decimal("0")},
        )
        allocator = FinancialAllocator(settings)
        allocator.state.intercontract_reserve = Decimal("3456")
        allocator.state.intercontract_months_remaining = Decimal("4")
        allocator.state.intercontract_break_active = True
        allocator.state.contract_obligations_reserve = Decimal("4321")
        allocator.state.cycle_income = Decimal("7890")
        db.save_allocator(telegram_id, allocator)
        loaded = db.load_allocator(telegram_id)
        self.assertEqual(loaded.settings.income_rhythm, "cyclic")
        self.assertEqual(loaded.settings.income_gap_months, Decimal("6"))
        self.assertEqual(loaded.settings.income_work_months, Decimal("5"))
        self.assertEqual(loaded.settings.reliable_gap_income, Decimal("12000"))
        self.assertEqual(loaded.settings.stabilizer_target_months, Decimal("3"))
        self.assertEqual(loaded.settings.contract_obligations, {"ЖКХ": Decimal("5000")})
        self.assertEqual(loaded.state.intercontract_reserve, Decimal("3456"))
        self.assertEqual(loaded.state.intercontract_months_remaining, Decimal("4"))
        self.assertTrue(loaded.state.intercontract_break_active)
        self.assertEqual(loaded.state.contract_obligations_reserve, Decimal("4321"))
        self.assertEqual(loaded.state.cycle_income, Decimal("7890"))
        self.assertEqual(loaded.settings.profile_type, "cyclic")

    def test_unknown_income_type_is_rejected_when_profile_has_types(self):
        settings = UserSettings(
            has_debts=False, employment_type="Наёмный",
            critical_life=Decimal("1000"), household_reserve=Decimal("0"),
            average_income=Decimal("1000"), income_type_tax_rates={"Зарплата": Decimal("0")},
        )
        with self.assertRaisesRegex(ValueError, "не найден"):
            FinancialAllocator(settings).process_income(Decimal("1000"), "Опечатка")

    def test_planned_payment_target_is_recalculated_from_remaining_deadline(self):
        telegram_id = 880007
        settings = UserSettings(
            has_debts=False, employment_type="Фрилансер",
            critical_life=Decimal("2000"), household_reserve=Decimal("0"),
            average_income=Decimal("2000"), life_categories={"Образование": Decimal("1000")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        payment_id = db.add_planned_payment(
            telegram_id, "Образование", "Образование", "Семестр",
            Decimal("3000"), Decimal("500"), "2026-11-01",
        )
        db.update_planned_payment_saved(telegram_id, payment_id, Decimal("1000"), True)
        refresh_planned_payment_targets(telegram_id, allocator, date(2026, 9, 1))
        payment = db.load_planned_payments(telegram_id)[0]
        self.assertEqual(payment["monthly_amount"], Decimal("1000.00"))
        self.assertEqual(allocator.settings.life_categories["Образование"], Decimal("1500.00"))

    def test_legacy_tax_profile_is_migrated_to_per_type_rates(self):
        taxable, rates = deserialize_income_types(
            serialize_json(["Заказ ФЛ", "Заказ ЮЛ"]),
            Decimal("6"),
        )
        self.assertEqual(taxable, ["Заказ ФЛ", "Заказ ЮЛ"])
        self.assertEqual(rates, {"Заказ ФЛ": Decimal("6"), "Заказ ЮЛ": Decimal("6")})

    def test_tax_due_date_helpers(self):
        self.assertEqual(parse_tax_due_date("01.12.2026"), date(2026, 12, 1))
        self.assertIsNone(parse_tax_due_date("2026-12-01"))
        self.assertEqual(months_until_due_date(date(2026, 8, 19), date(2026, 12, 1)), 4)
        self.assertEqual(months_until_due_date(date(2026, 8, 19), date(2026, 8, 30)), 1)

    def test_transport_tax_is_stored_in_common_tax_envelope(self):
        item = {
            "category": "transport",
            "category_label": "Транспорт",
            "subcategory": "tax",
            "name": "Автомобиль",
            "amount": "12000",
            "months": "12",
            "monthly": "1000",
        }
        storage = default_km_storage(item)
        self.assertEqual(storage["storage"], "separate")
        self.assertEqual(storage["envelope_name"], "Налоги")

    def test_unconfirmed_tax_does_not_enter_life_categories(self):
        legacy_or_manually_changed = {
            "category": "transport",
            "subcategory": "tax",
            "item_name": "Автомобиль",
            "monthly": "1000",
            "storage": "salary",
            "envelope_name": "Транспортный налог",
        }
        categories = life_categories_from_storage([legacy_or_manually_changed])
        self.assertEqual(categories, {})

    def test_contract_obligations_can_select_one_phone_but_not_another(self):
        data = {
            "income_work_months": "5",
            "contract_obligation_keys": ["km:0"],
            "km_items": [
                {"name": "Мегафон", "amount": "150", "months": "1", "monthly": "150"},
                {"name": "МТС", "amount": "200", "months": "1", "monthly": "200"},
            ],
            "br_items": [],
        }
        obligations, lines, total = build_contract_obligations(data)
        self.assertEqual(obligations, {"Мегафон": "750.00"})
        self.assertEqual(total, Decimal("750.00"))
        self.assertEqual(len(lines), 1)
        self.assertIn("Мегафон", lines[0])
        self.assertNotIn("МТС", lines[0])

    def test_public_transport_period_is_only_used_for_averaging(self):
        item = {
            "category": "transport",
            "category_label": "Транспорт",
            "subcategory": "public",
            "name": "Общественный транспорт",
            "amount": "9508.50",
            "months": "6",
            "monthly": "1584.75",
        }
        storage = default_km_storage(item)
        self.assertEqual(storage["storage"], "salary")
        self.assertIsNone(storage["envelope_name"])

    def test_unlimited_pass_uses_separate_envelope(self):
        item = {
            "category": "transport",
            "category_label": "Транспорт",
            "subcategory": "pass",
            "name": "Безлимитный проездной",
            "amount": "12000",
            "months": "6",
            "monthly": "2000",
        }
        storage = default_km_storage(item)
        self.assertEqual(storage["storage"], "separate")
        self.assertEqual(storage["envelope_name"], "Проездной")

    def test_housing_obligations_share_real_estate_envelope(self):
        items = [
            {
                "category": "housing",
                "category_label": "Жильё, Аренда, ЖКХ",
                "subcategory": "regular",
                "name": name,
                "amount": "3000",
                "months": "1",
                "monthly": "3000",
            }
            for name in ("ЖКХ", "Ипотека", "Студия")
        ]
        storage_items = [default_km_storage(item) for item in items]
        self.assertTrue(all(item["storage"] == "separate" for item in storage_items))
        self.assertTrue(all(item["envelope_name"] == "Недвижимость" for item in storage_items))

    def test_large_education_payment_keeps_deadline_metadata(self):
        item = {
            "category": "education",
            "category_label": "Образование",
            "subcategory": "large",
            "name": "Обучение — платёж 1",
            "amount": "30000",
            "months": "5",
            "monthly": "6000",
            "due_date": "2027-03-01",
            "one_time": True,
        }
        storage = default_km_storage(item)
        self.assertEqual(storage["envelope_name"], "Образование")
        self.assertEqual(storage["due_date"], "2027-03-01")
        self.assertEqual(storage["target_amount"], "30000")

    def test_long_subscription_gets_separate_envelope(self):
        item = {
            "category": "communication",
            "category_label": "Связь и подписки",
            "name": "Яндекс Плюс",
            "amount": "2400",
            "months": "12",
            "monthly": "200",
        }
        storage = default_km_storage(item)
        self.assertEqual(storage["envelope_name"], "Подписки")

    def test_planned_tax_details_keep_objects_separate(self):
        items = [
            {
                "subcategory": "property_tax",
                "item_name": "Двушка",
                "amount": "6000",
                "monthly": "500",
                "annual_norm_active": True,
            },
            {
                "subcategory": "property_tax",
                "item_name": "Однушка",
                "amount": "3600",
                "monthly": "300",
                "annual_norm_active": True,
            },
        ]
        result = planned_taxes_from_storage(items)
        self.assertEqual(result["Налог на имущество · Двушка"], Decimal("500.00"))
        self.assertEqual(result["Налог на имущество · Однушка"], Decimal("300.00"))

    def test_onboarding_keeps_first_tax_out_of_life_until_payment(self):
        item = {
            "category": "housing",
            "category_label": "Недвижимость",
            "subcategory": "property_tax",
            "name": "Квартира",
            "amount": "7000",
            "months": "2",
            "monthly": "3500",
            "due_date": "2026-12-01",
        }
        self.assertEqual(km_group_totals([item]), {})
        storage = build_default_km_storage([item])
        self.assertEqual(storage[0]["monthly"], "3500.00")
        self.assertEqual(storage[0]["annual_monthly"], "0")
        self.assertEqual(life_categories_from_storage(storage), {})
        self.assertEqual(planned_taxes_from_storage(storage), {})

    def test_tax_report_leaves_amounts_to_diagram_and_explains_shared_envelope(self):
        groups = {
            name: {"total": Decimal("0"), "details": {}}
            for name in (
                "Налог на доход",
                "Налог на имущество",
                "Транспортный налог",
                "Земельный налог",
            )
        }
        text = report_text(groups, Decimal("0"), 2026, Decimal("0"), None, True)
        self.assertIn("Все налоги храним на одном накопительном счёте", text)
        self.assertIn("суммы не смешаются", text)
        self.assertNotIn("0 ₽", text)
        self.assertEqual(TAX_COLORS["Налог на доход"], "#7656D8")
        self.assertEqual(TAX_COLORS["Транспортный налог"], "#7A7F87")
        self.assertEqual(TAX_COLORS["Налог на имущество"], "#E2B93B")
        self.assertEqual(TAX_COLORS["Земельный налог"], "#8B5A2B")
        self.assertIsNone(make_pie_chart(groups))

    def test_income_operation_keeps_planned_tax_breakdown(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Налоги": Decimal("1000")},
            planned_taxes={
                "Налог на имущество · Двушка": Decimal("800"),
                "Транспортный налог · Автомобиль": Decimal("200"),
            },
        )
        allocator = FinancialAllocator(settings)
        allocator.process_income(Decimal("1000"), "Подарок")
        details = allocator.state.operation_log[-1]["planned_tax_details"]
        self.assertEqual(details["Налог на имущество · Двушка"], Decimal("640.00"))
        self.assertEqual(details["Транспортный налог · Автомобиль"], Decimal("160.00"))

    def test_planned_tax_is_funded_before_other_life_categories(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1350"),
            household_reserve=Decimal("0"),
            average_income=Decimal("3000"),
            life_categories={"Квартира": Decimal("900"), "Налоги": Decimal("450")},
            planned_taxes={"Налог на имущество · Хата": Decimal("450")},
        )
        allocator = FinancialAllocator(settings)
        allocations = {}
        allocator._allocate_to_life(Decimal("1000"), allocations)
        self.assertEqual(allocations["КЖ:Налоги"], Decimal("450"))
        self.assertEqual(allocator.state.period_life_topups["Налоги"], Decimal("450"))
        self.assertEqual(sum(allocations.values()), Decimal("1000"))

    def test_dynamic_tax_amount_returns_critical_life_to_base(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("90450"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Налоги": Decimal("450")},
            planned_taxes={"Налог на имущество · Хата": Decimal("450")},
        )
        self.assertEqual(settings.base_critical_life, Decimal("90000"))
        self.assertEqual(settings.critical_life, Decimal("90450"))
        settings.set_automatic_life_obligation(
            "tax:Налог на имущество · Хата", Decimal("0"),
        )
        settings.planned_taxes.clear()
        settings.life_categories.pop("Налоги", None)
        settings.recalculate_critical_life()
        self.assertEqual(settings.critical_life, Decimal("90000"))

    def test_income_operation_keeps_user_note(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Жизнь": Decimal("1000")},
        )
        allocator = FinancialAllocator(settings)
        allocator.process_income(
            Decimal("1000"),
            "Частный урок",
            note="Урок с Машей",
        )
        self.assertEqual(
            allocator.state.operation_log[-1]["note"],
            "Урок с Машей",
        )

    def test_completed_property_tax_keeps_annual_monthly_norm(self):
        telegram_id = 880001
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Налоги": Decimal("83.34")},
            planned_taxes={"Налог на имущество · Двушка": Decimal("83.34")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        db.add_tax_obligation(
            telegram_id,
            "Налог на имущество",
            "Двушка",
            Decimal("1000"),
            Decimal("0"),
            1,
            Decimal("1000"),
        )
        apply_planned_tax_allocation(telegram_id, allocator, Decimal("1000"))
        self.assertEqual(allocator.settings.life_categories["Налоги"], Decimal("83.34"))
        item = db.load_tax_obligations(telegram_id)[0]
        self.assertEqual(item["saved_before"], Decimal("1000"))
        self.assertEqual(item["monthly_amount"], Decimal("0"))

    def test_tax_target_recalculates_from_concrete_due_date(self):
        telegram_id = 880008
        settings = UserSettings(
            has_debts=False, employment_type="Наёмный",
            critical_life=Decimal("1500"), household_reserve=Decimal("0"),
            average_income=Decimal("3000"), life_categories={"Налоги": Decimal("500")},
            planned_taxes={"Налог на имущество · Дом": Decimal("500")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        db.add_tax_obligation(
            telegram_id, "Налог на имущество", "Дом", Decimal("3000"),
            Decimal("1000"), 4, Decimal("500"), "2026-12-01", Decimal("250"),
        )
        refresh_planned_tax_targets(telegram_id, allocator, date(2026, 9, 1))
        item = db.load_tax_obligations(telegram_id)[0]
        self.assertEqual(item["monthly_amount"], Decimal("1000.00"))
        # До ближайшей даты нужно собрать 1 000 ₽ в месяц, но КМ и
        # резервы растут только из годовой нормы: 3 000 / 12 = 250 ₽.
        self.assertEqual(allocator.settings.life_categories["Налоги"], Decimal("250.00"))
        self.assertEqual(
            allocator.settings.tax_catchups["Налог на имущество · Дом"],
            Decimal("1000.00"),
        )

    def test_annual_property_tax_does_not_expand_reserves_by_catchup_amount(self):
        telegram_id = 880021
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="irregular",
            critical_life=Decimal("90000"),
            household_reserve=Decimal("20000"),
            average_income=Decimal("180000"),
            force_majeure_months=Decimal("4"),
            stabilizer_target_months=Decimal("1"),
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        db.add_tax_obligation(
            telegram_id, "Земельный налог", "Дача", Decimal("7000"),
            Decimal("0"), 2, Decimal("3500"), "2026-12-01",
        )

        allocator = db.load_allocator(telegram_id)
        self.assertEqual(allocator.settings.critical_life, Decimal("90583.34"))
        self.assertEqual(allocator.settings.force_majeure_limit, Decimal("362333.36"))
        self.assertEqual(allocator.settings.stabilizer_full_limit, Decimal("110583.34"))

        refresh_planned_tax_targets(telegram_id, allocator, date(2026, 9, 1))
        self.assertEqual(
            allocator.settings.tax_catchups["Земельный налог · Дача"],
            Decimal("3500.00"),
        )

    def test_property_taxes_are_ready_one_month_before_payment_deadline(self):
        due = date(2026, 12, 1)
        self.assertEqual(tax_funding_date("Налог на имущество", due), date(2026, 11, 1))
        self.assertEqual(tax_months_remaining("Налог на имущество", due, date(2026, 9, 1)), 2)
        self.assertEqual(months_until_tax_ready(date(2026, 9, 1), due), 2)
        self.assertEqual(next_annual_tax_due_date(date(2026, 8, 29)), due)
        self.assertEqual(annual_tax_due_date(date(2026, 8, 29)), due)
        self.assertEqual(annual_tax_due_date(date(2026, 12, 1)), due)
        self.assertEqual(tax_notice_months_remaining(due, date(2026, 11, 1)), 1)

    def test_virtual_tax_balance_uses_internal_breakdown_in_shared_envelope(self):
        telegram_id = 880022
        key = "Транспортный налог · Автомобиль"
        db.add_tax_obligation(
            telegram_id, "Транспортный налог", "Автомобиль", Decimal("12000"),
            Decimal("2000"), 10, Decimal("1000"), "2026-12-01",
        )
        db.save_operation(telegram_id, "income_distribution", {
            "type": "income_distribution",
            "date": "2026-03-01",
            "planned_tax_details": {key: "3000"},
            "allocations": {"КЖ:Налоги": "3000"},
        })
        self.assertEqual(virtual_tax_balance(telegram_id, key), Decimal("5000"))
        db.save_tax_payment(telegram_id, key, Decimal("1200"))
        self.assertEqual(virtual_tax_balance(telegram_id, key), Decimal("3800"))

    def test_tax_rename_keeps_historical_balance_under_new_name(self):
        telegram_id = 880030
        old_key = "Земельный налог · Дача"
        new_key = "Земельный налог · Участок"
        obligation_id = db.add_tax_obligation(
            telegram_id, "Земельный налог", "Дача", Decimal("800"),
            Decimal("100"), 2, Decimal("350"), "2026-12-01",
        )
        db.save_operation(telegram_id, "income_distribution", {
            "type": "income_distribution",
            "date": "2026-09-01",
            "planned_tax_details": {old_key: "300"},
            "allocations": {"КЖ:Налоги": "300"},
        })
        db.save_tax_payment(
            telegram_id, old_key, Decimal("50"), obligation_id=obligation_id,
        )

        db.rename_tax_obligation(
            telegram_id, "Земельный налог", "Дача", "Участок",
        )

        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
        self.assertEqual(item["object_name"], "Участок")
        self.assertEqual(virtual_tax_balance(telegram_id, old_key), Decimal("0"))
        self.assertEqual(virtual_tax_balance(telegram_id, new_key), Decimal("350"))
        self.assertEqual(db.load_tax_payments(telegram_id)[0]["tax_name"], new_key)

    def test_tax_plan_amount_update_is_persisted(self):
        telegram_id = 880031
        obligation_id = db.add_tax_obligation(
            telegram_id, "Транспортный налог", "Автомобиль", Decimal("12000"),
            Decimal("8000"), 2, Decimal("2000"), "2026-12-01",
            Decimal("1000"),
        )
        db.update_tax_obligation_plan(
            telegram_id,
            obligation_id,
            target_amount=Decimal("14400"),
            months=1,
            monthly_amount=Decimal("6400"),
            annual_monthly_amount=Decimal("1200"),
        )
        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
        self.assertEqual(item["target_amount"], Decimal("14400"))
        self.assertEqual(item["months"], 1)
        self.assertEqual(item["monthly_amount"], Decimal("6400"))
        self.assertEqual(item["annual_monthly_amount"], Decimal("1200"))

    def test_legacy_tax_migration_does_not_subtract_same_norm_twice(self):
        with tempfile.TemporaryDirectory() as data_dir:
            database = Database(os.path.join(data_dir, "migration.db"))
            telegram_id = 880032
            key = "Земельный налог · Дача"
            settings = UserSettings(
                has_debts=False,
                employment_type="Фрилансер",
                critical_life=Decimal("92000"),
                base_critical_life=Decimal("90000"),
                automatic_life_obligations={f"tax:{key}": Decimal("2000")},
                household_reserve=Decimal("0"),
                average_income=Decimal("100000"),
                planned_taxes={key: Decimal("2000")},
            )
            database.save_allocator(telegram_id, FinancialAllocator(settings))
            database.add_tax_obligation(
                telegram_id, "Земельный налог", "Дача", Decimal("24000"),
                Decimal("0"), 2, Decimal("12000"), "2026-12-01",
                Decimal("2000"),
            )
            database.connection.execute(
                "UPDATE settings SET base_critical_life = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            database.connection.commit()

            loaded = database.load_allocator(telegram_id)
            self.assertEqual(loaded.settings.base_critical_life, Decimal("90000"))
            self.assertEqual(loaded.settings.critical_life, Decimal("92000"))

            obligation_id = database.load_tax_obligations(telegram_id)[0]["id"]
            database.deactivate_tax_obligation(telegram_id, obligation_id)
            loaded_without_tax = database.load_allocator(telegram_id)
            self.assertEqual(
                loaded_without_tax.settings.base_critical_life, Decimal("90000"),
            )
            self.assertEqual(loaded_without_tax.settings.critical_life, Decimal("90000"))
            database.close()

    def test_adding_and_deleting_multiple_taxes_returns_exact_base(self):
        with tempfile.TemporaryDirectory() as data_dir:
            database = Database(os.path.join(data_dir, "roundtrip.db"))
            telegram_id = 880034
            settings = UserSettings(
                has_debts=False,
                employment_type="Фрилансер",
                critical_life=Decimal("90000"),
                household_reserve=Decimal("20000"),
                average_income=Decimal("180000"),
                force_majeure_months=Decimal("4"),
                stabilizer_target_months=Decimal("1"),
            )
            database.save_allocator(telegram_id, FinancialAllocator(settings))
            land_id = database.add_tax_obligation(
                telegram_id, "Земельный налог", "Дача", Decimal("1200"),
                Decimal("0"), 2, Decimal("600"), "2026-12-01",
                Decimal("100"),
            )
            car_id = database.add_tax_obligation(
                telegram_id, "Транспортный налог", "Автомобиль", Decimal("2400"),
                Decimal("0"), 2, Decimal("1200"), "2026-12-01",
                Decimal("200"),
            )
            with_taxes = database.load_allocator(telegram_id)
            database.save_allocator(telegram_id, with_taxes)
            self.assertEqual(with_taxes.settings.critical_life, Decimal("90300"))

            database.deactivate_tax_obligation(telegram_id, land_id)
            one_tax = database.load_allocator(telegram_id)
            database.save_allocator(telegram_id, one_tax)
            self.assertEqual(one_tax.settings.critical_life, Decimal("90200"))

            database.deactivate_tax_obligation(telegram_id, car_id)
            no_taxes = database.load_allocator(telegram_id)
            self.assertEqual(no_taxes.settings.base_critical_life, Decimal("90000"))
            self.assertEqual(no_taxes.settings.critical_life, Decimal("90000"))
            self.assertEqual(no_taxes.settings.automatic_life_obligations, {})
            database.close()

    def test_deleted_income_rebuilds_tax_progress(self):
        telegram_id = 880033
        key = "Земельный налог · Дача"
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("90000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("100000"),
            planned_taxes={key: Decimal("100")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        obligation_id = db.add_tax_obligation(
            telegram_id, "Земельный налог", "Дача", Decimal("1200"),
            Decimal("0"), 2, Decimal("600"), "2026-12-01",
            Decimal("100"),
        )
        db.save_operation(telegram_id, "income_distribution", {
            "type": "income_distribution",
            "date": "2026-09-01",
            "planned_tax_details": {key: "300"},
            "allocations": {"КЖ:Налоги": "300"},
        })
        operation_id = db.load_operations(telegram_id)[0]["id"]
        db.update_tax_obligation_saved(
            telegram_id, obligation_id, Decimal("300"), True,
        )
        self.assertTrue(db.delete_income_operation(telegram_id, operation_id))

        allocator = db.load_allocator(telegram_id)
        reconcile_tax_obligation_balances(
            telegram_id, allocator, date(2026, 9, 1),
        )
        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
        self.assertEqual(item["saved_before"], Decimal("0"))
        self.assertEqual(item["monthly_amount"], Decimal("600.00"))

    def test_notice_flag_survives_storage_round_trip(self):
        telegram_id = 880023
        obligation_id = db.add_tax_obligation(
            telegram_id, "Налог на имущество", "Квартира", Decimal("14400"),
            Decimal("8000"), 1, Decimal("6400"), "2026-12-01",
            Decimal("1200"), notice_received=True, opening_amount=Decimal("0"),
        )
        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
        self.assertTrue(item["notice_received"])
        self.assertEqual(item["opening_amount"], Decimal("0"))

    def test_notice_recalculates_only_the_shortfall_until_december(self):
        remaining, months, catchup, annual_norm = calculate_notice_plan(
            Decimal("14400"), Decimal("8000"),
            date(2026, 12, 1), date(2026, 10, 1),
        )
        self.assertEqual(remaining, Decimal("6400"))
        self.assertEqual(months, 2)
        self.assertEqual(catchup, Decimal("3200.00"))
        self.assertEqual(annual_norm, Decimal("1200.00"))

    def test_paid_annual_tax_starts_next_year_cycle_and_reminder(self):
        telegram_id = 880025
        settings = UserSettings(
            has_debts=False, employment_type="Наёмный",
            critical_life=Decimal("11000"), household_reserve=Decimal("0"),
            average_income=Decimal("20000"),
            planned_taxes={"Транспортный налог · Автомобиль": Decimal("1000")},
        )
        allocator = FinancialAllocator(settings)
        db.save_allocator(telegram_id, allocator)
        old_id = db.add_tax_obligation(
            telegram_id, "Транспортный налог", "Автомобиль", Decimal("12000"),
            Decimal("12000"), 1, Decimal("0"), "2026-12-01", Decimal("1000"),
            opening_amount=Decimal("0"),
        )
        old_item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == old_id
        )
        db.save_operation(telegram_id, "income_distribution", {
            "type": "income_distribution", "date": "2026-11-01",
            "planned_tax_details": {"Транспортный налог · Автомобиль": "12000"},
            "allocations": {"КЖ:Налоги": "12000"},
        })
        db.save_tax_payment(
            telegram_id, "Транспортный налог · Автомобиль", Decimal("12000"),
        )
        db.deactivate_tax_obligation(telegram_id, old_id)
        result = start_next_annual_tax_cycle(
            telegram_id, old_item, allocator, date(2026, 12, 1),
        )
        self.assertEqual(result["due_date"], date(2027, 12, 1))
        self.assertEqual(result["saved_before"], Decimal("0"))
        self.assertEqual(result["monthly_amount"], Decimal("1090.91"))
        rows = db.due_tax_readiness_reminders("2027-11-01")
        self.assertTrue(any(row["id"] == result["id"] for row in rows))

    def test_tax_readiness_reminder_is_returned_only_once(self):
        telegram_id = 880009
        db.ensure_user(telegram_id)
        obligation_id = db.add_tax_obligation(
            telegram_id, "Земельный налог", "Дача", Decimal("900"),
            Decimal("900"), 1, Decimal("0"), "2026-12-01",
        )
        rows = db.due_tax_readiness_reminders("2026-11-01")
        self.assertTrue(any(row["id"] == obligation_id for row in rows))
        db.mark_tax_readiness_reminder_sent(telegram_id, obligation_id, "2026-11-01")
        rows = db.due_tax_readiness_reminders("2026-11-01")
        self.assertFalse(any(row["id"] == obligation_id for row in rows))

        rows = db.due_tax_readiness_reminders("2026-11-15")
        self.assertTrue(any(row["id"] == obligation_id for row in rows))
        db.mark_tax_readiness_reminder_sent(telegram_id, obligation_id, "2026-11-15")
        rows = db.due_tax_readiness_reminders("2026-11-25")
        self.assertTrue(any(row["id"] == obligation_id for row in rows))

    def test_partial_tax_payment_stays_open_until_full_amount(self):
        paid, remaining, closed = calculate_payment_progress(
            Decimal("12000"), Decimal("0"), Decimal("4000"),
        )
        self.assertEqual(paid, Decimal("4000"))
        self.assertEqual(remaining, Decimal("8000"))
        self.assertFalse(closed)

        paid, remaining, closed = calculate_payment_progress(
            Decimal("12000"), paid, Decimal("8000"),
        )
        self.assertEqual(paid, Decimal("12000"))
        self.assertEqual(remaining, Decimal("0"))
        self.assertTrue(closed)

    def test_tax_payment_is_linked_to_obligation_and_due_year(self):
        telegram_id = 880026
        obligation_id = db.add_tax_obligation(
            telegram_id, "Транспортный налог", "Автомобиль", Decimal("12000"),
            Decimal("12000"), 1, Decimal("0"), "2026-12-01",
        )
        db.save_tax_payment(
            telegram_id,
            "Транспортный налог · Автомобиль",
            Decimal("4000"),
            obligation_id=obligation_id,
            tax_due_year=2026,
        )
        self.assertEqual(
            db.tax_obligation_paid_amount(telegram_id, obligation_id), Decimal("4000"),
        )
        payment = db.load_tax_payments(telegram_id)[0]
        self.assertEqual(payment["obligation_id"], obligation_id)
        self.assertEqual(payment["tax_due_year"], 2026)

    def test_tax_chart_uses_current_balance_after_partial_payment(self):
        telegram_id = 880028
        key = "Транспортный налог · Автомобиль"
        obligation_id = db.add_tax_obligation(
            telegram_id, "Транспортный налог", "Автомобиль", Decimal("12000"),
            Decimal("0"), 1, Decimal("0"), "2026-12-01",
        )
        db.save_operation(telegram_id, "income_distribution", {
            "type": "income_distribution",
            "date": "2026-10-01",
            "planned_tax_details": {key: "12000"},
            "allocations": {"КЖ:Налоги": "12000"},
        })
        db.save_tax_payment(
            telegram_id, key, Decimal("4000"),
            obligation_id=obligation_id, tax_due_year=2026,
        )
        groups, current_total, _ = collect_tax_statistics(telegram_id, 2026)
        self.assertEqual(groups["Транспортный налог"]["total"], Decimal("8000"))
        self.assertEqual(current_total, Decimal("8000"))

    def test_fns_notice_keeps_payments_linked_to_same_obligation(self):
        telegram_id = 880029
        obligation_id = db.add_tax_obligation(
            telegram_id, "Налог на имущество", "Квартира", Decimal("15000"),
            Decimal("8000"), 2, Decimal("3500"), "2026-12-01",
        )
        db.save_tax_payment(
            telegram_id, "Налог на имущество · Квартира", Decimal("4000"),
            obligation_id=obligation_id, tax_due_year=2026,
        )
        db.update_tax_obligation_notice(
            telegram_id,
            obligation_id,
            target_amount=Decimal("14400"),
            saved_before=Decimal("8000"),
            months=1,
            monthly_amount=Decimal("6400"),
            annual_monthly_amount=Decimal("1200"),
            due_date="2026-12-01",
        )
        item = next(
            row for row in db.load_tax_obligations(telegram_id)
            if row["id"] == obligation_id
        )
        self.assertTrue(item["notice_received"])
        self.assertEqual(item["target_amount"], Decimal("14400"))
        self.assertEqual(
            db.tax_obligation_paid_amount(telegram_id, obligation_id), Decimal("4000"),
        )

    def test_tax_payment_reminders_repeat_after_deadline(self):
        telegram_id = 880027
        obligation_id = db.add_tax_obligation(
            telegram_id, "Налог на имущество", "Квартира", Decimal("900"),
            Decimal("900"), 1, Decimal("0"), "2026-12-01",
            notice_received=True,
        )
        self.assertFalse(any(
            row["id"] == obligation_id
            for row in db.due_tax_payment_reminders("2026-11-30")
        ))
        rows = db.due_tax_payment_reminders("2026-12-01")
        self.assertTrue(any(row["id"] == obligation_id for row in rows))
        db.mark_tax_payment_reminder_sent(telegram_id, obligation_id, "2026-12-01")
        self.assertFalse(any(
            row["id"] == obligation_id
            for row in db.due_tax_payment_reminders("2026-12-01")
        ))
        rows = db.due_tax_payment_reminders("2026-12-02")
        self.assertTrue(any(row["id"] == obligation_id for row in rows))
        db.mark_tax_payment_reminder_sent(telegram_id, obligation_id, "2026-12-02")
        self.assertFalse(any(
            row["id"] == obligation_id
            for row in db.due_tax_payment_reminders("2026-12-08")
        ))
        rows = db.due_tax_payment_reminders("2026-12-09")
        self.assertTrue(any(row["id"] == obligation_id for row in rows))

    def test_notice_received_does_not_trigger_preliminary_reminder(self):
        telegram_id = 880024
        obligation_id = db.add_tax_obligation(
            telegram_id, "Земельный налог", "Дача", Decimal("900"),
            Decimal("600"), 1, Decimal("300"), "2026-12-01",
            notice_received=True,
        )
        rows = db.due_tax_readiness_reminders("2026-11-01")
        self.assertFalse(any(row["id"] == obligation_id for row in rows))


if __name__ == "__main__":
    unittest.main()
