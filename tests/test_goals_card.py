import unittest
from decimal import Decimal as D
from io import BytesIO

from financial_engine import AllocatorState, FinancialAllocator, Goal, UserSettings
from goals_card import (
    CHEST_ROWS,
    FROZEN_MAIN,
    GOAL_ROWS,
    current_positions,
    position_color,
    render_goals_card,
)


class GoalsCardTests(unittest.TestCase):
    def test_deleting_neighbour_does_not_shift_saved_colour_index(self):
        first = Goal("Первая", 20)
        second = Goal("Вторая", 20)
        chest = Goal("Подарки", 60, position_type="chest", is_system_chest=True)
        allocator = FinancialAllocator(
            UserSettings(
                has_debts=False,
                employment_type="Наёмный",
                average_income=D("1000"),
                critical_life=D("100"),
                household_reserve=D("0"),
                goals=[first, second, chest],
            ),
            AllocatorState(),
        )
        saved_index = second.color_index

        allocator.settings.goals.remove(first)
        allocator.ensure_position_color_indices()

        self.assertEqual(second.color_index, saved_index)

    def test_layout_rules_match_product_limits(self):
        self.assertEqual(GOAL_ROWS, {
            0: (), 1: (1,), 2: (2,), 3: (3,), 4: (2, 2), 5: (3, 2),
        })
        self.assertEqual(CHEST_ROWS, {
            0: (), 1: (1,), 2: (2,), 3: (3,), 4: (4,),
            5: (3, 2), 6: (3, 3), 7: (4, 3), 8: (4, 4),
        })

    def test_current_positions_exclude_completed_and_archived(self):
        positions = [
            Goal("Активная", 50, order_index=2),
            Goal("Замороженная", 0, status="paused", order_index=1),
            Goal("Готовая", 0, status="completed", order_index=0),
            Goal("Архив", 0, status="archived", order_index=3),
        ]

        self.assertEqual(
            [goal.name for goal in current_positions(positions)],
            ["Замороженная", "Активная"],
        )

    def test_paused_positions_use_one_ice_blue_palette(self):
        active = Goal("Отпуск", 50, color_index=2)
        paused = Goal("Отпуск", 0, color_index=2, status="paused")
        another_paused = Goal("Айфон", 0, color_index=4, status="paused")

        self.assertNotEqual(position_color(active), position_color(paused))
        self.assertEqual(position_color(paused), FROZEN_MAIN)
        self.assertEqual(position_color(another_paused), FROZEN_MAIN)
        self.assertEqual(active.color_index, paused.color_index)

    def test_worst_case_is_one_dynamic_card_with_three_object_rows(self):
        from PIL import Image

        positions = [
            Goal(f"Цель {index}", 10, balance=D("300"), target_amount=D("1000"),
                 deadline="2028-01-01", color_index=index)
            for index in range(5)
        ] + [
            Goal(f"Сундук {index}", 10, balance=D("500"), position_type="chest",
                 color_index=index)
            for index in range(3)
        ]

        image = Image.open(BytesIO(render_goals_card(positions)))

        self.assertEqual(image.width, 1200)
        self.assertLessEqual(image.height, 1450)

    def test_height_shrinks_for_one_goal_and_one_chest(self):
        from PIL import Image

        short = [
            Goal("Отпуск", 50, target_amount=D("1000")),
            Goal("Подарки", 50, position_type="chest"),
        ]
        crowded = [
            Goal(f"Цель {index}", 10, target_amount=D("1000")) for index in range(5)
        ] + [
            Goal(f"Сундук {index}", 10, position_type="chest") for index in range(3)
        ]

        short_image = Image.open(BytesIO(render_goals_card(short)))
        crowded_image = Image.open(BytesIO(render_goals_card(crowded)))

        self.assertLess(short_image.height, crowded_image.height)


if __name__ == "__main__":
    unittest.main()
