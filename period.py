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
    result = allocator.start_intercontract_break()
    db.save_allocator(callback.from_user.id, allocator)
    await callback.message.answer(
        "<b>МЕЖКОНТРАКТНЫЙ ПЕРИОД НАЧАТ</b>\n\n"
        f"Месяцев: <b>{result['months_remaining']}</b>\n"
        f"Плановая зарплата себе: <b>{result['monthly_salary']} ₽</b>.\n\n"
        "Используйте кнопку «Зарплата из резерва» в начале каждого месяца.",
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
    await callback.message.answer(
        f"Из Межконтрактного резерва в Баланс жизни переведено <b>{amount} ₽</b>.\n\n"
        "Это внутренний перевод: налог и повторное распределение не рассчитываются.\n"
        f"Осталось месяцев: <b>{allocator.state.intercontract_months_remaining}</b>.",
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
    allocator.state.period_started_at = datetime.now().isoformat()
    db.save_allocator(callback.from_user.id, allocator)
    db.save_operation(
        callback.from_user.id,
        "period_reset",
        {"started_at": allocator.state.period_started_at, "message": "Начат новый расчётный период"},
    )

    await callback.message.answer(
        "✅ <b>НОВЫЙ РАСЧЁТНЫЙ ПЕРИОД НАЧАТ</b>\n\n"
        "Баланс жизни и месячные категории начаты заново.\n"
        "Подушка, цели, инвестиции, кредиты и история сохранены.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
