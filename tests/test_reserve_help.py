import unittest

from reserve_help import render_reserve_help


class ReserveHelpTests(unittest.TestCase):
    def test_stable_profile_mentions_only_pillow(self):
        text = render_reserve_help("stable", has_stabilizer=False, has_salary_fund=False)
        self.assertIn("часть денег из Подушки", text)
        self.assertNotIn("Стабилизатор", text)
        self.assertNotIn("Фонд зарплаты", text)

    def test_piecework_profile_mentions_stabilizer_when_enabled(self):
        text = render_reserve_help("piecework", has_stabilizer=True, has_salary_fund=False)
        self.assertIn("Подушки или Стабилизатора", text)
        self.assertNotIn("Фонд зарплаты", text)

    def test_cyclic_profile_without_stabilizer_keeps_salary_fund_separate(self):
        text = render_reserve_help("cyclic", has_stabilizer=False, has_salary_fund=True)
        self.assertIn("часть денег из Подушки", text)
        self.assertIn("Фонд зарплаты Аллокатор ведёт отдельно", text)
        self.assertIn("не нужно корректировать после каждой плановой выплаты", text)
        self.assertNotIn("Стабилизатор", text)

    def test_cyclic_profile_never_exposes_stabilizer(self):
        text = render_reserve_help("cyclic", has_stabilizer=True, has_salary_fund=True)
        self.assertNotIn("Стабилизатор", text)
        self.assertIn("Фонд зарплаты Аллокатор ведёт отдельно", text)


if __name__ == "__main__":
    unittest.main()
