import unittest
from unittest.mock import AsyncMock

from onboarding import (
    br_override_save,
    format_life_classification_section,
    km_override_save,
    life_result_breakdown_lines,
    life_result_keyboard,
    save_km_item,
    save_split_food_expense,
    show_life_classification,
    show_km_category_after_save,
)


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


class MutableState:
    def __init__(self, data):
        self.data = data

    async def get_data(self):
        return self.data

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, value):
        self.state = value


class LifeResultReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_result_keyboard_names_redistribution_and_keeps_all_actions(self):
        markup = life_result_keyboard()
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(
            labels,
            [
                "Продолжить →",
                "КМ ⇄ БР",
                "✎ Редактировать расходы",
                "✎ Изменить сумму КМ",
                "✎ Изменить сумму БР",
            ],
        )
        self.assertEqual(
            callbacks(markup),
            [
                "kmfinal:continue",
                "lifeclassification:show",
                "lifeedit:list",
                "kmfinal:override",
                "lifeoverride:br",
            ],
        )

    async def test_car_expense_returns_to_car_menu_until_done(self):
        message = AsyncMock()
        state = AsyncMock()
        state.get_data.return_value = {
            "pending_km_subcategory": "car_fuel",
            "km_items": [{
                "category": "transport",
                "subcategory": "car_fuel",
                "name": "Бензин",
                "monthly": "5000",
            }],
            "br_items": [],
        }

        await show_km_category_after_save(
            message,
            state,
            "transport",
            "Добавлено: <b>Бензин</b>",
        )

        text = message.answer.await_args.args[0]
        markup = message.answer.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("<b>АВТОМОБИЛЬ</b>", text)
        self.assertIn("<b>Бензин</b> — 5 000 ₽ / мес.", text)
        self.assertIn("✔️ Готово", labels)
        self.assertNotIn("Общественный транспорт", labels)
        self.assertIn("kmtransport:back", callbacks(markup))

    async def test_food_expense_can_be_split_between_km_and_reserve(self):
        message = AsyncMock()
        state = MutableState({
            "pending_km_item_amount": "25000",
            "pending_km_item_name": "Фастфуд",
            "pending_km_subcategory": "fastfood",
            "pending_food_months": "6",
            "km_items": [],
            "br_items": [],
        })

        await save_split_food_expense(message, state, required=5000)

        self.assertEqual(state.data["km_items"][0]["amount"], "5000")
        self.assertEqual(state.data["km_items"][0]["monthly"], "833.33")
        self.assertEqual(state.data["br_items"][0]["amount"], "20000")
        self.assertEqual(state.data["br_items"][0]["monthly"], "3333.33")
        self.assertEqual(state.data["km_items"][0]["classification_part"], "essential")
        self.assertEqual(state.data["br_items"][0]["classification_part"], "flexible")

    async def test_ambiguous_food_asks_role_after_amount_and_period(self):
        message = AsyncMock()
        state = MutableState({
            "pending_km_item_amount": "25000",
            "pending_km_item_name": "Доставка еды",
            "pending_km_category": "food",
            "pending_km_subcategory": "delivery",
        })

        await save_km_item(message, state, months=6)

        text = message.answer.await_args.args[0]
        markup = message.answer.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("КАКУЮ РОЛЬ ИГРАЕТ ЭТОТ РАСХОД?", text)
        self.assertIn("Необходима вся сумма", labels)
        self.assertIn("Необходима часть", labels)
        self.assertIn("Можно сократить", labels)
        self.assertEqual(state.data["pending_food_months"], "6")

    def test_classification_section_groups_items_and_explains_category_once(self):
        text = format_life_classification_section(
            "БЫТОВОЙ РЕЗЕРВ",
            [
                {"category": "food", "name": "Фастфуд", "monthly": "400"},
                {"category": "food", "name": "Доставка еды", "monthly": "7000"},
                {"category": "leisure", "name": "Кино", "monthly": "500"},
            ],
            "br",
        )

        self.assertIn("<b><u>БЫТОВОЙ РЕЗЕРВ</u></b>", text)
        self.assertIn("<b>ПИТАНИЕ</b>", text)
        self.assertEqual(text.count("Гибкая или нерегулярная часть питания."), 1)
        self.assertIn("• Фастфуд — 400 ₽", text)
        self.assertIn("• Доставка еды — 7 000 ₽", text)
        self.assertIn("<b>РАЗВЛЕЧЕНИЯ</b>", text)
        self.assertNotIn("Причина:", text)

    def test_result_expands_only_categories_split_between_km_and_reserve(self):
        km_items = [
            {"category": "food", "category_label": "Питание", "name": "Супермаркет", "monthly": "24500"},
            {"category": "health", "category_label": "Здоровье", "name": "Аптека", "monthly": "1000"},
        ]
        br_items = [
            {"category": "food", "category_label": "Питание", "name": "Кафе и рестораны", "monthly": "1000"},
            {"category": "clothes", "category_label": "Одежда", "name": "Одежда", "monthly": "2000"},
        ]

        km_lines = life_result_breakdown_lines(km_items, br_items)
        br_lines = life_result_breakdown_lines(br_items, km_items)

        self.assertIn("• Супермаркет — 24 500 ₽", km_lines)
        self.assertIn("• Кафе и рестораны — 1 000 ₽", br_lines)
        self.assertIn("• Здоровье — 1 000 ₽", km_lines)
        self.assertIn("• Одежда — 2 000 ₽", br_lines)
        self.assertNotIn("• Питание — 24 500 ₽", km_lines)
        self.assertNotIn("• Питание — 1 000 ₽", br_lines)

    async def test_classification_screen_uses_approved_intro(self):
        callback = AsyncMock()
        callback.message = AsyncMock()
        state = MutableState({
            "km_items": [{"category": "health", "name": "Стоматолог", "monthly": "1000"}],
            "br_items": [],
        })

        await show_life_classification(callback, state)

        text = callback.message.answer.await_args.args[0]
        self.assertIn("<b>ПРОВЕРЬТЕ КАТЕГОРИИ КМ И БР</b>", text)
        self.assertIn(
            "Аллокатор определил расходы Критического минимума и Бытового резерва. "
            "Причины основаны на правилах категорий и указанном периоде оплаты. "
            "Если расход оказался не в той части жизни, выберите его и перенесите.",
            text,
        )

    async def test_km_override_returns_all_review_actions(self):
        message = AsyncMock()
        message.text = "95000"
        state = AsyncMock()
        state.get_data.return_value = {
            "critical_life_exact": "90000",
            "household_reserve": "20000",
            "combined_life_onboarding": True,
        }

        await km_override_save(message, state)

        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("lifeoverride:br", callbacks(markup))
        self.assertIn("kmfinal:override", callbacks(markup))

    async def test_br_override_returns_all_review_actions(self):
        message = AsyncMock()
        message.text = "25000"
        state = AsyncMock()
        state.get_data.return_value = {
            "household_reserve_exact": "20000",
            "critical_life": "90000",
            "combined_life_onboarding": True,
        }

        await br_override_save(message, state)

        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("lifeoverride:br", callbacks(markup))
        self.assertIn("kmfinal:override", callbacks(markup))


if __name__ == "__main__":
    unittest.main()
