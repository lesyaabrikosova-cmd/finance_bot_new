import unittest
from decimal import Decimal as D
from io import BytesIO

from reserve_card import render_reserve_card


class ReserveCardTests(unittest.TestCase):
    def test_profile_controls_the_number_of_vessels(self):
        stable = render_reserve_card("stable", pillow_balance=D("10"), pillow_target=D("100"))
        piecework = render_reserve_card(
            "piecework", pillow_balance=D("10"), pillow_target=D("100"),
            stabilizer_balance=D("20"), stabilizer_critical_target=D("40"), stabilizer_full_target=D("80"),
        )
        cyclic = render_reserve_card(
            "cyclic", pillow_balance=D("10"), pillow_target=D("100"),
            stabilizer_balance=D("20"), stabilizer_critical_target=D("40"), stabilizer_full_target=D("80"),
            salary_fund_balance=D("30"), salary_fund_critical_target=D("50"), salary_fund_full_target=D("100"),
        )
        self.assertTrue(stable.startswith(b"\x89PNG"))
        self.assertTrue(piecework.startswith(b"\x89PNG"))
        self.assertTrue(cyclic.startswith(b"\x89PNG"))

    def test_uses_the_approved_reserve_colours(self):
        from PIL import Image
        data = render_reserve_card(
            "cyclic", pillow_balance=D("100"), pillow_target=D("100"),
            stabilizer_balance=D("80"), stabilizer_critical_target=D("40"), stabilizer_full_target=D("80"),
            salary_fund_balance=D("100"), salary_fund_critical_target=D("50"), salary_fund_full_target=D("100"),
        )
        colors = set(Image.open(BytesIO(data)).getdata())
        for color in ("#008080", "#000080", "#4E77F9", "#393939", "#A9A9A9"):
            rgb = tuple(bytes.fromhex(color[1:]))
            self.assertIn(rgb, colors)

    def test_upper_stabilizer_level_is_clipped_to_the_vessel_shape(self):
        from PIL import Image
        data = render_reserve_card(
            "piecework", pillow_balance=D("0"), pillow_target=D("100"),
            stabilizer_balance=D("100"), stabilizer_critical_target=D("40"), stabilizer_full_target=D("100"),
        )
        image = Image.open(BytesIO(data))
        light_blue = tuple(bytes.fromhex("4E77F9"))
        # The top-left corner of the inner rounded vessel is outside its mask;
        # a rectangular second level must never leak into that point.
        self.assertNotEqual(image.getpixel((667, 257)), light_blue)
        self.assertEqual(image.getpixel((740, 350)), light_blue)


if __name__ == "__main__":
    unittest.main()
