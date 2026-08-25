import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal


_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from onboarding import (  # noqa: E402
    add_calendar_months,
    build_contract_obligations,
    build_state_from_data,
    category_added_entries,
    category_added_totals,
    communication_item_name,
    default_km_storage,
    force_majeure_minimum_for_rhythm,
    housing_item_name,
    input_period_label,
    keyboard,
    km_item_display_name,
    km_item_totals_by_name,
    life_classification_reason,
    life_categories_from_storage,
    life_expense_summary,
    matching_housing_total,
    matching_communication_total,
    months_until_due_date,
    normalize_pass_months,
    parse_tax_due_date,
    planned_taxes_from_storage,
    should_auto_route_to_reserve,
)
from planned_payments import apply_planned_payment_allocation, refresh_planned_payment_targets  # noqa: E402
from taxes import (  # noqa: E402
    apply_planned_tax_allocation,
    make_pie_chart,
    refresh_planned_tax_targets,
    report_text,
)
from financial_engine import FinancialAllocator, UserSettings  # noqa: E402
from storage import (  # noqa: E402
    db,
    deserialize_income_rhythm,
    deserialize_income_types,
    serialize_income_types,
    serialize_json,
)


class TaxFeatureTests(unittest.TestCase):
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
            ["← Назад", "✔️ Сохранить", "✖️ Отмена", "✔️ Готово"],
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
        self.assertIn("<b>Медицинские услуги</b> — 32 970,00 ₽ / год", text)
        self.assertIn("<b><u>ОДЕЖДА</u></b>", text)
        self.assertIn("<b>Одежда</b> — 55 452,00 ₽ / 7 мес.", text)
        self.assertNotIn("Обязательная жизнь", text)

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

    def test_combined_onboarding_routes_nonmonthly_ambiguous_expenses_to_reserve(self):
        for category in ("communication", "habits", "fees"):
            self.assertTrue(
                should_auto_route_to_reserve(category, Decimal("3"), True)
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
        )
        db.save_allocator(telegram_id, FinancialAllocator(settings))
        loaded = db.load_allocator(telegram_id)
        self.assertEqual(
            loaded.settings.income_type_tax_rates,
            {"Заказ ФЛ": Decimal("4"), "Подарок": Decimal("0")},
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

    def test_tax_cannot_leave_common_tax_envelope(self):
        legacy_or_manually_changed = {
            "category": "transport",
            "subcategory": "tax",
            "item_name": "Автомобиль",
            "monthly": "1000",
            "storage": "salary",
            "envelope_name": "Транспортный налог",
        }
        categories = life_categories_from_storage([legacy_or_manually_changed])
        self.assertEqual(categories, {"Налоги": Decimal("1000.00")})

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
                "monthly": "500",
            },
            {
                "subcategory": "property_tax",
                "item_name": "Однушка",
                "monthly": "300",
            },
        ]
        result = planned_taxes_from_storage(items)
        self.assertEqual(result["Налог на имущество · Двушка"], Decimal("500.00"))
        self.assertEqual(result["Налог на имущество · Однушка"], Decimal("300.00"))

    def test_empty_tax_report_uses_zero_percent(self):
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
        self.assertIn("Налог на доход — 0,00 ₽ (0.0%)", text)
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

    def test_completed_tax_goal_stops_future_monthly_target(self):
        telegram_id = 880001
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=Decimal("1000"),
            household_reserve=Decimal("0"),
            average_income=Decimal("1000"),
            life_categories={"Налоги": Decimal("1000")},
            planned_taxes={"Налог на имущество · Двушка": Decimal("1000")},
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
        self.assertNotIn("Налоги", allocator.settings.life_categories)
        self.assertEqual(db.load_tax_obligations(telegram_id), [])

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
            Decimal("1000"), 4, Decimal("500"), "2026-11-01",
        )
        refresh_planned_tax_targets(telegram_id, allocator, date(2026, 9, 1))
        item = db.load_tax_obligations(telegram_id)[0]
        self.assertEqual(item["monthly_amount"], Decimal("1000.00"))
        self.assertEqual(allocator.settings.life_categories["Налоги"], Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
