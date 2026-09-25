import unittest
from decimal import Decimal as D
from io import BytesIO

from reserve_card import _fill_top_for_fraction, render_reserve_card, reserve_legend_items


class ReserveCardTests(unittest.TestCase):
    def test_fill_height_is_based_on_vessel_area_not_linear_height(self):
        from PIL import Image, ImageDraw

        # A triangular vessel is much wider at the bottom. Filling 25% of its
        # visible capacity therefore rises less than 25% of its height.
        mask = Image.new("L", (100, 100))
        ImageDraw.Draw(mask).polygon([(50, 0), (0, 100), (100, 100)], fill=255)
        fill_top = _fill_top_for_fraction(mask, D("0.25"))
        self.assertGreater(fill_top, 75)
        filled = sum(mask.crop((0, fill_top, 100, 100)).histogram()[1:])
        total = sum(mask.histogram()[1:])
        self.assertAlmostEqual(filled / total, 0.25, delta=0.02)

    def test_piecework_legend_includes_current_pillow_coverage(self):
        items = reserve_legend_items(
            stabilizer_balance=D("100"), stabilizer_critical_target=D("90"),
            stabilizer_full_target=D("110"), stabilizer_months=D("1"),
            salary_fund_balance=D("0"), salary_fund_critical_target=D("0"),
            salary_fund_full_target=D("0"), salary_fund_months=D("0"),
            pillow_balance=D("180"), pillow_target=D("270"), pillow_months=D("3"),
        )
        self.assertEqual(
            items,
            [
                ("#176B87", "Форс-мажор — хватит на 2 мес"),
                ("#3E6FD8", "Бытовой резерв — хватит на 0 мес"),
                ("#1b3a9d", "Критический Минимум — хватит на 1 мес"),
            ],
        )

    def test_debt_level_one_uses_only_the_minimum_pillow(self):
        from PIL import Image

        data = render_reserve_card(
            "debt_level_one", pillow_balance=D("90"), pillow_target=D("180"), pillow_months=D("2"),
            stabilizer_balance=D("100"), stabilizer_critical_target=D("90"),
            stabilizer_full_target=D("110"), stabilizer_months=D("1"),
            salary_fund_balance=D("100"), salary_fund_critical_target=D("90"),
            salary_fund_full_target=D("110"), salary_fund_months=D("1"),
        )
        colors = set(Image.open(BytesIO(data)).getdata())
        self.assertIn(tuple(bytes.fromhex("176B87")), colors)
        self.assertNotIn(tuple(bytes.fromhex("1b3a9d")), colors)
        self.assertNotIn(tuple(bytes.fromhex("3E6FD8")), colors)
        items = reserve_legend_items(
            stabilizer_balance=D("0"), stabilizer_critical_target=D("0"), stabilizer_full_target=D("0"),
            stabilizer_months=D("0"), salary_fund_balance=D("0"), salary_fund_critical_target=D("0"),
            salary_fund_full_target=D("0"), salary_fund_months=D("0"),
            pillow_balance=D("90"), pillow_target=D("180"), pillow_months=D("2"),
            pillow_legend_label="Минимальная подушка",
        )
        self.assertEqual(items, [("#176B87", "Минимальная подушка — хватит на 1 мес")])

    def test_legends_show_current_full_month_coverage(self):
        base = dict(
            stabilizer_critical_target=D("90"), stabilizer_full_target=D("110"), stabilizer_months=D("1"),
            salary_fund_balance=D("100"), salary_fund_critical_target=D("90"),
            salary_fund_full_target=D("110"), salary_fund_months=D("1"),
        )
        self.assertEqual(
            reserve_legend_items(stabilizer_balance=D("100"), **base),
            [
                ("#A9A9A9", "Бытовой резерв — хватит на 0 мес"),
                ("#3E6FD8", "Бытовой резерв — хватит на 0 мес"),
                ("#393939", "Критический Минимум — хватит на 1 мес"),
                ("#1b3a9d", "Критический Минимум — хватит на 1 мес"),
            ],
        )
        salary_base = dict(base, stabilizer_balance=D("0"), salary_fund_balance=D("0"))
        self.assertEqual(
            reserve_legend_items(**salary_base),
            [
                ("#A9A9A9", "Бытовой резерв — хватит на 0 мес"),
                ("#3E6FD8", "Бытовой резерв — хватит на 0 мес"),
                ("#393939", "Критический Минимум — хватит на 0 мес"),
                ("#1b3a9d", "Критический Минимум — хватит на 0 мес"),
            ],
        )

    def test_profile_controls_the_number_of_vessels(self):
        stable = render_reserve_card("stable", pillow_balance=D("10"), pillow_target=D("100"))
        piecework = render_reserve_card(
            "piecework", pillow_balance=D("10"), pillow_target=D("100"),
            stabilizer_balance=D("20"), stabilizer_critical_target=D("40"), stabilizer_full_target=D("80"),
        )
        cyclic = render_reserve_card(
            "cyclic", pillow_balance=D("10"), pillow_target=D("100"),
            salary_fund_balance=D("30"), salary_fund_critical_target=D("50"), salary_fund_full_target=D("100"),
        )
        self.assertTrue(stable.startswith(b"\x89PNG"))
        self.assertTrue(piecework.startswith(b"\x89PNG"))
        self.assertTrue(cyclic.startswith(b"\x89PNG"))

    def test_uses_the_approved_reserve_colours(self):
        from PIL import Image
        data = render_reserve_card(
            "cyclic", pillow_balance=D("100"), pillow_target=D("100"),
            salary_fund_balance=D("100"), salary_fund_critical_target=D("50"), salary_fund_full_target=D("100"),
        )
        colors = set(Image.open(BytesIO(data)).getdata())
        for color in ("#176B87", "#393939", "#A9A9A9"):
            rgb = tuple(bytes.fromhex(color[1:]))
            self.assertIn(rgb, colors)

    def test_upper_stabilizer_level_is_clipped_to_the_vessel_shape(self):
        from PIL import Image
        data = render_reserve_card(
            "piecework", pillow_balance=D("0"), pillow_target=D("100"),
            stabilizer_balance=D("100"), stabilizer_critical_target=D("40"), stabilizer_full_target=D("100"),
        )
        image = Image.open(BytesIO(data))
        light_blue = tuple(bytes.fromhex("3E6FD8"))
        # The top-left corner of the inner rounded vessel is outside its mask;
        # a rectangular second level must never leak into that point.
        self.assertNotEqual(image.getpixel((592, 257)), light_blue)
        self.assertEqual(image.getpixel((770, 350)), light_blue)


if __name__ == "__main__":
    unittest.main()
