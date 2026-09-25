"""Generate current documentation previews from synthetic financial data."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bracket_card import render_bracket_card
from financial_engine import Goal
from goals_card import render_goals_card
from income_colors import INCOME_COLOR_PALETTE
from reserve_card import render_reserve_card
from semantic_chart_colors import BALANCE_CHEST_COLORS, BALANCE_GOAL_COLORS


OUTPUT = ROOT / "docs" / "previews"
D = Decimal


def write(name: str, data: bytes) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_bytes(data)


def generate_brackets() -> None:
    rows = [
        {
            "stage": "A",
            "title": "Собираем на необходимое",
            "rate": D("20"),
            "target": "Полный Стабилизатор",
            "remainder": ["Критический минимум"],
        },
        {
            "stage": "B",
            "title": "Наполняем Бытовой резерв",
            "rate": D("25"),
            "target": "Полный Стабилизатор",
            "remainder": ["Бытовой резерв"],
        },
        {
            "stage": "C",
            "title": "Текущая жизнь обеспечена",
            "rate": D("30"),
            "target": "Полный Стабилизатор",
            "remainder": ["Инвестиции", "Цели и Сундуки"],
        },
        {
            "stage": "D",
            "title": "Распределяем сверхдоход",
            "rate": D("35"),
            "target": "Инвестиции",
            "remainder": ["Полный Стабилизатор"],
        },
    ]
    write(
        "brackets-piecework-5.png",
        render_bracket_card("Сдельный профиль", "Уровень 5", rows),
    )


def preview_positions() -> list[Goal]:
    return [
        Goal("Отпуск", D("30"), balance=D("120000"), target_amount=D("200000"), deadline="2027-06-15", order_index=0, color_index=0),
        Goal("Машина", D("20"), balance=D("450000"), target_amount=D("1500000"), deadline="2028-09-01", order_index=1, color_index=1),
        Goal("Квартира", D("20"), balance=D("600000"), target_amount=D("2000000"), deadline="2030-12-01", order_index=2, color_index=2),
        Goal("Айфон", D("10"), balance=D("35000"), target_amount=D("120000"), status="paused", order_index=3, color_index=3),
        Goal("Ремонт", D("20"), balance=D("210000"), target_amount=D("700000"), deadline="2028-03-20", order_index=4, color_index=4),
        Goal("Подарки", D("35"), balance=D("18500"), position_type="chest", order_index=5, color_index=0),
        Goal("Техника", D("35"), balance=D("36000"), position_type="chest", order_index=6, color_index=1),
        Goal("Хотелки", D("30"), balance=D("8000"), position_type="chest", status="paused", order_index=7, color_index=2),
    ]


def generate_goals_card() -> None:
    write("goals-and-chests-card.png", render_goals_card(preview_positions()))


def generate_reserve_cards() -> None:
    common = {
        "pillow_balance": D("180000"),
        "pillow_target": D("360000"),
        "pillow_months": D("6"),
    }
    write(
        "reserves-stable.png",
        render_reserve_card("stable", **common),
    )
    write(
        "reserves-piecework.png",
        render_reserve_card(
            "piecework",
            **common,
            stabilizer_balance=D("120000"),
            stabilizer_critical_target=D("90000"),
            stabilizer_full_target=D("240000"),
            stabilizer_months=D("3"),
        ),
    )
    write(
        "reserves-cyclic.png",
        render_reserve_card(
            "cyclic",
            **common,
            salary_fund_balance=D("200000"),
            salary_fund_critical_target=D("120000"),
            salary_fund_full_target=D("360000"),
            salary_fund_months=D("3"),
        ),
    )


def generate_charts() -> None:
    # Imported here so the two card renderers remain usable independently of
    # Telegram. In the normal project environment aiogram and Pillow are both
    # installed from requirements.txt.
    from charts import make_chart

    income_values = {
        "Основная работа": D("120000"),
        "Фриланс": D("45000"),
        "Продажи": D("25000"),
    }
    income_colors = {
        name: INCOME_COLOR_PALETTE[index]
        for index, name in enumerate(income_values)
    }
    write(
        "income-analysis-chart.png",
        make_chart(
            income_values,
            "АНАЛИЗ ДОХОДОВ",
            "Источники дохода · текущий расчётный период",
            income_colors,
            preserve_order=True,
            center_amount=sum(income_values.values(), D("0")),
        ),
    )

    goal_values = {
        "Сундук Подарков": D("35"),
        "Сундук Техники": D("35"),
        "Отпуск": D("30"),
    }
    goal_colors = {
        "Сундук Подарков": BALANCE_CHEST_COLORS[0],
        "Сундук Техники": BALANCE_CHEST_COLORS[1],
        "Отпуск": BALANCE_GOAL_COLORS[0],
    }
    write(
        "goals-allocation-chart.png",
        make_chart(
            goal_values,
            "ЦЕЛИ И СУНДУКИ",
            "Доли распределения",
            goal_colors,
            True,
            preserve_order=True,
            center_amount=(D("18000"), D("32000")),
            center_label="100%",
            center_suffix="в месяц",
            legend_amounts={
                "Сундук Подарков": "≈ 6 300—11 200 ₽",
                "Сундук Техники": "≈ 6 300—11 200 ₽",
                "Отпуск": "≈ 5 400—9 600 ₽",
            },
        ),
    )

    period_values = {
        "Налог": D("6000"),
        "Подушка": D("12000"),
        "Стабилизатор": D("8000"),
        "Инвестиции": D("14000"),
        "КМ · Недвижимость": D("28000"),
        "КМ · Здоровье": D("5000"),
        "КМ · Зарплата": D("17000"),
        "Бытовой резерв · Подарки": D("7000"),
        "Цели и Сундуки · Сундук Техники": D("6000"),
        "Цели и Сундуки · Отпуск": D("7000"),
    }
    period_colors = {
        "Налог": "#7C3AED",
        "Подушка": "#176B87",
        "Стабилизатор": "#3E6FD8",
        "Инвестиции": "#72B7D7",
        "КМ · Недвижимость": "#a61e2e",
        "КМ · Здоровье": "#d64550",
        "КМ · Зарплата": "#8e2832",
        "Бытовой резерв · Подарки": "#45A86B",
        "Цели и Сундуки · Сундук Техники": BALANCE_CHEST_COLORS[1],
        "Цели и Сундуки · Отпуск": BALANCE_GOAL_COLORS[0],
    }
    write(
        "period-balances-chart.png",
        make_chart(
            period_values,
            "БАЛАНСЫ",
            "Пополнения конвертов · с 01.09.2026",
            period_colors,
            preserve_order=True,
            center_amount=sum(period_values.values(), D("0")),
            center_label="Доход за период",
            center_suffix="₽ до налогов",
        ),
    )

    tax_values = {
        "Налог на доход · НПД · ФЛ · 4%": D("12000"),
        "Налог на имущество · Квартира": D("5000"),
        "Транспортный налог · Автомобиль": D("3000"),
    }
    tax_colors = {
        "Налог на доход · НПД · ФЛ · 4%": "#7656D8",
        "Налог на имущество · Квартира": "#E2B93B",
        "Транспортный налог · Автомобиль": "#7A7F87",
    }
    write(
        "taxes-chart.png",
        make_chart(
            tax_values,
            "НАЛОГИ",
            "Налоги разделены по типам доходов и правилам",
            tax_colors,
            preserve_order=True,
            legend_columns=1,
            center_amount=sum(tax_values.values(), D("0")),
            center_label="Отложено",
            center_suffix="₽ на налоги",
        ),
    )


def main() -> None:
    generate_brackets()
    generate_goals_card()
    generate_reserve_cards()
    generate_charts()
    print(f"Generated documentation previews in {OUTPUT}")


if __name__ == "__main__":
    main()
