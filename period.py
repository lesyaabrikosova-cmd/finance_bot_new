from __future__ import annotations

from datetime import datetime
from aiogram import F, Router
from aiogram.types import CallbackQuery

from storage import db
from ui import keyboard, main_menu_keyboard

router = Router()


@router.callback_query(F.data == "intercontract:start")
async def start_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None or allocator.settings.income_rhythm != "cyclic":
        await callback.message.answer("Межконтрактный период недоступен для этого профиля.")
        return
    break_life = allocator.settings.phase_life("break")
    if break_life is None or not break_life.completed:
        await callback.message.answer(
            "<b>СНАЧАЛА ЗАПОЛНИТЕ ЖИЗНЬ В ПЕРЕРЫВЕ</b>\n\n"
            "Без этих расходов Аллокатор не сможет правильно рассчитать Фонд Зарплаты.",
            reply_markup=keyboard([
                [("Заполнить жизнь в перерыве", "phaselife:fill:break")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
    result = allocator.start_intercontract_break()
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        "<b>ПЕРЕРЫВ МЕЖДУ КОНТРАКТАМИ НАЧАТ</b>\n\n"
        f"Месяцев: <b>{result['months_remaining']}</b>\n"
        f"Плановая зарплата себе: <b>{result['monthly_salary']} ₽</b>.\n\n"
        "Счётчик дохода продолжает учитывать полный цикл: рабочую часть и перерыв.\n\n"
        "<b>РЕЗЕРВ НА СЛЕДУЮЩУЮ РАБОЧУЮ ЧАСТЬ</b>\n"
        f"Нужно подготовить: <b>{result['next_work_obligations']} ₽</b>.\n"
        "Сейчас начинается накопление этого резерва. Он получает приоритет раньше Фонда Зарплаты, "
        "Стабилизатора и Подушки. Если денег пока недостаточно, Аллокатор покажет дефицит и будет "
        "закрывать его из следующих поступлений.\n\n"
        "В начале каждого месяца сначала начните новый расчётный период и добавьте внешние "
        "поступления, если они уже пришли. Затем нажмите «Заплатить себе из Фонда Зарплаты».",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "intercontract:salary")
async def pay_intercontract_salary(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    try:
        amount = allocator.pay_intercontract_salary()
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)
    transfer_text = (
        f"Из Фонда Зарплаты в Баланс жизни переведено <b>{amount} ₽</b>."
        if amount > 0
        else "Перевод из Фонда Зарплаты не потребовался."
    )
    cycle_text = (
        "\n\nВсе запланированные месяцы перерыва проведены. Когда перерыв действительно закончится, "
        "нажмите «Начать рабочую часть»."
        if allocator.state.intercontract_months_remaining <= 0
        else ""
    )
    await callback.message.answer(
        f"{transfer_text}\n\n"
        "Это внутренний перевод: налог и повторное распределение не рассчитываются.\n"
        f"Осталось месяцев: <b>{allocator.state.intercontract_months_remaining}</b>."
        f"{cycle_text}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "intercontract:finish")
async def finish_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    work_life = allocator.settings.phase_life("work")
    if work_life is None or not work_life.completed:
        await callback.message.answer(
            "<b>СНАЧАЛА ЗАПОЛНИТЕ РАБОЧУЮ ЖИЗНЬ</b>\n\n"
            "Так Аллокатор будет знать ваши личные расходы во время работы.",
            reply_markup=keyboard([
                [("Заполнить рабочую жизнь", "phaselife:fill:work")],
                [("← Главное меню", "menu:back")],
            ]),
        )
        return
    missing_obligations = max(
        0,
        allocator.settings.contract_obligations_total
        - allocator.state.contract_obligations_reserve,
    )
    if missing_obligations > 0:
        await callback.message.answer(
            "<b>РЕЗЕРВ НА РАБОЧУЮ ЧАСТЬ НЕ СОБРАН</b>\n\n"
            f"Не хватает <b>{missing_obligations} ₽</b> на обязательства, которые продолжатся во время работы.\n\n"
            "Добавьте доступные деньги через «Распределить текущие деньги» или новое поступление. "
            "Начать работу всё равно можно, но тогда часть обязательств останется без покрытия.",
            reply_markup=keyboard([
                [("← Главное меню", "menu:back")],
                [("Начать работу без полного резерва", "intercontract:finish:force")],
            ]),
        )
        return
    await complete_work_phase_start(callback, allocator)


@router.callback_query(F.data == "intercontract:finish:force")
async def force_finish_intercontract_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        return
    await complete_work_phase_start(callback, allocator)


async def complete_work_phase_start(callback: CallbackQuery, allocator):
    try:
        allocator.start_new_work_phase()
    except ValueError as error:
        await callback.message.answer(str(error))
        return
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        "<b>НАЧАЛАСЬ НОВАЯ РАБОЧАЯ ЧАСТЬ</b>\n\n"
        "Предыдущий финансовый цикл завершён. Счётчик дохода обнулён, и следующие поступления "
        "будут учитываться в новом цикле.\n\n"
        f"Резерв обязательств: <b>{allocator.state.contract_obligations_reserve} ₽</b> / "
        f"<b>{allocator.settings.contract_obligations_total} ₽</b>.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "fundsalary:help")
async def show_fund_salary_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "<b>КАК РАБОТАЕТ ФОНД ЗАРПЛАТЫ</b>\n\n"
        "🏦 Фонд Зарплаты оплачивает обычную жизнь во время планового перерыва между контрактами.\n\n"
        "Каждый месяц:\n"
        "1. Начните новый расчётный период.\n"
        "2. Добавьте уже полученные внешние доходы, если они были.\n"
        "3. Нажмите «Заплатить себе из Фонда Зарплаты».\n"
        "4. Переведите предложенную сумму на карту для повседневных расходов.\n\n"
        "Контракт, подработку, подарок и другие внешние поступления добавляйте через «Новый доход» "
        "под их обычными названиями. Аллокатор не делит деньги по происхождению: все поступления "
        "учитываются в общем доходе текущего финансового цикла.\n\n"
        "Выплата из Фонда Зарплаты — внутренний перевод ваших денег, а не новый доход. Поэтому налог "
        "и повторное распределение не рассчитываются. Если деньги хранятся в валюте, обменяйте только "
        "необходимую для выплаты сумму.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

@router.callback_query(F.data == "period:new")
async def ask_new_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Сначала нужно создать финансовый профиль через /start.")
        return

    await callback.message.answer(
        "📅 <b>НАЧАТЬ НОВЫЙ РАСЧЁТНЫЙ ПЕРИОД?</b>\n\n"
        "Будут обнулены только показатели текущего периода:\n"
        "🔄 Баланс жизни\n"
        "❤️ категории обязательной жизни\n"
        "💚 Бытовой резерв текущего периода\n"
        "💳 деньги, зарезервированные на минимальные платежи этого периода\n"
        "👛 доход и 🏛 налог текущего периода\n\n"
        "<b>Не сбрасываются:</b>\n"
        "🛡️ Подушка\n"
        "💰 общий объём направленных инвестиций\n"
        "⭐️ накопления по целям\n"
        "💳 остатки кредитов и общий объём досрочного погашения\n"
        "⚙️ настройки\n"
        "📜 история операций\n\n"
        "Для Цикличного (контрактного) профиля также сохраняются Фонд Зарплаты и общий доход "
        "текущего финансового цикла.\n\n"
        "Дата начала нового периода будет сохранена автоматически.",
        reply_markup=keyboard([
            [("✅ Начать новый период", "period:confirm")],
            [("Отмена", "period:cancel")],
        ]),
    )

@router.callback_query(F.data == "period:cancel")
async def cancel_new_period(callback: CallbackQuery):
    await callback.answer("Отменено")
    await callback.message.answer(
        "Расчётный период не изменён.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )

@router.callback_query(F.data == "period:confirm")
async def confirm_new_period(callback: CallbackQuery):
    await callback.answer()
    allocator = db.load_allocator(callback.from_user.id)
    if allocator is None:
        await callback.message.answer("Финансовый профиль не найден.")
        return

    allocator.reset_period()
    work_months_left = allocator.advance_work_month()
    allocator.state.period_started_at = datetime.now().isoformat()
    db.save_allocator(callback.from_user.id, allocator)
    db.save_operation(
        callback.from_user.id,
        "period_reset",
        {"started_at": allocator.state.period_started_at, "message": "Начат новый расчётный период"},
    )

    phase_text = ""
    if allocator.settings.income_rhythm == "cyclic" and allocator.state.current_cycle_phase == "work":
        phase_text = (
            f"\n\nВ рабочей части осталось: <b>{work_months_left} мес.</b>"
            + (
                "\nПлановая рабочая часть завершена. Когда работа фактически закончится, нажмите «Начать перерыв»."
                if work_months_left <= 0 else ""
            )
        )
    await callback.message.answer(
        "✅ <b>НОВЫЙ РАСЧЁТНЫЙ ПЕРИОД НАЧАТ</b>\n\n"
        "Баланс жизни и месячные категории начаты заново.\n"
        "Подушка, цели, инвестиции, кредиты и история сохранены. Для Цикличного (контрактного) "
        "профиля Фонд Зарплаты и счётчик полного финансового цикла тоже не сбрасываются."
        f"{phase_text}",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
