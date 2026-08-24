"""Единое сопоставление финансовых ступеней и смысловых изображений."""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
MODE_IMAGES_DIR = BASE_DIR / "assets" / "modes" / "semantic"

# Telegram message effect 🔥. Эффекты работают только в личных чатах.
FIRE_EFFECT_ID = "5104841245755180586"

PROFILE_MODE_ASSETS = {
    "stable": {
        1: "minimum_reserve",
        2: "debt_repayment",
        3: "force_majeure",
        4: "maximum",
    },
    "piecework": {
        1: "minimum_reserve",
        2: "debt_repayment",
        3: "stabilizer_critical",
        4: "stabilizer_sustainable",
        5: "force_majeure",
        6: "maximum",
    },
    "cyclic": {
        1: "minimum_reserve",
        2: "debt_repayment",
        3: "salary_fund_critical",
        4: "salary_fund_sustainable",
        5: "contract_delay",
        6: "stabilizer_sustainable",
        7: "force_majeure",
        8: "maximum",
    },
}


def mode_image_path(profile_id: str, mode: int) -> Path | None:
    asset_key = PROFILE_MODE_ASSETS.get(profile_id, {}).get(mode)
    if asset_key is None:
        return None
    path = MODE_IMAGES_DIR / f"{asset_key}.png"
    return path if path.exists() else None
