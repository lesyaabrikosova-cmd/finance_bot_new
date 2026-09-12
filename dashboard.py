from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
import re

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
from income_deletion import IncomeDeletionError, delete_income_safely


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


class PeriodAllocations(dict):
    """Allocation totals with presentation metadata kept beside the mapping."""

    def __init__(
        self,
        *args,
        envelope_kinds: dict[str, str] | None = None,
        period_income: Decimal | None = None,
        period_tax: Decimal | None = None,
        has_income_records: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.envelope_kinds = dict(envelope_kinds or {})
        self.period_income = None if period_income is None else D(period_income)
        self.period_tax = None if period_tax is None else D(period_tax)
        self.has_income_records = bool(has_income_records)


def period_ledger_snapshot(
    operations: list[dict],
) -> tuple[Decimal, Decimal, dict[str, Decimal], bool]:
    """Fold the open-period ledger, including explicit envelope transfers."""
    period_income = Decimal("0")
    period_tax = Decimal("0")
    allocations: PeriodAllocations = PeriodAllocations()
    key_by_identity: dict[str, str] = {}
    transfers: list[dict] = []
    has_income_records = False

    # Database rows are newest first. Additive income totals are order
    # independent; transfers are replayed oldest first afterwards.
    for operation in operations:
        operation_type = operation.get("type")
        if operation_type == "period_reset":
            break
        payload = operation.get("payload") or {}
        if operation_type == "envelope_transfer":
            transfers.append(payload)
            continue
        if operation_type != "income_distribution":
            continue

        has_income_records = True
        period_income += D(payload.get("income", 0))
        period_tax += D(payload.get("tax", 0))
        envelope_ids = payload.get("envelope_ids") or {}
        envelope_kinds = payload.get("envelope_kinds") or {}
        for raw_key, value in (payload.get("allocations") or {}).items():
            key = str(raw_key)
            allocations[key] = allocations.get(key, Decimal("0")) + D(value)
            identity = str(envelope_ids.get(raw_key, envelope_ids.get(key, "")) or "")
            if identity:
                key_by_identity[identity] = key
            kind = str(envelope_kinds.get(raw_key, envelope_kinds.get(key, "")) or "")
            if kind:
                allocations.envelope_kinds[key] = kind

    for transfer in reversed(transfers):
        source_identity = str(transfer.get("source_id") or "")
        destination_identity = str(transfer.get("destination_id") or "")
        source = key_by_identity.get(source_identity) or str(transfer.get("source") or "")
        destination = (
            key_by_identity.get(destination_identity)
            or str(transfer.get("destination") or "")
        )
        if not source or not destination or source == destination:
            continue
        available = max(Decimal("0"), D(allocations.get(source, 0)))
        requested = max(Decimal("0"), D(transfer.get("amount", 0)))
        # A goal transfer may contain its all-time bank balance. The period
        # report moves only the part actually earned in this open period.
        moved = min(available, requested)
        if moved <= 0:
            continue
        remaining = available - moved
        if remaining > 0:
            allocations[source] = remaining
        else:
            allocations.pop(source, None)
            allocations.envelope_kinds.pop(source, None)
        allocations[destination] = allocations.get(destination, Decimal("0")) + moved
        destination_kind = str(transfer.get("destination_kind") or "")
        if destination_kind:
            allocations.envelope_kinds[destination] = destination_kind
        if destination_identity:
            key_by_identity[destination_identity] = destination

    allocations.period_income = period_income
    allocations.period_tax = period_tax
    allocations.has_income_records = has_income_records
    return period_income, period_tax, allocations, has_income_records


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

    # SQLite treats LIMIT -1 as "all rows". A period can contain more than
    # 1000 service and income records, so a fixed slice would silently remove
    # the oldest part of the still-open period from the report.
    operations = db.load_operations(
        telegram_id,
        limit=-1,
    )

    period_income, period_tax, result, has_income_records = period_ledger_snapshot(
        operations
    )

    # История доходов — единственный источник, который содержит все операции
    # до и после смены подписи конверта. Состояние оставляем лишь как запасной
    # вариант для старых профилей, где журнал ещё пуст.
    if has_income_records:
        return result

    stored = getattr(allocator.state, "period_allocations", None) or {}
    return PeriodAllocations(
        {key: D(value) for key, value in stored.items()},
        period_income=D(getattr(allocator.state, "period_income", 0)),
        period_tax=D(getattr(allocator.state, "period_tax", 0)),
        has_income_records=False,
    )


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
    """Return every positive flow of the current period in UI order.

    ``load_operations`` has already resolved current labels by immutable IDs.
    A deleted identity is deliberately retained under an internal
    ``· прежний <id>`` suffix.  The chart turns that suffix into a readable
    archived label instead of dropping the money or merging it with a newly
    created envelope that happens to reuse the same name.
    """
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
    reserve_colors = (
        '#24734A', '#2F8F5B', '#48A66F', '#1F6440',
        '#65B985', '#3A8055', '#79C596', '#185337',
    )
    archived_suffix = re.compile(r"^(?P<name>.+?) · прежний (?P<id>[^ ]+)$")
    archived_counts: dict[tuple[str, str], int] = {}

    def readable_name(raw_name: str, kind: str) -> tuple[str, str, bool]:
        """Hide the technical ID while keeping archived identities distinct."""
        raw_name = str(raw_name)
        match = archived_suffix.match(raw_name)
        if match is None:
            return raw_name, raw_name, False
        base = match.group("name")
        identity = match.group("id")
        key = (kind, base)
        archived_counts[key] = archived_counts.get(key, 0) + 1
        number = archived_counts[key]
        noun = {
            "life": "прежняя категория",
            "reserve": "прежний конверт",
            "goal": "прежняя позиция",
        }.get(kind, "прежний конверт")
        suffix = noun if number == 1 else f"{noun} {number}"
        return f"{base} · {suffix}", identity, True

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

    consumed: set[str] = set()
    planned_tax = D(allocations.get('КЖ:Налоги', 0))
    direct_tax = D(allocations.get('Налог', 0))
    ledger_tax = getattr(allocations, 'period_tax', None)
    if ledger_tax is None:
        # Compatibility for direct calls with a plain dict and legacy users
        # whose operation ledger predates income snapshots.
        ledger_tax = D(allocator.state.period_tax)
    add('Налог', D(ledger_tax) + planned_tax + direct_tax, '#7656D8')
    consumed.update({'КЖ:Налоги', 'Налог'})

    add('Фонд Зарплаты', allocations.get('Фонд Зарплаты', 0), '#00B7E8')
    add('Подушка', allocations.get('Подушка', 0), '#173F8A')
    add('Стабилизатор', allocations.get('Стабилизатор дохода', 0), '#7EC8F5')
    add('Инвестиции', allocations.get('Инвестиции', 0), '#32A9E0')
    add('Минимальные платежи по долгам', allocations.get('Мин. платеж', 0), '#9C7BAB')
    add('Досрочное погашение', allocations.get('Досрочное', 0), '#74608C')
    consumed.update({
        'Фонд Зарплаты', 'Подушка', 'Стабилизатор дохода',
        'Инвестиции', 'Мин. платеж', 'Досрочное',
    })
    work_obligations = [
        (str(key), value)
        for key, value in allocations.items()
        if str(key).startswith('Рабочие обязательства:')
    ]
    for key, value in sorted(work_obligations, key=lambda item: D(item[1]), reverse=True):
        add(key.replace(':', ' · '), value, '#B36C75')
        consumed.add(key)

    settings = getattr(allocator, 'settings', None)
    envelope_kinds = getattr(allocations, 'envelope_kinds', {}) or {}
    category_ids = getattr(settings, 'life_category_ids', {}) or {}
    life = {
        str(key)[3:]: D(value)
        for key, value in allocations.items()
        if str(key).startswith('КЖ:') and str(key) != 'КЖ:Налоги'
    }
    consumed.update(
        str(key) for key in allocations
        if str(key).startswith('КЖ:')
    )
    # Very old ledgers did not keep detailed allocation keys. Only in that
    # case is the state snapshot the best available compatibility source.
    if not life:
        life = {
            str(name): D(value)
            for name, value in (
                getattr(allocator.state, 'period_life_topups', {}) or {}
            ).items()
            if str(name) != 'Налоги'
        }

    salary = life.pop('Зарплата', None)
    used_life_colors = set()
    for raw_name, value in sorted(life.items(), key=lambda item: D(item[1]), reverse=True):
        name, identity, _ = readable_name(raw_name, 'life')
        add(
            f'КМ · {name}', value,
            shade(category_ids.get(raw_name, identity), life_colors, used_life_colors),
        )
    if salary is not None:
        add('КМ · Зарплата', salary, shade(category_ids.get('Зарплата', 'Зарплата'), life_colors, used_life_colors))

    reserve_items = [
        (str(key)[3:], value)
        for key, value in allocations.items()
        if str(key).startswith('БР:')
    ]
    consumed.update(
        str(key) for key in allocations
        if str(key).startswith('БР:')
    )
    used_reserve_colors = set()
    for raw_name, value in sorted(reserve_items, key=lambda item: D(item[1]), reverse=True):
        name, identity, _ = readable_name(raw_name, 'reserve')
        add(
            f'Бытовой резерв · {name}', value,
            shade(
                getattr(settings, 'household_reserve_category_ids', {}).get(
                    raw_name, identity,
                ),
                reserve_colors,
                used_reserve_colors,
            ),
        )
    add('Бытовой резерв', allocations.get('Бытовой резерв', 0), '#24734A')
    consumed.add('Бытовой резерв')

    goal_map = {goal.name: goal for goal in getattr(settings, 'goals', [])}
    goal_items = [
        (key[5:], value, str(envelope_kinds.get(key, "")))
        for key, value in allocations.items()
        if key.startswith('Цели:')
    ]
    consumed.update(
        str(key) for key in allocations
        if str(key).startswith('Цели:')
    )
    used_goal_colors, used_chest_colors = set(), set()
    for raw_name, value, recorded_kind in sorted(
        goal_items, key=lambda item: D(item[1]), reverse=True,
    ):
        clean_name, identity, archived = readable_name(raw_name, 'goal')
        base_name = clean_name.split(' · прежняя позиция', 1)[0]
        goal = None if archived else goal_map.get(base_name)
        is_chest = (
            recorded_kind == 'chest'
            or bool(goal and goal.is_chest)
            or base_name.casefold().startswith('сундук ')
        )
        display = goal_display_name(base_name, is_chest)
        if archived:
            display += clean_name[len(base_name):]
        palette = chest_colors if is_chest else goal_colors
        used = used_chest_colors if is_chest else used_goal_colors
        identity = getattr(goal, 'uid', '') or identity
        add(f'Цели и Сундуки · {display}', value, shade(identity, palette, used))

    # Keep future/legacy one-off destinations visible until the presentation
    # layer receives an explicit family for them. Silently omitting a positive
    # allocation would make the period diagram fail its accounting purpose.
    for key, value in allocations.items():
        key = str(key)
        if key not in consumed:
            add(f'Прочее · {key.replace(":", " · ")}', value, '#8B7D91')
    return values, colors


def period_balance_fallback_text(
    allocator,
    balances: dict[str, Decimal],
    income: Decimal,
    until_critical: Decimal,
    until_sustainable: Decimal,
    next_info: dict | None,
) -> str:
    """Readable complete report when Telegram cannot receive the diagram."""

    def presentation(label: str) -> tuple[int, str, str]:
        fixed = {
            'Налог': (0, '🏛️', 'Налог'),
            'Фонд Зарплаты': (1, '🏦', 'Фонд Зарплаты'),
            'Подушка': (1, '🛡️', 'Подушка'),
            'Стабилизатор': (1, '🛟', 'Стабилизатор'),
            'Инвестиции': (1, '📈', 'Инвестиции'),
            'Минимальные платежи по долгам': (2, '💳', 'Минимальные платежи по долгам'),
            'Досрочное погашение': (2, '💳', 'Досрочное погашение'),
            'Бытовой резерв': (4, '💚', 'Бытовой резерв'),
        }
        if label in fixed:
            return fixed[label]
        if label.startswith('Рабочие обязательства · '):
            return 2, '💳', label.removeprefix('Рабочие обязательства · ')
        if label.startswith('КМ · '):
            return 3, '❤️', label.removeprefix('КМ · ')
        if label.startswith('Бытовой резерв · '):
            return 4, '💚', label.removeprefix('Бытовой резерв · ')
        if label.startswith('Цели и Сундуки · '):
            name = label.removeprefix('Цели и Сундуки · ')
            return 5, ('🧳' if name.casefold().startswith('сундук ') else '⭐️'), name
        return 6, '•', label.removeprefix('Прочее · ')

    allocation_lines: list[str] = []
    previous_group: int | None = None
    for label, amount in balances.items():
        group, icon, display = presentation(label)
        if previous_group is not None and group != previous_group:
            allocation_lines.append('')
        allocation_lines.append(
            f"{icon} <b>{escape(display)}</b> — {rub_plain(amount)} • {pct(D(amount), income)}"
        )
        previous_group = group

    lines = [
        '<b>БАЛАНСЫ ЗА ПЕРИОД</b>',
        '',
        f"💲 <b>Доход</b> — {rub(income)}",
    ]
    if allocation_lines:
        lines.extend([
            '',
            '<blockquote>' + '\n'.join(allocation_lines) + '</blockquote>',
        ])
    lines.extend([
        '—————————',
        f"↺ <b>Баланс жизни</b> — {rub_plain(allocator.state.life_balance)}",
        f"➤ До <b>Критич. минимума</b> — {rub_plain(until_critical)}",
        f"➤ До <b>Устойч. жизни</b> — {rub_plain(until_sustainable)}",
    ])
    if next_info:
        lines.append(
            f"➤ До следующего уровня — {escape(str(next_info['next_name']))}: "
            f"<b>{rub_plain(next_info['remaining'])}</b>"
        )
    else:
        lines.append(
            '➤ До следующего уровня — <b>максимальный уровень достигнут</b>'
        )
    if allocator.settings.developer_mode:
        lines.extend([
            '',
            '<b>ЗАЩИТНЫЕ РЕЗЕРВЫ — УРОВЕНЬ РАЗРАБОТЧИКА</b>',
            f"МП: {rub(allocator.state.pillow_minimum)} / "
            f"{rub(allocator.settings.minimum_reserve_limit)}",
            f"ФМ: {rub(allocator.state.pillow_force_majeure)} / "
            f"{rub(allocator.settings.force_majeure_limit)}",
            f"Стабилизатор дохода: {rub(allocator.state.pillow_stabilizer)} / "
            f"{rub(allocator.settings.stabilizer_full_limit)}",
        ])
    return '\n'.join(lines)


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
            "через /start.",
            reply_markup=keyboard([[("Настроить профиль", "setup:start")]]),
        )

        return

    settings = allocator.settings
    state = allocator.state

    allocations = (
        get_period_allocations(
            allocator,
            telegram_id,
        )
    )

    # The ledger is the source of truth for every number in this report.
    # Reading the centre total and the slices from different snapshots can
    # produce a mathematically impossible diagram after a migration or a
    # repaired/deleted operation.
    income = D(
        getattr(allocations, "period_income", None)
        if getattr(allocations, "period_income", None) is not None
        else state.period_income
    )
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

    critical_minimum = D(
        settings.critical_life
    )

    until_kzh = max(
        Decimal("0"),
        critical_minimum
        - allocator.critical_life_progress,
    )

    until_uzh = allocator.sustainable_life_remaining

    next_info = (
        allocator.next_mode_info()
    )

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
        fallback_text=period_balance_fallback_text(
            allocator,
            balances,
            income,
            until_kzh,
            until_uzh,
            next_info,
        ),
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
        for operation in db.load_operations(telegram_id, limit=-1)
        if operation.get("type") == "income_distribution"
    ]


def rebuild_period_analytics_from_history(allocator, telegram_id: int) -> None:
    """Make chart data match the remaining income ledger after a deletion."""
    operations = db.load_operations(telegram_id, limit=-1)
    period_income, period_tax, allocations, _ = period_ledger_snapshot(operations)
    life_topups = {
        name: Decimal("0")
        for name in allocator.settings.life_categories
    }
    life_topups.setdefault("Зарплата", Decimal("0"))
    for key, amount in allocations.items():
        if str(key).startswith("КЖ:"):
            name = str(key)[3:]
            life_topups[name] = life_topups.get(name, Decimal("0")) + D(amount)

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
    operation_date = income_history_date(operation)
    amount = rub_plain(payload.get('income', 0))
    # Keep the year visible while leaving room for the amount in Telegram's
    # compact inline button. The full type remains on the detail card.
    fixed_length = len(operation_date) + len(amount) + len(" ·  · ")
    available = max(4, 64 - fixed_length)
    if len(income_type) > available:
        income_type = income_type[:available - 1] + "…"
    return f"{operation_date} · {income_type} · {amount}"


def find_income_history_operation(telegram_id: int, operation_id: int) -> dict | None:
    return next(
        (
            operation
            for operation in income_history_operations(telegram_id)
            if operation.get("id") == operation_id
        ),
        None,
    )


def current_period_income_ids(telegram_id: int) -> set[int]:
    """IDs that may be replayed safely inside the still-open period."""
    result: set[int] = set()
    found_reset = False
    operations = db.load_operations(telegram_id, limit=-1)
    for operation in operations:
        if operation.get("type") == "period_reset":
            found_reset = True
            break
        if operation.get("type") == "income_distribution":
            try:
                result.add(int(operation["id"]))
            except (KeyError, TypeError, ValueError):
                continue
    if found_reset:
        return result

    # Compatibility for profiles whose active-period timestamp predates the
    # persistent period_reset event.  In ambiguous legacy history we hide the
    # destructive action instead of treating every old income as current.
    allocator = db.load_allocator(telegram_id)
    raw_start = getattr(getattr(allocator, "state", None), "period_started_at", None)
    if not raw_start:
        return result
    try:
        start_date = datetime.fromisoformat(str(raw_start)).date()
    except (TypeError, ValueError):
        return set()
    safe_ids: set[int] = set()
    operations_by_id = {
        int(operation["id"]): operation
        for operation in operations
        if operation.get("type") == "income_distribution" and operation.get("id") is not None
    }
    for operation_id in result:
        payload = (operations_by_id.get(operation_id) or {}).get("payload") or {}
        try:
            operation_date = date.fromisoformat(str(payload["date"])[:10])
        except (KeyError, TypeError, ValueError):
            continue
        if operation_date >= start_date:
            safe_ids.add(operation_id)
    return safe_ids


def income_history_navigation():
    return keyboard([
        [
            ("← Главное меню", "menu:back"),
            ("← Назад", "incomehistory:open"),
        ],
    ])


def income_note_edit_keyboard(operation_id: int, has_note: bool):
    rows = []
    if has_note:
        rows.append([
            ("🗑️ Удалить заметку", f"incomehistory:note_delete:{operation_id}"),
        ])
    rows.extend([
        [
            ("← Главное меню", "menu:back"),
            ("← Назад", f"incomehistory:note_back:{operation_id}"),
        ],
    ])
    return keyboard(rows)


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
                [
                    ("← Главное меню", "menu:back"),
                    ("← Назад", "menu:income_analysis"),
                ],
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
        [
            ("← Главное меню", "menu:back"),
            ("← Назад", "menu:income_analysis"),
        ],
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
        await message.answer(
            "Это поступление уже недоступно в истории.",
            reply_markup=income_history_navigation(),
        )
        return False
    has_note = bool((operation.get("payload") or {}).get("note"))
    current_period = operation_id in current_period_income_ids(telegram_id)
    edit_row = [
        (
            "✎ Заметка" if has_note else "+ Заметка",
            f"incomehistory:note:{operation_id}",
        ),
    ]
    if current_period:
        edit_row.append(
            ("🗑️ Удалить доход", f"incomehistory:delete:{operation_id}")
        )
    await message.answer(
        income_operation_card_text(operation),
        reply_markup=keyboard([
            [("Показать распределение", f"incomehistory:distribution:{operation_id}")],
            edit_row,
            [
                ("← Главное меню", "menu:back"),
                ("← Назад", "incomehistory:open"),
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
    envelope_kinds = {
        str(key): str(value)
        for key, value in (payload.get("envelope_kinds") or {}).items()
    }

    def add(group: int, emoji: str, name: str, amount) -> None:
        amount = D(amount)
        if amount > 0:
            groups[group].append(f"{emoji} <b>{escape(name)}</b> — {rub_plain(amount)}")

    # Налог с самого дохода и накопления на имущественные/прочие налоги
    # физически лежат в одном банковском конверте. Показываем пользователю
    # одну строку и не заставляем его разбираться во внутренних маршрутах.
    tax_total = (
        D(payload.get("tax", 0))
        + D(allocations.get("КЖ:Налоги", 0))
        + D(allocations.get("Налог", 0))
    )
    add(0, "🏛️", "Налоги", tax_total)
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

    reserve_items = [
        (key[3:], amount)
        for key, amount in allocations.items()
        if key.startswith("БР:")
    ]
    for name, amount in sorted(
        reserve_items, key=lambda item: item[1], reverse=True,
    ):
        add(4, "💚", name, amount)
    add(4, "💚", "Бытовой резерв", allocations.get("Бытовой резерв", 0))
    goal_map = {goal.name: goal for goal in allocator.settings.goals}
    goal_items = [
        (key, key[5:], amount)
        for key, amount in allocations.items()
        if key.startswith("Цели:")
    ]
    for key, name, amount in sorted(
        goal_items, key=lambda item: item[2], reverse=True,
    ):
        goal = goal_map.get(name)
        recorded_kind = envelope_kinds.get(key, "")
        is_chest = recorded_kind == "chest" or bool(goal and goal.is_chest)
        archived = re.match(r"^(?P<name>.+?) · прежний [^ ]+$", name)
        clean_name = archived.group("name") if archived else name
        display_name = goal_display_name(clean_name, is_chest)
        if archived:
            display_name += " · прежняя позиция"
        add(5, "🧳" if is_chest else "⭐️", display_name, amount)

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
        await callback.message.answer(
            "Не удалось открыть эту страницу истории.",
            reply_markup=income_history_navigation(),
        )
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
    operation = find_income_history_operation(callback.from_user.id, operation_id)
    if operation is None:
        await callback.message.answer(
            "Это поступление уже недоступно в истории.",
            reply_markup=income_history_navigation(),
        )
        return
    await state.update_data(history_note_operation_id=operation_id)
    await state.set_state(IncomeHistoryStates.note)
    await callback.message.answer(
        "<b>ЗАМЕТКА К ПОСТУПЛЕНИЮ</b>\n\n"
        "Введите новую короткую заметку. Не более 60 символов.",
        reply_markup=income_note_edit_keyboard(
            operation_id,
            bool((operation.get("payload") or {}).get("note")),
        ),
    )


@router.message(IncomeHistoryStates.note)
async def save_history_income_note(message: Message, state: FSMContext):
    note = " ".join((message.text or "").split())
    data = await state.get_data()
    operation_id = data.get("history_note_operation_id")
    operation = (
        find_income_history_operation(message.from_user.id, operation_id)
        if isinstance(operation_id, int)
        else None
    )
    if operation is None:
        await state.clear()
        await message.answer(
            "Это поступление уже недоступно в истории.",
            reply_markup=income_history_navigation(),
        )
        return
    note_keyboard = income_note_edit_keyboard(
        operation_id,
        bool((operation.get("payload") or {}).get("note")),
    )
    if not note:
        await message.answer(
            "Введите короткую заметку или выберите действие ниже.",
            reply_markup=note_keyboard,
        )
        return
    if len(note) > 60:
        await message.answer(
            "Заметка должна быть не длиннее 60 символов.",
            reply_markup=note_keyboard,
        )
        return
    if not db.update_income_note(
        message.from_user.id,
        operation_id,
        note,
    ):
        await state.clear()
        await message.answer(
            "Не удалось сохранить заметку. Попробуйте открыть доход снова.",
            reply_markup=income_history_navigation(),
        )
        return
    await state.clear()
    await send_income_history_detail(message, message.from_user.id, operation_id)


@router.callback_query(F.data.startswith("incomehistory:note_delete:"))
async def delete_history_income_note(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        operation_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        operation_id = 0
    if not db.update_income_note(callback.from_user.id, operation_id, ""):
        await state.clear()
        await callback.message.answer(
            "Заметку не удалось удалить: поступление больше недоступно.",
            reply_markup=income_history_navigation(),
        )
        return
    await state.clear()
    await send_income_history_detail(
        callback.message,
        callback.from_user.id,
        operation_id,
    )


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
        await callback.message.answer(
            "Это поступление уже недоступно в истории.",
            reply_markup=income_history_navigation(),
        )
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
            [("🗑️ Удалить доход", f"incomehistory:delete_confirm:{operation_id}")],
            [("← К доходу", f"incomehistory:detail:{operation_id}")],
            [
                ("← Главное меню", "menu:back"),
                ("← К истории", "incomehistory:open"),
            ],
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
    try:
        delete_income_safely(
            db,
            callback.from_user.id,
            operation_id,
            reconcile_taxes=reconcile_tax_obligation_balances,
            rebuild_period_analytics=rebuild_period_analytics_from_history,
        )
    except IncomeDeletionError as error:
        await callback.message.answer(
            "<b>ДОХОД НЕ УДАЛЁН</b>\n\n"
            f"{escape(str(error))}\n\n"
            "Бот сохранил все балансы без изменений.",
            reply_markup=keyboard([
                [("← К доходу", f"incomehistory:detail:{operation_id}")],
                [("← К истории", "incomehistory:open")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
    except Exception:
        await callback.message.answer(
            "<b>ДОХОД НЕ УДАЛЁН</b>\n\n"
            "Не удалось завершить удаление. Бот сохранил все балансы без изменений.\n\n"
            "Попробуйте ещё раз или обратитесь в поддержку.",
            reply_markup=keyboard([
                [("← К доходу", f"incomehistory:detail:{operation_id}")],
                [("← К истории", "incomehistory:open")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
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
        await callback.message.answer(
            "Не удалось открыть распределение этого поступления.",
            reply_markup=income_history_navigation(),
        )
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


def income_analysis_totals(allocator, operations: list[dict]) -> dict[str, Decimal]:
    """Aggregate income by immutable type ID and expose readable chart labels.

    Records created before income-type IDs use their (possibly migrated)
    normalized name as a compatibility identity.  Equal visible names that
    belong to different IDs are disambiguated instead of being added together.
    """
    current_by_id = {
        str(identifier): str(name)
        for name, identifier in (
            getattr(allocator.settings, "income_type_ids", {}) or {}
        ).items()
    }
    stored_labels = {
        str(identifier): str(name)
        for identifier, name in (
            getattr(allocator.settings, "income_type_labels", {}) or {}
        ).items()
    }
    grouped: dict[str, dict] = {}

    for operation in operations:
        operation_type = operation.get("type")
        if operation_type == "period_reset":
            break
        if operation_type != "income_distribution":
            continue
        payload = operation.get("payload") or {}
        amount = D(payload.get("income", Decimal("0")))
        if amount <= 0:
            continue
        recorded_name = str(payload.get("income_type", "Без типа"))
        identifier = str(payload.get("income_type_id") or "").strip()
        identity = f"id:{identifier}" if identifier else f"legacy:{recorded_name.casefold()}"
        label = (
            current_by_id.get(identifier)
            or stored_labels.get(identifier)
            or recorded_name
        )
        item = grouped.setdefault(identity, {
            "label": label,
            "amount": Decimal("0"),
            "active": bool(identifier and identifier in current_by_id),
        })
        item["amount"] += amount

    label_counts: dict[str, int] = {}
    for item in grouped.values():
        label_counts[item["label"]] = label_counts.get(item["label"], 0) + 1

    totals: dict[str, Decimal] = {}
    duplicate_indexes: dict[str, int] = {}
    for item in grouped.values():
        label = item["label"]
        if label_counts[label] > 1:
            if item["active"] and label not in totals:
                display_label = label
            else:
                duplicate_indexes[label] = duplicate_indexes.get(label, 0) + 1
                suffix = duplicate_indexes[label]
                display_label = f"{label} · прежний тип"
                if display_label in totals:
                    display_label = f"{display_label} {suffix}"
        else:
            display_label = label
        totals[display_label] = item["amount"]
    return totals


def income_analysis_fallback_text(
    totals: dict[str, Decimal],
    total_income: Decimal,
) -> str:
    """Complete text equivalent used when the chart cannot be delivered."""
    lines = [
        '<b>АНАЛИЗ ДОХОДОВ</b>',
        '',
        f"💲 <b>Доход итого</b> — {rub(total_income)}",
    ]
    if totals:
        lines.append('')
        for name, amount in totals.items():
            lines.append(
                f"<b>{escape(name)}</b> — {rub_plain(amount)} • "
                f"{pct(D(amount), total_income)}"
            )
    else:
        lines.extend(['', 'В текущем расчётном периоде пока нет поступлений.'])
    return '\n'.join(lines)


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
            "через /start.",
            reply_markup=keyboard([[("Настроить профиль", "setup:start")]]),
        )
        return

    # Берём операции прямо из SQLite.
    # Они уже отсортированы:
    # сначала самые новые.
    operations = db.load_operations(
        telegram_id,
        limit=-1,
    )

    totals = dict(sorted(
        income_analysis_totals(allocator, operations).items(),
        key=lambda item: item[1],
        reverse=True,
    ))
    total_income = sum(totals.values(), Decimal("0"))

    # ========================================================
    # НЕТ ПОСТУПЛЕНИЙ
    # ========================================================

    if total_income <= 0:

        await send_chart_report(
            message, {}, "АНАЛИЗ ДОХОДОВ",
            "В текущем расчётном периоде "
            "пока нет поступлений.",
            fallback_text=income_analysis_fallback_text({}, total_income),
            reply_markup=analysis_keyboard,
        )

        return

    await send_chart_report(
        message, totals, "АНАЛИЗ ДОХОДОВ", "",
        subtitle="Источники дохода · текущий расчётный период",
        center_amount=total_income,
        preserve_order=True,
        fallback_text=income_analysis_fallback_text(totals, total_income),
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
    rows.append([("← Главное меню", "menu:back")])
    await callback.message.answer(
        "<b>БАЛАНСЫ РЕЗЕРВОВ</b>\n\n"
        "Выберите резерв и укажите, сколько денег в нём сейчас. "
        "Аллокатор учтёт новую сумму при определении вашего уровня.",
        reply_markup=keyboard(rows),
    )
