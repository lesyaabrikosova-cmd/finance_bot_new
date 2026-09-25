"""Dynamic PNG map for the «Мой финансовый путь» forecast."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, ROUND_HALF_UP
from io import BytesIO
from math import hypot
from pathlib import Path
from typing import Any

from financial_engine import FinancialAllocator, fmt_money, goal_display_name
from goals_card import position_color


WIDTH = 1200
HEADER_BOTTOM = 190
OUTER_MARGIN = 72
CARD_WIDTH = 338
CARD_HEIGHT = 140
SHORT_CARD_HEIGHT = 130
LEVEL_HEIGHT = 82
MINIMUM_GAP = 32
MARKER_GAP = 118
FOOTER_HEIGHT = 76
BOTTOM_PADDING = 74

BACKGROUND_TOP = "#1B1534"
BACKGROUND_MIDDLE = "#21183B"
BACKGROUND_BOTTOM = "#0B1021"
ROAD_DARK = "#171525"
ROAD_MAIN = "#443267"
ROAD_DASH = "#F1C232"
CARD_BACKGROUND = "#171526"
CARD_BORDER = "#514465"
WHITE = "#F3EFF7"
MUTED = "#BEB2CD"
MONEY = "#F0C43D"
BRONZE = "#C87335"
BRONZE_LIGHT = "#F0B06B"
PURPLE = "#C14CF1"
CONNECTOR = CARD_BORDER
PILLOW = "#0A8787"
PAYMENT_BORDER = "#9A78C5"
TAX_BORDER = "#7895B8"
WARNING_BORDER = "#D76A67"

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"


@dataclass(frozen=True)
class TimelineItem:
    kind: str
    key: str
    months: Decimal
    title: str
    amount: Decimal | None = None
    level: int | None = None
    side: str | None = None
    color: str | None = None
    road_amplitude: int | None = None
    due_date: date | None = None
    border_color: str | None = None

    @property
    def height(self) -> int:
        if self.kind == "level":
            return LEVEL_HEIGHT
        return SHORT_CARD_HEIGHT if self.amount is None else CARD_HEIGHT


@dataclass(frozen=True)
class Placement:
    item: TimelineItem
    top: int
    road_x: int


PROFILE_LABELS = {
    "stable": "Стабильный профиль",
    "piecework": "Сдельный профиль",
    "cyclic": "Циклический профиль",
}


LEVEL_DESCRIPTIONS = {
    "stable": {
        1: "ФОРМИРОВАНИЕ МИНИМАЛЬНОЙ ПОДУШКИ",
        2: "ПОГАШЕНИЕ ДОЛГОВ",
        3: "ФОРМИРОВАНИЕ ФОРС-МАЖОРНОЙ ПОДУШКИ",
        4: "ИНВЕСТИЦИИ И ЦЕЛИ",
    },
    "piecework": {
        1: "ФОРМИРОВАНИЕ МИНИМАЛЬНОЙ ПОДУШКИ",
        2: "ПОГАШЕНИЕ ДОЛГОВ",
        3: "ФОРМИРОВАНИЕ ФОРС-МАЖОРНОЙ ПОДУШКИ",
        4: "МИНИМАЛЬНЫЙ СЛОЙ СТАБИЛИЗАТОРА",
        5: "ПОЛНЫЙ РАЗМЕР СТАБИЛИЗАТОРА",
        6: "ИНВЕСТИЦИИ И ЦЕЛИ",
    },
    "cyclic": {
        1: "ФОРМИРОВАНИЕ МИНИМАЛЬНОЙ ПОДУШКИ",
        2: "ПОГАШЕНИЕ ДОЛГОВ",
        3: "МИНИМАЛЬНЫЙ СЛОЙ ФОНДА ЗАРПЛАТЫ",
        4: "ПОЛНЫЙ РАЗМЕР ФОНДА ЗАРПЛАТЫ",
        5: "ФОРМИРОВАНИЕ ФОРС-МАЖОРНОЙ ПОДУШКИ",
        6: "ИНВЕСТИЦИИ И ЦЕЛИ",
    },
}


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    filename = "PTSans-Bold.ttf" if bold else "PTSans-Regular.ttf"
    return ImageFont.truetype(str(FONT_DIR / filename), size)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _mix(first: str, second: str, amount: float) -> str:
    a, b = _hex_rgb(first), _hex_rgb(second)
    rgb = tuple(round(x * (1 - amount) + y * amount) for x, y in zip(a, b))
    return "#" + "".join(f"{component:02x}" for component in rgb)


def _money(value: Decimal) -> str:
    return f"{fmt_money(Decimal(value))} ₽"


def _plural(value: int, one: str, few: str, many: str) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return one
    if value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
        return few
    return many


def _duration(months: Decimal) -> str:
    value = max(Decimal("0"), Decimal(months))
    lower = int(value.to_integral_value(rounding=ROUND_FLOOR))
    upper = int(value.to_integral_value(rounding=ROUND_CEILING))

    def whole_duration(total_months: int) -> str:
        if total_months <= 0:
            return "0 месяцев"
        years, rest = divmod(total_months, 12)
        parts: list[str] = []
        if years:
            parts.append(f"{years} {_plural(years, 'год', 'года', 'лет')}")
        if rest:
            parts.append(f"{rest} {_plural(rest, 'месяц', 'месяца', 'месяцев')}")
        return " и ".join(parts)

    if lower == upper:
        return whole_duration(lower)
    if lower == 0:
        return "1 месяц"
    lower_years, lower_months = divmod(lower, 12)
    upper_years, upper_months = divmod(upper, 12)
    if lower_years == upper_years == 0:
        return f"{lower}–{upper} {_plural(upper, 'месяц', 'месяца', 'месяцев')}"
    if lower_years == upper_years and lower_months and upper_months:
        years_text = f"{lower_years} {_plural(lower_years, 'год', 'года', 'лет')}"
        months_text = (
            f"{lower_months}–{upper_months} "
            f"{_plural(upper_months, 'месяц', 'месяца', 'месяцев')}"
        )
        return f"{years_text} и {months_text}"
    return f"{whole_duration(lower)} – {whole_duration(upper)}"


def _goal_for_key(allocator: FinancialAllocator, key: str):
    uid = key.partition(":")[2]
    return next(
        (goal for goal in allocator.settings.goals if str(goal.uid) == uid),
        None,
    )


def _reserve_metadata(
    allocator: FinancialAllocator,
    key: str,
) -> tuple[str, Decimal] | None:
    settings = allocator.settings
    values = {
        "pillow:min": ("Минимальная подушка", settings.minimum_reserve_limit),
        "pillow": ("Подушка", settings.force_majeure_limit),
        "salary:min": ("Фонд зарплаты: минимум", allocator.intercontract_current_life_limit),
        "salary:full": ("Фонд зарплаты: полный", allocator.intercontract_current_limit),
        "stabilizer:min": ("Стабилизатор: минимум", settings.stabilizer_life_limit),
        "stabilizer:full": ("Стабилизатор: полный", settings.stabilizer_full_limit),
    }
    return values.get(key)


def financial_path_items(
    allocator: FinancialAllocator,
    result: dict[str, Any],
) -> list[TimelineItem]:
    """Build current-and-future items in chronological order."""
    milestones = list(result.get("milestones", ()))
    starting_level = int(result.get("starting_mode", allocator.active_mode()))
    level_three_month = Decimal("0") if starting_level >= 3 else None
    for milestone in milestones:
        if milestone.key == "level:3":
            level_three_month = Decimal(milestone.months)
            break

    events: list[TimelineItem] = []
    for milestone in milestones:
        key = str(milestone.key)
        months = Decimal(milestone.months)
        if key.startswith("level:"):
            level = int(key.partition(":")[2])
            if level <= starting_level:
                continue
            events.append(TimelineItem(
                kind="level",
                key=key,
                months=months,
                title=LEVEL_DESCRIPTIONS[allocator.profile_id][level],
                level=level,
            ))
            continue

        metadata = _reserve_metadata(allocator, key)
        if metadata is not None:
            title, amount = metadata
            events.append(TimelineItem(
                kind="reserve",
                key=key,
                months=months,
                title=title,
                amount=Decimal(amount),
            ))
            continue

        if key.startswith("goal:"):
            goal = _goal_for_key(allocator, key)
            if goal is None or goal.full_target_amount is None:
                continue
            # Goals belong to level 3 and above. A defensive visual gate keeps
            # them above the third-level plaque even for legacy data.
            if level_three_month is not None:
                months = max(months, level_three_month)
            events.append(TimelineItem(
                kind="goal",
                key=key,
                months=months,
                title=f"Цель «{goal_display_name(goal.name, False)}»",
                amount=Decimal(goal.full_target_amount),
                color=position_color(goal),
            ))
            continue

        if milestone.kind == "debt":
            events.append(TimelineItem(
                kind="debt",
                key=key,
                months=months,
                title=str(milestone.label),
            ))
            continue

        if milestone.kind in {"payment", "tax"}:
            events.append(TimelineItem(
                kind=milestone.kind,
                key=key,
                months=months,
                title=str(milestone.label),
                amount=Decimal(getattr(milestone, "amount", 0)),
                due_date=getattr(milestone, "due_date", None),
                border_color=(
                    TAX_BORDER if milestone.kind == "tax" else PAYMENT_BORDER
                ),
            ))

    shortfall = result.get("debt_payment_shortfall")
    if shortfall:
        events.append(TimelineItem(
            kind="blocker",
            key="debts:shortfall",
            months=Decimal(shortfall["month"]),
            title="Платежи по долгам не покрываются",
            amount=Decimal(shortfall["amount"]),
            border_color=WARNING_BORDER,
        ))

    for shortfall in result.get("obligation_shortfalls", ()):
        kind = str(shortfall.get("kind", "payment"))
        events.append(TimelineItem(
            kind=f"{kind}_blocker",
            key=f"{shortfall['key']}:shortfall",
            months=Decimal(shortfall["month"]),
            title=str(shortfall["title"]),
            amount=Decimal(shortfall["amount"]),
            due_date=shortfall.get("due_date"),
            border_color=WARNING_BORDER,
        ))

    def event_priority(item: TimelineItem) -> tuple[Decimal, int, str]:
        # A reserve or the final debt card creates the next cup. The event is
        # therefore drawn before the level plaque at an equal forecast time.
        priority = 2 if item.kind == "goal" else 1 if item.kind == "level" else 0
        return item.months, priority, item.key

    events.sort(key=event_priority)
    with_sides: list[TimelineItem] = []
    event_index = 0
    road_amplitudes = (450, 140, 210, 260, 170, 230)
    for item in events:
        if item.kind == "level":
            with_sides.append(item)
            continue
        side = "left" if event_index % 2 == 0 else "right"
        amplitude = road_amplitudes[event_index % len(road_amplitudes)]
        event_index += 1
        with_sides.append(TimelineItem(**{
            **item.__dict__,
            "side": side,
            "road_amplitude": amplitude,
        }))
    return with_sides


def _fit_font(draw, text: str, maximum_width: int, size: int, minimum: int, *, bold=True):
    while size > minimum:
        candidate = _font(size, bold)
        if draw.textlength(text, font=candidate) <= maximum_width:
            return candidate
        size -= 1
    return _font(minimum, bold)


def _shadowed_round_rect(image, box, radius, *, fill, outline, width=3, blur=12):
    from PIL import Image, ImageDraw, ImageFilter

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shifted = (box[0], box[1] + 7, box[2], box[3] + 7)
    shadow_draw.rounded_rectangle(shifted, radius=radius, fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(shadow)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _smooth_points(points: list[tuple[float, float]], steps: int = 18):
    if len(points) < 2:
        return points
    extended = [points[0], *points, points[-1]]
    result: list[tuple[float, float]] = []
    for index in range(1, len(extended) - 2):
        p0, p1, p2, p3 = extended[index - 1:index + 3]
        for step in range(steps):
            t = step / steps
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            result.append((x, y))
    result.append(points[-1])
    return result


def _rounded_road_points(points: list[tuple[float, float]], steps: int = 36):
    """Build alternating rounded lobes around the centre line.

    Every financial event is the outer point of its own half-oval. Between
    events the road returns to the centre, so two neighbouring lobes form a
    soft figure eight instead of a diagonal zigzag. The final two points are
    the current-position marker and the centre of the current-level plaque.
    """
    if len(points) < 2:
        return points

    if len(points) == 2:
        start, end = points
        vertical = end[1] - start[1]
        return _cubic_points(
            start,
            (start[0], start[1] + vertical * .5),
            (end[0], start[1] + vertical * .5),
            end,
            steps,
        )

    event_points = points[:-2]
    marker, final_point = points[-2:]
    if not event_points:
        return _rounded_road_points([marker, final_point], steps)

    centre_x = marker[0]
    if len(event_points) > 1:
        top_span = (event_points[1][1] - event_points[0][1]) / 2
    else:
        top_span = (marker[1] - event_points[0][1]) / 2
    top_y = max(HEADER_BOTTOM + 24, event_points[0][1] - max(72, top_span))
    centres = [(centre_x, top_y)]
    centres.extend(
        (centre_x, (first[1] + second[1]) / 2)
        for first, second in zip(event_points, event_points[1:])
    )
    centres.append(marker)

    result = [centres[0]]
    # A slightly enlarged quarter-circle handle makes the bends feel broader
    # than a strict geometric ellipse while keeping all joins tangent-smooth.
    handle = .62
    for index, outer in enumerate(event_points):
        start, end = centres[index], centres[index + 1]
        direction = 1 if outer[0] >= centre_x else -1
        radius_x = abs(outer[0] - centre_x)
        upper_radius = max(24, outer[1] - start[1])
        lower_radius = max(24, end[1] - outer[1])

        result.extend(_cubic_points(
            start,
            (centre_x + direction * radius_x * handle, start[1]),
            (outer[0], outer[1] - upper_radius * handle),
            outer,
            steps,
        )[1:])
        result.extend(_cubic_points(
            outer,
            (outer[0], outer[1] + lower_radius * handle),
            (centre_x + direction * radius_x * handle, end[1]),
            end,
            steps,
        )[1:])

    vertical = final_point[1] - marker[1]
    result.extend(_cubic_points(
        marker,
        (marker[0], marker[1] + vertical * .45),
        (final_point[0], marker[1] + vertical * .55),
        final_point,
        steps,
    )[1:])
    return result


def _stroke_intersects_exclusions(points, exclusions):
    """Return true when any part of a complete dash enters an icon gap."""
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        squared_length = dx * dx + dy * dy
        for centre_x, centre_y, radius in exclusions:
            if squared_length <= 0:
                nearest_x, nearest_y = start
            else:
                projection = (
                    (centre_x - start[0]) * dx
                    + (centre_y - start[1]) * dy
                ) / squared_length
                projection = min(1.0, max(0.0, projection))
                nearest_x = start[0] + dx * projection
                nearest_y = start[1] + dy * projection
            if (
                (nearest_x - centre_x) ** 2
                + (nearest_y - centre_y) ** 2
                < radius ** 2
            ):
                return True
    return False


def _draw_dashed_line(
    draw,
    points,
    *,
    fill,
    width,
    dash=22,
    gap=18,
    exclusions=(),
):
    drawing = True
    remaining = float(dash)
    current_dash = []
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = hypot(dx, dy)
        if length <= 0:
            continue
        travelled = 0.0
        while travelled < length:
            segment = min(remaining, length - travelled)
            ratio_a = travelled / length
            ratio_b = (travelled + segment) / length
            if drawing:
                candidate_start = (
                    start[0] + dx * ratio_a,
                    start[1] + dy * ratio_a,
                )
                candidate_end = (
                    start[0] + dx * ratio_b,
                    start[1] + dy * ratio_b,
                )
                if not current_dash:
                    current_dash.append(candidate_start)
                current_dash.append(candidate_end)
            travelled += segment
            remaining -= segment
            if remaining <= 0.01:
                if drawing:
                    if (
                        len(current_dash) >= 2
                        and not _stroke_intersects_exclusions(
                            current_dash,
                            exclusions,
                        )
                    ):
                        draw.line(current_dash, fill=fill, width=width, joint="curve")
                    current_dash = []
                drawing = not drawing
                remaining = float(dash if drawing else gap)


def _draw_rounded_path(draw, points, *, fill, width):
    """Draw a continuous wide road without segment seams."""
    draw.line(points, fill=fill, width=width, joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _cubic_points(start, control_a, control_b, end, steps: int = 18):
    points = []
    for index in range(1, steps + 1):
        t = index / steps
        reverse = 1 - t
        points.append((
            reverse ** 3 * start[0]
            + 3 * reverse ** 2 * t * control_a[0]
            + 3 * reverse * t ** 2 * control_b[0]
            + t ** 3 * end[0],
            reverse ** 3 * start[1]
            + 3 * reverse ** 2 * t * control_a[1]
            + 3 * reverse * t ** 2 * control_b[1]
            + t ** 3 * end[1],
        ))
    return points


def _quadratic_points(start, control, end, steps: int = 16):
    points = []
    for index in range(1, steps + 1):
        t = index / steps
        reverse = 1 - t
        points.append((
            reverse ** 2 * start[0] + 2 * reverse * t * control[0] + t ** 2 * end[0],
            reverse ** 2 * start[1] + 2 * reverse * t * control[1] + t ** 2 * end[1],
        ))
    return points


def _transform_points(points, x: int, y: int, scale: float):
    return [(x + px * scale, y + py * scale) for px, py in points]


def _reserve_dash_cutout(image_size, placements):
    """Mask only the transparent cavities of reserve icons."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", image_size, 0)
    draw = ImageDraw.Draw(mask)
    for placement in placements:
        item = placement.item
        if item.kind == "level":
            continue
        x = placement.road_x
        y = placement.top + item.height / 2
        if item.key.startswith("pillow"):
            scale = .70
            inner = [(0, -49)]
            inner += _cubic_points((0, -49), (25, -30), (42, -26), (52, -22))
            inner.append((52, 4))
            inner += _cubic_points((52, 4), (52, 37), (29, 57), (0, 73))
            inner += _cubic_points((0, 73), (-29, 57), (-52, 37), (-52, 4))
            inner.append((-52, -22))
            inner += _cubic_points((-52, -22), (-42, -26), (-25, -30), (0, -49))
            draw.polygon(_transform_points(inner, x, y, scale), fill=255)
        elif item.key.startswith("stabilizer:"):
            scale = .80
            vessel = [(-13, -59), (-13, -22)]
            vessel += _cubic_points((-13, -22), (-36, -14), (-48, 7), (-48, 30))
            vessel += _cubic_points((-48, 30), (-48, 56), (-27, 70), (0, 70))
            vessel += _cubic_points((0, 70), (27, 70), (48, 56), (48, 30))
            vessel += _cubic_points((48, 30), (48, 7), (36, -14), (13, -22))
            vessel.extend(((13, -59), (-13, -59)))
            draw.polygon(_transform_points(vessel, x, y, scale), fill=255)
            draw.rounded_rectangle(
                (x - 19 * scale, y - 72 * scale,
                 x + 19 * scale, y - 62 * scale),
                radius=round(3 * scale),
                fill=255,
            )
        elif item.key.startswith("salary:"):
            scale = .76
            draw.rounded_rectangle(
                (x - 40 * scale, y - 50 * scale,
                 x + 40 * scale, y + 58 * scale),
                radius=round(14 * scale),
                fill=255,
            )
    return mask


def _paint_gradient(image, mask, top_color: str, bottom_color: str) -> None:
    from PIL import Image, ImageDraw

    bounds = mask.getbbox()
    if bounds is None:
        return
    top_rgb, bottom_rgb = _hex_rgb(top_color), _hex_rgb(bottom_color)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    height = max(1, bounds[3] - bounds[1] - 1)
    for row in range(bounds[1], bounds[3]):
        ratio = (row - bounds[1]) / height
        color = tuple(
            round(a * (1 - ratio) + b * ratio)
            for a, b in zip(top_rgb, bottom_rgb)
        )
        layer_draw.line((bounds[0], row, bounds[2], row), fill=(*color, 255))
    image.alpha_composite(Image.composite(layer, Image.new("RGBA", image.size), mask))


def _draw_star(image, x: int, y: int, color: str):
    from PIL import Image, ImageDraw

    scale = .84
    local = [
        (0, -48), (12, -16), (47, -15), (20, 6), (29, 41),
        (0, 22), (-29, 41), (-20, 6), (-47, -15), (-12, -16),
    ]
    points = _transform_points(local, x, y, scale)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    _paint_gradient(
        image,
        mask,
        _mix(color, "#FFFFFF", .45),
        _mix(color, "#B56A00", .28),
    )
    draw = ImageDraw.Draw(image)
    outline = _mix(color, "#FFFFFF", .52)
    draw.line(points + [points[0]], fill=outline, width=3, joint="curve")


def _draw_check(draw, x: int, y: int, scale: float = 1.0):
    draw.line(
        (
            x - 21 * scale,
            y,
            x - 6 * scale,
            y + 15 * scale,
            x + 25 * scale,
            y - 22 * scale,
        ),
        fill="#E9FFFF",
        width=max(3, round(6 * scale)),
        joint="curve",
    )


def _draw_shield(image, x: int, y: int, fraction: Decimal):
    from PIL import Image, ImageDraw

    # Exact paths and .70 scale from the approved stable-profile shield.
    scale = .70
    outer = [(0, -68)]
    outer += _cubic_points((0, -68), (31, -44), (52, -39), (69, -33))
    outer.append((69, 4))
    outer += _cubic_points((69, 4), (69, 49), (37, 76), (0, 94))
    outer += _cubic_points((0, 94), (-37, 76), (-69, 49), (-69, 4))
    outer.append((-69, -33))
    outer += _cubic_points((-69, -33), (-52, -39), (-31, -44), (0, -68))
    outer = _transform_points(outer, x, y, scale)

    inner = [(0, -49)]
    inner += _cubic_points((0, -49), (25, -30), (42, -26), (52, -22))
    inner.append((52, 4))
    inner += _cubic_points((52, 4), (52, 37), (29, 57), (0, 73))
    inner += _cubic_points((0, 73), (-29, 57), (-52, 37), (-52, 4))
    inner.append((-52, -22))
    inner += _cubic_points((-52, -22), (-42, -26), (-25, -30), (0, -49))
    inner = _transform_points(inner, x, y, scale)

    # Only the shield rim is opaque. The inner unfilled area deliberately
    # remains transparent so the actual road underneath is visible.
    rim_mask = Image.new("L", image.size, 0)
    rim_draw = ImageDraw.Draw(rim_mask)
    rim_draw.polygon(outer, fill=255)
    rim_draw.polygon(inner, fill=0)
    rim_layer = Image.new("RGBA", image.size, (*_hex_rgb("#2C2039"), 255))
    image.alpha_composite(
        Image.composite(rim_layer, Image.new("RGBA", image.size), rim_mask)
    )
    draw = ImageDraw.Draw(image)
    draw.line(outer + [outer[0]], fill="#F3CA76", width=5, joint="curve")

    inner_mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(inner_mask).polygon(inner, fill=255)
    if Decimal(fraction) >= Decimal("1"):
        _paint_gradient(image, inner_mask, "#4FC1C4", "#176B87")
    else:
        liquid = [(-50, 20)]
        liquid += _cubic_points((-50, 20), (-24, 13), (24, 13), (50, 20))
        liquid += _cubic_points((50, 20), (43, 43), (24, 60), (0, 73))
        liquid += _cubic_points((0, 73), (-24, 60), (-43, 43), (-50, 20))
        liquid = _transform_points(liquid, x, y, scale)
        liquid_mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(liquid_mask).polygon(liquid, fill=255)
        _paint_gradient(image, liquid_mask, "#4FC1C4", "#176B87")

    draw = ImageDraw.Draw(image)
    draw.line(inner + [inner[0]], fill="#55B6B9", width=3, joint="curve")
    check = _transform_points([(-25, 8), (-7, 26), (28, -16)], x, y, scale)
    draw.line(check, fill="#D5FFFF", width=5, joint="curve")


def _mask_fill_top(mask, fraction: Decimal) -> int:
    bounds = mask.getbbox()
    if bounds is None:
        return 0
    left, top, right, bottom = bounds
    fraction = min(Decimal("1"), max(Decimal("0"), Decimal(fraction)))
    if fraction <= 0:
        return bottom
    if fraction >= 1:
        return top
    rows = []
    for y in range(top, bottom):
        histogram = mask.crop((left, y, right, y + 1)).histogram()
        rows.append(sum(histogram[1:]))
    target = Decimal(sum(rows)) * fraction
    accumulated = 0
    for offset in range(len(rows) - 1, -1, -1):
        accumulated += rows[offset]
        if Decimal(accumulated) >= target:
            return top + offset
    return top


def _reserve_vessel(
    image,
    x: int,
    y: int,
    *,
    shape: str,
    fraction: Decimal,
    threshold: Decimal,
    full: bool,
):
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(image)
    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    if shape == "flask":
        scale = .80
        local = [(-13, -59), (-13, -22)]
        local += _cubic_points((-13, -22), (-36, -14), (-48, 7), (-48, 30))
        local += _cubic_points((-48, 30), (-48, 56), (-27, 70), (0, 70))
        local += _cubic_points((0, 70), (27, 70), (48, 56), (48, 30))
        local += _cubic_points((48, 30), (48, 7), (36, -14), (13, -22))
        local.extend(((13, -59), (-13, -59)))
        vessel = _transform_points(local, x, y, scale)
        mask_draw.polygon(vessel, fill=255)
        outline = "#9A6EC0"
        draw.line(vessel + [vessel[0]], fill="#8F69AA", width=4, joint="curve")
        draw.rounded_rectangle(
            (x - 23 * scale, y - 75 * scale, x + 23 * scale, y - 61 * scale),
            radius=round(4 * scale), outline=outline, width=4,
        )
        dark, light = "#1711A8", "#5171F0"
    else:
        scale = .76
        mask_draw.rounded_rectangle(
            (x - 40 * scale, y - 50 * scale, x + 40 * scale, y + 58 * scale),
            radius=round(14 * scale), fill=255,
        )
        outline = "#9B9DA5"
        draw.rounded_rectangle(
            (x - 45 * scale, y - 56 * scale, x + 45 * scale, y + 64 * scale),
            radius=round(18 * scale), outline=outline, width=4,
        )
        draw.rounded_rectangle(
            (x - 36 * scale, y - 70 * scale, x + 36 * scale, y - 56 * scale),
            radius=round(5 * scale), fill="#E1B84D", outline="#FFE28A", width=3,
        )
        dark, light = "#45464C", "#B9BDC3"

    threshold = min(Decimal("1"), max(Decimal("0"), threshold))
    fraction = min(Decimal("1"), max(Decimal("0"), fraction))
    threshold_top = _mask_fill_top(mask, threshold)
    fill_top = _mask_fill_top(mask, fraction)
    liquid = Image.new("RGBA", image.size, (0, 0, 0, 0))
    liquid_draw = ImageDraw.Draw(liquid)
    bounds = mask.getbbox()
    if bounds is not None:
        if full:
            liquid_draw.rectangle((bounds[0], threshold_top, bounds[2], bounds[3]), fill=dark)
            liquid_draw.rectangle((bounds[0], fill_top, bounds[2], threshold_top), fill=light)
        else:
            liquid_draw.rectangle((bounds[0], fill_top, bounds[2], bounds[3]), fill=dark)
        image.alpha_composite(Image.composite(liquid, Image.new("RGBA", image.size), mask))

    draw = ImageDraw.Draw(image)
    percentage = int((fraction * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = f"{percentage}%"
    font = _font(19 if percentage < 100 else 18, True)
    text_y = y + (44 * scale if not full else 35 * scale)
    draw.text((x, text_y), text, anchor="mm", font=font, fill="#FFFFFF")


def _draw_debt(draw, x: int, y: int):
    """Draw a recognisable credit card with a black magnetic stripe."""
    scale = .88
    draw.rounded_rectangle(
        (x - 52 * scale, y - 34 * scale, x + 52 * scale, y + 34 * scale),
        radius=round(11 * scale), fill="#D72E7D", outline="#FF83BD", width=4,
    )
    draw.rectangle(
        (x - 50 * scale, y - 19 * scale, x + 50 * scale, y - 7 * scale),
        fill="#15131B",
    )
    chip_box = (
        x - 37 * scale,
        y + 2 * scale,
        x - 13 * scale,
        y + 20 * scale,
    )
    draw.rounded_rectangle(
        chip_box,
        radius=round(4 * scale),
        fill="#F2C76B",
        outline="#FFE7A6",
        width=2,
    )
    chip_center_x = (chip_box[0] + chip_box[2]) / 2
    chip_center_y = (chip_box[1] + chip_box[3]) / 2
    draw.line((chip_center_x, chip_box[1] + 2, chip_center_x, chip_box[3] - 2),
              fill="#B77A32", width=1)
    draw.line((chip_box[0] + 2, chip_center_y, chip_box[2] - 2, chip_center_y),
              fill="#B77A32", width=1)
    number_y = y + 9 * scale
    for index in range(3):
        left = x + (-4 + index * 16) * scale
        draw.rounded_rectangle(
            (left, number_y, left + 10 * scale, number_y + 3 * scale),
            radius=1,
            fill="#FFD1E7",
        )
    draw.rounded_rectangle(
        (x + 28 * scale, y + 21 * scale, x + 44 * scale, y + 25 * scale),
        radius=1,
        fill="#EF9AC3",
    )


def _draw_planned_payment(draw, x: int, y: int, *, warning: bool = False):
    """Draw a compact receipt with a calendar tab and ruble mark."""
    border = WARNING_BORDER if warning else PAYMENT_BORDER
    fill = "#342943" if not warning else "#402426"
    draw.rounded_rectangle(
        (x - 42, y - 52, x + 42, y + 48),
        radius=10,
        fill=fill,
        outline=border,
        width=4,
    )
    draw.rounded_rectangle(
        (x - 28, y - 64, x + 28, y - 44),
        radius=6,
        fill="#B89AD8" if not warning else "#E28B87",
        outline="#E4D5F2" if not warning else "#FFD0CC",
        width=2,
    )
    draw.text((x, y - 4), "₽", anchor="mm", font=_font(34, True), fill=WHITE)
    for offset in (-22, 0, 22):
        draw.line((x - 25, y + 27 + offset / 10, x + 25, y + 27 + offset / 10),
                  fill="#8C7B9E", width=2)


def _draw_tax_obligation(draw, x: int, y: int, *, warning: bool = False):
    """Draw the existing tax metaphor as a small building with columns."""
    border = WARNING_BORDER if warning else TAX_BORDER
    fill = "#273345" if not warning else "#402426"
    roof = [(x, y - 58), (x - 52, y - 27), (x + 52, y - 27)]
    draw.polygon(roof, fill=fill, outline=border)
    draw.line(roof + [roof[0]], fill=border, width=4, joint="curve")
    draw.rectangle((x - 47, y - 24, x + 47, y + 42), fill=fill)
    for column_x in (x - 30, x - 10, x + 10, x + 30):
        draw.rounded_rectangle(
            (column_x - 5, y - 19, column_x + 5, y + 34),
            radius=3,
            fill="#A9BBD0" if not warning else "#D99A96",
        )
    draw.line((x - 52, y + 43, x + 52, y + 43), fill=border, width=6)


def _draw_pin(draw, x: int, y: int):
    scale = .72
    local = [(0, -45)]
    local += _cubic_points((0, -45), (28, -45), (47, -25), (47, 0))
    local += _cubic_points((47, 0), (47, 30), (17, 57), (0, 80))
    local += _cubic_points((0, 80), (-17, 57), (-47, 30), (-47, 0))
    local += _cubic_points((-47, 0), (-47, -25), (-28, -45), (0, -45))
    points = _transform_points(local, x, y, scale)
    draw.polygon(points, fill="#9A3FD1")
    draw.line(points + [points[0]], fill="#E3A4FF", width=4, joint="curve")
    radius = 17 * scale
    draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                 fill="#4B1C78", outline="#F4D9FF", width=4)


def _draw_trophy(draw, x: int, y: int, number: int):
    from PIL import Image, ImageDraw

    # Exact approved cup, including the .42 plaque scale.
    scale = .42
    body = [(-32, -27), (32, -27), (32, -7)]
    body += _cubic_points((32, -7), (32, 22), (13, 39), (0, 39))
    body += _cubic_points((0, 39), (-13, 39), (-32, 22), (-32, -7))
    body.append((-32, -27))
    points = _transform_points(body, x, y, scale)

    image = draw._image
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    _paint_gradient(image, mask, "#F0B06B", "#8F461F")
    draw = ImageDraw.Draw(image)
    draw.line(points, fill="#F2AE67", width=2, joint="curve")

    left = [(-31, -18), (-47, -18), (-47, -7)]
    left += _cubic_points((-47, -7), (-47, 13), (-34, 24), (-21, 24))
    right = [(31, -18), (47, -18), (47, -7)]
    right += _cubic_points((47, -7), (47, 13), (34, 24), (21, 24))
    draw.line(_transform_points(left, x, y, scale), fill="#C66D31", width=4, joint="curve")
    draw.line(_transform_points(right, x, y, scale), fill="#C66D31", width=4, joint="curve")
    draw.line(_transform_points([(0, 39), (0, 57)], x, y, scale),
              fill="#D88343", width=4)
    draw.line(_transform_points([(-23, 63), (23, 63)], x, y, scale),
              fill="#D88343", width=4)
    draw.text((x, y + 1), str(number), anchor="mm", font=_font(19, True), fill="#FFF0D5")


def _draw_level_plaque(image, top: int, level: int, description: str, *, current: bool):
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    box = (OUTER_MARGIN, top, WIDTH - OUTER_MARGIN, top + LEVEL_HEIGHT)
    _shadowed_round_rect(
        image,
        box,
        17,
        fill="#261C1D",
        outline="#B77842",
        width=3,
    )
    draw = ImageDraw.Draw(image)
    cup_x, center_y = 116, top + LEVEL_HEIGHT / 2
    # The trophy's stem and base extend farther down than its handles extend
    # upward, so its drawing origin must sit above the plaque's centre for the
    # complete icon to be optically centred.
    _draw_trophy(draw, cup_x, center_y - 8, level)
    draw.text((162, center_y), f"{level}-Й УРОВЕНЬ", anchor="lm",
              font=_font(26, True), fill=BRONZE_LIGHT)
    draw.line((363, top + 15, 363, top + LEVEL_HEIGHT - 15),
              fill="#7A5438", width=2)
    title_font = _fit_font(draw, description, 710, 23, 15, bold=True)
    draw.text((396, center_y), description, anchor="lm", font=title_font,
              fill="#E7D8C8")


def _event_fraction(allocator: FinancialAllocator, item: TimelineItem) -> tuple[Decimal, Decimal, bool]:
    if item.key == "stabilizer:min":
        full = allocator.settings.stabilizer_full_limit
        fraction = item.amount / full if full > 0 else Decimal("0")
        return fraction, fraction, False
    if item.key == "stabilizer:full":
        full = allocator.settings.stabilizer_full_limit
        threshold = allocator.settings.stabilizer_life_limit / full if full > 0 else Decimal("0")
        return Decimal("1"), threshold, True
    if item.key == "salary:min":
        full = allocator.intercontract_current_limit
        fraction = item.amount / full if full > 0 else Decimal("0")
        return fraction, fraction, False
    if item.key == "salary:full":
        full = allocator.intercontract_current_limit
        threshold = allocator.intercontract_current_life_limit / full if full > 0 else Decimal("0")
        return Decimal("1"), threshold, True
    return Decimal("1"), Decimal("1"), True


def _draw_event_icon(image, allocator: FinancialAllocator, item: TimelineItem, x: int, y: int):
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    if item.kind == "goal":
        _draw_star(image, x, y, item.color or MONEY)
    elif item.kind in {"debt", "blocker"}:
        _draw_debt(draw, x, y)
    elif item.kind in {"payment", "payment_blocker"}:
        _draw_planned_payment(
            draw,
            x,
            y,
            warning=item.kind.endswith("_blocker"),
        )
    elif item.kind in {"tax", "tax_blocker"}:
        _draw_tax_obligation(
            draw,
            x,
            y,
            warning=item.kind.endswith("_blocker"),
        )
    elif item.key == "pillow:min":
        full = allocator.settings.force_majeure_limit
        fraction = item.amount / full if full > 0 else Decimal("0")
        _draw_shield(image, x, y, fraction)
    elif item.key == "pillow":
        _draw_shield(image, x, y, Decimal("1"))
    elif item.key.startswith("stabilizer:"):
        fraction, threshold, full = _event_fraction(allocator, item)
        _reserve_vessel(image, x, y, shape="flask", fraction=fraction,
                        threshold=threshold, full=full)
    elif item.key.startswith("salary:"):
        fraction, threshold, full = _event_fraction(allocator, item)
        _reserve_vessel(image, x, y, shape="jar", fraction=fraction,
                        threshold=threshold, full=full)


def _draw_event_card(image, item: TimelineItem, top: int, road_x: int):
    from PIL import ImageDraw

    left = OUTER_MARGIN if item.side == "left" else WIDTH - OUTER_MARGIN - CARD_WIDTH
    right = left + CARD_WIDTH
    bottom = top + item.height
    icon_y = top + item.height // 2
    draw = ImageDraw.Draw(image)
    border_color = item.border_color or CARD_BORDER
    if item.side == "left":
        draw.line((right, icon_y, road_x - 58, icon_y), fill=border_color, width=3)
    else:
        draw.line((road_x + 58, icon_y, left, icon_y), fill=border_color, width=3)
    _shadowed_round_rect(
        image,
        (left, top, right, bottom),
        24,
        fill=CARD_BACKGROUND,
        outline=border_color,
    )
    draw = ImageDraw.Draw(image)
    text_x = left + 32
    title_font = _fit_font(draw, item.title, CARD_WIDTH - 64, 27, 18, bold=True)
    if item.kind == "blocker":
        duration = "срок рассчитать нельзя"
    elif item.kind.endswith("_blocker"):
        duration = "текущий план не выполняется"
    elif item.kind in {"payment", "tax"}:
        duration = f"будет собрано через {_duration(item.months)}"
    else:
        duration = f"через {_duration(item.months)}"
    duration_font = _fit_font(
        draw,
        duration,
        CARD_WIDTH - 64,
        26,
        19,
        bold=True,
    )
    amount_font = _font(19)

    def text_height(text, font):
        box = draw.textbbox((0, 0), text, font=font, anchor="lt")
        return box[3] - box[1]

    title_height = text_height(item.title, title_font)
    duration_height = text_height(duration, duration_font)
    if item.amount is not None:
        if item.kind == "blocker":
            amount = f"не хватает {_money(item.amount)} в месяц"
        elif item.kind.endswith("_blocker"):
            deadline = (
                item.due_date.strftime("%d.%m.%Y")
                if item.due_date is not None
                else "сроку"
            )
            amount = f"не хватает {_money(item.amount)} к {deadline}"
        else:
            amount = _money(item.amount)
            if item.due_date is not None:
                amount += f" · до {item.due_date.strftime('%d.%m.%Y')}"
        amount_font = _fit_font(
            draw,
            amount,
            CARD_WIDTH - 64,
            19,
            15,
            bold=False,
        )
        amount_height = text_height(amount, amount_font)
        line_gap = 14
        block_height = (
            title_height + amount_height + duration_height + line_gap * 2
        )
        line_y = top + (item.height - block_height) / 2
        draw.text((text_x, line_y), item.title, anchor="lt",
                  font=title_font, fill=WHITE)
        line_y += title_height + line_gap
        draw.text((text_x, line_y), amount, anchor="lt",
                  font=amount_font, fill=MUTED)
        line_y += amount_height + line_gap
    else:
        line_gap = 18
        block_height = title_height + duration_height + line_gap
        line_y = top + (item.height - block_height) / 2
        draw.text((text_x, line_y), item.title, anchor="lt",
                  font=title_font, fill=WHITE)
        line_y += title_height + line_gap
    draw.text((text_x, line_y), duration, anchor="lt",
              font=duration_font, fill=MONEY)


def _layout(items: list[TimelineItem], current_level: int):
    top_order = list(reversed(items))
    placements: list[Placement] = []
    cursor = HEADER_BOTTOM + 20
    for item in top_order:
        amplitude = item.road_amplitude or 0
        road_x = (
            600
            if item.kind == "level"
            else 600 + amplitude if item.side == "left"
            else 600 - amplitude
        )
        placements.append(Placement(item=item, top=cursor, road_x=road_x))
        cursor += item.height + MINIMUM_GAP
    if placements:
        cursor += MARKER_GAP - MINIMUM_GAP
    else:
        cursor += 82
    current_top = cursor
    footer_top = current_top + LEVEL_HEIGHT + MINIMUM_GAP
    height = footer_top + FOOTER_HEIGHT + BOTTOM_PADDING
    return placements, current_top, footer_top, height


def _gradient_image(height: int):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    top, middle, bottom = map(_hex_rgb, (BACKGROUND_TOP, BACKGROUND_MIDDLE, BACKGROUND_BOTTOM))
    middle_y = int(height * .52)
    for y in range(height):
        if y <= middle_y:
            ratio = y / max(1, middle_y)
            color = tuple(round(a * (1 - ratio) + b * ratio) for a, b in zip(top, middle))
        else:
            ratio = (y - middle_y) / max(1, height - 1 - middle_y)
            color = tuple(round(a * (1 - ratio) + b * ratio) for a, b in zip(middle, bottom))
        draw.line((0, y, WIDTH, y), fill=(*color, 255))
    return image


def _draw_level_zones(image, placements: list[Placement], current_top: int, height: int):
    from PIL import Image, ImageDraw

    plaques = sorted(
        [p.top for p in placements if p.item.kind == "level"] + [current_top]
    )
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    boundaries = [HEADER_BOTTOM, *plaques, height - 72]
    colors = ((109, 75, 45, 13), (102, 60, 137, 15))
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        draw.rectangle((54, start, WIDTH - 54, end), fill=colors[index % 2])
    image.alpha_composite(overlay)


def render_financial_path_card(
    allocator: FinancialAllocator,
    result: dict[str, Any],
) -> bytes:
    """Render the current and future financial route as a Telegram PNG."""
    from PIL import Image, ImageDraw

    items = financial_path_items(allocator, result)
    current_level = allocator.active_mode()
    placements, current_top, footer_top, height = _layout(items, current_level)
    image = _gradient_image(height)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((18, 18, WIDTH - 18, height - 18), radius=42,
                           outline="#4B3A68", width=3)
    draw.rounded_rectangle((38, 38, WIDTH - 38, height - 38), radius=34,
                           outline="#31234E", width=2)
    draw.text((72, 94), "МОЙ ФИНАНСОВЫЙ ПУТЬ", anchor="lm",
              font=_font(52, True), fill="#FFD58A")
    subtitle = (
        f"{PROFILE_LABELS[allocator.profile_id]} · "
        f"средний доход {_money(result.get('average_income', allocator.settings.average_income))}"
    )
    draw.text((74, 148), subtitle, anchor="lm", font=_font(24), fill="#C8BDD9")
    draw.line((72, HEADER_BOTTOM, WIDTH - 72, HEADER_BOTTOM), fill="#635675", width=2)

    _draw_level_zones(image, placements, current_top, height)

    road_points: list[tuple[float, float]] = []
    for placement in placements:
        if placement.item.kind != "level":
            road_points.append((placement.road_x, placement.top + placement.item.height / 2))
    marker_y = current_top - 58
    road_points.extend(((600, marker_y), (600, current_top + LEVEL_HEIGHT / 2)))
    if len(road_points) == 1:
        road_points.insert(0, (600, HEADER_BOTTOM + 60))
    smooth = _rounded_road_points(road_points)
    draw = ImageDraw.Draw(image)
    _draw_rounded_path(draw, smooth, fill=ROAD_DARK, width=100)
    _draw_rounded_path(draw, smooth, fill=ROAD_MAIN, width=82)
    dash_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    _draw_dashed_line(
        ImageDraw.Draw(dash_layer),
        smooth,
        fill=ROAD_DASH,
        width=5,
    )
    dash_layer.paste(
        (0, 0, 0, 0),
        mask=_reserve_dash_cutout(image.size, placements),
    )
    image.alpha_composite(dash_layer)

    for placement in placements:
        item = placement.item
        if item.kind == "level":
            _draw_level_plaque(
                image,
                placement.top,
                item.level or 0,
                item.title,
                current=False,
            )
        else:
            _draw_event_card(image, item, placement.top, placement.road_x)
            _draw_event_icon(
                image,
                allocator,
                item,
                placement.road_x,
                placement.top + item.height // 2,
            )

    _draw_pin(ImageDraw.Draw(image), 600, marker_y)
    _draw_level_plaque(
        image,
        current_top,
        current_level,
        LEVEL_DESCRIPTIONS[allocator.profile_id][current_level],
        current=True,
    )

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (OUTER_MARGIN, footer_top, WIDTH - OUTER_MARGIN, footer_top + FOOTER_HEIGHT),
        radius=24,
        fill="#151321",
        outline="#403750",
        width=2,
    )
    center_y = footer_top + FOOTER_HEIGHT / 2
    draw.ellipse((94, center_y - 14, 122, center_y + 14), outline="#8E72C6", width=3)
    draw.text((108, center_y + 1), "i", anchor="mm", font=_font(20, True), fill="#B9A6E5")
    if allocator.profile_id == "piecework":
        footer_font = _font(17)
        draw.text(
            (140, center_y - 12),
            "Прогноз считает, что каждый месяц вы получаете среднюю сумму из настроек.",
            anchor="lm", font=footer_font, fill="#B8AEC9",
        )
        draw.text(
            (140, center_y + 14),
            "Реальные сроки могут отличаться из-за колебаний дохода.",
            anchor="lm", font=footer_font, fill="#B8AEC9",
        )
    else:
        footer = "Будущие сроки пересчитываются после каждого дохода и изменения настроек"
        footer_font = _fit_font(draw, footer, 940, 18, 15, bold=False)
        draw.text((140, center_y), footer, anchor="lm", font=footer_font, fill="#B8AEC9")

    output = BytesIO()
    image.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()
