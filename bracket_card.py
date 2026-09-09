"""Детерминированная PNG-карточка; без сети, персональных файлов и AI-генерации."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
WIDTH = 1080
HEIGHT = 1690


def percent(value):
    return format(Decimal(value).normalize(), "f") + "%"


def render_bracket_card(profile_label, level_label, rows, variant="") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    if len(rows) != 4:
        raise ValueError("Карточка должна содержать четыре бракета.")
    image = Image.new("RGB", (WIDTH, HEIGHT), "#191321")
    draw = ImageDraw.Draw(image)
    gold, white, muted = "#F1CD83", "#F9F4ED", "#D0C2D8"

    fonts = {}

    def font(size, bold=False):
        key = (size, bold)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(str(FONT_DIR / ("PTSans-Bold.ttf" if bold else "PTSans-Regular.ttf")), size)
        return fonts[key]

    def line(text, x, y, size=42, fill=white, bold=False):
        draw.text((x, y), text, font=font(size, bold), fill=fill, anchor="lt")

    def wrapped(text, x, y, width, max_lines=2, size=42, fill=white):
        selected_font = font(size)
        lines, current = [], ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=selected_font) > width:
                if not current or draw.textlength(word, font=selected_font) > width:
                    raise ValueError("Текст не помещается в карточку.")
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            raise ValueError("Слишком много строк в карточке.")
        for index, text_line in enumerate(lines):
            line(text_line, x, y + index * 48, size, fill)

    draw.line((48, 40, 1032, 40), fill=gold, width=3)
    line("БРАКЕТЫ", 48, 67, 84, gold, True)
    line(f"{profile_label} · {level_label}", 50, 166, 42)
    if variant:
        line(variant, 50, 218, 34, muted)

    for index, row in enumerate(rows):
        top = 280 + index * 315
        draw.rounded_rectangle((40, top, 1040, top + 295), radius=22, fill="#2C2039", outline="#614969", width=2)
        draw.rounded_rectangle((62, top + 20, 117, top + 73), radius=10, fill="#EAC47D")
        line(row["stage"], 76, top + 27, 38, "#251A2E", True)
        line(row["title"], 137, top + 25, 42, white, True)
        draw.line((540, top + 98, 540, top + 255), fill="#614969", width=2)
        line("БРАКЕТ", 70, top + 91, 29, muted)
        line("ОСТАТОК", 574, top + 91, 29, muted)
        line(percent(row["rate"]), 70, top + 125, 62, gold, True)
        remainder = Decimal(100) - row["rate"]
        line(percent(remainder), 574, top + 125, 62, gold, True)
        wrapped(row["target"], 70, top + 192, 440)
        targets = row["remainder"]
        if len(targets) == 1:
            wrapped(targets[0], 574, top + 192, 430)
        else:
            # Доли именно остатка: их смысл не зависит от размера брекета.
            short = {"Цели и Сундуки": "Цели и Сундуки", "Полный Стабилизатор": "Стабилизатор",
                     "Стабилизатор-КМ": "Стабилизатор-КМ"}
            for part_index, target in enumerate(targets):
                text = "50% — " + short.get(target, target)
                wrapped(text, 574, top + 192 + part_index * 42, 430, max_lines=1, size=36)

    line("Проценты относятся к разным частям денег.", 50, 1560, 37, muted)
    line("После заполнения резерва правила меняются.", 50, 1610, 37, muted)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
