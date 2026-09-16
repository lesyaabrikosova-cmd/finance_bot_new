"""PNG card with vertical reserve vessels for Telegram."""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path


FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
BACKGROUND = "#191321"
GOLD = "#F1CD83"
WHITE = "#F9F4ED"
MUTED = "#D0C2D8"
PILLOW_COLOR = "#176B87"
STABILIZER_CRITICAL_COLOR = "#1b3a9d"
STABILIZER_SUSTAINABLE_COLOR = "#3E6FD8"


def _tint(image, mask, color: str, opacity: float) -> None:
    """Blend a colour through a mask without flattening the card underneath."""
    from PIL import Image
    image.paste(Image.blend(image, Image.new("RGB", image.size, color), opacity), mask=mask)


def _money(value: Decimal) -> str:
    amount = f"{Decimal(value):,.0f}".replace(",", " ")
    return f"{amount} ₽"


def _covered_months(balance: Decimal, reserve_target: Decimal, target_months: Decimal) -> int:
    """Return whole months the current balance covers at this reserve level."""
    reserve_target = Decimal(reserve_target)
    target_months = Decimal(target_months)
    if reserve_target <= 0 or target_months <= 0:
        return 0
    monthly_amount = reserve_target / target_months
    return max(0, int(Decimal(balance) // monthly_amount))


def reserve_legend_items(*, stabilizer_balance: Decimal, stabilizer_critical_target: Decimal,
                         stabilizer_full_target: Decimal, stabilizer_months: Decimal,
                         salary_fund_balance: Decimal, salary_fund_critical_target: Decimal,
                         salary_fund_full_target: Decimal, salary_fund_months: Decimal,
                         pillow_balance: Decimal = Decimal("0"), pillow_target: Decimal = Decimal("0"),
                         pillow_months: Decimal = Decimal("0"),
                         pillow_legend_label: str = "Форс-мажор"):
    """Return current coverage for every reserve level the user has configured."""
    # The order is also the visual hierarchy in the two-column legend:
    # light (УЖ) levels on top, dark (КМ) levels underneath; salary fund on
    # the left, stabilizer on the right. Items are permanent, not milestones.
    items = []
    if pillow_target > 0:
        months = _covered_months(pillow_balance, pillow_target, pillow_months)
        items.append((PILLOW_COLOR, f"{pillow_legend_label} — хватит на {months} мес"))
    if salary_fund_full_target > 0:
        months = _covered_months(salary_fund_balance, salary_fund_full_target, salary_fund_months)
        items.append(("#A9A9A9", f"Бытовой резерв — хватит на {months} мес"))
    if stabilizer_full_target > 0:
        months = _covered_months(stabilizer_balance, stabilizer_full_target, stabilizer_months)
        items.append((STABILIZER_SUSTAINABLE_COLOR, f"Бытовой резерв — хватит на {months} мес"))
    if salary_fund_critical_target > 0:
        months = _covered_months(salary_fund_balance, salary_fund_critical_target, salary_fund_months)
        items.append(("#393939", f"Критический Минимум — хватит на {months} мес"))
    if stabilizer_critical_target > 0:
        months = _covered_months(stabilizer_balance, stabilizer_critical_target, stabilizer_months)
        items.append((STABILIZER_CRITICAL_COLOR, f"Критический Минимум — хватит на {months} мес"))
    return items


def _clamp(value: Decimal, maximum: Decimal) -> Decimal:
    if maximum <= 0:
        return Decimal("0")
    return min(max(Decimal("0"), Decimal(value)), maximum)


def _cubic(start, control_a, control_b, end, steps: int = 20):
    """Sample a cubic Bézier curve for smooth Pillow masks."""
    points = []
    for index in range(1, steps + 1):
        t = index / steps
        reverse = 1 - t
        points.append((
            round(reverse ** 3 * start[0] + 3 * reverse ** 2 * t * control_a[0]
                  + 3 * reverse * t ** 2 * control_b[0] + t ** 3 * end[0]),
            round(reverse ** 3 * start[1] + 3 * reverse ** 2 * t * control_a[1]
                  + 3 * reverse * t ** 2 * control_b[1] + t ** 3 * end[1]),
        ))
    return points


def _shape_masks(shape: str, *, x: int, top: int, bottom: int, width: int, image_size):
    """Return outer and inner masks for a reserve's distinct vessel silhouette."""
    from PIL import Image, ImageDraw

    outer = Image.new("L", image_size)
    inner = Image.new("L", image_size)
    outer_draw, inner_draw = ImageDraw.Draw(outer), ImageDraw.Draw(inner)
    left, right = x - width // 2, x + width // 2
    if shape == "shield":
        crest = (x, top + 28)
        outer_points = [crest]
        outer_points += _cubic(crest, (x + 58, top + 74), (right - 45, top + 92), (right, top + 103))
        outer_points += _cubic((right, top + 103), (right, top + 300), (right - 48, bottom - 74), (x, bottom))
        outer_points += _cubic((x, bottom), (left + 48, bottom - 74), (left, top + 300), (left, top + 103))
        outer_points += _cubic((left, top + 103), (left + 45, top + 92), (x - 58, top + 74), crest)
        outer_draw.polygon(outer_points, fill=255)
        inner_left, inner_right = left + 17, right - 17
        inner_crest = (x, top + 50)
        inner_points = [inner_crest]
        inner_points += _cubic(inner_crest, (x + 55, top + 88), (inner_right - 40, top + 105), (inner_right, top + 117))
        inner_points += _cubic((inner_right, top + 117), (inner_right, top + 293),
                               (inner_right - 42, bottom - 82), (x, bottom - 24))
        inner_points += _cubic((x, bottom - 24), (inner_left + 42, bottom - 82),
                               (inner_left, top + 293), (inner_left, top + 117))
        inner_points += _cubic((inner_left, top + 117), (inner_left + 40, top + 105),
                               (x - 55, top + 88), inner_crest)
        inner_draw.polygon(inner_points, fill=255)
    elif shape == "flask":
        # Florence flask: a long narrow neck joined to a geometrically round
        # bulb. The bulb diameter is capped by the available vertical space so
        # widening the two-vessel layout cannot stretch it into an oval.
        neck = max(34, width // 7)
        lip = neck + 11
        bulb_radius = min(width // 2, (bottom - top - 170) // 2)
        bulb_top = bottom - bulb_radius * 2
        bulb_center_y = bottom - bulb_radius
        outer_draw.rounded_rectangle((x - lip, top, x + lip, top + 28), radius=9, fill=255)
        outer_draw.rectangle((x - neck, top + 18, x + neck, bulb_center_y), fill=255)
        outer_draw.ellipse((x - bulb_radius, bulb_top, x + bulb_radius, bottom), fill=255)
        inner_neck = neck - 11
        inner_radius = bulb_radius - 14
        inner_bulb_top = bottom - 14 - inner_radius * 2
        inner_center_y = bottom - 14 - inner_radius
        inner_draw.rectangle((x - inner_neck, top + 31, x + inner_neck, inner_center_y), fill=255)
        inner_draw.ellipse(
            (x - inner_radius, inner_bulb_top, x + inner_radius, bottom - 14),
            fill=255,
        )
    elif shape == "jar":
        # A wide mouth and straight storage-jar body.
        neck = width // 2 - 8
        outer_draw.rounded_rectangle((x - neck, top, x + neck, top + 115), radius=14, fill=255)
        outer_draw.polygon([(x - neck, top + 90), (left, top + 155), (left, bottom - 48),
                            (right, bottom - 48), (right, top + 155), (x + neck, top + 90)], fill=255)
        outer_draw.rounded_rectangle((left, top + 145, right, bottom), radius=30, fill=255)
        inner_neck = neck - 12
        inner_draw.rounded_rectangle((x - inner_neck, top + 12, x + inner_neck, top + 108), radius=8, fill=255)
        inner_draw.polygon([(x - inner_neck, top + 96), (left + 13, top + 160), (left + 13, bottom - 48),
                            (right - 13, bottom - 48), (right - 13, top + 160), (x + inner_neck, top + 96)], fill=255)
        inner_draw.rounded_rectangle((left + 13, top + 155, right - 13, bottom - 13), radius=20, fill=255)
    else:
        raise ValueError(f"Unknown reserve vessel shape: {shape}")
    return outer, inner


def _fill_top_for_fraction(mask, fraction: Decimal) -> int:
    """Find the liquid surface whose filled mask area matches ``fraction``.

    A vessel is not a rectangle: the same vertical step can represent very
    different amounts in a wide bulb and in a narrow neck. Counting the
    silhouette pixels makes the displayed percentage proportional to the
    vessel's visible capacity instead of its height.
    """
    bounds = mask.getbbox()
    if bounds is None:
        return 0
    left, top, right, bottom = bounds
    fraction = min(max(Decimal("0"), Decimal(fraction)), Decimal("1"))
    if fraction <= 0:
        return bottom
    if fraction >= 1:
        return top

    row_areas = []
    for y in range(top, bottom):
        histogram = mask.crop((left, y, right, y + 1)).histogram()
        row_areas.append(sum(histogram[1:]))
    target_area = Decimal(sum(row_areas)) * fraction
    filled_area = 0
    for offset in range(len(row_areas) - 1, -1, -1):
        filled_area += row_areas[offset]
        if Decimal(filled_area) >= target_area:
            return top + offset
    return top


def _vessel(draw, *, x: int, top: int, bottom: int, width: int, balance: Decimal,
            critical_target: Decimal | None, full_target: Decimal, colors: tuple[str, ...],
            font, name: str, shape: str) -> None:
    """Draw one vessel. A two-level reserve uses a colour per real threshold."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    image = draw._image
    left, right = x - width // 2, x + width // 2
    outer_mask, vessel_mask = _shape_masks(shape, x=x, top=top, bottom=bottom, width=width, image_size=image.size)
    _tint(image, outer_mask.filter(ImageFilter.GaussianBlur(24)), colors[-1], 0.18)
    outline_color = {"shield": "#F3CA76", "flask": "#9B78BE", "jar": "#C8C7D7"}[shape]
    image.paste(outline_color, mask=outer_mask.filter(ImageFilter.MaxFilter(11)))
    image.paste("#2C2039", mask=outer_mask)
    inner_left, inner_right = left + 12, right - 12
    inner_top, inner_bottom = top + 12, bottom - 12
    balance = _clamp(balance, full_target)
    liquid = Image.new("RGB", image.size)
    liquid_draw = ImageDraw.Draw(liquid)
    fill_mask = Image.new("L", image.size)
    fill_draw = ImageDraw.Draw(fill_mask)
    critical_top = None
    liquid_top = inner_bottom

    if critical_target is None or critical_target <= 0 or critical_target >= full_target:
        fill_fraction = balance / full_target if full_target > 0 else Decimal("0")
        fill_top = _fill_top_for_fraction(vessel_mask, fill_fraction)
        if fill_top < inner_bottom:
            liquid_draw.rectangle((inner_left, fill_top, inner_right, inner_bottom), fill=colors[0])
            fill_draw.rectangle((inner_left, fill_top, inner_right, inner_bottom), fill=255)
            liquid_top = fill_top
    else:
        critical_target = _clamp(critical_target, full_target)
        critical_top = _fill_top_for_fraction(vessel_mask, critical_target / full_target)
        first_fill_top = _fill_top_for_fraction(vessel_mask, min(balance, critical_target) / full_target)
        if first_fill_top < inner_bottom:
            liquid_draw.rectangle((inner_left, first_fill_top, inner_right, inner_bottom), fill=colors[0])
            fill_draw.rectangle((inner_left, first_fill_top, inner_right, inner_bottom), fill=255)
            liquid_top = first_fill_top
        if balance > critical_target:
            second_fill_top = _fill_top_for_fraction(vessel_mask, balance / full_target)
            liquid_draw.rectangle((inner_left, second_fill_top, inner_right, critical_top), fill=colors[1])
            fill_draw.rectangle((inner_left, second_fill_top, inner_right, critical_top), fill=255)
            liquid_top = second_fill_top
    liquid_mask = ImageChops.multiply(vessel_mask, fill_mask)
    image.paste(liquid, mask=liquid_mask)
    if shape == "shield":
        inner_edge = ImageChops.subtract(vessel_mask.filter(ImageFilter.MaxFilter(9)), vessel_mask)
        _tint(image, inner_edge, "#55B6B9", 0.75)
    if liquid_top < inner_bottom:
        # A translucent surface and a narrow gloss make the fill read as liquid,
        # rather than as a flat geometric block.
        surface = Image.new("L", image.size)
        ImageDraw.Draw(surface).ellipse(
            (left + 16, liquid_top - 13, right - 16, liquid_top + 18), fill=130,
        )
        _tint(image, ImageChops.multiply(surface, vessel_mask), "#EAF9FF", 0.45)
        if liquid_top + 22 < inner_bottom - 38:
            gloss = Image.new("L", image.size)
            ImageDraw.Draw(gloss).rounded_rectangle(
                (left + 27, liquid_top + 22, left + 40, inner_bottom - 38), radius=7, fill=45,
            )
            _tint(image, ImageChops.multiply(gloss, liquid_mask), "#FFFFFF", 0.50)
    percentage = Decimal("0") if full_target <= 0 else balance / full_target * Decimal("100")
    percent_text = f"{int(percentage.quantize(Decimal('1')))}%"
    percent_y = (
        bottom - 145
        if shape == "flask"
        else max(top + 195, min(inner_bottom - 65, (liquid_top + inner_bottom) // 2))
    )
    draw.text((x, percent_y), percent_text, anchor="mm", font=font(50, True), fill=WHITE)

    if shape == "jar":
        # The gold lid is deliberately rendered last so it sits in front of the glass.
        draw.rounded_rectangle((left - 9, top - 4, right + 9, top + 35), radius=13,
                               fill="#B77A2E", outline="#F4C36A", width=4)
        draw.line((left + 3, top + 24, right - 3, top + 24), fill="#E7AD53", width=4)

    draw.text((x, bottom + 34), name, anchor="ma", font=font(38, True), fill=WHITE)
    draw.text((x, bottom + 88), _money(balance), anchor="ma", font=font(34, True), fill=GOLD)
    draw.text((x, bottom + 136), f"из {_money(full_target)}", anchor="ma", font=font(27), fill=MUTED)


def render_reserve_card(profile_id: str, *, pillow_balance: Decimal, pillow_target: Decimal,
                        stabilizer_balance: Decimal = Decimal("0"), stabilizer_critical_target: Decimal = Decimal("0"),
                        stabilizer_full_target: Decimal = Decimal("0"), salary_fund_balance: Decimal = Decimal("0"),
                        salary_fund_critical_target: Decimal = Decimal("0"), salary_fund_full_target: Decimal = Decimal("0"),
                        stabilizer_months: Decimal = Decimal("0"), salary_fund_months: Decimal = Decimal("0"),
                        pillow_months: Decimal = Decimal("0")) -> bytes:
    """Render the reserves required by the user's financial profile.

    The filled zones are based on actual money thresholds, rather than fixed
    decorative proportions.
    """
    from PIL import Image, ImageDraw, ImageFont

    vessels = [("Подушка", pillow_balance, None, pillow_target, (PILLOW_COLOR,), "shield")]
    if profile_id == "piecework":
        vessels.append(("Стабилизатор", stabilizer_balance, stabilizer_critical_target,
                        stabilizer_full_target,
                        (STABILIZER_CRITICAL_COLOR, STABILIZER_SUSTAINABLE_COLOR), "flask"))
    if profile_id == "cyclic":
        vessels = [
            ("Фонд Зарплаты", salary_fund_balance, salary_fund_critical_target,
             salary_fund_full_target, ("#393939", "#A9A9A9"), "jar"),
            ("Подушка", pillow_balance, None, pillow_target, (PILLOW_COLOR,), "shield"),
            ("Стабилизатор", stabilizer_balance, stabilizer_critical_target,
             stabilizer_full_target,
             (STABILIZER_CRITICAL_COLOR, STABILIZER_SUSTAINABLE_COLOR), "flask"),
        ]

    image = Image.new("RGB", (1080, 1200), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts: dict[tuple[int, bool], object] = {}

    def font(size: int, bold: bool = False):
        key = (size, bold)
        if key not in fonts:
            filename = "PTSans-Bold.ttf" if bold else "PTSans-Regular.ttf"
            fonts[key] = ImageFont.truetype(str(FONT_DIR / filename), size)
        return fonts[key]

    # Layered background and frame: the card has a deliberate visual boundary
    # when Telegram places it on a chat background.
    for y in range(1200):
        blend = y / 1199
        red = int(20 * (1 - blend) + 13 * blend)
        green = int(18 * (1 - blend) + 15 * blend)
        blue = int(33 * (1 - blend) + 27 * blend)
        draw.line((0, y, 1080, y), fill=(red, green, blue))
    draw.rounded_rectangle((18, 18, 1062, 1182), radius=38, outline="#3F384E", width=3)
    draw.ellipse((58, 48, 164, 154), fill="#272337")
    draw.polygon([(111, 67), (141, 83), (136, 126), (111, 143), (86, 126), (81, 83)], outline=GOLD, width=5)
    draw.line((97, 105, 107, 116, 128, 91), fill=GOLD, width=6)
    draw.text((194, 48), "ЗАЩИТНЫЕ РЕЗЕРВЫ", font=font(54, True), fill=GOLD)
    draw.text((194, 123), "Заполнение резервов на текущий момент", font=font(30), fill=MUTED)
    draw.line((54, 185, 1026, 185), fill="#51495F", width=3)
    layouts = {
        1: ((540, 380),),
        2: ((300, 360), (770, 360)),
        3: ((200, 245), (540, 300), (875, 300)),
    }[len(vessels)]
    for (x, width), (name, balance, critical, target, colors, shape) in zip(layouts, vessels):
        _vessel(draw, x=x, top=245, bottom=735, width=width, balance=balance,
                critical_target=critical, full_target=max(Decimal("0"), Decimal(target)),
                colors=colors, font=font, name=name, shape=shape)

    legend_items = reserve_legend_items(
        stabilizer_balance=stabilizer_balance,
        stabilizer_critical_target=(
            stabilizer_critical_target if profile_id in {"piecework", "cyclic"} else Decimal("0")
        ),
        stabilizer_full_target=(
            stabilizer_full_target if profile_id in {"piecework", "cyclic"} else Decimal("0")
        ),
        stabilizer_months=stabilizer_months,
        salary_fund_balance=salary_fund_balance,
        salary_fund_critical_target=(salary_fund_critical_target if profile_id == "cyclic" else Decimal("0")),
        salary_fund_full_target=(salary_fund_full_target if profile_id == "cyclic" else Decimal("0")),
        salary_fund_months=salary_fund_months,
        pillow_balance=pillow_balance,
        pillow_target=(
            pillow_target if profile_id in {"stable", "piecework", "debt_level_one"} else Decimal("0")
        ),
        pillow_months=pillow_months,
        pillow_legend_label=("Минимальная подушка" if profile_id == "debt_level_one" else "Форс-мажор"),
    )

    if legend_items:
        legend_frame = (54, 1018, 1026, 1148)
        draw.rounded_rectangle(legend_frame, radius=30, outline="#3F384E", width=2)
        legend_slots = {
            PILLOW_COLOR: (0, 0),
            "#A9A9A9": (0, 0), STABILIZER_SUSTAINABLE_COLOR: (1, 0),
            "#393939": (0, 1), STABILIZER_CRITICAL_COLOR: (1, 1),
        }
        for color, text in legend_items:
            column, row = legend_slots[color]
            center_x = 540 if len(vessels) == 1 else 297 + column * 486
            y = 1067 if len(vessels) == 1 else 1042 + row * 50
            legend_size = 22
            legend_font = font(legend_size)
            while draw.textlength(text, font=legend_font) > 390 and legend_size > 14:
                legend_size -= 1
                legend_font = font(legend_size)
            text_width = draw.textlength(text, font=legend_font)
            item_width = 24 + 18 + text_width
            x = round(center_x - item_width / 2)
            draw.rounded_rectangle((x, y + 3, x + 24, y + 27), radius=5, fill=color)
            draw.text((x + 40, y), text, font=legend_font, fill=WHITE)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
