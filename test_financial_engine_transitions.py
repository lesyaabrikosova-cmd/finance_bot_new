import unittest
from decimal import Decimal

from financial_engine import (
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    UserSettings,
    money,
)


D = Decimal


class ModeTransitionTests(unittest.TestCase):
    def test_stage_b_splits_children_into_the_same_named_envelope(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=D("100"),
            household_reserve=D("100"),
            household_reserve_categories={"Дети": D("40")},
            average_income=D("100"),
            force_majeure_months=D("1"),
            bracket_b=D("0"),
        )
        allocator = FinancialAllocator(settings, AllocatorState(life_balance=D("100")))
        result = allocator.process_income(D("100"), "Зарплата", tax_override=D("0"))
        self.assertEqual(result.allocations["БР:Дети"], D("40"))
        self.assertEqual(result.allocations["Бытовой резерв"], D("60"))
        self.assertEqual(allocator.state.life_balance, D("200"))

    def test_profile_specific_mode_scales(self):
        stable = self.make_allocator(employment="Наёмный")
        piecework = self.make_allocator(employment="Фрилансер")
        self.assertEqual(stable.profile_id, "stable")
        self.assertEqual(stable.profile_mode_total, 4)
        self.assertEqual(piecework.profile_id, "piecework")
        self.assertEqual(piecework.profile_mode_total, 6)

        cyclic = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="cyclic",
            income_rhythm="cyclic",
            income_gap_months=D("2"),
            critical_life=D("100"),
            household_reserve=D("100"),
            average_income=D("100"),
            force_majeure_months=D("1"),
        ))
        self.assertEqual(cyclic.profile_mode_total, 8)
        self.assertEqual(cyclic.active_mode(), 3)
        cyclic.state.intercontract_reserve = D("200")
        self.assertEqual(cyclic.active_mode(), 4)
        cyclic.state.intercontract_reserve = D("400")
        self.assertEqual(cyclic.active_mode(), 5)
        cyclic.state.pillow_force_majeure = D("100")
        self.assertEqual(cyclic.active_mode(), 6)
        cyclic.state.pillow_stabilizer = D("100")
        self.assertEqual(cyclic.active_mode(), 7)
        cyclic.state.pillow_stabilizer = D("200")
        self.assertEqual(cyclic.active_mode(), 8)
        self.assertEqual(cyclic.mode_title(3), "Заплати будущему себе.")
        self.assertEqual(cyclic.mode_title(4), "Не на хлебе и воде.")

    def test_cyclic_mode_uses_only_remaining_break_months_for_newcomer(self):
        cyclic = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="cyclic",
            income_rhythm="cyclic",
            income_gap_months=D("7"),
            critical_life=D("39000"),
            household_reserve=D("11000"),
            average_income=D("57000"),
            force_majeure_months=D("6"),
        ))
        cyclic.state.current_cycle_phase = "break"
        cyclic.state.intercontract_break_active = True
        cyclic.state.intercontract_months_remaining = D("2")
        cyclic.state.intercontract_reserve = D("100000")

        self.assertEqual(cyclic.intercontract_current_life_limit, D("78000"))
        self.assertEqual(cyclic.intercontract_current_limit, D("100000"))
        self.assertEqual(cyclic.active_mode(), 5)
        self.assertEqual(cyclic.mode_display_name(), "Режим 5")

    def test_cyclic_work_obligations_are_reserved_before_modes(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="cyclic",
            critical_life=D("100"),
            household_reserve=D("0"),
            average_income=D("1000"),
            force_majeure_months=D("1"),
            contract_obligations={"Квартплата": D("300"), "Мегафон": D("200")},
            contract_obligation_storage={
                "Квартплата": "Недвижимость",
                "Мегафон": "Фонд Обязательств",
            },
            use_contract_obligations_fund=True,
        )
        allocator = FinancialAllocator(settings, AllocatorState(life_balance=D("100")))
        result = allocator.process_income(D("250"), "Контракт", tax_override=D("0"))
        self.assertEqual(allocator.state.contract_obligations_reserve, D("250"))
        self.assertEqual(result.allocations["Рабочие обязательства:Недвижимость:Квартплата"], D("150"))
        self.assertEqual(result.allocations["Рабочие обязательства:Фонд Обязательств:Мегафон"], D("100"))
        self.assertTrue(result.checks["ok"])

    def test_contract_obligations_keep_one_expense_and_native_storage(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            income_rhythm="cyclic",
            critical_life=D("100"),
            household_reserve=D("100"),
            average_income=D("1000"),
            contract_obligations={
                "ЖКХ": D("19000"),
                "Налог": D("900"),
                "Яндекс Диск": D("829.17"),
                "ВК Музыка": D("995"),
            },
            contract_obligation_storage={
                "ЖКХ": "Недвижимость",
                "Налог": "Налоги",
                "Яндекс Диск": "Бытовой резерв",
                "ВК Музыка": "Фонд Обязательств",
            },
            use_contract_obligations_fund=True,
        )

        self.assertEqual(settings.contract_obligations_total, D("21724.17"))
        self.assertEqual(settings.contract_obligations_by_envelope, {
            "Недвижимость": D("19000"),
            "Налоги": D("900"),
            "Бытовой резерв": D("829.17"),
            "Фонд Обязательств": D("995"),
        })

    def test_cyclic_regular_base_uses_full_cycle(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("100"),
            household_reserve=D("0"),
            average_income=D("750"),
            income_rhythm="cyclic",
            income_work_months=D("1"),
            income_gap_months=D("1"),
            force_majeure_months=D("1"),
            life_categories={"Жизнь": D("100")},
        )
        allocator = FinancialAllocator(settings)

        first = allocator.process_income(D("1500"), "Контракт", tax_override=D("0"))

        self.assertEqual(first.regular_income_part, D("1500"))
        self.assertEqual(first.super_income_part, D("0"))
        self.assertEqual(allocator.state.cycle_income, D("1500"))

    def test_cyclic_cycle_counter_survives_break_and_resets_after_last_gap_month(self):
        settings = UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("100"),
            household_reserve=D("0"),
            average_income=D("750"),
            income_rhythm="cyclic",
            income_work_months=D("1"),
            income_gap_months=D("1"),
            force_majeure_months=D("1"),
            life_categories={"Жизнь": D("100")},
        )
        allocator = FinancialAllocator(settings)
        allocator.process_income(D("1000"), "Контракт", tax_override=D("0"))

        allocator.reset_period()
        self.assertEqual(allocator.state.period_income, D("0"))
        self.assertEqual(allocator.state.cycle_income, D("1000"))

        second = allocator.process_income(D("700"), "Контракт", tax_override=D("0"))
        self.assertEqual(second.regular_income_part, D("500"))
        self.assertEqual(second.super_income_part, D("200"))

        allocator.start_intercontract_break()
        self.assertEqual(allocator.state.cycle_income, D("1700"))

        # Гарантированное поступление в перерыве остаётся частью того же цикла.
        allocator.reset_period()
        allocator.process_income(D("100"), "Пособие", tax_override=D("0"))
        self.assertEqual(allocator.state.cycle_income, D("1800"))

        # Баланс жизни уже заполнен поступлением, поэтому внутренний перевод
        # равен нулю, но последний месяц перерыва всё равно завершается.
        allocator.state.life_balance = allocator.settings.household_life
        self.assertEqual(allocator.pay_intercontract_salary(), D("0"))
        self.assertEqual(allocator.state.intercontract_months_remaining, D("0"))
        self.assertTrue(allocator.state.intercontract_break_active)
        self.assertEqual(allocator.state.cycle_income, D("1800"))

        allocator.start_new_work_phase()
        self.assertFalse(allocator.state.intercontract_break_active)
        self.assertEqual(allocator.state.cycle_income, D("0"))

    def test_work_phase_can_start_before_predicted_break_end_without_moving_balances(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="cyclic",
            income_rhythm="cyclic",
            income_work_months=D("5"),
            income_gap_months=D("7"),
            critical_life=D("39000"),
            household_reserve=D("11000"),
            average_income=D("57000"),
        ))
        allocator.state.intercontract_break_active = True
        allocator.state.current_cycle_phase = "break"
        allocator.state.intercontract_months_remaining = D("2")
        allocator.state.current_phase_months_remaining = D("2")
        allocator.state.life_balance = D("8000")
        allocator.state.intercontract_reserve = D("100000")

        with self.assertRaises(ValueError):
            allocator.start_new_work_phase()
        allocator.start_new_work_phase(allow_early=True)

        self.assertEqual(allocator.state.current_cycle_phase, "work")
        self.assertEqual(allocator.state.current_phase_months_remaining, D("5"))
        self.assertEqual(allocator.state.life_balance, D("8000"))
        self.assertEqual(allocator.state.intercontract_reserve, D("100000"))

    def test_break_can_be_extended_without_moving_money_between_reserves(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="cyclic",
            income_rhythm="cyclic",
            income_gap_months=D("2"),
            critical_life=D("39000"),
            household_reserve=D("11000"),
            average_income=D("57000"),
        ))
        allocator.state.intercontract_break_active = True
        allocator.state.current_cycle_phase = "break"
        allocator.state.intercontract_months_remaining = D("0")
        allocator.state.intercontract_reserve = D("20000")
        allocator.state.pillow_stabilizer = D("15000")
        allocator.state.pillow_force_majeure = D("30000")

        result = allocator.extend_intercontract_break()

        self.assertEqual(result["periods_remaining"], D("1"))
        self.assertEqual(result["required"], D("50000"))
        self.assertEqual(result["shortfall"], D("30000"))
        self.assertEqual(allocator.state.intercontract_reserve, D("20000"))
        self.assertEqual(allocator.state.pillow_stabilizer, D("15000"))
        self.assertEqual(allocator.state.pillow_force_majeure, D("30000"))

    def test_break_extension_rejects_fractional_period(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            profile_type="cyclic",
            income_rhythm="cyclic",
            income_gap_months=D("2"),
            critical_life=D("100"),
            household_reserve=D("0"),
            average_income=D("100"),
        ))
        allocator.state.intercontract_break_active = True
        with self.assertRaises(ValueError):
            allocator.extend_intercontract_break(D("0.5"))

    def test_salary_remainder_can_go_to_one_goal(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("100"), household_reserve=D("20"), average_income=D("100"),
            goals=[Goal("Отпуск", D("60")), Goal("Ноутбук", D("40"))],
        ))
        label = allocator.transfer_salary_remainder(D("500"), "goal:1")
        self.assertEqual(label, "Цель «Ноутбук»")
        self.assertEqual(allocator.state.goal_balances["Ноутбук"], D("500"))
        self.assertEqual(allocator.state.goal_balances["Отпуск"], D("0"))

    def test_salary_remainder_can_follow_all_goal_proportions(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Фрилансер",
            critical_life=D("100"), household_reserve=D("20"), average_income=D("100"),
            goals=[Goal("Отпуск", D("60")), Goal("Ноутбук", D("40"))],
        ))
        allocator.transfer_salary_remainder(D("1000"), "goals")
        self.assertEqual(allocator.state.goal_balances["Отпуск"], D("600"))
        self.assertEqual(allocator.state.goal_balances["Ноутбук"], D("400"))

    def test_exact_salary_remainder_destination_does_not_leak_through_waterfall(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=D("100"), household_reserve=D("20"), average_income=D("100"),
            force_majeure_months=D("1"),
        ))
        allocator.state.pillow_force_majeure = D("100")
        allocator.transfer_salary_remainder(D("500"), "pillow")
        self.assertEqual(allocator.state.pillow_force_majeure, D("600"))
        self.assertEqual(allocator.state.investments, D("0"))

    def test_priority_salary_remainder_preserves_full_amount_after_filling_protection(self):
        allocator = FinancialAllocator(UserSettings(
            has_debts=False,
            employment_type="Наёмный",
            critical_life=D("100"), household_reserve=D("0"), average_income=D("100"),
            force_majeure_months=D("1"),
        ))
        allocator.transfer_salary_remainder(D("150"), "priority")
        self.assertEqual(allocator.state.pillow_force_majeure, D("100"))
        self.assertEqual(allocator.state.investments, D("50"))

    def make_allocator(
        self,
        *,
        employment="Фрилансер",
        minimum="100",
        force="200",
        stabilizer="0",
        debt=None,
        life="2000",
    ):
        credits = []
        if debt is not None:
            credits.append(
                Credit(
                    name="Долг",
                    principal_balance=D(debt),
                    full_repayment_amount=None,
                    annual_rate=D("10"),
                    minimum_payment=D("10"),
                )
            )

        settings = UserSettings(
            has_debts=bool(credits),
            employment_type=employment,
            critical_life=D("1000"),
            household_reserve=D("1000"),
            average_income=D("1000"),
            minimum_reserve_months=D("0.1"),
            force_majeure_months=D("0.2"),
            bracket_a=D("20"),
            bracket_b=D("25"),
            bracket_c=D("30"),
            bracket_d=D("40"),
            bracket_e=D("40"),
            goals_share_c=D("50"),
            pillow_share_c=D("50"),
            goals=[Goal("Цель", D("100"))],
            credits=credits,
        )
        state = AllocatorState(
            life_balance=D(life),
            pillow_minimum=D(minimum),
            pillow_force_majeure=max(D("0"), D(force) - D(minimum)),
            pillow_stabilizer=D(stabilizer),
            goal_balances={"Цель": D("0")},
        )
        return FinancialAllocator(settings, state)

    def assert_money(self, actual, expected):
        self.assertEqual(money(actual), D(expected))

    def test_mode_1_to_2_keeps_stage_c(self):
        allocator = self.make_allocator(
            minimum="0",
            force="0",
            debt="1000",
        )

        result = allocator.process_income(D("500"), "Тест")

        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Досрочное"], "400.00")

    def test_mode_2_can_continue_through_later_modes(self):
        allocator = self.make_allocator(
            force="0",
            debt="100",
        )

        result = allocator.process_income(D("2000"), "Тест")

        self.assert_money(result.allocations["Досрочное"], "100.00")
        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Стабилизатор дохода"], "1291.00")
        self.assert_money(result.allocations["Инвестиции"], "194.00")
        self.assert_money(result.allocations["Цели:Цель"], "315.00")
        self.assert_money(result.super_income_part, "1000.00")

    def test_mode_3_to_4_keeps_stage_c(self):
        allocator = self.make_allocator(force="100")

        result = allocator.process_income(D("1500"), "Тест")

        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Стабилизатор дохода"], "1030.00")
        self.assert_money(result.allocations["Инвестиции"], "20.00")
        self.assert_money(result.allocations["Цели:Цель"], "350.00")
        self.assert_money(result.super_income_part, "500.00")

    def test_average_income_splits_current_receipt_by_period_total(self):
        allocator = self.make_allocator(employment="Наёмный", life="2000")
        allocator.state.pillow_force_majeure = D("200")
        allocator.state.period_income = D("800")
        result = allocator.process_income(D("500"), "Тест")
        self.assert_money(result.regular_income_part, "200.00")
        self.assert_money(result.super_income_part, "300.00")
        self.assert_money(result.allocations["Инвестиции"], "180.00")
        self.assert_money(result.allocations["Цели:Цель"], "320.00")

    def test_cyclic_employee_gets_salary_fund_and_separate_stabilizer(self):
        allocator = self.make_allocator(employment="Наёмный")
        allocator.settings.income_rhythm = "cyclic"
        allocator.settings.income_gap_months = D("3")
        self.assertTrue(allocator.settings.needs_stabilizer)
        self.assertEqual(allocator.settings.intercontract_life_limit, D("3000"))
        self.assertEqual(allocator.settings.intercontract_full_limit, D("6000"))
        self.assertEqual(allocator.settings.stabilizer_life_limit, D("1000"))
        self.assertEqual(allocator.settings.stabilizer_full_limit, D("2000"))

        allocator.settings.stabilizer_target_months = D("2")
        self.assertEqual(allocator.settings.stabilizer_life_limit, D("2000"))
        self.assertEqual(allocator.settings.stabilizer_full_limit, D("4000"))

    def test_cyclic_income_fills_salary_fund_before_force_majeure(self):
        allocator = self.make_allocator(employment="Наёмный", force="0")
        allocator.settings.income_rhythm = "cyclic"
        allocator.settings.income_gap_months = D("2")
        result = allocator.process_income(D("1000"), "Тест")
        self.assert_money(allocator.state.intercontract_reserve, "1000")
        self.assert_money(allocator.state.pillow_force_majeure, "0")
        self.assert_money(result.allocations["Фонд Зарплаты"], "1000")

    def test_reliable_gap_income_does_not_reduce_salary_fund_target(self):
        allocator = self.make_allocator(employment="Наёмный")
        allocator.settings.income_rhythm = "cyclic"
        allocator.settings.income_gap_months = D("3")
        allocator.settings.reliable_gap_income = D("600")
        self.assertEqual(allocator.settings.intercontract_life_limit, D("3000"))
        self.assertEqual(allocator.settings.intercontract_full_limit, D("6000"))

    def test_salary_fund_remains_full_when_gap_income_covers_sustainable_life(self):
        allocator = self.make_allocator(employment="Наёмный")
        allocator.settings.income_rhythm = "cyclic"
        allocator.settings.reliable_gap_income = D("2000")
        self.assertTrue(allocator.settings.needs_intercontract_reserve)
        self.assertEqual(allocator.settings.intercontract_full_limit, D("2000"))

    def test_intercontract_salary_is_internal_transfer_and_reduces_dynamic_target(self):
        allocator = self.make_allocator(employment="Наёмный", life="0")
        allocator.settings.income_rhythm = "cyclic"
        allocator.settings.income_gap_months = D("2")
        allocator.state.intercontract_reserve = D("4000")
        allocator.start_intercontract_break()
        amount = allocator.pay_intercontract_salary()
        self.assertEqual(amount, D("2000"))
        self.assertEqual(allocator.state.life_balance, D("2000"))
        self.assertEqual(allocator.state.intercontract_reserve, D("2000"))
        self.assertEqual(allocator.state.intercontract_months_remaining, D("1"))
        self.assertEqual(allocator.intercontract_current_limit, D("2000"))
        self.assertEqual(allocator.state.period_income, D("0"))
        self.assertEqual(allocator.state.period_tax, D("0"))

    def test_mode_3_to_maximum_for_employee_keeps_stage_c(self):
        allocator = self.make_allocator(
            employment="Наёмный",
            force="100",
        )

        result = allocator.process_income(D("1000"), "Тест")

        self.assert_money(result.allocations["Подушка"], "100.00")
        self.assert_money(result.allocations["Инвестиции"], "253.85")
        self.assert_money(result.allocations["Цели:Цель"], "646.15")

    def test_mode_4_to_5_uses_30_35_35_for_remainder(self):
        allocator = self.make_allocator(stabilizer="900")

        result = allocator.process_income(D("1000"), "Тест")

        self.assert_money(result.allocations["Подушка"], "0.00")
        self.assert_money(result.allocations["Стабилизатор дохода"], "396.15")
        self.assert_money(result.allocations["Инвестиции"], "253.85")
        self.assert_money(result.allocations["Цели:Цель"], "350.00")

    def test_mode_5_to_maximum_keeps_stage_c(self):
        allocator = self.make_allocator(stabilizer="1900")

        result = allocator.process_income(D("1000"), "Тест")

        self.assert_money(result.allocations["Подушка"], "0.00")
        self.assert_money(result.allocations["Стабилизатор дохода"], "100.00")
        self.assert_money(result.allocations["Инвестиции"], "300.00")
        self.assert_money(result.allocations["Цели:Цель"], "600.00")

    def test_stage_a_is_repeated_after_mode_change(self):
        allocator = self.make_allocator(
            minimum="90",
            debt="1000",
            life="0",
        )
        allocations = {
            "Подушка": D("0"),
            "Инвестиции": D("0"),
            "Досрочное": D("0"),
            "Бытовой резерв": D("0"),
        }
        steps = []

        remainder = allocator.run_stage_with_mode_transitions(
            D("100"),
            lambda amount, mode: allocator.stage_a(
                amount,
                mode,
                steps,
                allocations,
            ),
        )

        self.assert_money(remainder, "0.00")
        self.assert_money(allocations["Подушка"], "0.00")
        self.assert_money(allocations["Досрочное"], "20.00")
        self.assert_money(
            allocator.state.life_balance
            + allocator.state.accumulated_minimum_payments,
            "80.00",
        )

    def test_stage_b_is_repeated_after_mode_change(self):
        allocator = self.make_allocator(
            stabilizer="990",
            life="1900",
        )
        allocations = {
            "Подушка": D("0"),
            "Инвестиции": D("0"),
            "Досрочное": D("0"),
            "Бытовой резерв": D("0"),
        }
        steps = []

        remainder = allocator.run_stage_with_mode_transitions(
            D("133.3333333333333333333333333333333333333"),
            lambda amount, mode: allocator.stage_b(
                amount,
                mode,
                steps,
                allocations,
            ),
        )

        self.assert_money(remainder, "0.00")
        self.assert_money(allocations["Подушка"], "0.00")
        self.assert_money(allocations["Стабилизатор дохода"], "33.33")
        self.assert_money(allocations["Бытовой резерв"], "100.00")
        self.assert_money(allocations["Инвестиции"], "0.00")

    def test_super_income_is_repeated_after_mode_change(self):
        allocator = self.make_allocator(stabilizer="900")
        allocations = {
            "Подушка": D("0"),
            "Инвестиции": D("0"),
            "Досрочное": D("0"),
            "Бытовой резерв": D("0"),
        }

        remainder = allocator.run_stage_with_mode_transitions(
            D("1000"),
            lambda amount, mode: allocator.super_income(
                amount,
                mode,
                allocations,
            ),
        )

        self.assert_money(remainder, "0.00")
        self.assert_money(allocations["Подушка"], "0.00")
        self.assert_money(allocations["Стабилизатор дохода"], "640.00")
        self.assert_money(allocations["Инвестиции"], "360.00")


if __name__ == "__main__":
    unittest.main()
