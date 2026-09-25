"""Shared semantic colors used by every financial chart."""

from hashlib import sha256


BALANCE_CHEST_COLORS = (
    "#6a432f", "#a66f45", "#7b523a", "#be8760",
    "#5c392B", "#915c47", "#b47452", "#845744",
)
BALANCE_GOAL_COLORS = ("#FDE047", "#F7C948", "#F0A929", "#E08B00", "#C77D00")


def stable_palette_index(identity: str, ordered_ids=()) -> int:
    """Return a saved position, with a deterministic index for archived data."""
    identity = str(identity)
    ordered = [str(item) for item in ordered_ids if str(item)]
    try:
        return ordered.index(identity)
    except ValueError:
        return len(ordered) + int.from_bytes(sha256(identity.encode("utf-8")).digest()[:8], "big")


def stable_palette_color(identity: str, palette: tuple[str, ...], ordered_ids=()) -> str:
    """Assign a fixed family shade by saved position, with a stable legacy fallback."""
    return palette[stable_palette_index(identity, ordered_ids) % len(palette)]


def position_palette_color(
    identity: str,
    palette: tuple[str, ...],
    *,
    color_index: int | None = None,
    ordered_ids=(),
) -> str:
    """Use the persisted position colour; retain deterministic legacy support."""
    if color_index is not None:
        return palette[max(0, int(color_index)) % len(palette)]
    return stable_palette_color(identity, palette, ordered_ids)
