import os
import json
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any
from dataclasses import dataclass, field, asdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ============================================
# 1. КЛАССЫ ФИНАНСОВОЙ СИСТЕМЫ (упрощённая версия)
# ============================================

@dataclass
class KZCategory:
    name: str
    amount: Decimal

@dataclass
class Goal:
    name: str
    percent: Decimal

@dataclass
class UserSettings:
    kz: Decimal = Decimal('85000')
    br: Decimal = Decimal('25000')
    tax_rate: Decimal = Decimal('6')
    taxable_income_types: list = field(default_factory=lambda: ['Зарплата'])
    fm_months: int = 4
    kz_categories: list = field(default_factory=lambda: [
        KZCategory('Квартира', Decimal('43000')),
        KZCategory('Транспорт', Decimal('2075')),
        KZCategory('Кот', Decimal('3485')),
        KZCategory('Зарплата', Decimal('36440'))
    ])
    goals: list = field(default_factory=lambda: [
        Goal('Хотелки', Decimal('30')),
        Goal('Продвижение', Decimal('30')),
        Goal('Отпуск', Decimal('20')),
        Goal('Амортизация', Decimal('17')),
        Goal('Подарки', Decimal('3'))
    ])

@dataclass
class MonthlyCounters:
    income: Decimal = Decimal('0')
    tax: Decimal = Decimal('0')
    pillow_month: Decimal = Decimal('0')
    balance_life_month: Decimal = Decimal('0')
    br_month: Decimal = Decimal('0')
    goals_month: Decimal = Decimal('0')
    investments_month: Decimal = Decimal('0')
    kz_categories_month: Dict[str, Decimal] = field(default_factory=dict)
    goals_details: Dict[str, Decimal] = field(default_factory=dict)

@dataclass
class State:
    pillow: Decimal = Decimal('0')
    mp_pillow: Decimal = Decimal('0')
    fm_pillow: Decimal = Decimal('0')
    stabd_pillow: Decimal = Decimal('0')
    balance_life: Decimal = Decimal('0')
    current_mode: str = '🟠3'
    monthly: MonthlyCounters = field(default_factory=MonthlyCounters)

class FinancialAllocator:
    def __init__(self, settings: UserSettings):
        self.settings = settings
        self.state = State()
        self.kz = settings.kz
        self.br = settings.br
        self.uz = self.kz + self.br
        self.fm_limit = Decimal(settings.fm_months) * self.kz
        self.kz_total = self.kz
        self.stabd_full_limit = self.uz
        
        # Инициализация месячных счетчиков
        for cat in settings.kz_categories:
            self.state.monthly.kz_categories_month[cat.name] = Decimal('0')
        for goal in settings.goals:
            self.state.monthly.goals_details[goal.name] = Decimal('0')
    
    def process_income(self, amount: Decimal, income_type: str) -> Dict:
        """Обработка дохода (упрощённая версия)"""
        # Налог
        tax = Decimal('0')
        if income_type in self.settings.taxable_income_types:
            tax = amount * (self.settings.tax_rate / Decimal('100'))
        
        sum_to_distribute = amount - tax
        
        # Обновляем счётчики
        self.state.monthly.income += amount
        self.state.monthly.tax += tax
        
        # Упрощённое распределение: 30% в подушку, 30% в КЖ, 20% в БР, 20% в цели
        to_pillow = sum_to_distribute * Decimal('0.3')
        to_kz = sum_to_distribute * Decimal('0.3')
        to_br = sum_to_distribute * Decimal('0.2')
        to_goals = sum_to_distribute * Decimal('0.2')
        
        # Подушка
        self.state.pillow += to_pillow
        self.state.fm_pillow += to_pillow
        self.state.monthly.pillow_month += to_pillow
        
        # КЖ (распределяем по категориям)
        total_kz = sum(cat.amount for cat in self.settings.kz_categories)
        for cat in self.settings.kz_categories:
            if total_kz > 0:
                share = cat.amount / total_kz
                cat_amount = to_kz * share
                self.state.monthly.kz_categories_month[cat.name] += cat_amount
                self.state.monthly.balance_life_month += cat_amount
        
        # БР
        self.state.monthly.br_month += to_br
        
        # Цели
        total_goal_pct = sum(goal.percent for goal in self.settings.goals)
        for goal in self.settings.goals:
            if total_goal_pct > 0:
                goal_amount = to_goals * (goal.percent / total_goal_pct)
                self.state.monthly.goals_details[goal.name] += goal_amount
                self.state.monthly.goals_month += goal_amount
        
        return {
            'amount': amount,
            'income_type': income_type,
            'tax': tax,
            'distributed': sum_to_distribute,
            'pillow': self.state.pillow,
            'balance_life': self.state.balance_life
        }
    
    def get_summary(self) -> Dict:
        return {
            'monthly': self.state.monthly,
            'pillow': self.state.pillow,
            'fm_pillow': self.state.fm_pillow,
            'stabd_pillow': self.state.stabd_pillow,
            'balance_life': self.state.balance_life,
            'current_mode': self.state.current_mode,
            'fm_limit': self.fm_limit,
            'uz': self.uz
        }

# ============================================
# 2. ХРАНЕНИЕ ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ
# ============================================

class UserDataManager:
    def __init__(self, data_dir="user_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
    
    def get_user_file(self, user_id: int) -> str:
        return os.path.join(self.data_dir, f"user_{user_id}.json")
    
    def load_user_data(self, user_id: int) -> Dict:
        file_path = self.get_user_file(user_id)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def save_user_data(self, user_id: int, data: Dict):
        file_path = self.get_user_file(user_id)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    
    def user_exists(self, user_id: int) -> bool:
        return os.path.exists(self.get_user_file(user_id))

# ============================================
# 3. TELEGRAM БОТ
# ============================================

class FinanceBot:
    def __init__(self, token: str):
        self.token = token
        self.user_manager = UserDataManager()
        self.app = Application.builder().token(token).build()
        self._setup_handlers()
    
    def _setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("income", self.cmd_income))
        self.app.add_handler(CommandHandler("summary", self.cmd_summary))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("reset", self.cmd_reset))
        self.app.add_handler(CallbackQueryHandler(self.callback_handler))
    
    def get_allocator(self, user_id: int) -> FinancialAllocator:
        user_data = self.user_manager.load_user_data(user_id)
        
        if user_data is None:
            settings = UserSettings()
            allocator = FinancialAllocator(settings)
            self.user_manager.save_user_data(user_id, {'settings': asdict(settings), 'state': asdict(allocator.state)})
            return allocator
        
        settings = UserSettings(
            kz=Decimal(str(user_data['settings'].get('kz', 85000))),
            br=Decimal(str(user_data['settings'].get('br', 25000))),
            tax_rate=Decimal(str(user_data['settings'].get('tax_rate', 6))),
            fm_months=user_data['settings'].get('fm_months', 4)
        )
        
        allocator = FinancialAllocator(settings)
        
        # Восстанавливаем состояние
        state_data = user_data.get('state', {})
        allocator.state.pillow = Decimal(str(state_data.get('pillow', 0)))
        allocator.state.fm_pillow = Decimal(str(state_data.get('fm_pillow', 0)))
        allocator.state.stabd_pillow = Decimal(str(state_data.get('stabd_pillow', 0)))
        allocator.state.balance_life = Decimal(str(state_data.get('balance_life', 0)))
        allocator.state.current_mode = state_data.get('current_mode', '🟠3')
        
        return allocator
    
    def save_allocator_state(self, user_id: int, allocator: FinancialAllocator):
        self.user_manager.save_user_data(user_id, {
            'settings': asdict(allocator.settings),
            'state': asdict(allocator.state)
        })
    
    # ============================================
    # ОБРАБОТЧИКИ КОМАНД
    # ============================================
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.user_manager.user_exists(user_id):
            allocator = self.get_allocator(user_id)
            self.save_allocator_state(user_id, allocator)
        
        keyboard = [
            [InlineKeyboardButton("💰 Добавить доход", callback_data="add_income")],
            [InlineKeyboardButton("📊 Сводка", callback_data="summary")],
            [InlineKeyboardButton("📈 Статус", callback_data="status")],
            [InlineKeyboardButton("🆘 Помощь", callback_data="help")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"👋 Привет! Я Финансовый AI-аллокатор.\n\n"
            f"📌 Что я умею:\n"
            f"• Распределять доход\n"
            f"• Считать налоги\n"
            f"• Отслеживать подушку безопасности\n"
            f"• Помогать копить на цели\n\n"
            f"Начни с добавления дохода!",
            reply_markup=reply_markup
        )
    
    async def cmd_income(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        try:
            if not context.args:
                await update.message.reply_text(
                    "❌ Укажи сумму и тип дохода\n"
                    "Пример: /income 180000 Зарплата"
                )
                return
            
            amount = Decimal(str(context.args[0]))
            income_type = context.args[1] if len(context.args) > 1 else "Зарплата"
            
            allocator = self.get_allocator(user_id)
            result = allocator.process_income(amount, income_type)
            self.save_allocator_state(user_id, allocator)
            
            response = f"✅ **ДОХОД ОБРАБОТАН!**\n\n"
            response += f"💰 Сумма: {amount:,.2f} ₽\n"
            response += f"📌 Тип: {income_type}\n"
            response += f"🏛️ Налог: {result['tax']:,.2f} ₽\n"
            response += f"📊 К распределению: {result['distributed']:,.2f} ₽\n\n"
            response += f"🛟 Подушка: {result['pillow']:,.2f} ₽\n"
            
            await update.message.reply_text(response, parse_mode='Markdown')
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}\nИспользуй: /income 180000 Зарплата")
    
    async def cmd_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        allocator = self.get_allocator(user_id)
        summary = allocator.get_summary()
        
        response = f"📊 **СВОДНАЯ ТАБЛИЦА**\n\n"
        response += f"📈 Доход (за месяц): {summary['monthly'].income:,.2f} ₽\n"
        response += f"🏛️ Налог (за месяц): {summary['monthly'].tax:,.2f} ₽\n\n"
        
        response += f"🛟 **ПОДУШКА:**\n"
        response += f"• За месяц: {summary['monthly'].pillow_month:,.2f} ₽\n"
        response += f"• Всего: {summary['pillow']:,.2f} ₽\n"
        response += f"• ФМ: {summary['fm_pillow']:,.2f} ₽ / {summary['fm_limit']:,.2f} ₽\n\n"
        
        response += f"❤️ **КРИТИЧЕСКАЯ ЖИЗНЬ:**\n"
        for cat, amount in summary['monthly'].kz_categories_month.items():
            if amount > 0:
                response += f"• {cat}: {amount:,.2f} ₽\n"
        
        response += f"\n🟢 **БЫТОВОЙ РЕЗЕРВ:**\n"
        response += f"• За месяц: {summary['monthly'].br_month:,.2f} ₽\n\n"
        
        response += f"🟡 **ЦЕЛИ:**\n"
        response += f"• За месяц: {summary['monthly'].goals_month:,.2f} ₽\n"
        for goal, amount in summary['monthly'].goals_details.items():
            if amount > 0:
                response += f"• {goal}: {amount:,.2f} ₽\n"
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        allocator = self.get_allocator(user_id)
        summary = allocator.get_summary()
        
        response = f"📈 **ТЕКУЩИЙ СТАТУС**\n\n"
        response += f"⚙️ Режим: {summary['current_mode']}\n"
        response += f"🛟 Подушка: {summary['pillow']:,.2f} ₽\n"
        response += f"📊 Баланс жизни: {summary['balance_life']:,.2f} ₽\n"
        
        await update.message.reply_text(response, parse_mode='Markdown')
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
📚 **ПОМОЩЬ ПО КОМАНДАМ**

/start - Главное меню
/income [сумма] [тип] - Добавить доход
/summary - Сводная таблица
/status - Текущий статус
/reset - Сбросить месяц
/help - Эта помощь

📱 **КОМАНДЫ В КНОПКАХ:**
• 💰 Добавить доход - введи сумму и тип
• 📊 Сводка - вся статистика за месяц
• 📈 Статус - текущие балансы
        """
        await update.message.reply_text(help_text)
    
    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        allocator = self.get_allocator(user_id)
        allocator.state.monthly = MonthlyCounters()
        self.save_allocator_state(user_id, allocator)
        await update.message.reply_text("✅ Расчётный период сброшен!")
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "add_income":
            await query.edit_message_text(
                "💰 **ДОБАВИТЬ ДОХОД**\n\n"
                "Используй команду:\n"
                "`/income [сумма] [тип]`\n\n"
                "Пример:\n"
                "`/income 180000 Зарплата`",
                parse_mode='Markdown'
            )
        elif query.data == "summary":
            await self.cmd_summary(update, context)
        elif query.data == "status":
            await self.cmd_status(update, context)
        elif query.data == "help":
            await self.cmd_help(update, context)
        elif query.data == "reset":
            await self.cmd_reset(update, context)

# ============================================
# 4. ЗАПУСК БОТА
# ============================================

def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    
    if not token:
        print("❌ Ошибка: TELEGRAM_TOKEN не найден!")
        print("1. Найди @BotFather в Telegram")
        print("2. Создай бота командой /newbot")
        print("3. Скопируй токен")
        print("4. В Railway добавь переменную окружения TELEGRAM_TOKEN")
        return
    
    bot = FinanceBot(token)
    print("🚀 Бот запущен!")
    bot.app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()