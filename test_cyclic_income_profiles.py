import unittest
from dataclasses import dataclass
from decimal import Decimal

from income_cycle import (
    CYCLIC,
    STABLE_EMPLOYEE,
    STABLE_FREELANCER,
    CyclicIncomePlan,
    financial_profile,
    profile_route,
)


D = Decimal


@dataclass(frozen=True)
class ShiftPortrait:
    name: str
    work: str
    region: str
    paid_months: int
    gap_months: int
    km: str
    sustainable: str
    payout: str
    delay: int = 2
    fm_months: int = 6


PORTRAITS = (
    ShiftPortrait("Надя", "музыкант по зарубежному контракту", "Москва / Индия", 5, 7, "35000", "40000", "420000"),
    ShiftPortrait("Алексей", "моряк дальнего плавания", "Владивосток", 6, 4, "70000", "90000", "1100000"),
    ShiftPortrait("Ирина", "судовой врач", "Мурманск", 4, 2, "65000", "82000", "620000", 1),
    ShiftPortrait("Роман", "нефтяник", "ХМАО", 1, 1, "75000", "95000", "230000", 1),
    ShiftPortrait("Сергей", "бурильщик", "ЯНАО", 2, 1, "68000", "85000", "410000"),
    ShiftPortrait("Ольга", "повар на месторождении", "Коми", 2, 2, "52000", "65000", "300000"),
    ShiftPortrait("Тимур", "автомеханик вахтовой бригады", "Татарстан / Север", 2, 1, "60000", "76000", "330000"),
    ShiftPortrait("Марина", "горничная круизного судна", "Сочи", 7, 5, "48000", "60000", "720000"),
    ShiftPortrait("Виктор", "геолог", "Красноярский край", 3, 2, "72000", "88000", "590000"),
    ShiftPortrait("Анна", "археолог экспедиции", "Новосибирск", 4, 8, "45000", "57000", "480000", 3),
    ShiftPortrait("Денис", "монтажник", "Новый Уренгой", 2, 2, "78000", "98000", "470000"),
    ShiftPortrait("Светлана", "проводник сезонного поезда", "Омск", 6, 3, "50000", "63000", "510000"),
    ShiftPortrait("Павел", "рыбак промыслового флота", "Камчатка", 5, 3, "80000", "100000", "850000"),
    ShiftPortrait("Лейла", "танцовщица по контракту", "Казань / ОАЭ", 6, 6, "55000", "70000", "760000"),
    ShiftPortrait("Михаил", "строитель зимника", "Иркутская область", 4, 8, "62000", "78000", "650000", 3),
    ShiftPortrait("Елена", "вахтовый фельдшер", "Якутия", 1, 1, "67000", "83000", "190000"),
    ShiftPortrait("Николай", "оператор добычи", "Сахалин", 2, 2, "90000", "115000", "560000"),
    ShiftPortrait("Дарья", "аниматор гостиничного сезона", "Анапа", 5, 7, "42000", "54000", "390000", 3),
    ShiftPortrait("Артур", "сварщик", "Башкортостан / Ямал", 3, 1, "64000", "80000", "470000"),
    ShiftPortrait("Вера", "сезонный гид", "Алтай", 5, 7, "46000", "59000", "440000", 3),
    ShiftPortrait("Илья", "авиационный техник", "Красноярск / Арктика", 2, 2, "74000", "92000", "520000"),
    ShiftPortrait("Ксения", "певица на круизном лайнере", "Санкт-Петербург", 8, 4, "65000", "85000", "960000"),
    ShiftPortrait("Борис", "лесозаготовитель", "Карелия", 3, 3, "58000", "72000", "410000"),
    ShiftPortrait("Галина", "работница рыбзавода", "Сахалин", 4, 4, "60000", "75000", "480000"),
    ShiftPortrait("Егор", "инженер пусконаладки", "Екатеринбург / Азия", 6, 2, "85000", "110000", "1050000"),
)


class ProfileClassificationTests(unittest.TestCase):
    def test_three_primary_profiles_have_distinct_routes(self):
        self.assertEqual(financial_profile("Наёмный", "monthly"), STABLE_EMPLOYEE)
        self.assertEqual(financial_profile("Фрилансер", "irregular"), STABLE_FREELANCER)
        self.assertEqual(financial_profile("Наёмный", "cyclic"), CYCLIC)
        self.assertEqual(len(profile_route(STABLE_EMPLOYEE)), 4)
        self.assertEqual(len(profile_route(STABLE_FREELANCER)), 6)
        self.assertEqual(len(profile_route(CYCLIC)), 8)


class CyclicPortraitTests(unittest.TestCase):
    pass


def run_portrait(test_case: unittest.TestCase, portrait: ShiftPortrait):
    plan = CyclicIncomePlan(
        portrait.paid_months, portrait.gap_months, D(portrait.km),
        D(portrait.sustainable), portrait.delay, portrait.fm_months,
    )
    allocation = plan.allocate_protection(D(portrait.payout))
    test_case.assertEqual(plan.monthly_self_salary, D(portrait.sustainable))
    test_case.assertEqual(plan.intercontract_full, D(portrait.sustainable) * portrait.gap_months)
    test_case.assertEqual(sum(plan.contract_contribution_schedule, D("0")), plan.intercontract_full)
    test_case.assertEqual(len(plan.contract_contribution_schedule), portrait.paid_months)
    test_case.assertEqual(sum(allocation.values(), D("0")), D(portrait.payout))
    test_case.assertTrue(all(value >= 0 for value in allocation.values()))
    if allocation["Свободный остаток"] > 0:
        test_case.assertEqual(allocation["Межконтрактный резерв-КМ"], plan.intercontract_critical)
        test_case.assertEqual(
            allocation["Межконтрактный резерв-КМ"] + allocation["Межконтрактный резерв-УЖ"],
            plan.intercontract_full,
        )
        test_case.assertEqual(allocation["ФМ-подушка"], plan.force_majeure)
        test_case.assertEqual(allocation["Стабилизатор-КМ"], plan.stabilizer_critical)
        test_case.assertEqual(
            allocation["Стабилизатор-КМ"] + allocation["Стабилизатор-УЖ"],
            plan.stabilizer_full,
        )
    for months_left in range(portrait.gap_months, -1, -1):
        test_case.assertEqual(
            plan.remaining_intercontract_target(months_left),
            D(portrait.sustainable) * months_left,
        )


for number, portrait in enumerate(PORTRAITS, 1):
    def scenario(self, item=portrait):
        run_portrait(self, item)

    scenario.__name__ = f"test_{number:02d}_{portrait.name.lower()}"
    scenario.__doc__ = (
        f"{portrait.name}: {portrait.work}, {portrait.region}, "
        f"цикл {portrait.paid_months}/{portrait.gap_months}."
    )
    setattr(CyclicPortraitTests, scenario.__name__, scenario)


if __name__ == "__main__":
    unittest.main()
