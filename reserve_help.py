"""Текст справки для сверки фактических балансов резервов."""

from __future__ import annotations


def _reserve_phrases(has_stabilizer: bool) -> tuple[str, str, str]:
    """Return genitive, instrumental and recovery phrases for active reserves."""
    if has_stabilizer:
        return (
            "Подушки или Стабилизатора",
            "Подушкой или Стабилизатором",
            "Подушка или Стабилизатор уменьшились",
        )
    return "Подушки", "Подушкой", "Подушка уменьшилась"


def render_reserve_help(
    profile_id: str,
    *,
    has_stabilizer: bool,
    has_salary_fund: bool,
) -> str:
    """Build help from the reserves that are available in this user profile."""
    has_stabilizer = bool(has_stabilizer and profile_id == "piecework")
    reserve_genitive, reserve_instrumental, recovery_phrase = _reserve_phrases(
        has_stabilizer,
    )
    is_cyclic = profile_id == "cyclic" and has_salary_fund

    paragraphs = [
        "ℹ️ <b>ЗАЧЕМ СВЕРЯТЬ РЕЗЕРВЫ?</b>",
        "Аллокатор считает, сколько денег должно быть в ваших резервах. "
        "Но реальные деньги лежат в банке, поэтому со временем суммы могут отличаться.",
        "Например, банк начислил проценты. Или вам пришлось взять часть денег "
        f"из {reserve_genitive}.",
        "<b>ПОЧЕМУ ЭТО ВАЖНО?</b>",
        "От размера резервов зависит ваш финансовый уровень и то, как Аллокатор будет "
        "распределять следующие доходы.",
        f"Если {recovery_phrase}, система это учтёт и снова начнёт её восстанавливать."
        if not has_stabilizer
        else f"Если {recovery_phrase}, система это учтёт и снова начнёт их восстанавливать.",
    ]

    if is_cyclic:
        paragraphs.append(
            "Фонд зарплаты Аллокатор ведёт отдельно: он сам учитывает, сколько месяцев "
            "без дохода осталось и сколько денег должно оставаться в фонде.",
        )

    if profile_id in {"stable", "piecework"} or is_cyclic:
        paragraphs.append(
            "Когда защита вернётся на нужный уровень, больше денег снова сможет идти "
            "на Цели и Инвестиции.",
        )

    frequency = [
        "<b>КАК ЧАСТО СВЕРЯТЬ?</b>",
        f"• Если вы воспользовались {reserve_instrumental} — лучше обновить сумму сразу.",
    ]
    if is_cyclic:
        frequency.append(
            "• Фонд зарплаты не нужно корректировать после каждой плановой выплаты себе. "
            "Сверяйте его только если реальная сумма в банке отличается от расчётной.",
        )
    frequency.append(
        "• Если разница появилась только из-за банковских процентов — "
        "не нужно вносить каждое начисление. Достаточно раз в несколько месяцев "
        "сверить сумму с банком.",
    )
    return "\n\n".join(paragraphs + ["\n".join(frequency)])
