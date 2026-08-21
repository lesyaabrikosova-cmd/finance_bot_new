from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from aiogram import (
    Bot,
    Dispatcher,
    F,
    Router,
)

from aiogram.client.default import (
    DefaultBotProperties,
)

from aiogram.enums import ParseMode

from aiogram.filters import (
    Command,
)

from aiogram.fsm.context import (
    FSMContext,
)

from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from financial_engine import (
    MODE_NAMES,
    MODE_TITLES,
    FinancialAllocator,
    fmt_money,
)

from onboarding import (
    router as onboarding_router,
)

from income import (
    router as income_router,
)

from period import (
    router as period_router,
)

from forecast import router as forecast_router

from settings_editor import (
    router as settings_router,
)

from dashboard import (
    router as dashboard_router,
)

from taxes import router as taxes_router

from ui import (
    keyboard,
    main_menu_keyboard,
)

from storage import db


# ============================================================
# ЗАГРУЗКА НАСТРОЕК
# ============================================================


load_dotenv()


BOT_TOKEN = os.getenv(
    "BOT_TOKEN"
)


if not BOT_TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN.\n\n"
        "Создайте файл .env рядом с bot.py "
        "и добавьте строку:\n\n"
        "BOT_TOKEN=ваш_токен"
    )


# ============================================================
# ROUTER ОСНОВНОГО ИНТЕРФЕЙСА
# ============================================================


router = Router()


# ============================================================
# КНОПКИ
# ============================================================


# ============================================================
# ПРОВЕРКА НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ
# ============================================================


async def get_allocator_or_warn(
    message: Message,
) -> FinancialAllocator | None:

    allocator = db.load_allocator(
        message.from_user.id
    )

    if allocator is None:

        await message.answer(
            "Сначала нужно создать финансовый профиль.\n\n"
            "Отправьте команду /start и пройдите "
            "первоначальную настройку."
        )

        return None

    return allocator


# ============================================================
# КОМАНДА /MENU
# ============================================================


@router.message(
    Command("menu")
)
async def command_menu(
    message: Message,
):

    allocator = db.load_allocator(
        message.from_user.id
    )

    if allocator is None:

        await message.answer(
            "Финансовый профиль пока не настроен.\n\n"
            "Начните с команды /start."
        )

        return

    await message.answer(
        "🧪 <b>ФИНАНСОВЫЙ АЛЛОКАТОР</b>\n\n"
        "Что хотите сделать?",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


# ============================================================
# КОМАНДА /STATE
# ============================================================


@router.message(
    Command("state")
)
async def command_state(
    message: Message,
):

    allocator = await get_allocator_or_warn(
        message
    )

    if allocator is None:
        return

    await send_state(
        message,
        allocator,
    )


@router.callback_query(
    F.data == "menu:state"
)
async def menu_state(
    callback: CallbackQuery,
):

    await callback.answer()

    allocator = db.load_allocator(
        callback.from_user.id
    )

    if allocator is None:

        await callback.message.answer(
            "Сначала выполните /start."
        )

        return

    await send_state(
        callback.message,
        allocator,
    )


# ============================================================
# СОСТОЯНИЕ
# ============================================================


async def send_state(
    message: Message,
    allocator: FinancialAllocator,
):

    settings = allocator.settings
    state = allocator.state

    mode = allocator.active_mode()

    next_info = allocator.next_mode_info()

    profile_debt = (
        "с долгами"
        if any(
            credit.active
            for credit in settings.credits
        )
        else "без долгов"
    )

    profile_names = {
        "monthly": "Стабильный",
        "irregular": "Сдельный",
        "cyclic": "Контрактный (цикличный)",
    }
    text = (
        "🧭 <b>ТЕКУЩЕЕ СОСТОЯНИЕ</b>\n\n"

        f"👤 Профиль: "
        f"<b>{profile_names.get(settings.income_rhythm, settings.employment_type)}, "
        f"{profile_debt}</b>\n\n"

        f"⚙️ Активный режим: "
        f"<b>{MODE_NAMES[mode]} "
        f"{MODE_TITLES[mode]}</b>\n\n"

        f"💰 Доход за период: "
        f"<b>{fmt_money(state.period_income)} ₽</b>\n"

        f"🏛 Налог за период: "
        f"<b>{fmt_money(state.period_tax)} ₽</b>\n\n"

        f"🔄 Баланс жизни: "
        f"<b>{fmt_money(state.life_balance)} ₽</b>\n"

        f"🛡️ Подушка всего: "
        f"<b>{fmt_money(state.pillow_balance)} ₽</b>\n"

        f"📈 Инвестиции всего: "
        f"<b>{fmt_money(state.investments)} ₽</b>\n"

        f"💳 Досрочно погашено: "
        f"<b>{fmt_money(state.early_repayment)} ₽</b>"
    )

    if settings.income_rhythm == "cyclic":
        text += (
            "\n\n<b>Фонд Зарплаты</b>: "
            f"<b>{fmt_money(state.intercontract_reserve)} ₽</b> / "
            f"{fmt_money(settings.intercontract_full_limit)} ₽"
        )

    if next_info:

        text += (
            "\n\n🏆 До следующего режима "
            f"{next_info['next_name']} осталось:\n"
            f"<b>{fmt_money(next_info['remaining'])} ₽</b>"
        )

    if settings.developer_mode:

        text += (
            "\n\n🛠 <b>СЛОИ ПОДУШКИ</b>\n\n"

            f"Минимальная: "
            f"{fmt_money(state.pillow_minimum)} ₽ "
            f"/ "
            f"{fmt_money(settings.minimum_reserve_limit)} ₽\n"

            f"Фонд Зарплаты: "
            f"{fmt_money(state.intercontract_reserve)} ₽ / "
            f"{fmt_money(settings.intercontract_full_limit)} ₽\n"

            f"Форс-мажорная: "
            f"{fmt_money(state.pillow_force_majeure)} ₽ "
            f"/ "
            f"{fmt_money(settings.force_majeure_limit)} ₽\n"

            f"Стабилизатор: "
            f"{fmt_money(state.pillow_stabilizer)} ₽ "
            f"/ "
            f"{fmt_money(settings.stabilizer_full_limit)} ₽"
        )

    await message.answer(
        text,
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


# ============================================================
# ПОМОЩЬ
# ============================================================


HELP_TEXT = """
❓ <b>КАК РАБОТАЕТ ФИНАНСОВЫЙ АЛЛОКАТОР</b>

Обычный трекер расходов рассказывает, куда деньги уже ушли.

Аллокатор работает наоборот: когда деньги приходят, он заранее решает, какую часть оставить на обязательную жизнь, какую отправить в резерв, на цели, погашение долгов или инвестиции.

<b>🔴 Обязательная жизнь</b>

Деньги на необходимые ежемесячные расходы: жильё, продукты, коммунальные услуги, связь, транспорт, лекарства и другие обязательства.

<b>🟢 Бытовой резерв</b>

Деньги на нерегулярные, но нормальные жизненные расходы: одежду, ремонт, кафе, подарки, стрижку, бытовые покупки и подобные траты.

<b>🛡️ Финансовая подушка</b>

Аллокатор постепенно формирует защитный запас.

Если есть долги, сначала создаётся небольшая минимальная подушка.

После закрытия долгов формируется более серьёзная форс-мажорная подушка.

Для человека с нерегулярным доходом дополнительно создаётся стабилизатор дохода — запас на слабые месяцы, отпуск или временное отсутствие заказов.

<b>💳 Долги</b>

Когда уровень безопасности позволяет, алгоритм начинает направлять свободные деньги на досрочное погашение.

<b>⭐️ Цели</b>

На более устойчивых этапах открывается накопление на отпуск, подарки, технику, ремонт и любые другие цели.

<b>📈 Инвестиции</b>

После достижения определённых уровней финансовой устойчивости часть дохода начинает направляться на долгосрочный капитал.

<b>Главный принцип</b>

Каждое новое поступление проходит через систему последовательно. Сначала защищается текущая жизнь и финансовая устойчивость, а уже затем свободные деньги направляются дальше.

Вам не нужно самостоятельно считать проценты. Добавляйте реальные поступления, а бот рассчитает распределение.
"""


@router.message(
    Command("help")
)
async def command_help(
    message: Message,
):

    await message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.callback_query(
    F.data == "menu:help"
)
async def menu_help(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# КРЕДИТЫ
# ============================================================


@router.callback_query(
    F.data == "menu:credits"
)
async def menu_credits(
    callback: CallbackQuery,
):

    await callback.answer()

    allocator = db.load_allocator(
        callback.from_user.id
    )

    if allocator is None:
        return

    credits = allocator.settings.credits

    if not credits:

        await callback.message.answer(
            "💳 <b>КРЕДИТЫ</b>\n\n"
            "У вас нет активных кредитов "
            "в финансовом профиле.",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )

        return

    lines = [
        "💳 <b>КРЕДИТНЫЙ РЕЕСТР</b>",
        "",
    ]

    total = 0

    for number, credit in enumerate(
        credits,
        start=1,
    ):

        total += credit.principal_balance

        status_icon = (
            "🟢"
            if not credit.active
            else "🔴"
        )

        lines.extend([
            f"<b>{number}. {credit.name}</b>",
            f"Остаток: "
            f"{fmt_money(credit.principal_balance)} ₽",
            f"Ставка: {credit.annual_rate}%",
            f"Мин. платёж: "
            f"{fmt_money(credit.minimum_payment)} ₽",
            f"{status_icon} {credit.status}",
            "",
        ])

    lines.append(
        f"Общий остаток долга: "
        f"<b>{fmt_money(total)} ₽</b>"
    )

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# ЦЕЛИ
# ============================================================


@router.callback_query(
    F.data == "menu:goals"
)
async def menu_goals(
    callback: CallbackQuery,
):

    await callback.answer()

    allocator = db.load_allocator(
        callback.from_user.id
    )

    if allocator is None:
        return

    goals = allocator.settings.goals

    if not goals:

        await callback.message.answer(
            "⭐️ <b>ЦЕЛИ</b>\n\n"
            "Отдельные категории целей пока "
            "не настроены.\n\n"
            "Когда алгоритм начнёт направлять "
            "деньги на цели, они будут учитываться "
            "в общей категории «Цели (всего)».",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )

        return

    lines = [
        "⭐️ <b>ФИНАНСОВЫЕ ЦЕЛИ</b>",
        "",
    ]

    for goal in goals:

        balance = (
            allocator.state.goal_balances.get(
                goal.name,
                0,
            )
        )

        lines.append(
            f"⭐️ <b>{goal.name}</b>\n"
            f"Доля: {goal.percentage}%\n"
            f"Накоплено: {fmt_money(balance)} ₽\n"
        )

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# СВОДКА — ПОКА БАЗОВАЯ
# ============================================================


@router.callback_query(
    F.data == "menu:summary"
)
async def menu_summary(
    callback: CallbackQuery,
):

    await callback.answer()

    allocator = db.load_allocator(
        callback.from_user.id
    )

    if allocator is None:
        return

    state = allocator.state

    await callback.message.answer(
        "📋 <b>СВОДКА ТЕКУЩЕГО ПЕРИОДА</b>\n\n"

        f"💰 Доход: "
        f"<b>{fmt_money(state.period_income)} ₽</b>\n"

        f"🏛 Налог: "
        f"<b>{fmt_money(state.period_tax)} ₽</b>\n"

        f"🛡️ Подушка всего: "
        f"<b>{fmt_money(state.pillow_balance)} ₽</b>\n"

        f"📈 Инвестиции всего: "
        f"<b>{fmt_money(state.investments)} ₽</b>\n\n"

        "Полную сводную таблицу по правилам "
        "исходного алгоритма подключим после "
        "обработчика поступлений.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# НАЗАД
# ============================================================


@router.callback_query(
    F.data == "menu:back"
)
async def menu_back(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.answer(
        "🧪 <b>ФИНАНСОВЫЙ АЛЛОКАТОР</b>\n\n"
        "Выберите действие.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


# ============================================================
# НЕИЗВЕСТНОЕ СООБЩЕНИЕ
# ============================================================


@router.message()
async def unknown_message(
    message: Message,
    state: FSMContext,
):

    current_state = await state.get_state()

    # Если человек находится внутри какого-либо
    # мастера FSM, этот обработчик не должен мешать.
    if current_state is not None:
        return

    allocator = db.load_allocator(
        message.from_user.id
    )

    if allocator is None:

        await message.answer(
            "Я пока не знаю ваш финансовый профиль.\n\n"
            "Отправьте /start, и я проведу вас "
            "через настройку."
        )

        return

    await message.answer(
        "Не понял команду.\n\n"
        "Используйте кнопки меню или команду /menu.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


# ============================================================
# TELEGRAM COMMAND MENU
# ============================================================


async def set_bot_commands(
    bot: Bot,
):

    commands = [
        BotCommand(
            command="start",
            description="Начать / настроить профиль",
        ),
        BotCommand(
            command="menu",
            description="Главное меню",
        ),
        BotCommand(
            command="state",
            description="Мой режим",
        ),
        BotCommand(
            command="about",
            description="От разработчика",
        ),
        BotCommand(
            command="help",
            description="Как пользоваться ботом",
        ),
    ]

    await bot.set_my_commands(
        commands
    )


# ============================================================
# ЗАПУСК
# ============================================================


async def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher()

    # Мастер первоначальной настройки
    dp.include_router(
        onboarding_router
    )

    # Добавление и распределение доходов
    dp.include_router(
        income_router
    )
    # Главное меню, Балансы, режим, помощь
    # ВАЖНО: этот роутер должен быть ДО settings_router.
    dp.include_router(
        dashboard_router
    )

    dp.include_router(taxes_router)

    # Новый расчётный период
    dp.include_router(
        period_router
    )
    dp.include_router(forecast_router)

    # Редактирование пользовательских настроек
    dp.include_router(
        settings_router
    )

    # Старые обработчики и остальные команды
    dp.include_router(
        router
    )

    await set_bot_commands(
        bot
    )

    logging.info(
        "Финансовый Аллокатор запущен."
    )

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:
        db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        print(
            "\nБот остановлен."
        )
