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


def _tint(image, mask, color: str, opacity: float) -> None:
    """Blend a colour through a mask without flattening the card underneath."""
    from PIL import Image
    image.paste(Image.blend(image, Image.new("RGB", image.size, color), opacity), mask=mask)


def _money(value: Decimal) -> str:
    amount = f"{Decimal(value):,.0f}".replace(",", " ")
    return f"{amount} ₽"


def _clamp(value: Decimal, maximum: Decimal) -> Decimal:
    if maximum <= 0:
        return Decimal("0")
    return min(max(Decimal("0"), Decimal(value)), maximum)


def _shape_masks(shape: str, *, x: int, top: int, bottom: int, width: int, image_size):
    """Return outer and inner masks for a reserve's distinct vessel silhouette."""
    from PIL import Image, ImageDraw

    outer = Image.new("L", image_size)
    inner = Image.new("L", image_size)
    outer_draw, inner_draw = ImageDraw.Draw(outer), ImageDraw.Draw(inner)
    left, right = x - width // 2, x + width // 2
    if shape == "shield":
        outer_draw.polygon(
            [(x, top), (right, top + 42), (right, top + 255), (x, bottom), (left, top + 255), (left, top + 42)],
            fill=255,
        )
        inner_draw.polygon(
            [(x, top + 15), (right - 13, top + 50), (right - 13, top + 248),
             (x, bottom - 18), (left + 13, top + 248), (left + 13, top + 50)], fill=255,
        )
    elif shape == "flask":
        # Long narrow neck, shoulders and a round body.
        neck = width // 4
        outer_draw.rectangle((x - neck, top, x + neck, top + 170), fill=255)
        outer_draw.polygon([(x - neck, top + 140), (left, top + 275), (left, bottom - 112),
                            (right, bottom - 112), (right, top + 275), (x + neck, top + 140)], fill=255)
        outer_draw.ellipse((left, bottom - 245, right, bottom), fill=255)
        inner_neck = neck - 12
        inner_draw.rectangle((x - inner_neck, top + 12, x + inner_neck, top + 174), fill=255)
        inner_draw.polygon([(x - inner_neck, top + 158), (left + 13, top + 283), (left + 13, bottom - 112),
                            (right - 13, bottom - 112), (right - 13, top + 283), (x + inner_neck, top + 158)], fill=255)
        inner_draw.ellipse((left + 13, bottom - 232, right - 13, bottom - 13), fill=255)
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


def _vessel(draw, *, x: int, top: int, bottom: int, width: int, balance: Decimal,
            critical_target: Decimal | None, full_target: Decimal, colors: tuple[str, ...],
            font, name: str, shape: str) -> None:
    """Draw one vessel. A two-level reserve uses a colour per real threshold."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    image = draw._image
    left, right = x - width // 2, x + width // 2
    outer_mask, vessel_mask = _shape_masks(shape, x=x, top=top, bottom=bottom, width=width, image_size=image.size)
    _tint(image, outer_mask.filter(ImageFilter.GaussianBlur(24)), colors[-1], 0.18)
    image.paste("#765D81", mask=outer_mask.filter(ImageFilter.MaxFilter(11)))
    image.paste("#2C2039", mask=outer_mask)
    inner_left, inner_right = left + 12, right - 12
    inner_top, inner_bottom = top + 12, bottom - 12
    usable_height = inner_bottom - inner_top
    balance = _clamp(balance, full_target)
    liquid = Image.new("RGB", image.size)
    liquid_draw = ImageDraw.Draw(liquid)
    fill_mask = Image.new("L", image.size)
    fill_draw = ImageDraw.Draw(fill_mask)
    critical_top = None
    liquid_top = inner_bottom

    if critical_target is None or critical_target <= 0 or critical_target >= full_target:
        fill_top = inner_bottom - int(usable_height * balance / full_target) if full_target > 0 else inner_bottom
        if fill_top < inner_bottom:
            liquid_draw.rectangle((inner_left, fill_top, inner_right, inner_bottom), fill=colors[0])
            fill_draw.rectangle((inner_left, fill_top, inner_right, inner_bottom), fill=255)
            liquid_top = fill_top
    else:
        critical_target = _clamp(critical_target, full_target)
        critical_height = int(usable_height * critical_target / full_target)
        critical_top = inner_bottom - critical_height
        first_fill_top = inner_bottom - int(usable_height * min(balance, critical_target) / full_target)
        if first_fill_top < inner_bottom:
            liquid_draw.rectangle((inner_left, first_fill_top, inner_right, inner_bottom), fill=colors[0])
            fill_draw.rectangle((inner_left, first_fill_top, inner_right, inner_bottom), fill=255)
            liquid_top = first_fill_top
        if balance > critical_target:
            second_fill_top = inner_bottom - int(usable_height * balance / full_target)
            liquid_draw.rectangle((inner_left, second_fill_top, inner_right, critical_top), fill=colors[1])
            fill_draw.rectangle((inner_left, second_fill_top, inner_right, critical_top), fill=255)
            liquid_top = second_fill_top
    liquid_mask = ImageChops.multiply(vessel_mask, fill_mask)
    image.paste(liquid, mask=liquid_mask)
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
    if critical_top is not None:
        draw.line((left - 20, critical_top, right + 20, critical_top), fill="#E7DCEB", width=3)
        draw.text((right + 28, critical_top - 19), "КМ", font=font(26, True), fill=MUTED)
        draw.text((right + 28, inner_top - 14), "УЖ", font=font(26, True), fill=MUTED)

    draw.text((x, bottom + 34), name, anchor="ma", font=font(38, True), fill=WHITE)
    draw.text((x, bottom + 88), _money(balance), anchor="ma", font=font(34, True), fill=GOLD)
    draw.text((x, bottom + 136), f"из {_money(full_target)}", anchor="ma", font=font(27), fill=MUTED)


def render_reserve_card(profile_id: str, *, pillow_balance: Decimal, pillow_target: Decimal,
                        stabilizer_balance: Decimal = Decimal("0"), stabilizer_critical_target: Decimal = Decimal("0"),
                        stabilizer_full_target: Decimal = Decimal("0"), salary_fund_balance: Decimal = Decimal("0"),
                        salary_fund_critical_target: Decimal = Decimal("0"), salary_fund_full_target: Decimal = Decimal("0")) -> bytes:
    """Render the reserves required by the user's financial profile.

    The filled zones are based on actual money thresholds, rather than fixed
    decorative proportions.
    """
    from PIL import Image, ImageDraw, ImageFont

    vessels = [("Подушка", pillow_balance, None, pillow_target, ("#008080",), "shield")]
    if profile_id in {"piecework", "cyclic"}:
        vessels.append(("Стабилизатор", stabilizer_balance, stabilizer_critical_target,
                        stabilizer_full_target, ("#000080", "#4E77F9"), "flask"))
    if profile_id == "cyclic":
        vessels.append(("Фонд зарплаты", salary_fund_balance, salary_fund_critical_target,
                        salary_fund_full_target, ("#393939", "#A9A9A9"), "jar"))

    image = Image.new("RGB", (1080, 1120), BACKGROUND)
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
    for y in range(1120):
        blend = y / 1119
        red = int(20 * (1 - blend) + 13 * blend)
        green = int(18 * (1 - blend) + 15 * blend)
        blue = int(33 * (1 - blend) + 27 * blend)
        draw.line((0, y, 1080, y), fill=(red, green, blue))
    draw.rounded_rectangle((18, 18, 1062, 1102), radius=38, outline="#3F384E", width=3)
    draw.ellipse((58, 48, 164, 154), fill="#272337")
    draw.polygon([(111, 67), (141, 83), (136, 126), (111, 143), (86, 126), (81, 83)], outline=GOLD, width=5)
    draw.line((97, 105, 107, 116, 128, 91), fill=GOLD, width=6)
    draw.text((194, 48), "ЗАЩИТНЫЕ РЕЗЕРВЫ", font=font(54, True), fill=GOLD)
    draw.text((194, 123), "Заполнение резервов на текущий момент", font=font(30), fill=MUTED)
    draw.line((54, 185, 1026, 185), fill="#51495F", width=3)
    centers = {1: (540,), 2: (340, 740), 3: (220, 540, 860)}[len(vessels)]
    for x, (name, balance, critical, target, colors, shape) in zip(centers, vessels):
        _vessel(draw, x=x, top=245, bottom=745, width=190, balance=balance,
                critical_target=critical, full_target=max(Decimal("0"), Decimal(target)),
                colors=colors, font=font, name=name, shape=shape)

    draw.rounded_rectangle((105, 1006, 975, 1070), radius=30, outline="#3F384E", width=2)
    draw.text((540, 1025), "КМ — критический минимум  ·  УЖ — устойчивая жизнь", anchor="ma", font=font(27), fill=MUTED)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
