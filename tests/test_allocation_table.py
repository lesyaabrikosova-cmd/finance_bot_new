import unittest

from allocation_table import allocation_table


class AllocationTableTests(unittest.TestCase):
    def test_aligns_labels_and_decimal_columns(self):
        table = allocation_table([[
            ("📈 Инвестиции", "6 764,99"),
            ("⭐️ Отпуск", "2 647"),
            ("🧳 Сундук Продвижения", "13 970,50"),
        ]])
        self.assertEqual(
            table,
            "<pre>📈 Инвестиции         —  6 764,99\n"
            "⭐️ Отпуск             —  2 647   \n"
            "🧳 Сундук Продвижения — 13 970,50</pre>",
        )
