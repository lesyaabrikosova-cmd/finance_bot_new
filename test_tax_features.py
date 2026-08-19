import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal


_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from onboarding import (  # noqa: E402
    default_km_storage,
    months_until_due_date,
    parse_tax_due_date,
    planned_taxes_from_storage,
)
from taxes import apply_planned_tax_allocation, make_pie_chart, report_text  # noqa: E402
from financial_engine import FinancialAllocator, UserSettings  # noqa: E402
from storage import db, deserialize_income_types, serialize_json  # noqa: E402


class TaxFeatureTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
