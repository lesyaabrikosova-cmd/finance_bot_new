"""Dynamic Goals and Chests summary card for Telegram."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Iterable

from financial_engine import Goal
from semantic_chart_colors import (
    BALANCE_CHEST_COLORS,
    BALANCE_GOAL_COLORS,
    position_palette_color,
)


WIDTH = 1200
BACKGROUND_TOP = "#11101F"
BACKGROUND_BOTTOM = "#171326"
GOLD = "#F1CD83"
WHITE = "#F9F4ED"
MUTED = "#AEB9F4"
SUBTLE = "#746D90"
FROZEN_MAIN = "#8ED8FF"
FROZEN_LIGHT = "#C8F0FF"
FROZEN_DARK = "#4EA7D8"
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"


GOAL_ROWS = {
    0: (),
    1: (1,),
    2: (2,),
    3: (3,),
    4: (2, 2),
    5: (3, 2),
}

CHEST_ROWS = {
    0: (),
    1: (1,),
    2: (2,),
    3: (3,),
    4: (4,),
    5: (3, 2),
    6: (3, 3),
    7: (4, 3),
    8: (4, 4),
}


def current_positions(goals: Iterable[Goal]) -> list[Goal]:
    """Return current positions only; completed and archived Goals are history."""
    return sorted(
        (goal for goal in goals if goal.status in {"active", "paused"}),
        key=lambda goal: goal.order_index,
    )


def _layout_rows(count: int, *, kind: str) -> tuple[int, ...]:
    configured = GOAL_ROWS if kind == "goal" else CHEST_ROWS
    if count in configured:
        return configured[count]
    maximum = 3 if kind == "goal" else 4
    full_rows, remainder = divmod(count, maximum)
    return (maximum,) * full_rows + ((remainder,) if remainder else ())


def _hex_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _rgb_hex(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{component:02x}" for component in color)


def _mix(first: str, second: str, amount: float) -> str:
    a, b = _hex_rgb(first), _hex_rgb(second)
    return _rgb_hex(tuple(round(x * (1 - amount) + y * amount) for x, y in zip(a, b)))


def _lighter(color: str, amount: float = 0.25) -> str:
    return _mix(color, "#FFFFFF", amount)


def _darker(color: str, amount: float = 0.32) -> str:
    return _mix(color, "#05040A", amount)


def position_color(goal: Goal) -> str:
    if goal.status == "paused":
        return FROZEN_MAIN
    palette = BALANCE_CHEST_COLORS if goal.is_chest else BALANCE_GOAL_COLORS
    return position_palette_color(
        goal.uid or goal.name,
        palette,
        color_index=goal.color_index,
    )


def _money(value: Decimal) -> str:
    amount = f"{Decimal(value):,.0f}".replace(",", " ")
    return f"{amount} ₽"


def _deadline(value: str | None) -> str:
    if not value:
        return "Без срока"
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d.%m.%Y")


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    filename = "PTSans-Bold.ttf" if bold else "PTSans-Regular.ttf"
    return ImageFont.truetype(str(FONT_DIR / filename), size)


def _fit_text(draw, value: str, maximum: int, size: int, minimum: int = 17, *, bold=True):
    value = " ".join(str(value).split())
    selected = size
    while selected > minimum and draw.textlength(value, font=_font(selected, bold)) > maximum:
        selected -= 1
    font = _font(selected, bold)
    if draw.textlength(value, font=font) <= maximum:
        return value, font
    shortened = value
    while shortened and draw.textlength(shortened + "…", font=font) > maximum:
        shortened = shortened[:-1]
    return shortened.rstrip() + "…", font


def _row_centers(count: int) -> tuple[int, ...]:
    presets = {
        1: (0.50,),
        2: (0.35, 0.65),
        3: (0.20, 0.50, 0.80),
        4: (0.125, 0.375, 0.625, 0.875),
    }
    return tuple(round(WIDTH * fraction) for fraction in presets[count])


def _star_points(cx: int, cy: int, outer: int, inner: int) -> list[tuple[float, float]]:
    from math import cos, pi, sin

    points = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = -pi / 2 + index * pi / 5
        points.append((cx + cos(angle) * radius, cy + sin(angle) * radius))
    return points


def _glow(image, box, color: str, radius: int = 22, opacity: int = 100) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    rgba = (*_hex_rgb(color), opacity)
    ImageDraw.Draw(layer).ellipse(box, fill=rgba)
    layer = layer.filter(ImageFilter.GaussianBlur(radius))
    image.alpha_composite(layer)


def _draw_centered_parts(draw, center_x: int, y: int, parts) -> None:
    widths = [draw.textlength(text, font=font) for text, font, _ in parts]
    cursor = center_x - sum(widths) / 2
    for (text, font, color), width in zip(parts, widths):
        draw.text((cursor, y), text, anchor="lm", font=font, fill=color)
        cursor += width


def _draw_goal(image, goal: Goal, balance: Decimal, center_x: int, top: int) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    color = position_color(goal)
    center_y = top + 110
    radius = 110
    ring_box = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
    _glow(
        image,
        (center_x - 88, center_y - 88, center_x + 88, center_y + 88),
        color,
        radius=28,
        opacity=105 if goal.status == "paused" else 82,
    )
    draw.ellipse(ring_box, outline="#292541", width=15)
    target = goal.full_target_amount or Decimal("0")
    progress = Decimal("0") if target <= 0 else min(Decimal("1"), max(Decimal("0"), balance / target))
    if progress > 0:
        draw.arc(
            ring_box,
            start=-90,
            end=-90 + float(progress * Decimal("360")),
            fill=color,
            width=15,
        )
    star_outline = FROZEN_LIGHT if goal.status == "paused" else _lighter(color, 0.38)
    star_points = _star_points(center_x, center_y, 87, 40)
    draw.polygon(star_points, fill=color)
    facet_light = FROZEN_LIGHT if goal.status == "paused" else _lighter(color, 0.32)
    facet_dark = FROZEN_DARK if goal.status == "paused" else _darker(color, 0.23)
    for index in range(0, 10, 2):
        previous_point = star_points[(index - 1) % 10]
        outer_point = star_points[index]
        next_point = star_points[(index + 1) % 10]
        draw.polygon((previous_point, outer_point, (center_x, center_y)), fill=facet_light)
        draw.polygon((outer_point, next_point, (center_x, center_y)), fill=facet_dark)
    draw.line(star_points + [star_points[0]], fill=star_outline, width=3, joint="curve")
    draw.ellipse(
        (center_x - 38, center_y - 38, center_x + 38, center_y + 38),
        fill=FROZEN_DARK if goal.status == "paused" else _darker(color, 0.58),
    )
    percent = int((progress * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    percent_text, percent_font = _fit_text(
        draw,
        f"{percent}%",
        65,
        31,
        23,
    )
    draw.text((center_x, center_y), percent_text, anchor="mm", font=percent_font, fill=WHITE)

    text_width = 350
    name, name_font = _fit_text(draw, goal.name, text_width, 31, 20)
    draw.text((center_x, top + 240), name, anchor="mm", font=name_font, fill=WHITE)
    if target > 0:
        accent = FROZEN_MAIN if goal.status == "paused" else color
        _draw_centered_parts(draw, center_x, top + 280, (
            (_money(balance), _font(27, True), accent),
            (f" из {_money(target)}", _font(25), MUTED),
        ))
    else:
        draw.text(
            (center_x, top + 280),
            f"Накоплено {_money(balance)}",
            anchor="mm",
            font=_font(25),
            fill=FROZEN_MAIN if goal.status == "paused" else color,
        )
    if goal.status == "paused":
        draw.text(
            (center_x, top + 320),
            "Заморожено",
            anchor="mm",
            font=_font(25, True),
            fill=FROZEN_MAIN,
        )
    else:
        draw.text(
            (center_x, top + 320),
            "до " + _deadline(goal.deadline),
            anchor="mm",
            font=_font(24),
            fill=MUTED,
        )


def _draw_chest_icon(image, draw, center_x: int, top: int, color: str, *, frozen: bool) -> None:
    chest_width = 224
    left, right = center_x - chest_width // 2, center_x + chest_width // 2
    band_top, band_bottom = top + 58, top + 82
    body_top, bottom = top + 70, top + 160
    light = FROZEN_LIGHT if frozen else _lighter(color, 0.24)
    dark = FROZEN_DARK if frozen else _darker(color, 0.23)
    mid = _mix(light, color, 0.52)
    deep = _darker(color, 0.38)
    if frozen:
        _glow(image, (left - 10, top - 8, right + 10, bottom + 10), FROZEN_MAIN, radius=25, opacity=95)

    # The lid and body deliberately share the same outer width. The inset
    # centre panel creates the rounded wooden/icy cap without narrowing it.
    draw.rounded_rectangle(
        (left + 4, top + 4, right - 4, top + 79),
        radius=29,
        fill=mid,
        outline=light,
        width=3,
    )
    draw.rounded_rectangle(
        (left + 35, top + 4, right - 35, top + 68),
        radius=20,
        fill=dark,
    )
    draw.rectangle((left + 35, top + 43, right - 35, top + 73), fill=dark)
    draw.polygon(
        ((left + 5, top + 37), (left + 35, top + 9), (left + 35, top + 70), (left + 5, top + 70)),
        fill=_lighter(mid, 0.12),
    )
    draw.polygon(
        ((right - 5, top + 37), (right - 35, top + 9), (right - 35, top + 70), (right - 5, top + 70)),
        fill=_darker(mid, 0.08),
    )

    draw.rounded_rectangle(
        (left + 7, body_top, right - 7, bottom),
        radius=8,
        fill=deep,
        outline=dark,
        width=3,
    )
    draw.rectangle((left + 32, body_top + 3, right - 32, bottom - 10), fill=dark)
    draw.rounded_rectangle((left, band_top, right, band_bottom), radius=6, fill=mid, outline=dark, width=2)
    draw.line((left + 5, band_top + 4, right - 5, band_top + 4), fill=light, width=3)

    side = _mix(light, color, 0.38)
    draw.rounded_rectangle((left + 7, body_top, left + 39, bottom), radius=7, fill=side)
    draw.rounded_rectangle((right - 39, body_top, right - 7, bottom), radius=7, fill=side)
    draw.rectangle((left + 39, bottom - 24, right - 39, bottom), fill=_mix(dark, color, 0.30))

    rivet = FROZEN_LIGHT if frozen else _lighter(light, 0.22)
    for rivet_x in (left + 16, right - 16):
        draw.ellipse((rivet_x - 4, band_top + 8, rivet_x + 4, band_top + 16), fill=rivet)

    draw.rounded_rectangle(
        (center_x - 27, band_top + 8, center_x + 27, band_top + 59),
        radius=6,
        fill=light,
        outline=dark,
        width=3,
    )
    lock_dark = "#124C73" if frozen else "#24150E"
    draw.ellipse((center_x - 6, band_top + 25, center_x + 6, band_top + 37), fill=lock_dark)
    draw.rectangle((center_x - 3, band_top + 34, center_x + 3, band_top + 47), fill=lock_dark)
    draw.rounded_rectangle((left + 5, bottom - 27, left + 42, bottom + 3), radius=6, fill=side)
    draw.rounded_rectangle((right - 42, bottom - 27, right - 5, bottom + 3), radius=6, fill=side)


def _draw_chest(image, goal: Goal, balance: Decimal, center_x: int, top: int) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    color = position_color(goal)
    frozen = goal.status == "paused"
    _draw_chest_icon(image, draw, center_x, top, color, frozen=frozen)
    name, name_font = _fit_text(draw, goal.name, 270, 29, 19)
    draw.text((center_x, top + 192), name, anchor="mm", font=name_font, fill=WHITE)
    draw.text(
        (center_x, top + 232),
        _money(balance),
        anchor="mm",
        font=_font(29, True),
        fill=FROZEN_MAIN if frozen else GOLD,
    )
    if goal.status == "paused":
        draw.text(
            (center_x, top + 269),
            "Заморожено",
            anchor="mm",
            font=_font(24, True),
            fill=FROZEN_MAIN,
        )


def _section_heading(draw, top: int, title: str, subtitle: str, *, leading_line=False) -> int:
    if leading_line:
        draw.line((54, top, WIDTH - 54, top), fill="#49415F", width=2)
        top += 14
    draw.text((70, top), title, font=_font(48, True), fill=GOLD)
    draw.text((70, top + 57), subtitle, font=_font(29), fill=MUTED)
    draw.line((54, top + 108, WIDTH - 54, top + 108), fill="#49415F", width=2)
    return top + 126


def _draw_footer(draw, top: int) -> int:
    bottom = top + 76
    draw.rounded_rectangle(
        (54, top, WIDTH - 54, bottom),
        radius=38,
        outline="#3B3354",
        width=2,
    )
    draw.text(
        (WIDTH // 2, top + 39),
        "Суммы показывают, сколько всего было отложено денег с помощью Аллокатора.",
        anchor="mm",
        font=_font(23),
        fill=MUTED,
    )
    return bottom


def render_goals_card(goals: Iterable[Goal], balances: dict[str, Decimal] | None = None) -> bytes:
    """Render one dynamically sized card for all current Goals and Chests."""
    from PIL import Image, ImageDraw

    positions = current_positions(goals)
    goal_items = [goal for goal in positions if goal.is_goal]
    chest_items = [goal for goal in positions if goal.is_chest]
    # The product limits new profiles to 8 current positions / 5 Goals.
    # Extra rows remain available only so a legacy over-limit profile never
    # loses its visual summary while the user decides what to archive.
    goal_rows = _layout_rows(len(goal_items), kind="goal")
    chest_rows = _layout_rows(len(chest_items), kind="chest")
    goal_content_height = 42 if not goal_rows else len(goal_rows) * 340
    chest_content_height = 42 if not chest_rows else len(chest_rows) * 282
    # Outer margins + headings + contents + footer. The second heading includes
    # its own separator and 14 px breathing room above the title.
    height = 40 + 126 + goal_content_height + 140 + chest_content_height + 28 + 76 + 40

    image = Image.new("RGBA", (WIDTH, height), BACKGROUND_TOP)
    draw = ImageDraw.Draw(image)
    top_rgb, bottom_rgb = _hex_rgb(BACKGROUND_TOP), _hex_rgb(BACKGROUND_BOTTOM)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(a * (1 - ratio) + b * ratio) for a, b in zip(top_rgb, bottom_rgb))
        draw.line((0, y, WIDTH, y), fill=color)
    draw.rounded_rectangle((18, 18, WIDTH - 18, height - 18), radius=34, outline="#302743", width=3)

    balance_map = balances or {}
    cursor = _section_heading(
        draw,
        40,
        "ЦЕЛИ",
        "Конкретная сумма, которую нужно накопить к сроку",
    )
    if not goal_rows:
        cursor += 42
    else:
        item_index = 0
        for count in goal_rows:
            for item, center_x in zip(
                goal_items[item_index:item_index + count],
                _row_centers(count),
            ):
                balance = Decimal(str(balance_map.get(item.name, item.balance)))
                _draw_goal(image, item, balance, center_x, cursor)
            item_index += count
            cursor += 340

    cursor = _section_heading(
        draw,
        cursor,
        "СУНДУКИ",
        "Постоянные запасы, которые можно пополнять и использовать снова",
        leading_line=True,
    )
    if not chest_rows:
        cursor += 42
    else:
        item_index = 0
        for count in chest_rows:
            for item, center_x in zip(
                chest_items[item_index:item_index + count],
                _row_centers(count),
            ):
                balance = Decimal(str(balance_map.get(item.name, item.balance)))
                _draw_chest(image, item, balance, center_x, cursor)
            item_index += count
            cursor += 282

    cursor += 28
    _draw_footer(draw, cursor)

    output = BytesIO()
    image.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()
