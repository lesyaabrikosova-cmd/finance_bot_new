import unittest
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from charts import chart_items, report_fallback_text, send_chart_report, wrap_legend_label


class ChartLegendIntegrityTests(unittest.TestCase):
    def test_long_similar_labels_are_wrapped_in_full_and_stay_distinct(self):
        prefix = "Очень длинное название источника дохода с общим началом"
        labels = [f"{prefix} Альфа", f"{prefix} Бета"]

        wrapped = [wrap_legend_label(label, 18, len) for label in labels]

        for label, lines in zip(labels, wrapped):
            self.assertEqual([word for line in lines for word in line.split()], label.split())
            self.assertTrue(all(len(line) <= 18 for line in lines))
            self.assertNotIn("…", "".join(lines))
        self.assertNotEqual(wrapped[0], wrapped[1])

    def test_single_long_word_is_split_without_losing_characters(self):
        label = "СверхдлинноеНазваниеИсточникаБезПробелов"

        lines = wrap_legend_label(label, 9, len)

        self.assertEqual("".join(lines), label)
        self.assertTrue(all(len(line) <= 9 for line in lines))

    def test_other_slice_discloses_every_aggregated_category(self):
        values = {f"Источник {index}": D(index) for index in range(1, 13)}

        items = chart_items(values)

        self.assertEqual(len(items), 8)
        remainder_label, remainder_total = items[-1]
        self.assertTrue(remainder_label.startswith("Остальное: "))
        for expected_name in ("Источник 5", "Источник 4", "Источник 3", "Источник 2", "Источник 1"):
            self.assertIn(expected_name, remainder_label)
        self.assertEqual(remainder_total, D(15))
        self.assertEqual(sum(value for _, value in items), sum(values.values(), D(0)))

    def test_preserved_order_exposes_every_category(self):
        values = {f"Цель {index}": D(index + 1) for index in range(12)}

        items = chart_items(values, preserve_order=True)

        self.assertEqual([label for label, _ in items], list(values))
        self.assertFalse(any(label.startswith("Остальное") for label, _ in items))


class ChartFallbackIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_any_render_error_returns_nonempty_title_and_navigation(self):
        navigation = object()
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())

        with patch("charts.make_chart", side_effect=RuntimeError("renderer failed")):
            await send_chart_report(
                message,
                {"Работа": D(10)},
                "АНАЛИЗ ДОХОДОВ",
                "",
                reply_markup=navigation,
            )

        message.answer.assert_awaited_once_with("АНАЛИЗ ДОХОДОВ", reply_markup=navigation)
        message.answer_photo.assert_not_awaited()

    async def test_photo_delivery_error_returns_requested_fallback_and_navigation(self):
        navigation = object()
        message = SimpleNamespace(
            answer=AsyncMock(),
            answer_photo=AsyncMock(side_effect=RuntimeError("photo rejected")),
        )

        with patch("charts.make_chart", return_value=b"png"):
            await send_chart_report(
                message,
                {"Работа": D(10)},
                "ДОХОДЫ",
                "Подпись диаграммы",
                reply_markup=navigation,
                fallback_text="Полный текстовый отчёт",
            )

        message.answer.assert_awaited_once_with("Полный текстовый отчёт", reply_markup=navigation)
        message.answer_photo.assert_awaited_once()

    def test_fallback_text_is_never_empty(self):
        self.assertEqual(report_fallback_text("", "", ""), "Отчёт временно недоступен.")
        self.assertEqual(report_fallback_text("ЗАГОЛОВОК", " ", "\n"), "ЗАГОЛОВОК")


if __name__ == "__main__":
    unittest.main()
