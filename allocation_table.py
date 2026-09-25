"""Formatting helpers for aligned monetary allocation tables in Telegram."""

from __future__ import annotations

from html import escape, unescape
import re
from typing import Iterable


_VARIATION_SELECTOR = "\ufe0f"


def _visible_length(value: str) -> int:
    """Approximate Telegram's width for labels containing emoji."""
    return len(value.replace(_VARIATION_SELECTOR, ""))


def _split_amount(value: str) -> tuple[str, str]:
    """Return the integral and fractional parts of an already formatted amount."""
    integral, separator, fraction = value.rpartition(",")
    return (integral, fraction) if separator else (value, "")


def allocation_table(groups: Iterable[Iterable[tuple[str, str]]]) -> str:
    """Render labelled monetary rows in a Telegram HTML monospace block.

    Labels, dashes, thousands and decimal separators are put in stable columns.
    Empty groups retain a visual gap between allocation stages.
    """
    normalized = [list(group) for group in groups]
    normalized = [group for group in normalized if group]
    rows = [row for group in normalized for row in group]
    if not rows:
        return "<pre>Нет свободной суммы для распределения</pre>"

    label_width = max(_visible_length(label) for label, _ in rows)
    amounts = [_split_amount(amount) for _, amount in rows]
    integral_width = max(len(integral) for integral, _ in amounts)
    fraction_width = max((len(fraction) for _, fraction in amounts), default=0)

    rendered_groups: list[str] = []
    for group in normalized:
        rendered_rows = []
        for label, amount in group:
            integral, fraction = _split_amount(amount)
            padded_label = label + " " * (label_width - _visible_length(label))
            padded_amount = integral.rjust(integral_width)
            if fraction_width:
                padded_amount += (
                    "," + fraction.ljust(fraction_width)
                    if fraction
                    else " " * (fraction_width + 1)
                )
            rendered_rows.append(f"{padded_label} — {padded_amount}")
        rendered_groups.append("\n".join(rendered_rows))
    return "<pre>" + escape("\n\n".join(rendered_groups)) + "</pre>"


def allocation_table_from_text(text: str) -> str:
    """Convert legacy allocation text (groups split by blank lines) into a table."""
    groups: list[list[tuple[str, str]]] = []
    for group in text.split("\n\n"):
        rows = []
        for line in group.splitlines():
            label, separator, amount = line.rpartition(" — ")
            if not separator:
                continue
            rows.append((unescape(re.sub(r"<[^>]+>", "", label)), unescape(amount)))
        if rows:
            groups.append(rows)
    return allocation_table(groups)
