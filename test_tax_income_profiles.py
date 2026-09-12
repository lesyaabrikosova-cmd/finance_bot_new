import os
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_DATA = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _DATA.name

import taxes
from income import save_custom_tax_percent, show_income_confirmation
from onboarding import profile_income_save, profile_income_tax_choice
from financial_engine import FinancialAllocator, UserSettings
from settings_editor import (
    income_tax_rule_select_patent,
    income_type_add_named_rule,
    income_type_add_rate,
    show_income_types_settings,
)
from taxes import (
    LAND_TAX_BROWNS,
    PROPERTY_TAX_YELLOWS,
    TRANSPORT_TAX_GRAYS,
    TaxStates,
    income_tax_menu_back,
    income_tax_profile_choice,
    income_tax_profile_delete,
    patent_save,
    save_income_tax_profile,
    show_income_tax_profile_menu,
    show_ip_usn_rate_menu,
    show_self_employed_rate_menu,
    show_tax_obligations_edit,
    show_taxes,
    tax_obligations_overview,
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


class MemoryState:
    def __init__(self, **data):
        self.data = data
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, value):
        self.state = value

    async def clear(self):
        self.data.clear()
        self.state = None


class TaxIncomeProfileMenus(unittest.IsolatedAsyncioTestCase):
    async def test_income_types_are_grouped_by_two_buttons_per_row(self):
        current = allocator()
        current.settings.income_type_tax_rates = {
            "Зарплата": Decimal("6"),
            "Халтура": Decimal("0"),
            "Частник": Decimal("0"),
            "Подарки": Decimal("0"),
            "Сервизория": Decimal("0"),
        }
        message = SimpleNamespace(answer=AsyncMock())
        with patch("settings_editor.db") as db:
            db.load_allocator.return_value = current
            await show_income_types_settings(message, 42)

        self.assertEqual(button_rows(message)[:3], [
            ["Зарплата", "Халтура"],
            ["Частник", "Подарки"],
            ["Сервизория"],
        ])

    async def test_one_off_custom_usn_rate_keeps_usn_label(self):
        current = allocator()
        current.settings.income_type_tax_rates = {"Зарплата": Decimal("0")}
        state = MemoryState(
            income_amount="100000",
            income_type="Зарплата",
            income_date="2026-09-13",
            income_flow_id="flow-1",
            tax_edit_custom_profile_prefix="ИП · УСН",
        )
        message = SimpleNamespace(
            text="5", from_user=SimpleNamespace(id=42), answer=AsyncMock(),
        )
        with patch("income.db") as income_db:
            income_db.load_allocator.return_value = current
            await save_custom_tax_percent(message, state)

        self.assertEqual(state.data["tax_override_profile"], "ИП · УСН · 5%")
        self.assertIn("Налог • 5% — 5 000", message.answer.await_args.args[0])

    async def test_custom_usn_rate_keeps_usn_label(self):
        state = MemoryState(
            income_type_action="add",
            income_type_draft_name="Зарплата",
            income_rule_target="new",
        )
        callback = SimpleNamespace(
            data="incomesettings:newrule:custom_ip_usn",
            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()),
        )
        await income_type_add_named_rule(callback, state)
        message = SimpleNamespace(
            text="5", from_user=SimpleNamespace(id=42), answer=AsyncMock(),
        )
        await income_type_add_rate(message, state)

        self.assertEqual(state.data["income_type_draft_rate"], "5")
        self.assertEqual(
            state.data["income_type_draft_profile"], "ИП · УСН · 5%",
        )
        self.assertIn("ИП · УСН · 5%", message.answer.await_args.args[0])

    async def test_stale_profile_button_redirects_without_writing_catalog(self):
        current = allocator()
        callback = SimpleNamespace(
            data="taxincome:npd:physical:3",
            answer=AsyncMock(), from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = MemoryState(old="draft")
        with patch("settings_editor.db") as settings_db:
            settings_db.load_allocator.return_value = current
            await income_tax_profile_choice(callback, state)

        self.assertEqual(current.settings.income_tax_profiles, {})
        self.assertNotIn("old", state.data)
        self.assertEqual(state.data["income_types_return"], "taxes:add")
        rendered = "\n".join(call.args[0] for call in callback.message.answer.await_args_list)
        self.assertIn("старой версии", rendered)
        self.assertIn("<b>ТИПЫ ДОХОДОВ</b>", rendered)

    async def test_existing_patent_is_confirmed_before_new_income_type_is_saved(self):
        state = MemoryState(
            available_patent_names=["Музыкальные занятия"],
            income_rule_target="new",
            income_type_action="add",
            income_type_draft_name="Зарплата",
        )
        callback = SimpleNamespace(
            data="incomesettings:psn_select:0",
            answer=AsyncMock(), from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        await income_tax_rule_select_patent(callback, state)

        self.assertEqual(state.data["income_type_draft_rate"], "0")
        self.assertEqual(
            state.data["income_type_draft_profile"],
            "ИП · ПСН · Музыкальные занятия · по плану",
        )
        self.assertIn(
            "<b>ПРОВЕРЬТЕ ТИП ДОХОДА</b>",
            callback.message.answer.await_args.args[0],
        )

    async def test_patent_income_confirmation_explains_zero_withholding(self):
        current = allocator()
        current.settings.income_type_tax_rates = {"Зарплата": Decimal("0")}
        current.settings.income_type_tax_profiles = {
            "Зарплата": "ИП · ПСН · Музыкальные занятия · по плану",
        }
        message = SimpleNamespace(answer=AsyncMock())
        state = MemoryState(
            income_amount="100000",
            income_type="Зарплата",
            income_date="2026-09-13",
            tax_override=None,
            income_flow_id="flow-1",
        )
        with patch("income.db") as income_db:
            income_db.load_allocator.return_value = current
            await show_income_confirmation(message, state, 42)

        text = message.answer.await_args.args[0]
        self.assertIn("Налог с этого поступления — <b>не удерживается</b>", text)
        self.assertIn("Патент копится отдельно по плану", text)

    async def test_two_patent_payments_and_income_binding_save_together(self):
        current = allocator()
        state = MemoryState(
            tax_goal_name="Музыкальные занятия",
            patent_payment_count=2,
            patent_first_amount="30000",
            patent_first_due_date="2027-04-01",
            patent_second_amount="60000",
            patent_second_due_date="2027-12-28",
            patent_attach_income_type=True,
            patent_types_return="settings:open",
            income_rule_target="new",
            income_type_draft_name="Зарплата",
        )
        callback = SimpleNamespace(
            answer=AsyncMock(), from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        transaction = MagicMock()
        transaction.__enter__.return_value = None
        transaction.__exit__.return_value = False
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = []
            db.load_allocator.return_value = current
            db.add_tax_obligation.side_effect = [101, 102]
            db.transaction.return_value = transaction
            await patent_save(callback, state)

        self.assertEqual(db.add_tax_obligation.call_count, 2)
        names = [call.args[2] for call in db.add_tax_obligation.call_args_list]
        self.assertEqual(names, [
            "Музыкальные занятия — 1-й платёж",
            "Музыкальные занятия — 2-й платёж",
        ])
        self.assertEqual(current.settings.income_type_tax_rates["Зарплата"], Decimal("0"))
        self.assertEqual(
            current.settings.income_type_tax_profiles["Зарплата"],
            "ИП · ПСН · Музыкальные занятия · по плану",
        )
        self.assertEqual(state.data, {})
        self.assertEqual(button_rows(callback.message)[0], ["✓ Готово"])

    async def test_income_tax_entry_opens_types_without_standalone_profiles(self):
        current = allocator()
        current.settings.income_type_tax_rates = {"Зарплата": Decimal("3")}
        current.settings.income_type_tax_profiles = {"Зарплата": "НПД · ФЛ · 3%"}
        current.settings.income_tax_profiles = {
            "Самозанятость · Физики · 3%": {"rate": "3"},
            "НПД · ФЛ · 3%": {"rate": "3"},
        }
        message = SimpleNamespace(answer=AsyncMock())
        with patch("settings_editor.db") as settings_db:
            settings_db.load_allocator.return_value = current
            await show_income_tax_profile_menu(message, 42)
        text = message.answer.await_args.args[0]
        self.assertIn("<b>ТИПЫ ДОХОДОВ</b>", text)
        self.assertIn("• Зарплата · НПД · ФЛ · 3%", text)
        self.assertNotIn("Добавленные налоговые правила", text)
        self.assertNotIn("Налоговый профиль", sum(button_rows(message), []))

    async def test_deleting_rule_removes_legacy_duplicate_and_default_binding(self):
        current = allocator()
        old_name = "Самозанятость · Физики · 3%"
        current.settings.income_type_tax_rates = {
            "Зарплата": Decimal("3"), old_name: Decimal("3"),
        }
        current.settings.income_tax_profiles = {
            old_name: {"rate": "3"}, "НПД · ФЛ · 3%": {"rate": "3"},
        }
        current.settings.income_type_tax_profiles = {
            "Зарплата": "НПД · ФЛ · 3%",
        }
        state = MemoryState(tax_profile_delete_name="НПД · ФЛ · 3%")
        callback = SimpleNamespace(
            answer=AsyncMock(), from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        with patch("taxes.db") as db:
            db.load_allocator.return_value = current
            db.load_tax_obligations.return_value = []
            await income_tax_profile_delete(callback, state)
        self.assertEqual(current.settings.income_tax_profiles, {})
        self.assertNotIn(old_name, current.settings.income_type_tax_rates)
        self.assertEqual(current.settings.income_type_tax_rates["Зарплата"], Decimal("0"))
        self.assertEqual(current.settings.income_type_tax_profiles, {})

    async def test_onboarding_preserves_named_rule_for_income_source(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = MemoryState(
            pending_income_type_name="Зарплата",
            income_type_tax_rates={},
            income_type_tax_profiles={},
        )
        choice = SimpleNamespace(
            data="profileincome:tax:usn_6", answer=AsyncMock(), message=message,
        )
        await profile_income_tax_choice(choice, state)
        save = SimpleNamespace(answer=AsyncMock(), message=message)
        await profile_income_save(save, state)
        self.assertEqual(state.data["income_type_tax_rates"], {"Зарплата": "6"})
        self.assertEqual(
            state.data["income_type_tax_profiles"],
            {"Зарплата": "ИП · УСН · 6%"},
        )

    async def test_empty_planned_tax_list_offers_add_tax(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = []
            await show_tax_obligations_edit(message, 42)
        self.assertEqual(button_rows(message), [
            ["＋ Добавить налог"],
            ["← Главное меню", "← Назад"],
        ])

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
        self.assertEqual(report.await_args.kwargs["legend_columns"], 1)

    async def test_income_tax_menu_has_type_and_patent_actions(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("settings_editor.db") as db:
            db.load_allocator.return_value = allocator()
            await show_income_tax_profile_menu(message, 42)
        self.assertEqual(button_rows(message), [
            ["＋ Добавить тип дохода"],
            ["＋ Настроить патент (ПСН)"],
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
        self.assertEqual(message.answer.await_args.args[0], (
            "<b>САМОЗАНЯТОСТЬ (НПД)</b>\n\n"
            "<b>НПД</b> — налог на профессиональный доход или самозанятость.\n"
            "<b>ФЛ</b> — физические лица,\n"
            "<b>ЮЛ</b> — юридические лица.\n\n"
            "<b>Обычные ставки:</b>\n"
            "• 4% с доходов от ФЛ\n"
            "• 6% от ЮЛ.\n\n"
            "<b>Пониженые ставки:</b>\n"
            "• 3% с доходов от ФЛ\n"
            "• 4% от ЮЛ.\n\n"
            "<b>P.S.:</b> Ставки понижаются при приветственном налоговом бонусе "
            "в 10 000 <b>₽</b>. Аллокатор не знает остаток бонуса — после его "
            "исчерпания выберите обычную ставку."
        ))
        self.assertEqual(button_rows(message)[:-1], [
            ["НПД · ФЛ · 4%", "НПД · ФЛ · 3%"],
            ["НПД · ЮЛ · 6%", "НПД · ЮЛ · 4%"],
            ["Своя ставка"],
        ])

    async def test_ip_usn_screen_has_base_and_custom_rate(self):
        message = SimpleNamespace(answer=AsyncMock())
        await show_ip_usn_rate_menu(message)
        self.assertEqual(message.answer.await_args.args[0], (
            "<b>ИП НА УСН «ДОХОДЫ»</b>\n\n"
            "<b>ИП</b> — индивидуальный предприниматель.\n"
            "<b>УСН «Доходы»</b> — упрощённая система налогообложения с объектом "
            "«Доходы».\n\n"
            "Выберите, сколько откладывать с каждого поступления.\n\n"
            "Базовый вариант — 6%; если у вас действует другая ставка, укажите её вручную."
        ))
        self.assertEqual(button_rows(message)[:-1], [["ИП · УСН · 6%", "Своя ставка"]])

    async def test_back_clears_draft_and_restores_tax_state(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        state = MemoryState(old="value")
        with patch("settings_editor.db") as db:
            db.load_allocator.return_value = allocator()
            await income_tax_menu_back(callback, state)
        self.assertEqual(state.data["income_types_return"], "taxes:add")
        self.assertNotIn("old", state.data)
        self.assertIn("<b>ТИПЫ ДОХОДОВ</b>", callback.message.answer.await_args.args[0])

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
    def test_old_tax_is_not_relabelled_as_current_patent_rule(self):
        current = allocator()
        current.settings.income_type_tax_rates = {"Зарплата": Decimal("0")}
        current.settings.income_type_tax_profiles = {
            "Зарплата": "ИП · ПСН · Музыкальные занятия · по плану",
        }
        operations = [{
            "id": 1,
            "payload": {
                "type": "income_distribution", "income_type": "Зарплата",
                "income": "100000", "tax": "6000",
            },
        }]
        with patch("taxes.db") as db:
            db.load_allocator.return_value = current
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = []
            groups, _, _ = taxes.collect_tax_statistics(42, 2026)

        self.assertEqual(
            groups["Налог на доход"]["details"],
            {"Зарплата": Decimal("6000")},
        )

    def test_legacy_profile_used_as_source_is_not_repeated_in_legend(self):
        current = allocator()
        current.settings.income_tax_profiles = {
            "Самозанятость · Физики · 3%": {"rate": "3"},
        }
        operations = [{
            "id": 1,
            "payload": {
                "type": "income_distribution",
                "income_type": "Самозанятость · Физики · 3%",
                "income": "10000",
                "tax": "300",
            },
        }]
        with patch("taxes.db") as db:
            db.load_allocator.return_value = current
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = []
            groups, _, _ = taxes.collect_tax_statistics(42, 2026)

        self.assertEqual(
            groups["Налог на доход"]["details"],
            {"НПД · ФЛ · 3%": Decimal("300")},
        )

    def test_legacy_income_operations_keep_their_source_names(self):
        operations = [
            {"id": 1, "payload": {"type": "income_distribution", "income_type": "Зарплата", "income": "100000", "tax": "6000"}},
            {"id": 2, "payload": {"type": "income_distribution", "income_type": "Халтура", "income": "10000", "tax": "300"}},
        ]
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = []
            groups, _, _ = taxes.collect_tax_statistics(42, 2026)
        self.assertEqual(groups["Налог на доход"]["details"], {
            "Зарплата": Decimal("6000"),
            "Халтура": Decimal("300"),
        })

    def test_income_source_precedes_compact_profile_in_legend(self):
        operations = [
            {"id": 1, "payload": {
                "type": "income_distribution", "income_type": "Зарплата",
                "income": "100000", "tax": "6000",
                "tax_profile": "ИП · УСН · 6%",
            }},
            {"id": 2, "payload": {
                "type": "income_distribution", "income_type": "Зарплата",
                "income": "10000", "tax": "300",
                "tax_profile": "НПД · ФЛ · 3%",
            }},
            {"id": 3, "payload": {
                "type": "income_distribution", "income_type": "Халтура",
                "income": "5000", "tax": "200",
            }},
        ]
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = []
            groups, _, _ = taxes.collect_tax_statistics(42, 2026)
        self.assertEqual(
            groups["Налог на доход"]["details"],
            {
                "Зарплата · ИП · УСН · 6%": Decimal("6000"),
                "Зарплата · НПД · ФЛ · 3%": Decimal("300"),
                "Халтура": Decimal("200"),
            },
        )

    def test_legacy_receipts_use_unique_saved_rule_with_same_rate(self):
        current = allocator()
        current.settings.income_tax_profiles = {
            "ИП · УСН «Доходы» · 6%": {
                "subject": "ИП", "mode": "УСН «Доходы»", "rate": "6",
            },
            "Самозанятость · Физики · 3%": {
                "subject": "Самозанятость", "mode": "Физики", "rate": "3",
            },
        }
        operations = [
            {"id": 1, "payload": {"type": "income_distribution", "income_type": "Зарплата", "income": "100000", "tax": "6000"}},
            {"id": 2, "payload": {"type": "income_distribution", "income_type": "Халтура", "income": "10000", "tax": "300"}},
        ]
        with patch("taxes.db") as db:
            db.load_allocator.return_value = current
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = []
            groups, _, _ = taxes.collect_tax_statistics(42, 2026)
        self.assertEqual(groups["Налог на доход"]["details"], {
            "Зарплата · ИП · УСН · 6%": Decimal("6000"),
            "Халтура · НПД · ФЛ · 3%": Decimal("300"),
        })

    def test_legacy_receipt_is_not_guessed_when_rate_is_ambiguous(self):
        current = allocator()
        current.settings.income_tax_profiles = {
            "ИП · УСН · 6%": {"rate": "6"},
            "НПД · ЮЛ · 6%": {"rate": "6"},
        }
        operations = [{
            "id": 1,
            "payload": {"type": "income_distribution", "income_type": "Зарплата", "income": "100000", "tax": "6000"},
        }]
        with patch("taxes.db") as db:
            db.load_allocator.return_value = current
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = []
            groups, _, _ = taxes.collect_tax_statistics(42, 2026)
        self.assertEqual(
            groups["Налог на доход"]["details"], {"Зарплата": Decimal("6000")},
        )

    def test_overview_includes_only_accrued_income_tax_buckets(self):
        current = allocator()
        current.settings.income_type_tax_rates = {"Зарплата": Decimal("6")}
        overview = tax_obligations_overview(
            [], {"Халтура · 3%": Decimal("300")}, current.settings,
        )
        self.assertIn("<b>С доходов</b>", overview)
        self.assertIn("Халтура · 3%", overview)
        self.assertNotIn("Зарплата", overview)
        self.assertNotIn("нет добавленных", overview)

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
            buckets[("income", "НПД · ФЛ · 4%")]["amount"],
            Decimal("0"),
        )
        self.assertEqual(
            buckets[("income", "НПД · ЮЛ · 6%")]["amount"],
            Decimal("600"),
        )

    def test_legacy_payment_name_never_consumes_another_income_source(self):
        operations = [
            {"id": 1, "payload": {"type": "income_distribution", "income_type": "Зарплата", "income": "100000", "tax": "6000", "tax_profile": "ИП · УСН · 6%"}},
            {"id": 2, "payload": {"type": "income_distribution", "income_type": "Халтура", "income": "10000", "tax": "300", "tax_profile": "НПД · ФЛ · 3%"}},
        ]
        payment = {
            "id": 1, "amount": Decimal("300"), "obligation_id": None,
            "tax_name": "Налог на доход · Халтура",
        }
        with patch("taxes.db") as db:
            db.load_tax_obligations.return_value = []
            db.load_operations.return_value = operations
            db.load_tax_payments.return_value = [payment]
            buckets, _ = taxes._tax_ledger(42)
        self.assertEqual(buckets[("income", "Зарплата · ИП · УСН · 6%")]["amount"], Decimal("6000"))
        self.assertEqual(buckets[("income", "Халтура · НПД · ФЛ · 3%")]["amount"], Decimal("0"))


if __name__ == "__main__":
    unittest.main()
