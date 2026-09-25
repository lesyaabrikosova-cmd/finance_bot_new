import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from financial_engine import AllocatorState, FinancialAllocator, PhaseLifeBudget, UserSettings
from onboarding import life_editor_items, rebuild_km_storage, save_edited_life
from settings_editor import cost_breakdown_blocks, life_breakdown_blocks
from storage import deserialize_income_rhythm, serialize_income_types


class CostLivingBreakdownTests(unittest.TestCase):
    def test_old_profile_gets_editable_placeholders_instead_of_a_blank_form(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("42000"),
            household_reserve=Decimal("8000"),
            average_income=Decimal("100000"),
        )

        critical, household = life_editor_items(settings)

        self.assertEqual(critical[0]["monthly"], "42000")
        self.assertEqual(household[0]["monthly"], "8000")
        self.assertIn("прежнего расчёта", critical[0]["name"])

    def test_recalculation_keeps_an_existing_custom_envelope(self):
        item = {
            "category": "health",
            "category_label": "Здоровье",
            "name": "Стоматолог",
            "amount": "12000",
            "months": "12",
            "monthly": "1000",
        }
        previous = [{
            "item_name": "Стоматолог",
            "category": "health",
            "category_label": "Здоровье",
            "subcategory": None,
            "monthly": "1000",
            "storage": "separate",
            "envelope_name": "Зубы",
        }]

        rebuilt = rebuild_km_storage([item], previous)

        self.assertEqual(rebuilt[0]["storage"], "separate")
        self.assertEqual(rebuilt[0]["envelope_name"], "Зубы")

    def test_items_are_grouped_by_onboarding_section(self):
        blocks, subtotal = life_breakdown_blocks([
            {
                "category_label": "Красота и уход",
                "name": "Маникюр",
                "amount": "3000",
                "months": "1",
                "monthly": "3000",
            },
            {
                "category_label": "Красота и уход",
                "name": "Стрижка",
                "amount": "12000",
                "months": "12",
                "monthly": "1000",
            },
            {
                "category_label": "Недвижимость",
                "name": "ЖКХ",
                "amount": "7000",
                "months": "1",
                "monthly": "7000",
            },
        ])

        text = "\n\n".join(blocks)
        self.assertEqual(subtotal, Decimal("11000"))
        self.assertIn("КРАСОТА И УХОД", text)
        self.assertIn("Маникюр", text)
        self.assertIn("Стрижка", text)
        self.assertIn("введено 12 000 ₽ в год", text)
        self.assertIn("НЕДВИЖИМОСТЬ", text)
        self.assertIn("ЖКХ", text)

    def test_total_below_items_is_called_out(self):
        blocks = cost_breakdown_blocks(
            "КРИТИЧЕСКИЙ МИНИМУМ",
            Decimal("9000"),
            [{
                "category_label": "Недвижимость",
                "name": "Квартира",
                "monthly": "10000",
            }],
            {},
        )

        text = "\n\n".join(blocks)
        self.assertIn("меньше суммы перечисленных расходов", text)
        self.assertIn("1 000 ₽", text)

    def test_breakdown_survives_settings_serialization(self):
        critical = [{
            "category": "food",
            "category_label": "Питание",
            "name": "Продукты",
            "amount": "15000",
            "months": "1",
            "monthly": "15000",
        }]
        household = [{
            "category": "care",
            "category_label": "Красота и уход",
            "name": "Маникюр",
            "amount": "3000",
            "months": "1",
            "monthly": "3000",
        }]
        storage_items = [{
            "item_name": "Продукты",
            "category": "food",
            "monthly": "15000",
            "storage": "salary",
            "envelope_name": None,
        }]
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=Decimal("15000"),
            household_reserve=Decimal("3000"),
            average_income=Decimal("50000"),
            critical_life_breakdown=critical,
            household_reserve_breakdown=household,
            critical_life_storage_items=storage_items,
            phase_life_budgets={
                "break": PhaseLifeBudget(
                    critical_life=Decimal("15000"),
                    household_reserve=Decimal("3000"),
                    critical_life_breakdown=critical,
                    household_reserve_breakdown=household,
                    critical_life_storage_items=storage_items,
                    completed=True,
                )
            },
        )

        restored = deserialize_income_rhythm(serialize_income_types(settings))

        self.assertEqual(restored["critical_life_breakdown"], critical)
        self.assertEqual(restored["household_reserve_breakdown"], household)
        self.assertEqual(restored["critical_life_storage_items"], storage_items)
        self.assertEqual(
            restored["phase_life_budgets"]["break"].critical_life_breakdown,
            critical,
        )
        self.assertEqual(
            restored["phase_life_budgets"]["break"].critical_life_storage_items,
            storage_items,
        )


class _FakeState:
    def __init__(self, data):
        self.data = data
        self.cleared = False

    async def get_data(self):
        return self.data

    async def clear(self):
        self.cleared = True


class _FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class CostLivingEditorSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_updates_profile_without_resetting_period_balances(self):
        allocator = FinancialAllocator(
            UserSettings(
                has_debts=False,
                employment_type="Наёмный",
                critical_life=Decimal("10000"),
                household_reserve=Decimal("2000"),
                average_income=Decimal("50000"),
                life_categories={"Старый конверт": Decimal("1000")},
            ),
            AllocatorState(
                period_life_topups={"Старый конверт": Decimal("400")},
                period_allocations={"КЖ:Старый конверт": Decimal("400")},
            ),
        )
        item = {
            "category": "food",
            "category_label": "Питание",
            "name": "Продукты",
            "amount": "10000",
            "months": "1",
            "monthly": "10000",
        }
        state = _FakeState({
            "critical_life": "10000",
            "household_reserve": "2000",
            "life_categories": {"Новый конверт": "1000"},
            "km_items": [item],
            "br_items": [{
                "category": "care",
                "category_label": "Уход",
                "name": "Стрижка",
                "amount": "2000",
                "months": "1",
                "monthly": "2000",
            }],
            "km_storage_items": [],
            "life_editor_automatic_amount": "0",
            "historical_gifts_monthly": "0",
        })
        message = _FakeMessage()
        fake_db = Mock()
        fake_db.load_allocator.return_value = allocator

        with patch("onboarding.db", fake_db):
            await save_edited_life(message, state, 123)

        self.assertTrue(state.cleared)
        fake_db.save_allocator.assert_called_once_with(123, allocator)
        self.assertEqual(
            allocator.state.period_life_topups["Зарплата"], Decimal("400")
        )
        self.assertEqual(
            allocator.state.period_allocations["КЖ:Зарплата"], Decimal("400")
        )
        self.assertEqual(allocator.settings.base_critical_life, Decimal("10000"))
        self.assertEqual(allocator.settings.household_reserve, Decimal("2000"))
        self.assertEqual(
            allocator.settings.critical_life_breakdown[0]["name"], "Продукты"
        )


if __name__ == "__main__":
    unittest.main()
