import os
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_DATA = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _DATA.name

import taxes
from financial_engine import FinancialAllocator, UserSettings
from taxes import (
    LAND_TAX_BROWNS,
    PROPERTY_TAX_YELLOWS,
    TRANSPORT_TAX_GRAYS,
    TaxStates,
    income_tax_menu_back,
    save_income_tax_profile,
    show_income_tax_profile_menu,
    show_ip_usn_rate_menu,
    show_self_employed_rate_menu,
    show_taxes,
    tax_chart_values_and_colors,
    tax_obligation_start,
    tax_payment_start,
)


def allocator():
    return FinancialAllocator(UserSettings(
        has_debts=False,
        employment_type="Фрилансер",
        critical_life=Decimal("1000"),
        household_reserve=Decimal("0"),
        average_income=Decimal("1000"),
        income_type_tax_rates={},
    ))


def button_rows(message):
    markup = message.answer.await_args.kwargs["reply_markup"]
    return [[button.text for button in row] for row in markup.inline_keyboard]


class TaxIncomeProfileMenus(unittest.IsolatedAsyncioTestCase):
    async def test_choose_tax_screen_has_fixed_navigation(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(set_state=AsyncMock())
        await tax_obligation_start(callback, state)
        self.assertEqual(button_rows(callback.message), [
            ["Налог на доход"],
            ["Налог на имущество"],
            ["Транспортный налог"],
            ["Земельный налог"],
            ["Другой налог"],
            ["← Главное меню", "← К налогам"],
        ])

    async def test_tax_screen_still_sends_report(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        current = allocator()
        transaction = MagicMock()
        transaction.__enter__.return_value = None
        transaction.__exit__.return_value = False
        with (
            patch("taxes.db") as db,
            patch("taxes.refresh_planned_tax_targets"),
            patch("taxes.collect_tax_statistics", return_value=({}, Decimal("0"), Decimal("0"))),
            patch("taxes.send_chart_report", new=AsyncMock()) as report,
        ):
            db.load_allocator.return_value = current
            db.transaction.return_value = transaction
            db.load_tax_payments.return_value = []
            db.load_tax_obligations.return_value = []
            await show_taxes(message, 42)
        report.assert_awaited_once()

    async def test_income_tax_menu_has_fixed_navigation(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("taxes.db") as db:
            db.load_allocator.return_value = allocator()
            await show_income_tax_profile_menu(message, 42)
        self.assertEqual(button_rows(message), [
            ["Самозанятость (НПД)"],
            ["ИП на УСН «Доходы»"],
            ["ИП на ПСН (патент)"],
            ["✓ Готово"],
            ["← Главное меню", "← Назад"],
        ])

    async def test_rate_screens_have_one_fixed_navigation_row(self):
        for sender in (show_self_employed_rate_menu, show_ip_usn_rate_menu):
            message = SimpleNamespace(answer=AsyncMock())
            await sender(message)
            self.assertEqual(
                button_rows(message)[-1],
                ["← Главное меню", "← Назад"],
            )

    async def test_self_employed_screen_has_all_supported_rates(self):
        message = SimpleNamespace(answer=AsyncMock())
        await show_self_employed_rate_menu(message)
        self.assertEqual(button_rows(message)[:-1], [
            ["Физики · 4%", "Физики · 3%"],
            ["Юрики · 6%", "Юрики · 4%"],
            ["Своя ставка"],
        ])

    async def test_ip_usn_screen_has_base_and_custom_rate(self):
        message = SimpleNamespace(answer=AsyncMock())
        await show_ip_usn_rate_menu(message)
        self.assertEqual(button_rows(message)[:-1], [["6%", "Своя ставка"]])

    async def test_back_clears_draft_and_restores_tax_state(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
        with patch("taxes.db") as db:
            db.load_allocator.return_value = allocator()
            await income_tax_menu_back(callback, state)
        state.clear.assert_awaited_once()
        state.set_state.assert_awaited_once_with(TaxStates.obligation_type)

    async def test_profile_save_is_idempotent(self):
        current = allocator()
        with patch("taxes.db") as db:
            db.load_allocator.return_value = current
            first = await save_income_tax_profile(
                42, "Самозанятость", "Физики", Decimal("4"),
            )
            second = await save_income_tax_profile(
                42, "Самозанятость", "Физики", Decimal("4"),
            )
        self.assertTrue(first[1])
        self.assertFalse(second[1])
        self.assertEqual(len(current.settings.income_tax_profiles), 1)

    async def test_payment_menu_contains_income_profile_balance(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        buckets = {
            ("income", "profile-1"): {
                "amount": Decimal("400"),
                "key": "Налог на доход · Самозанятость · Физики · 4%",
                "group": "Налог на доход",
                "detail": "Самозанятость · Физики · 4%",
            }
        }
        with patch("taxes.db") as db, patch("taxes._tax_ledger", return_value=(buckets, Decimal("400"))):
            db.load_tax_obligations.return_value = []
            await tax_payment_start(callback, state)
        self.assertEqual(
            button_rows(callback.message)[0][0],
            "Самозанятость · Физики · 4% · 400 ₽",
        )


class TaxIncomeChartColours(unittest.TestCase):
    def test_property_objects_get_separate_family_colours(self):
        groups = {
            "Налог на имущество": {
                "total": Decimal("300"),
                "details": {"Квартира": Decimal("100"), "Дом": Decimal("200")},
            },
            "Транспортный налог": {
                "total": Decimal("700"),
                "details": {"Автомобиль": Decimal("300"), "Мотоцикл": Decimal("400")},
            },
            "Земельный налог": {
                "total": Decimal("1100"),
                "details": {"Дача": Decimal("500"), "Участок": Decimal("600")},
            },
        }
        values, colors = tax_chart_values_and_colors(groups)
        expected = {
            "Налог на имущество · Квартира",
            "Налог на имущество · Дом",
            "Транспортный налог · Автомобиль",
            "Транспортный налог · Мотоцикл",
            "Земельный налог · Дача",
            "Земельный налог · Участок",
        }
        self.assertEqual(set(values), expected)
        palettes = {
            "Налог на имущество": PROPERTY_TAX_YELLOWS,
            "Транспортный налог": TRANSPORT_TAX_GRAYS,
            "Земельный налог": LAND_TAX_BROWNS,
        }
        for prefix, palette in palettes.items():
            family = [colors[label] for label in expected if label.startswith(prefix)]
            self.assertEqual(len(set(family)), 2)
            self.assertTrue(set(family).issubset(palette))

    def test_object_colours_do_not_depend_on_detail_order(self):
        first = {
            "Налог на имущество": {
                "total": Decimal("300"),
                "details": {"Квартира": Decimal("100"), "Дом": Decimal("200")},
            }
        }
        second = {
            "Налог на имущество": {
                "total": Decimal("300"),
                "details": dict(reversed(list(first["Налог на имущество"]["details"].items()))),
            }
        }
        self.assertEqual(tax_chart_values_and_colors(first), tax_chart_values_and_colors(second))

    def test_profile_colours_do_not_depend_on_input_order(self):
        first = {
            "Налог на доход": {
                "details": {"ИП · УСН · 6%": Decimal("60"), "Самозанятость · Физики · 4%": Decimal("40")},
                "total": Decimal("100"),
            }
        }
        second = {
            "Налог на доход": {
                "details": dict(reversed(list(first["Налог на доход"]["details"].items()))),
                "total": Decimal("100"),
            }
        }
        self.assertEqual(
            tax_chart_values_and_colors(first)[1],
            tax_chart_values_and_colors(second)[1],
        )

    def test_named_income_payment_reduces_only_selected_profile(self):
        operations = [
            {"id": 1, "payload": {"type": "income_distribution", "tax": "400", "income_type": "Самозанятость · Физики · 4%"}},
            {"id": 2, "payload": {"type": "income_distribution", "tax": "600", "income_type": "Самозанятость · Юрики · 6%"}},
        ]
        payment = {
            "id": 1,
            "amount": Decimal("400"),
            "obligation_id": None,
            "tax_name": "Налог на доход · Самозанятость · Физики · 4%",
        }
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = [payment]
            buckets, _ = taxes._tax_ledger(42)
        self.assertEqual(
            buckets[("income", "Самозанятость · Физики · 4%")]["amount"],
            Decimal("0"),
        )
        self.assertEqual(
            buckets[("income", "Самозанятость · Юрики · 6%")]["amount"],
            Decimal("600"),
        )


if __name__ == "__main__":
    unittest.main()
