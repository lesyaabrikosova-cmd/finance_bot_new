import sqlite3
import tempfile
import unittest
from pathlib import Path

from archetypes import ARCHETYPES, ARCHETYPE_ROWS
from storage import Database


class FinancialArchetypeTests(unittest.TestCase):
    def test_catalogue_has_the_fixed_twelve_button_order_and_images(self):
        self.assertEqual(
            ARCHETYPE_ROWS,
            (
                ("bull", "bear", "whale"),
                ("shark", "wolf", "sheep"),
                ("pig", "rabbit", "turtle"),
                ("hamster", "ostrich", "moose"),
            ),
        )
        self.assertEqual({slug for row in ARCHETYPE_ROWS for slug in row}, set(ARCHETYPES))
        self.assertTrue(all(item.image_path.is_file() for item in ARCHETYPES.values()))

    def test_preference_persists_and_isolated_from_financial_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allocator.db"
            db = Database(path)
            db.set_financial_archetype(101, "turtle")
            self.assertEqual(db.get_financial_archetype(101), "turtle")
            self.assertIsNone(db.load_settings(101))
            db.connection.close()

            reopened = Database(path)
            self.assertEqual(reopened.get_financial_archetype(101), "turtle")
            reopened.set_financial_archetype(101, None)
            self.assertIsNone(reopened.get_financial_archetype(101))

    def test_database_rejects_an_unknown_archetype(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "allocator.db")
            with self.assertRaises(sqlite3.IntegrityError):
                db.set_financial_archetype(101, "not-an-archetype")


if __name__ == "__main__":
    unittest.main()
