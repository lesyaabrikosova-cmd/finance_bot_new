"""Stable, perceptually distinct colours for income-type charts."""

from __future__ import annotations

import math
from collections.abc import Iterable


# Euclidean OKLab distance.  At .12, adjacent sectors remain distinct at the
# chart's normal size; small changes in one hue do not pass this threshold.
MIN_INCOME_COLOR_DISTANCE = 0.12

INCOME_COLOR_FAMILIES = (
    ("Красный", "❤️", ("#E68091", "#C9566B", "#F0A6B2", "#F6CDD4")),
    ("Оранжевый", "🧡", ("#E89555", "#C96B2A", "#F1B887", "#F6D4B3")),
    ("Жёлтый", "💛", ("#E5B65B", "#C7922D", "#F0CF8B", "#F6E2B4")),
    ("Зелёный", "💚", ("#69BE98", "#3C9C73", "#9AD7B7", "#C3E9D4")),
    ("Синий", "💙", ("#5584DB", "#315EAF", "#88A9E9", "#B7C9F2")),
    ("Фиолетовый", "💜", ("#9675E5", "#7152C8", "#B59AEC", "#D0C1F4")),
    ("Розовый", "🩷", ("#CE91D1", "#A65BAA", "#E2B5E4", "#EFD5F0")),
    ("Коричневый", "🤎", ("#9A6B4A", "#70482F", "#BC9274", "#D8BBA7")),
    ("Серый", "🩶", ("#8B92A1", "#636B78", "#B2B8C2", "#D0D4DA")),
)

# The first nine automatic assignments use one vetted shade per family.  They
# are pairwise at least ``MIN_INCOME_COLOR_DISTANCE`` apart in OKLab; therefore
# all nine can be assigned before any family needs a second shade.
INCOME_COLOR_PALETTE = (
    "#E68091", "#C96B2A", "#E5B65B", "#69BE98", "#5584DB",
    "#7152C8", "#E2B5E4", "#70482F", "#8B92A1",
)
_EXTENDED_PALETTE = tuple(shade for _, _, shades in INCOME_COLOR_FAMILIES for shade in shades)


def _linear_srgb(channel: float) -> float:
    return channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4


def hex_to_oklab(color: str) -> tuple[float, float, float]:
    """Convert sRGB ``#RRGGBB`` to the perceptually uniform OKLab space."""
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB colour, got {color!r}")
    red, green, blue = (_linear_srgb(int(value[index:index + 2], 16) / 255) for index in (0, 2, 4))
    l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    l, m, s = (math.copysign(abs(item) ** (1 / 3), item) for item in (l, m, s))
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def income_color_distance(first: str, second: str) -> float:
    """Perceptual distance between two colours in OKLab."""
    return math.dist(hex_to_oklab(first), hex_to_oklab(second))


def is_distinct_income_color(candidate: str, used_colors: Iterable[str]) -> bool:
    return all(income_color_distance(candidate, existing) >= MIN_INCOME_COLOR_DISTANCE for existing in used_colors)


def _srgb_from_linear(channel: float) -> float:
    return 12.92 * channel if channel <= .0031308 else 1.055 * channel ** (1 / 2.4) - .055


def _oklch_to_hex(lightness: float, chroma: float, hue: float) -> str | None:
    """Return an in-gamut muted OKLCH colour, or ``None`` outside sRGB."""
    angle = math.radians(hue)
    a, b = chroma * math.cos(angle), chroma * math.sin(angle)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    if any(channel < 0 or channel > 1 for channel in rgb):
        return None
    return "#" + "".join(f"{round(_srgb_from_linear(channel) * 255):02X}" for channel in rgb)


def _generated_candidates() -> Iterable[str]:
    """Deterministic reserve palette, used only after prepared colours."""
    for lightness in (.52, .68, .80):
        for hue in range(0, 360, 15):
            color = _oklch_to_hex(lightness, .10, hue)
            if color is not None:
                yield color


def select_income_color(used_colors: Iterable[str], preferred: Iterable[str] = ()) -> str:
    """Pick a colour distinct from every active colour, without random RGB."""
    used = tuple(dict.fromkeys(str(color).upper() for color in used_colors if color))
    candidates = (*preferred, *INCOME_COLOR_PALETTE, *_EXTENDED_PALETTE, *_generated_candidates())
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.upper()
        if candidate not in seen:
            seen.add(candidate)
            if is_distinct_income_color(candidate, used):
                return candidate
    raise ValueError("Не удалось подобрать визуально различимый цвет дохода.")


def _active_income_type_ids(allocator, excluded_identifier: str = "") -> list[str]:
    settings = allocator.settings
    rates = getattr(settings, "income_type_tax_rates", {}) or {}
    return [str(identifier) for name, identifier in (getattr(settings, "income_type_ids", {}) or {}).items()
            if name in rates and str(identifier) != excluded_identifier]


def assign_missing_income_type_colors(allocator, excluded_identifier: str = "") -> bool:
    """Persist automatic colours for active types that do not have one yet."""
    colors = getattr(allocator.settings, "income_type_colors", None)
    # Compatibility with old, partially deserialised profiles.  Real settings
    # always own this mapping; leaving a read-only legacy object untouched is
    # preferable to inventing state during a report.
    if colors is None:
        return False
    changed = False
    for identifier in _active_income_type_ids(allocator, excluded_identifier):
        if colors.get(identifier):
            continue
        colors[identifier] = next_automatic_income_color(allocator, identifier)
        changed = True
    return changed


def next_automatic_income_color(allocator, identifier: str) -> str:
    """Choose against active types only; deleted history must not consume colour."""
    colors = getattr(allocator.settings, "income_type_colors", {}) or {}
    active_ids = _active_income_type_ids(allocator, excluded_identifier=str(identifier))
    return select_income_color(colors[item] for item in active_ids if colors.get(item))


def income_color_name(color: str | None) -> str:
    for _, icon, shades in INCOME_COLOR_FAMILIES:
        if color and color.upper() in shades:
            return icon
    return "Автоматический"


def next_income_color_shade(allocator, identifier: str, family_index: int) -> str:
    """Choose a safe shade from a requested family, then another safe hue."""
    _, _, shades = INCOME_COLOR_FAMILIES[family_index]
    colors = allocator.settings.income_type_colors
    active_ids = _active_income_type_ids(allocator, excluded_identifier=str(identifier))
    used = [colors[item] for item in active_ids if colors.get(item)]
    # A few pre-migration callers only provide the colour map.  In that case,
    # treat every saved colour as active rather than risk a duplicate.
    if not active_ids:
        used = [color for saved_id, color in colors.items() if str(saved_id) != str(identifier)]
    return select_income_color(used, preferred=shades)
