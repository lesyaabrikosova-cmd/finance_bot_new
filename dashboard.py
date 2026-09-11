from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, FSInputFile

from financial_engine import (
    MODE_NAMES,
    MODE_TITLES,
    fmt_money,
    goal_display_name,
)
from storage import db
from ui import keyboard, main_menu_keyboard
from mode_presentation import mode_image_path
from charts import send_chart_report
from taxes import reconcile_tax_obligation_balances


router = Router()


class IncomeHistoryStates(StatesGroup):
    note = State()


ASSETS_DIR = Path(__file__).resolve().parent / "assets"
INCOME_ANALYSIS_IMAGE_PATH = ASSETS_DIR / "menu" / "income_analysis.png"
async def send_text_with_image(
    message: Message,
    text: str,
    image_path: Path | None,
    reply_markup=None,
) -> None:
    if image_path is None or not image_path.exists():
        await message.answer(
            text,
            reply_markup=reply_markup,
        )
        return

    caption_lines = []
    remaining_lines = []

    for line in text.split("\n"):
        candidate = "\n".join([*caption_lines, line])

        if not remaining_lines and len(candidate) <= 1024:
            caption_lines.append(line)
        else:
            remaining_lines.append(line)

    await message.answer_photo(
        photo=FSInputFile(image_path),
        caption="\n".join(caption_lines) or None,
        reply_markup=(
            reply_markup
            if not remaining_lines
            else None
        ),
    )

    if remaining_lines:
        await message.answer(
            "\n".join(remaining_lines),
            reply_markup=reply_markup,
        )


# ============================================================
# ОПИСАНИЯ УРОВНЕЙ
# ============================================================

EMPLOYEE_MODES = {
    1: (
        "🏆➖➖➖",
        (
            "В срочном порядке формируй минимальную Подушку "
            "на 1–2 месяца закрытия обязательств "
            "(включая платежи по кредитам). Ни о каком "
            "досрочном погашении не может быть и речи. "
            "Про инвестиции и цели вообще забудь. "
            "Мы сейчас спасаем твою жопу."
        ),
    ),
    2: (
        "🏆🏆➖➖",
        (
            "Твоя жопа в минимальной безопасности, а значит "
            "можно все средства бросить на досрочное "
            "погашение кредитов. Если кредитов несколько, "
            "советую гасить по методу «Лавина», и уменьшать "
            "срок, а не платёж — так ты сэкономишь больше "
            "денег. Цели и инвестиции не доступны."
        ),
    ),
    3: (
        "🏆🏆🏆➖",
        (
            "Продолжай формировать подушку безопасности "
            "на случай форс-мажора, который, поверь, может "
            "случиться! Рекомендую установить размер подушки "
            "от 3 до 6 месяцев обязательств. Инвестиции и "
            "цели недоступны, но скоро это изменится."
        ),
    ),
    6: (
        "🏆🏆🏆🏆",
        (
            "Ты прошёл непростой путь, чтобы обрести "
            "финансовую безопасность. Теперь работай над тем, "
            "чтоб обрести финансовую свободу. Твои цели будут "
            "быстро копиться, и потребительский кредит тебе "
            "станет не нужен. Инвестиции рекомендую направить "
            "на пенсию, так как государство не сможет "
            "позаботиться о тебе в старости. С ростом дохода "
            "старайся не увеличивать потребление, иначе ты "
            "перестанешь богатеть, как 90% людей."
        ),
    ),
}


FREELANCER_MODES = {
    1: (
        "🏆➖➖➖➖➖",
        EMPLOYEE_MODES[1][1],
    ),
    2: (
        "🏆🏆➖➖➖➖",
        EMPLOYEE_MODES[2][1],
    ),
    3: (
        "🏆🏆🏆➖➖➖",
        (
            "Продолжай формировать подушку безопасности "
            "на случай форс-мажора, который, поверь, может "
            "случиться! Рекомендую установить размер подушки "
            "от 6 до 12 месяцев обязательств. Инвестиции и "
            "цели недоступны, но скоро это изменится."
        ),
    ),
    4: (
        "🏆🏆🏆🏆➖➖",
        (
            "Ты фрилансер, а это само по себе рисково. "
            "Поэтому твоя подушка не обычная, а двухуровневая. "
            "Первый уровень — форс-мажорный — у тебя уже "
            "накоплен, постарайся к нему прикасаться только "
            "тогда, когда реально случилась катастрофа: "
            "авария, операция, смерть родственника, потеря "
            "жилья, потеря дохода, война, вынужденный переезд, "
            "пандемия… В нашей реальности это всё может "
            "произойти в течение одного года.\n\n"
            "Второй уровень — Стабилизатор Дохода. Это "
            "дополнительная сумма на Подушке, которая равна "
            "твоей Устойчивой Жизни. Стабилизатор нужен на "
            "случай сезонной просадки заказов, больничного "
            "или отпуска, которые тебе никто не оплачивает. "
            "Это не форс-мажор, не путай, это вполне цикличные "
            "события, которые выбивают из колеи 90% "
            "фрилансеров. В «тощие» месяцы можешь спокойно "
            "взять из подушки недостающую сумму "
            "(в рамках среднего дохода), в «жирные» месяцы — "
            "придётся его восполнить. Инвестиции и цели пока "
            "не доступны."
        ),
    ),
    5: (
        "🏆🏆🏆🏆🏆➖",
        (
            "Твоего стабилизатора уже хватит на то, чтобы "
            "закрыть месячные обязательства без заимствования "
            "из форс-мажорной подушки. Осталось чуть-чуть. "
            "Но чтоб копилось не так грустно — ты уже можешь "
            "установить Цели. Они будут копиться в пол силы. "
            "Рекомендую в цели установить Подарки близким "
            "от 3 до 7%, Отпуск, Амортизацию техники "
            "(заранее копить на новый телефон, ноут)."
        ),
    ),
    6: (
        "🏆🏆🏆🏆🏆🏆",
        (
            "Ты прошёл длинный путь, чтобы обрести финансовую "
            "безопасность. Теперь работай над тем, чтоб обрести "
            "финансовую свободу. Твои цели теперь копятся "
            "быстрее, а потребительский кредит больше не нужен. "
            "Инвестиции рекомендую направить на пенсию, так как "
            "государство не сможет позаботиться о тебе в старости. "
            "С ростом дохода старайся не увеличивать потребление, "
            "иначе ты перестанешь богатеть, как 90% людей."
        ),
    ),
}


# ============================================================
# ТЕКСТ ОТ РАЗРАБОТЧИКА
# ============================================================

ABOUT_TEXT = (
    "<b>ОТ РАЗРАБОТЧИКА</b>\n\n"
    "Большинство финансовых систем начинают с прошлого: "
    "сколько ты потратил, где перерасходовал и в какой "
    "категории опять всё пошло не по плану.\n\n"
    "<b>Аллокатор работает с будущим.</b> Деньги получают "
    "задачу в момент поступления — ещё до того, как успевают "
    "раствориться в повседневности.\n\n"
    "Я собрала эту систему из принципов множества финансовых "
    "подходов и адаптировала их в одну последовательную "
    "механику. Здесь одновременно учитываются обязательная "
    "жизнь, бытовой резерв, долги, финансовая подушка, "
    "нестабильность фриланса, цели и инвестиции.\n\n"
    "<b>Главное отличие — правила меняются вместе с твоим "
    "финансовым состоянием.</b> Человеку без минимальной "
    "защиты система не предлагает изображать инвестора. "
    "Человеку с дорогими долгами — не предлагает копить на "
    "хотелки в ущерб процентам. А когда безопасность уже "
    "построена, деньги начинают работать на цели и капитал.\n\n"
    "Ты вводишь реальные поступления, а система показывает, "
    "какую работу должен выполнить каждый рубль."
)


HELP_TEXT = (
    "<b>ПОМОЩЬ</b>\n\n"
    "Новый доход — внести поступление и получить распределение.\n\n"
    "Балансы — посмотреть текущее положение дел и аналитику "
    "расчётного периода.\n\n"
    "Уровень — посмотреть текущий финансовый уровень, описание "
    "и остаток до следующего уровня.\n\n"
    "Кредиты — посмотреть кредиты и остатки.\n\n"
    "Цели — посмотреть цели и накопления.\n\n"
    "Новый расчетный период — вручную начать новый период.\n\n"
    "Настройки — изменить Подушку, обязательные расходы, "
    "категории, налог и проценты.\n\n"
    "От разработчика — идея авторского метода."
)


# ============================================================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# ============================================================

def D(value) -> Decimal:
    return Decimal(str(value))


def rub(value) -> str:
    return f"{fmt_money(D(value))} ₽"

def rub_plain(value) -> str:
    return fmt_money(D(value))


def pct(
    amount: Decimal,
    income: Decimal,
) -> str:

    if income <= 0:
        return "0,00%"

    result = (
        amount
        / income
        * Decimal("100")
    )

    return (
        f"{result:.2f}%"
        .replace(".", ",")
    )


def operation_is_in_current_period(
    operation: dict,
    period_started_at: str | None,
) -> bool:

    if not period_started_at:
        return True

    try:
        start_date = datetime.fromisoformat(
            period_started_at
        ).date()
    except ValueError:
        return True

    raw_date = operation.get("date")

    if not raw_date:
        return True

    try:
        operation_date = date.fromisoformat(
            str(raw_date)[:10]
        )
    except ValueError:
        return True

    return operation_date >= start_date


def get_period_allocations(
    allocator,
    telegram_id: int,
) -> dict[str, Decimal]:
    """
    Возвращает распределения текущего расчётного периода.

    Суммы восстанавливаются из постоянного SQLite operation_log после
    последнего period_reset. Это сохраняет всю историю периода при смене
    подписи конверта. Значения state нужны только старым профилям без журнала.
    """

    result: dict[str, Decimal] = {}
    has_income_records = False

    operations = db.load_operations(
        telegram_id,
        limit=1000,
    )

    # Операции идут от новых к старым.
    # Всё после последнего period_reset относится
    # к текущему расчётному периоду.
    for operation in operations:

        operation_type = operation.get(
            "type"
        )

        if operation_type == "period_reset":
            break

        if operation_type != "income_distribution":
            continue

        has_income_records = True

        payload = (
            operation.get("payload")
            or {}
        )

        allocations = (
            payload.get("allocations")
            or {}
        )

        for key, value in allocations.items():
            result[key] = (
                result.get(
                    key,
                    Decimal("0"),
                )
                + D(value)
            )

    # История доходов — единственный источник, который содержит все операции
    # до и после смены подписи конверта. Состояние оставляем лишь как запасной
    # вариант для старых профилей, где журнал ещё пуст.
    if has_income_records:
        return result

    stored = getattr(allocator.state, "period_allocations", None) or {}
    return {key: D(value) for key, value in stored.items()}


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

async def show_menu(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    allocator = db.load_allocator(
        message.from_user.id
    )

    if allocator is None:

        await message.answer(
            "Сначала создайте финансовый профиль "
            "командой /start."
        )

        return

    await message.answer(
        "<b>ГЛАВНОЕ МЕНЮ</b>",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.message(
    Command("menu")
)
async def command_menu(
    message: Message,
    state: FSMContext,
):

    await show_menu(
        message,
        state,
    )


@router.callback_query(
    F.data == "menu:back"
)
async def menu_back(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        "<b>ГЛАВНОЕ МЕНЮ</b>",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# УРОВЕНЬ
# ============================================================

async def send_mode(
    message: Message,
    telegram_id: int,
):

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:

        await message.answer(
            "Сначала создайте финансовый профиль "
            "через /start."
        )

        return

    settings = allocator.settings

    mode = allocator.active_mode()

    reward = "🏆" * mode + "➖" * (allocator.profile_mode_total - mode)
    profile_label = {
        "stable": "Стабильный",
        "piecework": "Сдельный",
        "cyclic": "Цикличный (контрактный)",
    }[allocator.profile_id]

    debt_profile = (
        "с долгами"
        if any(
            credit.active
            for credit in settings.credits
        )
        else "без долгов"
    )

    capital = allocator.protective_capital_balance
    target = allocator.protective_capital_target
    remaining = allocator.remaining_to_profile_transition()
    priority = allocator.current_protection_priority()
    plan = allocator.reserve_rebalancing_plan()
    if priority:
        priority_text = (
            "<b><u>ТЕКУЩИЙ ПРИОРИТЕТ</u></b>\n\n"
            f"{escape(str(priority['name']))} — не хватает <b>{rub(priority['deficit'])}</b>."
        )
    else:
        priority_text = "<b><u>ТЕКУЩИЙ ПРИОРИТЕТ</u></b>\n\nВсе защитные нормативы сформированы."

    if remaining is None:
        next_text = "\n\n<b>Максимальный уровень устойчивости достигнут.</b>"
    else:
        next_text = f"\n\nДо следующего кубка осталось: <b>{rub(remaining)}</b>"

    text = (
        "<b>УРОВЕНЬ ФИНАНСОВОЙ УСТОЙЧИВОСТИ</b>\n\n"
        f"Профиль — <b>{profile_label}, {debt_profile}</b>\n\n"
        f"<b>{reward}</b>\n\n"
        f"В защитных резервах — <b>{rub(capital)}</b> из <b>{rub(target)}</b>."
        f"{next_text}\n\n"
        f"{priority_text}"
    )

    rows = [[(f"Почему у меня {mode} кубков?", "mode:why")]]
    if plan["transfers"]:
        rows.append([("Сбалансировать резервы", "mode:rebalance")])
    rows.extend([
        [("Изменить размеры резервов", "menu:reserves")],
        [("← Главное меню", "menu:back")],
    ])

    await send_text_with_image(
        message,
        text,
        mode_image_path(allocator.profile_id, mode),
        reply_markup=keyboard(rows),
    )


@router.message(
    Command("state")
)
async def command_state(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await send_mode(
        message,
        message.from_user.id,
    )


@router.callback_query(
    F.data == "menu:state"
)
async def menu_state(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await send_mode(
        callback.message,
        callback.from_user.id,
    )


@router.callback_query(F.data == "mode:why")
async def explain_resilience_mode(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала создайте финансовый профиль через /start.")
        return

    mode = allocator.active_mode()
    capital = allocator.protective_capital_balance
    has_debts = any(credit.active for credit in allocator.settings.credits)
    if has_debts:
        if mode == 1:
            explanation = (
                "У вас есть активный долг, а Минимальная подушка ещё не сформирована. "
                "Поэтому действует первый уровень независимо от денег в других резервах."
            )
        else:
            explanation = (
                "Минимальная подушка сформирована, но остаётся активный долг. "
                "До его закрытия действует второй уровень."
            )
        levels = ""
    else:
        level_lines = []
        for reached_mode, target, name in allocator.resilience_transition_targets():
            mark = "✔️" if capital >= target else "▫️"
            level_lines.append(
                f"{mark} {reached_mode} кубков — {escape(name)}: <b>{rub(target)}</b>"
            )
        levels = "\n\n" + "\n".join(level_lines)
        explanation = (
            "Аллокатор складывает деньги защитных резервов в один виртуальный сосуд. "
            "Кубки отражают общий объём защиты, а не то, на каком именно банковском счёте лежат деньги."
        )

    priority = allocator.current_protection_priority()
    priority_text = (
        f"\n\nФактический приоритет — <b>{escape(str(priority['name']))}</b>. "
        f"Не хватает <b>{rub(priority['deficit'])}</b>."
        if priority else
        "\n\nВсе фактические защитные нормативы сформированы."
    )
    await callback.message.answer(
        f"<b>ПОЧЕМУ У ВАС {mode} КУБКОВ</b>\n\n"
        f"{explanation}\n\n"
        f"Защитный капитал — <b>{rub(capital)}</b>."
        f"{levels}{priority_text}\n\n"
        "Деньги между банковскими счетами автоматически не переводятся.",
        reply_markup=keyboard([
            [("← К уровню", "menu:state")],
            [("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data == "mode:rebalance")
async def show_reserve_rebalancing(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала создайте финансовый профиль через /start.")
        return
    plan = allocator.reserve_rebalancing_plan()
    if plan.get("blocked_reason") == "active_debt":
        await callback.message.answer(
            "<b>СЕЙЧАС ДЕЙСТВУЕТ ДОЛГОВОЙ МАРШРУТ</b>\n\n"
            "Сначала Аллокатор проверяет Минимальную подушку и погашение долга. "
            "Обычная балансировка защитных резервов станет доступна после закрытия долгов.",
            reply_markup=keyboard([[('← К уровню', 'menu:state')]]),
        )
        return
    account_lines = []
    for account in plan["accounts"]:
        details = ""
        if account["surplus"] > 0:
            details = f" · профицит <b>{rub(account['surplus'])}</b>"
        elif account["deficit"] > 0:
            details = f" · не хватает <b>{rub(account['deficit'])}</b>"
        else:
            details = " · ✔️"
        account_lines.append(
            f"• <b>{escape(str(account['name']))}</b> — "
            f"{rub(account['balance'])} из {rub(account['target'])}{details}"
        )
    transfer_lines = [
        f"• <b>{rub(transfer['amount'])}</b>: "
        f"«{escape(str(transfer['source']))}» → «{escape(str(transfer['destination']))}»"
        for transfer in plan["transfers"]
    ]
    if not transfer_lines:
        await callback.message.answer(
            "<b>БАЛАНСИРОВКА НЕ ТРЕБУЕТСЯ</b>\n\n" + "\n".join(account_lines),
            reply_markup=keyboard([[('← К уровню', 'menu:state')]]),
        )
        return

    surplus_text = ""
    if plan["free_surplus"] > 0:
        surplus_text = (
            f"\n\nПосле балансировки останется свободный излишек — "
            f"<b>{rub(plan['free_surplus'])}</b>. Его распределение по целям и инвестициям "
            "будет предложено отдельно."
        )
    await callback.message.answer(
        "<b>ПЛАН БАЛАНСИРОВКИ РЕЗЕРВОВ</b>\n\n"
        + "\n".join(account_lines)
        + "\n\nРекомендованные переводы:\n\n"
        + "\n".join(transfer_lines)
        + surplus_text
        + "\n\nАллокатор не переводит банковские деньги. Выполните переводы самостоятельно, "
        "а затем подтвердите их.",
        reply_markup=keyboard([
            [("✔️ Переводы выполнены", "mode:rebalance:confirm")],
            [("Изменить размеры резервов", "menu:reserves")],
            [("Пока не выполнять", "menu:state")],
        ]),
    )


@router.callback_query(F.data == "mode:rebalance:confirm")
async def confirm_reserve_rebalancing(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала создайте финансовый профиль через /start.")
        return
    plan = allocator.reserve_rebalancing_plan()
    if not plan["transfers"]:
        await callback.message.answer(
            "Балансы уже изменились, поэтому прежний план больше не требуется.",
            reply_markup=keyboard([[('Открыть уровень', 'menu:state')]]),
        )
        return
    allocator.apply_reserve_rebalancing()
    db.save_allocator(callback.from_user.id, allocator)
    lines = [
        f"• {rub(transfer['amount'])}: «{escape(str(transfer['source']))}» → "
        f"«{escape(str(transfer['destination']))}»"
        for transfer in plan["transfers"]
    ]
    await callback.message.answer(
        "<b>БАЛАНСЫ ОБНОВЛЕНЫ</b>\n\n"
        "Аллокатор зафиксировал подтверждённые переводы:\n\n"
        + "\n".join(lines)
        + "\n\nЭта операция не считается новым доходом и не изменяет налоговую статистику.",
        reply_markup=keyboard([[('Открыть уровень', 'menu:state')]]),
    )


# ============================================================
# БАЛАНСЫ
# ============================================================

def period_balance_chart(allocator, allocations):
    """Disjoint period flows: no opening balances or aggregate/detail duplication."""
    from hashlib import sha256
    values, colors = {}, {}

    life_colors = (
        '#B83F4A', '#D45A66', '#E77A82', '#9D2635',
        '#F09AA0', '#C94B5B', '#A83345', '#D96B73',
    )
    goal_colors = (
        '#E7B52E', '#F2C94C', '#D99A1A', '#F6D365',
        '#C98A13', '#FFD86B', '#E9AD35', '#F4C54F',
    )
    chest_colors = (
        '#7A4B2B', '#9C6338', '#B87943', '#6B3E25',
        '#C68A50', '#8A5732', '#A96D3D', '#704126',
    )

    def add(label, value, color):
        value = D(value)
        if value > 0:
            values[label] = values.get(label, Decimal(0)) + value
            colors[label] = color

    def shade(name, palette, used):
        """Choose distinct shades within a color family for the current chart."""
        seed = int.from_bytes(sha256(name.encode()).digest()[:4], 'big')
        start = seed % len(palette)
        for offset in range(len(palette)):
            color = palette[(start + offset) % len(palette)]
            if color not in used:
                used.add(color)
                return color
        return palette[start]

    add('Налог', allocator.state.period_tax, '#7656D8')
    add('Фонд Зарплаты', allocations.get('Фонд Зарплаты', 0), '#00B7E8')
    add('Подушка', allocations.get('Подушка', 0), '#173F8A')
    add('Стабилизатор', allocations.get('Стабилизатор дохода', 0), '#7EC8F5')
    add('Инвестиции', allocations.get('Инвестиции', 0), '#32A9E0')
    add('Минимальные платежи по долгам', allocations.get('Мин. платеж', 0), '#9C7BAB')
    add('Досрочное погашение', allocations.get('Досрочное', 0), '#74608C')
    for key, value in allocations.items():
        if key.startswith('Рабочие обязательства:'):
            add(key.replace(':', ' · '), value, '#B36C75')

    settings = getattr(allocator, 'settings', None)
    configured_life = getattr(settings, 'life_categories', None)
    category_ids = getattr(settings, 'life_category_ids', {}) or {}
    # Состояние старых версий могло содержать прежнюю подпись категории.
    # В отчёте допустимы только действующие категории профиля: их постоянный
    # ID переживает переименование, а имя остаётся лишь подписью.
    allocation_life = {
        key[3:]: D(value)
        for key, value in allocations.items()
        if str(key).startswith('КЖ:')
    }
    if configured_life is not None:
        life = {
            name: allocation_life.get(
                name,
                D(allocator.state.period_life_topups.get(name, 0)),
            )
            for name in configured_life
        }
        if 'Зарплата' not in configured_life:
            life['Зарплата'] = D(allocator.state.period_life_topups.get('Зарплата', 0))
    else:
        life = dict(allocator.state.period_life_topups)
        for key, value in allocations.items():
            if key.startswith('КЖ:') and not allocator.state.period_life_topups:
                life[key[3:]] = value
    # Все налоговые обязательства — один фиолетовый банковский конверт.
    planned_tax = life.pop('Налоги', Decimal(0))
    if planned_tax > 0:
        values['Налог'] = values.get('Налог', Decimal(0)) + planned_tax
        colors['Налог'] = '#7656D8'
    salary = life.pop('Зарплата', None)
    used_life_colors = set()
    for name, value in sorted(life.items(), key=lambda item: D(item[1]), reverse=True):
        add(
            f'КМ · {name}', value,
            shade(category_ids.get(name, name), life_colors, used_life_colors),
        )
    if salary is not None:
        add('КМ · Зарплата', salary, shade(category_ids.get('Зарплата', 'Зарплата'), life_colors, used_life_colors))
    add('Бытовой резерв', allocations.get('Бытовой резерв', 0), '#24734A')
    goal_map = {goal.name: goal for goal in getattr(settings, 'goals', [])}
    goal_items = [
        (key[5:], value)
        for key, value in allocations.items()
        if key.startswith('Цели:')
        and (not goal_map or key[5:] in goal_map)
    ]
    used_goal_colors, used_chest_colors = set(), set()
    for name, value in sorted(goal_items, key=lambda item: D(item[1]), reverse=True):
        goal = goal_map.get(name)
        display = goal_display_name(name, bool(goal and goal.is_chest))
        palette = chest_colors if goal and goal.is_chest else goal_colors
        used = used_chest_colors if goal and goal.is_chest else used_goal_colors
        identity = getattr(goal, 'uid', '') or name
        add(f'Цели и Сундуки · {display}', value, shade(identity, palette, used))
    return values, colors


async def send_balances(
    message: Message,
    telegram_id: int,
):

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:

        await message.answer(
            "Сначала создайте финансовый профиль "
            "через /start."
        )

        return

    settings = allocator.settings
    state = allocator.state

    income = D(
        state.period_income
    )

    tax = D(
        state.period_tax
    )

    allocations = (
        get_period_allocations(
            allocator,
            telegram_id,
        )
    )

    # --------------------------------------------------------
    # Основные суммы периода
    # --------------------------------------------------------

    pillow_period = allocations.get(
        "Подушка",
        Decimal("0"),
    )
    stabilizer_period = allocations.get("Стабилизатор дохода", Decimal("0"))
    fund_salary_period = allocations.get("Фонд Зарплаты", Decimal("0"))

    investment_period = allocations.get(
        "Инвестиции",
        Decimal("0"),
    )

    household_period = allocations.get(
        "Бытовой резерв",
        Decimal("0"),
    )

    minimum_period = allocations.get(
        "Мин. платеж",
        Decimal("0"),
    )

    early_period = allocations.get(
        "Досрочное",
        Decimal("0"),
    )

    # --------------------------------------------------------
    # Заголовок периода
    # --------------------------------------------------------

    period_label = (
        "текущий расчётный период"
    )

    period_started_at = getattr(
        state,
        "period_started_at",
        None,
    )

    if period_started_at:

        try:

            started = datetime.fromisoformat(
                period_started_at
            )

            period_label = (
                f"с {started.strftime('%d.%m.%Y')}"
            )

        except ValueError:
            pass

    lines = [
        "<b>БАЛАНСЫ ЗА ПЕРИОД</b>", "",
        f"💲 <b>Доход</b> — {rub(income)}", "",
        f"🏛️ <b>Налог</b> — {rub_plain(tax)} • {pct(tax, income)}", "",
    ]
    if settings.income_rhythm == "cyclic" and fund_salary_period > 0:
        lines.append(f"🏦 <b>Фонд Зарплаты</b> — {rub_plain(fund_salary_period)} • {pct(fund_salary_period, income)}")
    if pillow_period > 0:
        lines.append(f"🛡️ <b>Подушка</b> — {rub_plain(pillow_period)} • {pct(pillow_period, income)}")
    if settings.needs_stabilizer and stabilizer_period > 0:
        lines.append(f"🛟 <b>Стабилизатор</b> — {rub_plain(stabilizer_period)} • {pct(stabilizer_period, income)}")
    if investment_period > 0:
        lines.append(f"📈 <b>Инвестиции</b> — {rub_plain(investment_period)} • {pct(investment_period, income)}")
    if minimum_period > 0 or early_period > 0:
        lines.append("")
        if minimum_period > 0:
            lines.append(f"💳 <b>Минимальные платежи по долгам</b> — {rub_plain(minimum_period)} • {pct(minimum_period, income)}")
        if early_period > 0:
            lines.append(f"💳 <b>Досрочное погашение</b> — {rub_plain(early_period)} • {pct(early_period, income)}")
    if lines[-1] != "":
        lines.append("")

    # --------------------------------------------------------
    # Каждая категория КЖ
    #
    # period_life_topups — самый надёжный источник именно
    # для КЖ текущего расчётного периода.
    # Нулевые категории текущего периода не показываем.
    # --------------------------------------------------------

    category_names = list(
        settings.life_categories.keys()
    )

    if "Зарплата" not in category_names:

        category_names.append(
            "Зарплата"
        )

    for name in sorted(
        category_names,
        key=lambda name: D(state.period_life_topups.get(name, 0)),
        reverse=True,
    ):

        amount = D(
            state.period_life_topups.get(
                name,
                Decimal("0"),
            )
        )

        if amount > 0:
            lines.append(f"❤️ <b>{escape(name)}</b> — {rub_plain(amount)} • {pct(amount, income)}")

    # --------------------------------------------------------
    # Бытовой резерв
    # --------------------------------------------------------

    if household_period > 0:
        lines.extend(["", f"💚 <b>Бытовой резерв</b> — {rub_plain(household_period)} • {pct(household_period, income)}"])

    # --------------------------------------------------------
    # Каждая цель
    #
    goal_lines = []
    # --------------------------------------------------------

    if settings.goals:

        for goal in sorted(
            settings.goals,
            key=lambda goal: D(allocations.get(f"Цели:{goal.name}", 0)),
            reverse=True,
        ):

            amount = allocations.get(
                f"Цели:{goal.name}",
                Decimal("0"),
            )

            goal_icon = "🧳" if goal.is_chest else "⭐️"
            display_name = goal_display_name(goal.name, goal.is_chest)
            if amount > 0:
                goal_lines.append(f"{goal_icon} <b>{escape(display_name)}</b> — {rub_plain(amount)} • {pct(amount, income)}")

    else:

        amount = allocations.get(
            "Цели:ЦЕЛИ (всего)",
            Decimal("0"),
        )

        if amount > 0:
            goal_lines.append(f"⭐️ <b>Цели (всего)</b> — {rub_plain(amount)} • {pct(amount, income)}")
    if goal_lines:
        lines.extend(["", *goal_lines])

    # --------------------------------------------------------
    # Кредиты
    # --------------------------------------------------------

    # --------------------------------------------------------
    # Пороги
    # --------------------------------------------------------

    critical_minimum = D(
        settings.critical_life
    )

    sustainable_life = (
        D(settings.critical_life)
        + D(settings.household_reserve)
    )

    life_balance = D(
        state.life_balance
    )

    until_kzh = max(
        Decimal("0"),
        critical_minimum
        - life_balance,
    )

    until_uzh = max(
        Decimal("0"),
        sustainable_life
        - life_balance,
    )

    next_info = (
        allocator.next_mode_info()
    )

    # Выделяем все периодические поступления в конверты одной цитатой.
    threshold_index = lines.index("<b>ПОРОГИ</b>") if "<b>ПОРОГИ</b>" in lines else len(lines)
    tax_index = next((i for i, line in enumerate(lines) if line.startswith("🏛️ <b>Налог</b>")), None)
    if tax_index is not None and tax_index < threshold_index:
        quoted = "\n".join(lines[tax_index:threshold_index]).strip()
        lines[tax_index:threshold_index] = ["<blockquote>" + quoted + "</blockquote>"]

    lines.extend([
        "",
        "—————————",
        f"↺ <b>Баланс жизни</b> — {rub_plain(state.life_balance)}",
        f"➤ До <b>Критич. минимума</b> — {rub_plain(until_kzh)}",
        f"➤ До <b>Устойч. жизни</b> — {rub_plain(until_uzh)}",
    ])

    if next_info:

        lines.append(
            f"➤ До следующего уровня — "
            f"{next_info['next_name']}: "
            f"<b>{rub_plain(next_info['remaining'])}</b>"
        )

    else:

        lines.append(
            "➤ До следующего уровня — "
            "<b>максимальный уровень достигнут</b>"
        )

    # --------------------------------------------------------
    # Только уровень разработчика
    # --------------------------------------------------------

    if settings.developer_mode:

        lines.extend([
            "",
            "<b>ЗАЩИТНЫЕ РЕЗЕРВЫ — "
            "УРОВЕНЬ РАЗРАБОТЧИКА</b>",
            f"МП: "
            f"{rub(state.pillow_minimum)} / "
            f"{rub(settings.minimum_reserve_limit)}",
            f"ФМ: "
            f"{rub(state.pillow_force_majeure)} / "
            f"{rub(settings.force_majeure_limit)}",
            f"Стабилизатор дохода: "
            f"{rub(state.pillow_stabilizer)} / "
            f"{rub(settings.stabilizer_full_limit)}",
        ])

    balances, colors = period_balance_chart(allocator, allocations)
    summary_lines = [
        "<b>БАЛАНСЫ ЗА ПЕРИОД</b>", "",
        f"↺ <b>Баланс жизни</b> — {rub_plain(state.life_balance)}",
        f"➤ До <b>Критич. минимума</b> — {rub_plain(until_kzh)}",
        f"➤ До <b>Устойч. жизни</b> — {rub_plain(until_uzh)}",
    ]
    if next_info:
        summary_lines.append(
            f"➤ До следующего уровня — {next_info['next_name']}: "
            f"<b>{rub_plain(next_info['remaining'])}</b>"
        )
    else:
        summary_lines.append("➤ До следующего уровня — <b>максимальный уровень достигнут</b>")
    await send_chart_report(
        message, balances, "БАЛАНСЫ", "\n".join(summary_lines),
        subtitle=f"Пополнения конвертов · {period_label}",
        colors=colors, preserve_order=True, center_amount=income,
        fallback_text="\n".join(lines),
        reply_markup=keyboard([
            [("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(
    F.data == "menu:analytics"
)
async def menu_balances(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await send_balances(
        callback.message,
        callback.from_user.id,
    )


# ============================================================
# ПОМОЩЬ
# ============================================================

@router.message(
    Command("help")
)
async def command_help(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(
    F.data == "menu:help"
)
async def menu_help(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# ОТ РАЗРАБОТЧИКА
# ============================================================

@router.message(
    Command("about")
)
async def command_about(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        ABOUT_TEXT,
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(
    F.data == "menu:about"
)
async def menu_about(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        ABOUT_TEXT,
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

    
# ============================================================
# АНАЛИЗ ДОХОДОВ
# ============================================================

INCOME_HISTORY_PAGE_SIZE = 8


def income_history_operations(telegram_id: int) -> list[dict]:
    """Return recorded income operations, newest first, for one user only."""
    return [
        operation
        for operation in db.load_operations(telegram_id, limit=1000)
        if operation.get("type") == "income_distribution"
    ]


def rebuild_period_analytics_from_history(allocator, telegram_id: int) -> None:
    """Make chart data match the remaining income ledger after a deletion."""
    period_income = Decimal("0")
    period_tax = Decimal("0")
    allocations: dict[str, Decimal] = {}
    life_topups = {
        name: Decimal("0")
        for name in allocator.settings.life_categories
    }
    life_topups.setdefault("Зарплата", Decimal("0"))

    for operation in db.load_operations(telegram_id, limit=1000):
        if operation.get("type") == "period_reset":
            break
        if operation.get("type") != "income_distribution":
            continue
        payload = operation.get("payload") or {}
        period_income += D(payload.get("income", 0))
        period_tax += D(payload.get("tax", 0))
        for key, amount in (payload.get("allocations") or {}).items():
            value = D(amount)
            allocations[key] = allocations.get(key, Decimal("0")) + value
            if str(key).startswith("КЖ:"):
                name = str(key)[3:]
                life_topups[name] = life_topups.get(name, Decimal("0")) + value

    allocator.state.period_income = period_income
    allocator.state.period_tax = period_tax
    allocator.state.period_allocations = allocations
    allocator.state.period_life_topups = life_topups


def income_history_date(operation: dict) -> str:
    payload = operation.get("payload") or {}
    raw_date = payload.get("date") or operation.get("created_at")
    try:
        return date.fromisoformat(str(raw_date)[:10]).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return "Без даты"


def income_history_button_label(operation: dict) -> str:
    payload = operation.get("payload") or {}
    income_type = " ".join(str(payload.get("income_type", "Без типа")).split())
    if len(income_type) > 24:
        income_type = income_type[:23] + "…"
    return (
        f"{income_history_date(operation)[:5]} · {income_type} · "
        f"{rub_plain(payload.get('income', 0))}"
    )


def find_income_history_operation(telegram_id: int, operation_id: int) -> dict | None:
    return next(
        (
            operation
            for operation in income_history_operations(telegram_id)
            if operation.get("id") == operation_id
        ),
        None,
    )


async def send_income_history(message: Message, telegram_id: int, page: int = 0):
    operations = income_history_operations(telegram_id)
    last_page = max(0, (len(operations) - 1) // INCOME_HISTORY_PAGE_SIZE)
    page = max(0, min(page, last_page))
    page_operations = operations[
        page * INCOME_HISTORY_PAGE_SIZE:(page + 1) * INCOME_HISTORY_PAGE_SIZE
    ]

    if not operations:
        await message.answer(
            "<b>ИСТОРИЯ ДОХОДОВ</b>\n\n"
            "Вы ещё не добавили ни одного дохода.",
            reply_markup=keyboard([
                [("← К анализу доходов", "menu:income_analysis")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return

    rows = [
        [(income_history_button_label(operation), f"incomehistory:detail:{operation['id']}")]
        for operation in page_operations
    ]
    if last_page:
        navigation = []
        if page > 0:
            navigation.append(("← Новее", f"incomehistory:page:{page - 1}"))
        if page < last_page:
            navigation.append(("Старше →", f"incomehistory:page:{page + 1}"))
        rows.append(navigation)
    rows.extend([
        [("← К анализу доходов", "menu:income_analysis")],
        [("← Главное меню", "menu:back")],
    ])
    await message.answer(
        "<b>ИСТОРИЯ ДОХОДОВ</b>",
        reply_markup=keyboard(rows),
    )


def income_operation_card_text(operation: dict) -> str:
    payload = operation.get("payload") or {}
    income = D(payload.get("income", 0))
    tax = D(payload.get("tax", 0))
    income_type = escape(str(payload.get("income_type", "Без типа")))
    note = payload.get("note")
    note_block = (
        f"\n————————————\n📝 {escape(str(note))}"
        if note
        else ""
    )
    return (
        "<b>ДОХОД</b>\n\n"
        f"{income_history_date(operation)}\n"
        f"{income_type} — {rub_plain(income)}"
        f"{note_block}\n"
        "————————————\n"
        f"🏛️ Налог — {rub_plain(tax)}\n"
        f"К распределению — {rub_plain(income - tax)}"
    )


async def send_income_history_detail(
    message: Message,
    telegram_id: int,
    operation_id: int,
) -> bool:
    operation = find_income_history_operation(telegram_id, operation_id)
    if operation is None:
        await message.answer("Это поступление уже недоступно в истории.")
        return False
    has_note = bool((operation.get("payload") or {}).get("note"))
    await message.answer(
        income_operation_card_text(operation),
        reply_markup=keyboard([
            [("Показать распределение", f"incomehistory:distribution:{operation_id}")],
            [
                (
                    "✎ Заметка" if has_note else "+ Заметка",
                    f"incomehistory:note:{operation_id}",
                ),
                ("🗑️ Удалить доход", f"incomehistory:delete:{operation_id}"),
            ],
            [
                ("← Главное меню", "menu:back"),
                ("← К истории", "incomehistory:open"),
            ],
        ]),
    )
    return True


def income_distribution_text(operation: dict, allocator) -> str:
    payload = operation.get("payload") or {}
    allocations = {
        str(key): D(value)
        for key, value in (payload.get("allocations") or {}).items()
    }
    groups: list[list[str]] = [[], [], [], [], [], []]

    def add(group: int, emoji: str, name: str, amount) -> None:
        amount = D(amount)
        if amount > 0:
            groups[group].append(f"{emoji} <b>{escape(name)}</b> — {rub_plain(amount)}")

    add(0, "🏛️", "Налог", payload.get("tax", 0))
    add(0, "🏛️", "Налоги", allocations.get("КЖ:Налоги", 0))
    add(1, "🏦", "Фонд Зарплаты", allocations.get("Фонд Зарплаты", 0))
    add(1, "🛡️", "Подушка", allocations.get("Подушка", 0))
    add(1, "🛟", "Стабилизатор", allocations.get("Стабилизатор дохода", 0))
    add(1, "📈", "Инвестиции", allocations.get("Инвестиции", 0))
    add(2, "💳", "Минимальные платежи", allocations.get("Мин. платеж", 0))
    add(2, "💳", "Досрочное погашение", allocations.get("Досрочное", 0))

    for key, amount in allocations.items():
        if key.startswith("Рабочие обязательства:"):
            _, *parts = key.split(":", 2)
            label = " → ".join(reversed(parts)) if len(parts) == 2 else key[21:]
            add(2, "💳", label, amount)

    life_items = [
        (key[3:], amount)
        for key, amount in allocations.items()
        if key.startswith("КЖ:") and key not in {"КЖ:Налоги", "КЖ:Зарплата"}
    ]
    for name, amount in sorted(life_items, key=lambda item: item[1], reverse=True):
        add(3, "❤️", name, amount)
    add(3, "❤️", "Зарплата", allocations.get("КЖ:Зарплата", 0))

    add(4, "💚", "Бытовой резерв", allocations.get("Бытовой резерв", 0))
    goal_map = {goal.name: goal for goal in allocator.settings.goals}
    goal_items = [
        (key[5:], amount)
        for key, amount in allocations.items()
        if key.startswith("Цели:")
    ]
    for name, amount in sorted(goal_items, key=lambda item: item[1], reverse=True):
        goal = goal_map.get(name)
        add(5, "🧳" if goal and goal.is_chest else "⭐️", goal_display_name(name, bool(goal and goal.is_chest)), amount)

    quote = "\n\n".join("\n".join(group) for group in groups if group)
    return (
        "<b>РАСПРЕДЕЛЕНИЕ ДОХОДА</b>\n\n"
        f"{income_history_date(operation)}\n"
        f"{escape(str(payload.get('income_type', 'Без типа')))} — {rub_plain(payload.get('income', 0))}\n\n"
        "<blockquote>"
        f"{quote or 'Распределений не найдено.'}"
        "</blockquote>"
    )


@router.callback_query(F.data == "incomehistory:open")
async def open_income_history(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await send_income_history(callback.message, callback.from_user.id)


@router.callback_query(F.data.startswith("incomehistory:page:"))
async def income_history_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        await callback.message.answer("Не удалось открыть эту страницу истории.")
        return
    await state.clear()
    await send_income_history(callback.message, callback.from_user.id, page)


@router.callback_query(F.data.startswith("incomehistory:detail:"))
async def income_history_detail(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    await state.clear()
    await send_income_history_detail(
        callback.message,
        callback.from_user.id,
        operation_id,
    )


@router.callback_query(F.data.startswith("incomehistory:note:"))
async def ask_history_income_note(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    if find_income_history_operation(callback.from_user.id, operation_id) is None:
        await callback.message.answer("Это поступление уже недоступно в истории.")
        return
    await state.update_data(history_note_operation_id=operation_id)
    await state.set_state(IncomeHistoryStates.note)
    await callback.message.answer(
        "<b>ЗАМЕТКА К ПОСТУПЛЕНИЮ</b>\n\n"
        "Введите новую короткую заметку. Не более 60 символов.",
        reply_markup=keyboard([
            [
                ("← Назад", f"incomehistory:note_back:{operation_id}"),
                ("✗ Отмена", f"incomehistory:note_cancel:{operation_id}"),
            ],
        ]),
    )


@router.message(IncomeHistoryStates.note)
async def save_history_income_note(message: Message, state: FSMContext):
    note = " ".join((message.text or "").split())
    if not note:
        await message.answer("Введите короткую заметку или нажмите «← Назад».")
        return
    if len(note) > 60:
        await message.answer("Заметка должна быть не длиннее 60 символов.")
        return
    data = await state.get_data()
    operation_id = data.get("history_note_operation_id")
    if not isinstance(operation_id, int) or not db.update_income_note(
        message.from_user.id,
        operation_id,
        note,
    ):
        await state.clear()
        await message.answer("Не удалось сохранить заметку. Попробуйте открыть доход снова.")
        return
    await state.clear()
    await send_income_history_detail(message, message.from_user.id, operation_id)


@router.callback_query(F.data.startswith("incomehistory:note_back:"))
@router.callback_query(F.data.startswith("incomehistory:note_cancel:"))
async def history_income_note_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    await state.clear()
    await send_income_history_detail(callback.message, callback.from_user.id, operation_id)


@router.callback_query(F.data.startswith("incomehistory:delete:"))
async def ask_delete_income_history(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    operation = find_income_history_operation(callback.from_user.id, operation_id)
    if operation is None:
        await callback.message.answer("Это поступление уже недоступно в истории.")
        return
    await state.clear()
    payload = operation.get("payload") or {}
    await callback.message.answer(
        "<b>УДАЛИТЬ ДОХОД?</b>\n\n"
        f"{income_history_date(operation)}\n"
        f"{escape(str(payload.get('income_type', 'Без типа')))} — "
        f"{rub_plain(payload.get('income', 0))}\n\n"
        "Будут откатены налог, распределение по конвертам, цели, "
        "резервы и итоги расчётного периода.\n\n"
        "Удалить это поступление безвозвратно?",
        reply_markup=keyboard([
            [
                ("✗ Отмена", f"incomehistory:delete_cancel:{operation_id}"),
                ("🗑️ Удалить доход", f"incomehistory:delete_confirm:{operation_id}"),
            ],
            [("← К доходу", f"incomehistory:detail:{operation_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("incomehistory:delete_cancel:"))
async def cancel_delete_income_history(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Удаление отменено")
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    await state.clear()
    await send_income_history_detail(callback.message, callback.from_user.id, operation_id)


@router.callback_query(F.data.startswith("incomehistory:delete_confirm:"))
async def confirm_delete_income_history(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    operations = income_history_operations(callback.from_user.id)
    operation = next((item for item in operations if item.get("id") == operation_id), None)
    allocator = db.load_allocator(callback.from_user.id)
    if operation is None or allocator is None:
        await callback.message.answer("Это поступление уже недоступно в истории.")
        return

    # A snapshot gives an exact restoration for the newest income. For an
    # earlier entry, the engine reverses only its recorded contribution and
    # keeps all subsequent, unrelated operations in place.
    restored = deepcopy(allocator)
    payload = operation.get("payload") or {}
    try:
        restored.rollback_income_operation(
            payload,
            restore_snapshot=bool(operations and operations[0].get("id") == operation_id),
        )
    except ValueError as error:
        await callback.message.answer(
            "<b>ДОХОД НЕ УДАЛЁН</b>\n\n"
            f"{escape(str(error))}\n\n"
            "Бот сохранил все балансы без изменений.",
            reply_markup=keyboard([
                [("← К доходу", f"incomehistory:detail:{operation_id}")],
                [("← К истории", "incomehistory:open")],
            ]),
        )
        return

    if not db.delete_income_operation(callback.from_user.id, operation_id):
        await callback.message.answer(
            "Не удалось завершить удаление. Балансы не изменяйте и обратитесь в поддержку."
        )
        return
    reconcile_tax_obligation_balances(callback.from_user.id, restored)
    rebuild_period_analytics_from_history(restored, callback.from_user.id)
    db.save_allocator(callback.from_user.id, restored)
    await state.clear()
    await callback.message.answer(
        "<b>ДОХОД УДАЛЁН</b>\n\n"
        "Налог, распределение, балансы и итоги периода восстановлены.",
        reply_markup=keyboard([
            [("← К истории доходов", "incomehistory:open")],
            [("← Главное меню", "menu:back")],
        ]),
    )


@router.callback_query(F.data.startswith("incomehistory:distribution:"))
async def income_history_distribution(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    operation = find_income_history_operation(callback.from_user.id, operation_id)
    allocator = db.load_allocator(callback.from_user.id)
    if operation is None or allocator is None:
        await callback.message.answer("Не удалось открыть распределение этого поступления.")
        return
    await state.clear()
    await callback.message.answer(
        income_distribution_text(operation, allocator),
        reply_markup=keyboard([
            [("← К доходу", f"incomehistory:detail:{operation_id}")],
            [("← К истории", "incomehistory:open")],
            [("← Главное меню", "menu:back")],
        ]),
    )

async def send_income_analysis(
    message: Message,
    telegram_id: int,
):
    analysis_keyboard = keyboard([
        [("История доходов", "incomehistory:open")],
        [("← Главное меню", "menu:back")],
    ])

    allocator = db.load_allocator(
        telegram_id
    )

    if allocator is None:
        await message.answer(
            "Сначала создайте финансовый профиль "
            "через /start."
        )
        return

    # Берём операции прямо из SQLite.
    # Они уже отсортированы:
    # сначала самые новые.
    operations = db.load_operations(
        telegram_id,
        limit=1000,
    )

    totals: dict[str, Decimal] = {}
    total_income = Decimal("0")

    # ========================================================
    # ВАЖНО
    #
    # Идём от самых новых операций назад.
    # Как только встретили последний period_reset —
    # останавливаемся.
    #
    # Значит учитываются ТОЛЬКО доходы,
    # сделанные после последнего сброса периода.
    # ========================================================

    for operation in operations:

        operation_type = operation.get(
            "type"
        )

        # Дошли до начала текущего периода.
        if operation_type == "period_reset":
            break

        # Остальные типы операций нам не нужны.
        if operation_type != "income_distribution":
            continue

        payload = (
            operation.get("payload")
            or {}
        )

        income_type = str(
            payload.get(
                "income_type",
                "Без типа",
            )
        )

        amount = D(
            payload.get(
                "income",
                Decimal("0"),
            )
        )

        if amount <= 0:
            continue

        totals[income_type] = (
            totals.get(
                income_type,
                Decimal("0"),
            )
            + amount
        )

        total_income += amount

    # ========================================================
    # НЕТ ПОСТУПЛЕНИЙ
    # ========================================================

    if total_income <= 0:

        await send_chart_report(
            message, {}, "АНАЛИЗ ДОХОДОВ",
            "В текущем расчётном периоде "
            "пока нет поступлений.",
            reply_markup=analysis_keyboard,
        )

        return

    await send_chart_report(
        message, totals, "АНАЛИЗ ДОХОДОВ", "",
        subtitle="Источники дохода · текущий расчётный период",
        center_amount=total_income,
        reply_markup=analysis_keyboard,
    )


@router.callback_query(
    F.data == "menu:income_analysis"
)
async def menu_income_analysis(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await send_income_analysis(
        callback.message,
        callback.from_user.id,
    )


@router.callback_query(F.data == "menu:reserves")
async def menu_reserves(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала создайте профиль через /start.")
        return
    rows = [[("Баланс Подушки", "settings:pillow")]]
    if allocator.settings.needs_stabilizer:
        rows.append([("Баланс Стабилизатора", "settings:stabilizer_balance")])
    if allocator.profile_id == "cyclic":
        rows.append([("Баланс Фонда Зарплаты", "settings:intercontract_balance")])
    rows.append([("Главное меню", "menu:back")])
    await callback.message.answer(
        "<b>БАЛАНСЫ РЕЗЕРВОВ</b>\n\n"
        "Выберите резерв и укажите, сколько денег в нём сейчас. "
        "Аллокатор учтёт новую сумму при определении вашего уровня.",
        reply_markup=keyboard(rows),
    )
