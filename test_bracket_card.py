import os
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from PIL import Image
from financial_engine import FinancialAllocator, UserSettings
from bracket_card import render_bracket_card, WIDTH, HEIGHT
from brackets import (bracket_rows, card_caption, card_keyboard, show_bracket_card,
                      toggle_card, show_bracket_text, PROFILE_NAMES)


def allocator(profile="piecework"):
    return FinancialAllocator(UserSettings(
        has_debts=False, employment_type="Наёмный" if profile == "stable" else "Фрилансер",
        profile_type=profile, income_rhythm={"stable": "monthly", "piecework": "irregular", "cyclic": "cyclic"}[profile],
        critical_life=D(100), household_reserve=D(100), average_income=D(1000),
        force_majeure_months=D(4), stabilizer_target_months=D(2),
    ))


class CardRenderTests(unittest.TestCase):
    def test_every_profile_rule_and_boundary_rate_renders(self):
        for profile in PROFILE_NAMES:
            a = allocator(profile)
            for mode in range(1, 7):
                a.allocation_mode = lambda mode=mode: mode
                for rates in ((20, 25, 30, 35), (0, 0, 0, 0), (99, 99, 100, 100)):
                    with self.subTest(profile=profile, mode=mode, rates=rates):
                        a.settings.set_brackets(*rates)
                        data = render_bracket_card(PROFILE_NAMES[profile], "Максимальный уровень", bracket_rows(a))
                        with Image.open(BytesIO(data)) as image:
                            self.assertEqual(image.size, (WIDTH, HEIGHT))
                            self.assertEqual(image.format, "PNG")
                        self.assertLess(len(data), 1_000_000)
                        self.assertLess(len(card_caption(a)), 1024)

    def test_updated_rates_change_image_without_mutating_profile(self):
        a = allocator()
        old = render_bracket_card("Сдельный", "Уровень 3", bracket_rows(a))
        a.settings.set_brackets(10, 10, 15, 20)
        before = deepcopy(a)
        new = render_bracket_card("Сдельный", "Уровень 3", bracket_rows(a))
        self.assertNotEqual(old, new)
        self.assertEqual(a.state, before.state)
        self.assertEqual(a.settings, before.settings)


class CardDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_caption_and_buttons(self):
        msg = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        a = allocator()
        await show_bracket_card(msg, a)
        call = msg.answer_photo.await_args
        self.assertTrue(call.kwargs["photo"].data.startswith(b"\x89PNG"))
        self.assertLess(len(call.kwargs["caption"]), 1024)
        actions = [b.callback_data for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn("brackets:text", actions)
        self.assertIn("brackets:edit", actions)
        self.assertIn("brackets:example", actions)
        msg.answer.assert_not_called()

    async def test_missing_font_falls_back_to_text(self):
        msg = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        with patch("brackets.render_bracket_card", side_effect=OSError("font unavailable")), self.assertLogs("brackets", level="WARNING"):
            await show_bracket_card(msg, allocator())
        msg.answer_photo.assert_not_called()
        self.assertIn("БРАКЕТЫ", msg.answer.await_args.args[0])

    async def test_telegram_photo_rejection_falls_back(self):
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import SendPhoto
        error = TelegramBadRequest(method=SendPhoto(chat_id=42, photo="x"), message="photo rejected")
        msg = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock(side_effect=error))
        with self.assertLogs("brackets", level="WARNING"):
            await show_bracket_card(msg, allocator())
        msg.answer.assert_awaited_once()

    async def test_card_switch_and_text_use_same_variant_without_saving(self):
        a = allocator()
        original = deepcopy(a)
        data = {}
        state = AsyncMock()
        state.get_data.side_effect = lambda: data
        async def update(**values):
            data.update(values)
        state.update_data.side_effect = update
        callback = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=42),
                                   message=SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock()))
        with patch("brackets.db") as db, patch("brackets.show_bracket_card", new=AsyncMock()) as show:
            db.load_allocator.return_value = a
            await toggle_card(callback, state)
            displayed = show.await_args.args[1]
            self.assertEqual(displayed.settings.protective_stage_c_strategy, "protection")
            await show_bracket_text(callback, state)
            self.assertIn("Остаток → ФМ-подушка", callback.message.answer.await_args.args[0])
            db.save_allocator.assert_not_called()
        self.assertEqual(a.settings, original.settings)
        self.assertEqual(a.state, original.state)

