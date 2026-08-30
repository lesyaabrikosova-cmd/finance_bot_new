import unittest

from mode_presentation import MODE_IMAGES_DIR, PROFILE_MODE_ASSETS, mode_image_path


class ModePresentationTests(unittest.TestCase):
    def test_shared_meanings_resolve_across_profiles(self):
        self.assertEqual(mode_image_path("stable", 3).name, "force_majeure.png")
        self.assertEqual(mode_image_path("piecework", 3).name, "force_majeure.png")
        self.assertEqual(mode_image_path("cyclic", 5).name, "force_majeure.png")

        self.assertEqual(mode_image_path("stable", 4).name, "maximum.png")
        self.assertEqual(mode_image_path("piecework", 6).name, "maximum.png")
        self.assertEqual(mode_image_path("cyclic", 8).name, "maximum.png")

    def test_cyclic_route_uses_all_special_images(self):
        expected = {
            3: "salary_fund_critical.png",
            4: "salary_fund_sustainable.png",
            5: "force_majeure.png",
            6: "contract_delay.png",
            7: "stabilizer_sustainable.png",
        }
        for mode, filename in expected.items():
            self.assertEqual(mode_image_path("cyclic", mode).name, filename)

    def test_unknown_mode_has_no_image(self):
        self.assertIsNone(mode_image_path("stable", 8))

    def test_every_configured_mode_has_an_existing_image(self):
        configured_assets = set()
        for profile_id, modes in PROFILE_MODE_ASSETS.items():
            for mode, asset_key in modes.items():
                configured_assets.add(asset_key)
                path = mode_image_path(profile_id, mode)
                self.assertIsNotNone(path, f"Нет изображения: {profile_id}, режим {mode}")
                self.assertTrue(path.is_file())

        files_on_disk = {path.stem for path in MODE_IMAGES_DIR.glob("*.png")}
        self.assertEqual(files_on_disk, configured_assets)


if __name__ == "__main__":
    unittest.main()
