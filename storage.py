# storage.py
#
# Хранилище финансового аллокатора.
#
# Использует SQLite.
# Не требует отдельного сервера базы данных.
#
# Основная задача:
# 1. сохранить настройки пользователя;
# 2. сохранить текущее состояние;
# 3. сохранить кредиты;
# 4. сохранить цели;
# 5. сохранить журнал операций;
# 6. восстановить FinancialAllocator после перезапуска бота.


from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Optional

from financial_engine import (
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    PhaseLifeBudget,
    UserSettings,
    normalize_profile_id,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

import os

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(
    os.getenv(
        "ALLOCATOR_DATA_DIR",
        str(BASE_DIR),
    )
)

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

DATABASE_PATH = DATA_DIR / "allocator.db"


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def decimal_to_string(value: Decimal) -> str:
    """
    Decimal нельзя напрямую записывать в SQLite как Decimal.

    Поэтому храним денежные значения строками.

    Например:

        Decimal("12500.50")

    превращается в:

        "12500.50"
    """

    return str(value)


def string_to_decimal(value) -> Decimal:
    """
    Обратное преобразование строки в Decimal.
    """

    if value is None:
        return Decimal("0")

    return Decimal(str(value))


def json_default(value):
    """
    Как превращать специальные Python-типы в JSON.

    Нужно для журналов операций.
    """

    if isinstance(value, Decimal):
        return {
            "__decimal__": str(value)
        }

    if isinstance(value, datetime):
        return {
            "__datetime__": value.isoformat()
        }

    raise TypeError(
        f"Объект типа {type(value)} "
        f"не поддерживается JSON."
    )


def json_object_hook(value):
    """
    Восстанавливает Decimal и datetime
    после чтения JSON.
    """

    if "__decimal__" in value:
        return Decimal(
            value["__decimal__"]
        )

    if "__datetime__" in value:
        return datetime.fromisoformat(
            value["__datetime__"]
        )

    return value


def serialize_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=json_default,
    )


def deserialize_json(value):
    return json.loads(
        value,
        object_hook=json_object_hook,
    )


def serialize_income_types(settings: UserSettings) -> str:
    return serialize_json({
        "version": 10,
        "rates": {
            name: decimal_to_string(rate)
            for name, rate in settings.income_type_tax_rates.items()
        },
        "rhythm": settings.income_rhythm,
        "profile_type": normalize_profile_id(
            settings.profile_type,
            settings.employment_type,
            settings.income_rhythm,
        ),
        "gap_months": decimal_to_string(settings.income_gap_months),
        "work_months": decimal_to_string(settings.income_work_months),
        "reliable_gap_income": decimal_to_string(settings.reliable_gap_income),
        "stabilizer_months": decimal_to_string(settings.stabilizer_target_months),
        "contract_obligations": {
            name: decimal_to_string(amount)
            for name, amount in settings.contract_obligations.items()
        },
        "contract_obligation_storage": dict(settings.contract_obligation_storage),
        "household_reserve_categories": {
            name: decimal_to_string(amount)
            for name, amount in settings.household_reserve_categories.items()
        },
        "historical_gifts_monthly": decimal_to_string(settings.historical_gifts_monthly),
        "protective_stage_c_goals_share": decimal_to_string(
            settings.protective_stage_c_goals_share
        ),
        "gift_guideline_min": decimal_to_string(settings.gift_guideline_min),
        "gift_guideline_max": decimal_to_string(settings.gift_guideline_max),
        "gift_warning_limit": decimal_to_string(settings.gift_warning_limit),
        "phase_life_budgets": {
            phase: {
                "critical_life": decimal_to_string(budget.critical_life),
                "household_reserve": decimal_to_string(budget.household_reserve),
                "life_categories": {
                    name: decimal_to_string(amount)
                    for name, amount in budget.life_categories.items()
                },
                "household_reserve_categories": {
                    name: decimal_to_string(amount)
                    for name, amount in budget.household_reserve_categories.items()
                },
                "historical_gifts_monthly": decimal_to_string(budget.historical_gifts_monthly),
                "currency_code": budget.currency_code,
                "currency_symbol": budget.currency_symbol,
                "exchange_rate_to_rub": decimal_to_string(budget.exchange_rate_to_rub),
                "exchange_rate_mode": budget.exchange_rate_mode,
                "exchange_rate_updated_at": budget.exchange_rate_updated_at,
                "completed": budget.completed,
            }
            for phase, budget in settings.phase_life_budgets.items()
        },
    })


def deserialize_income_types(value, legacy_rate: Decimal) -> tuple[list[str], dict[str, Decimal]]:
    raw = deserialize_json(value)
    if isinstance(raw, dict) and raw.get("version") in {2, 3, 4, 5, 6, 7, 8, 9, 10}:
        rates = {
            str(name): string_to_decimal(rate)
            for name, rate in raw.get("rates", {}).items()
        }
        return [name for name, rate in rates.items() if rate > 0], rates
    legacy_types = [str(name) for name in raw] if isinstance(raw, list) else []
    return legacy_types, {name: legacy_rate for name in legacy_types}


def deserialize_income_rhythm(value) -> dict:
    raw = deserialize_json(value)
    if isinstance(raw, dict) and raw.get("version") in {3, 4, 5, 6, 7, 8, 9, 10}:
        rhythm = str(raw.get("rhythm", "monthly"))
        return {
            "income_rhythm": rhythm,
            "profile_type": str(raw.get("profile_type", "")),
            "income_gap_months": max(Decimal("1"), string_to_decimal(raw.get("gap_months", "1"))),
            "income_work_months": max(Decimal("1"), string_to_decimal(raw.get("work_months", "1"))),
            "reliable_gap_income": max(Decimal("0"), string_to_decimal(raw.get("reliable_gap_income", "0"))),
            "stabilizer_target_months": max(Decimal("1"), string_to_decimal(raw.get("stabilizer_months", "1" if rhythm != "cyclic" else "2"))),
            "contract_obligations": {
                str(name): string_to_decimal(amount)
                for name, amount in raw.get("contract_obligations", {}).items()
            },
            "contract_obligation_storage": {
                str(name): str(envelope)
                for name, envelope in raw.get("contract_obligation_storage", {}).items()
            },
            "household_reserve_categories": {
                str(name): string_to_decimal(amount)
                for name, amount in raw.get("household_reserve_categories", {}).items()
            },
            "historical_gifts_monthly": max(
                Decimal("0"), string_to_decimal(raw.get("historical_gifts_monthly", "0"))
            ),
            "protective_stage_c_goals_share": string_to_decimal(
                raw.get("protective_stage_c_goals_share", "35")
            ),
            "gift_guideline_min": string_to_decimal(raw.get("gift_guideline_min", "3")),
            "gift_guideline_max": string_to_decimal(raw.get("gift_guideline_max", "7")),
            "gift_warning_limit": string_to_decimal(raw.get("gift_warning_limit", "10")),
            "phase_life_budgets": {
                str(phase): PhaseLifeBudget(
                    critical_life=budget.get("critical_life", "0"),
                    household_reserve=budget.get("household_reserve", "0"),
                    life_categories=budget.get("life_categories", {}),
                    household_reserve_categories=budget.get(
                        "household_reserve_categories", {}
                    ),
                    historical_gifts_monthly=budget.get("historical_gifts_monthly", "0"),
                    currency_code=budget.get("currency_code", "RUB"),
                    currency_symbol=budget.get("currency_symbol", "₽"),
                    exchange_rate_to_rub=budget.get("exchange_rate_to_rub", "1"),
                    exchange_rate_mode=budget.get("exchange_rate_mode", "official"),
                    exchange_rate_updated_at=budget.get("exchange_rate_updated_at"),
                    completed=bool(budget.get("completed", False)),
                )
                for phase, budget in raw.get("phase_life_budgets", {}).items()
                if phase in {"work", "break"} and isinstance(budget, dict)
            },
        }
    return {"income_rhythm": "monthly", "income_gap_months": Decimal("1")}


# ============================================================
# КЛАСС DATABASE
# ============================================================

class Database:
    """
    Единая точка доступа к SQLite.

    Telegram-обработчики не должны напрямую работать
    с SQL.

    Они обращаются сюда:

        db.get_allocator(...)
        db.save_allocator(...)
        db.save_operation(...)
    """

    def __init__(
        self,
        database_path: str | Path = DATABASE_PATH,
    ):
        self.database_path = Path(
            database_path
        )

        self.connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )

        self.connection.row_factory = (
            sqlite3.Row
        )

        # SQLite в нашем случае используется
        # как постоянное локальное хранилище.
        self.connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        self.connection.execute(
            "PRAGMA journal_mode = WAL"
        )

        self.create_tables()

    # ========================================================
    # СОЗДАНИЕ ТАБЛИЦ
    # ========================================================

    def create_tables(self):
        """
        Создаёт все необходимые таблицы.

        IF NOT EXISTS означает:
        повторный запуск бота не уничтожит данные.
        """

        cursor = self.connection.cursor()

        # ----------------------------------------------------
        # Пользователи
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # Настройки пользователя
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                telegram_id INTEGER PRIMARY KEY,

                has_debts INTEGER NOT NULL,
                employment_type TEXT NOT NULL,

                critical_life TEXT NOT NULL,
                base_critical_life TEXT,
                automatic_life_obligations TEXT NOT NULL DEFAULT '{}',
                household_reserve TEXT NOT NULL,
                average_income TEXT NOT NULL,

                tax_rate TEXT NOT NULL,

                taxable_income_types TEXT NOT NULL,

                minimum_reserve_months TEXT NOT NULL,
                force_majeure_months TEXT NOT NULL,

                bracket_a TEXT NOT NULL,
                bracket_b TEXT NOT NULL,
                bracket_c TEXT NOT NULL,
                bracket_d TEXT NOT NULL,
                bracket_e TEXT NOT NULL,

                goals_share_c TEXT NOT NULL,
                pillow_share_c TEXT NOT NULL,
                protective_stage_c_strategy TEXT NOT NULL DEFAULT 'balanced',
                use_contract_obligations_fund INTEGER NOT NULL DEFAULT 0,

                life_categories TEXT NOT NULL,
                life_category_ids TEXT NOT NULL DEFAULT '{}',

                debt_strategy TEXT NOT NULL,

                calculate_interest_savings INTEGER NOT NULL,
                developer_mode INTEGER NOT NULL,

                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # Цели
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                telegram_id INTEGER NOT NULL,

                name TEXT NOT NULL,
                uid TEXT NOT NULL DEFAULT '',
                is_system_chest INTEGER NOT NULL DEFAULT 0,
                percentage TEXT NOT NULL,
                balance TEXT NOT NULL,
                position_type TEXT NOT NULL DEFAULT 'goal',
                order_index INTEGER NOT NULL DEFAULT 0,
                is_auto_percentage INTEGER NOT NULL DEFAULT 0,
                currency_code TEXT NOT NULL DEFAULT 'RUB',
                target_amount TEXT,
                deadline TEXT,
                buffer_enabled INTEGER NOT NULL DEFAULT 0,
                buffer_percent TEXT NOT NULL DEFAULT '0',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT,
                archived_at TEXT,
                previous_percentage TEXT,

                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # Кредиты
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                telegram_id INTEGER NOT NULL,

                name TEXT NOT NULL,

                principal_balance TEXT NOT NULL,
                full_repayment_amount TEXT,

                annual_rate TEXT NOT NULL,
                minimum_payment TEXT NOT NULL,

                payment_type TEXT NOT NULL,
                early_repayment_action TEXT NOT NULL,

                status TEXT NOT NULL,

                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # Состояние алгоритма
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                telegram_id INTEGER PRIMARY KEY,

                life_balance TEXT NOT NULL,
                accumulated_minimum_payments TEXT NOT NULL,

                pillow_minimum TEXT NOT NULL,
                intercontract_reserve TEXT NOT NULL DEFAULT '0',
                intercontract_months_remaining TEXT NOT NULL DEFAULT '0',
                intercontract_break_active INTEGER NOT NULL DEFAULT 0,
                current_cycle_phase TEXT NOT NULL DEFAULT '',
                current_phase_months_remaining TEXT NOT NULL DEFAULT '0',
                contract_obligations_reserve TEXT NOT NULL DEFAULT '0',
                pillow_force_majeure TEXT NOT NULL,
                pillow_stabilizer TEXT NOT NULL,

                investments TEXT NOT NULL,
                early_repayment TEXT NOT NULL,

                goal_balances TEXT NOT NULL,
                period_life_topups TEXT NOT NULL,

                period_income TEXT NOT NULL,
                cycle_income TEXT NOT NULL DEFAULT '0',
                period_tax TEXT NOT NULL,

                period_started_at TEXT,
                period_ends_at TEXT,
                period_anchor_day INTEGER NOT NULL DEFAULT 0,
                period_status TEXT NOT NULL DEFAULT 'legacy',
                period_activation_date TEXT,
                period_reminder_sent_for TEXT,
                initial_distribution_completed INTEGER NOT NULL DEFAULT 0,
                break_period_salary_paid INTEGER NOT NULL DEFAULT 0,
                fund_salary_currencies TEXT NOT NULL DEFAULT '{}',
                fund_salary_period_rates TEXT NOT NULL DEFAULT '{}',
                fund_salary_start_reserves TEXT NOT NULL DEFAULT '{}',
                fund_salary_rates_locked_at TEXT,

                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # Журнал операций
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                telegram_id INTEGER NOT NULL,

                operation_type TEXT NOT NULL,

                created_at TEXT NOT NULL,

                payload TEXT NOT NULL,

                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS exchange_rates (
                currency_code TEXT PRIMARY KEY,
                rub_per_unit TEXT NOT NULL,
                rate_date TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'CBR'
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tax_configuration (
                telegram_id INTEGER PRIMARY KEY,
                planned_taxes TEXT NOT NULL DEFAULT '{}',
                track_payments INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tax_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                tax_name TEXT NOT NULL,
                amount TEXT NOT NULL,
                paid_at TEXT NOT NULL,
                obligation_id INTEGER,
                tax_due_year INTEGER,
                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tax_obligations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                tax_type TEXT NOT NULL,
                object_name TEXT NOT NULL,
                target_amount TEXT NOT NULL,
                opening_amount TEXT NOT NULL DEFAULT '0',
                saved_before TEXT NOT NULL DEFAULT '0',
                months INTEGER NOT NULL,
                monthly_amount TEXT NOT NULL,
                annual_monthly_amount TEXT NOT NULL DEFAULT '0',
                notice_received INTEGER NOT NULL DEFAULT 0,
                ready_reminder_sent_at TEXT,
                last_notice_reminder_at TEXT,
                last_payment_reminder_at TEXT,
                next_payment_reminder_date TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS planned_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                envelope_name TEXT NOT NULL,
                payment_name TEXT NOT NULL,
                target_amount TEXT NOT NULL,
                saved_amount TEXT NOT NULL DEFAULT '0',
                monthly_amount TEXT NOT NULL,
                due_date TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            )
            """
        )

        # Неразрушающая миграция старых локальных баз.
        tax_columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(tax_obligations)").fetchall()
        }
        if "due_date" not in tax_columns:
            cursor.execute("ALTER TABLE tax_obligations ADD COLUMN due_date TEXT")
        if "ready_reminder_sent_at" not in tax_columns:
            cursor.execute("ALTER TABLE tax_obligations ADD COLUMN ready_reminder_sent_at TEXT")
        if "annual_monthly_amount" not in tax_columns:
            cursor.execute(
                "ALTER TABLE tax_obligations ADD COLUMN annual_monthly_amount TEXT NOT NULL DEFAULT '0'"
            )
        if "notice_received" not in tax_columns:
            cursor.execute(
                "ALTER TABLE tax_obligations ADD COLUMN notice_received INTEGER NOT NULL DEFAULT 0"
            )
        if "last_notice_reminder_at" not in tax_columns:
            cursor.execute(
                "ALTER TABLE tax_obligations ADD COLUMN last_notice_reminder_at TEXT"
            )
        if "last_payment_reminder_at" not in tax_columns:
            cursor.execute(
                "ALTER TABLE tax_obligations ADD COLUMN last_payment_reminder_at TEXT"
            )
        if "next_payment_reminder_date" not in tax_columns:
            cursor.execute(
                "ALTER TABLE tax_obligations ADD COLUMN next_payment_reminder_date TEXT"
            )

        payment_columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(tax_payments)").fetchall()
        }
        if "obligation_id" not in payment_columns:
            cursor.execute("ALTER TABLE tax_payments ADD COLUMN obligation_id INTEGER")
        if "tax_due_year" not in payment_columns:
            cursor.execute("ALTER TABLE tax_payments ADD COLUMN tax_due_year INTEGER")

        state_columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(state)").fetchall()
        }
        if "intercontract_reserve" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN intercontract_reserve TEXT NOT NULL DEFAULT '0'"
            )
        if "intercontract_months_remaining" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN intercontract_months_remaining TEXT NOT NULL DEFAULT '0'"
            )
        if "cycle_income" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN cycle_income TEXT NOT NULL DEFAULT '0'"
            )
        if "intercontract_break_active" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN intercontract_break_active INTEGER NOT NULL DEFAULT 0"
            )
        if "contract_obligations_reserve" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN contract_obligations_reserve TEXT NOT NULL DEFAULT '0'"
            )
        if "current_cycle_phase" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN current_cycle_phase TEXT NOT NULL DEFAULT ''"
            )
        if "current_phase_months_remaining" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN current_phase_months_remaining TEXT NOT NULL DEFAULT '0'"
            )
        if "period_ends_at" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN period_ends_at TEXT")
        if "period_anchor_day" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN period_anchor_day INTEGER NOT NULL DEFAULT 0"
            )
        if "period_status" not in state_columns:
            cursor.execute(
                "ALTER TABLE state ADD COLUMN period_status TEXT NOT NULL DEFAULT 'legacy'"
            )
        if "period_activation_date" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN period_activation_date TEXT")
        if "period_reminder_sent_for" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN period_reminder_sent_for TEXT")
        if "initial_distribution_completed" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN initial_distribution_completed INTEGER NOT NULL DEFAULT 0")
        if "break_period_salary_paid" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN break_period_salary_paid INTEGER NOT NULL DEFAULT 0")
        if "fund_salary_currencies" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN fund_salary_currencies TEXT NOT NULL DEFAULT '{}'")
        if "fund_salary_period_rates" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN fund_salary_period_rates TEXT NOT NULL DEFAULT '{}'")
        if "fund_salary_start_reserves" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN fund_salary_start_reserves TEXT NOT NULL DEFAULT '{}'")
        if "fund_salary_rates_locked_at" not in state_columns:
            cursor.execute("ALTER TABLE state ADD COLUMN fund_salary_rates_locked_at TEXT")

        settings_columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(settings)").fetchall()
        }
        if "protective_stage_c_strategy" not in settings_columns:
            cursor.execute(
                "ALTER TABLE settings ADD COLUMN protective_stage_c_strategy "
                "TEXT NOT NULL DEFAULT 'balanced'"
            )
        if "use_contract_obligations_fund" not in settings_columns:
            cursor.execute(
                "ALTER TABLE settings ADD COLUMN use_contract_obligations_fund "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "life_category_ids" not in settings_columns:
            cursor.execute(
                "ALTER TABLE settings ADD COLUMN life_category_ids "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        if "base_critical_life" not in settings_columns:
            cursor.execute("ALTER TABLE settings ADD COLUMN base_critical_life TEXT")
        if "automatic_life_obligations" not in settings_columns:
            cursor.execute(
                "ALTER TABLE settings ADD COLUMN automatic_life_obligations "
                "TEXT NOT NULL DEFAULT '{}'"
            )

        goal_columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(goals)").fetchall()
        }
        goal_migrations = {
            # До разделения сущностей все старые позиции были бессрочными:
            # у них не было конечной суммы и даты, то есть по новой модели
            # они являются Сундуками.
            "position_type": "TEXT NOT NULL DEFAULT 'chest'",
            "order_index": "INTEGER NOT NULL DEFAULT 0",
            "is_auto_percentage": "INTEGER NOT NULL DEFAULT 0",
            "currency_code": "TEXT NOT NULL DEFAULT 'RUB'",
            "target_amount": "TEXT",
            "deadline": "TEXT",
            "buffer_enabled": "INTEGER NOT NULL DEFAULT 0",
            "buffer_percent": "TEXT NOT NULL DEFAULT '0'",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "created_at": "TEXT",
            "updated_at": "TEXT",
            "completed_at": "TEXT",
            "archived_at": "TEXT",
            "previous_percentage": "TEXT",
            "uid": "TEXT NOT NULL DEFAULT ''",
            "is_system_chest": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_sql in goal_migrations.items():
            if column_name not in goal_columns:
                cursor.execute(
                    f"ALTER TABLE goals ADD COLUMN {column_name} {column_sql}"
                )

        # ----------------------------------------------------
        # Индексы
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_operation_log_user
            ON operation_log(telegram_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_operation_log_date
            ON operation_log(telegram_id, created_at)
            """
        )

        self.connection.commit()

    # ========================================================
    # ПОЛЬЗОВАТЕЛЬ
    # ========================================================

    def ensure_user(
        self,
        telegram_id: int,
    ):
        """
        Создаёт пользователя, если его ещё нет.

        Повторный вызов безопасен.
        """

        now = datetime.utcnow().isoformat()

        self.connection.execute(
            """
            INSERT INTO users (
                telegram_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (
                telegram_id,
                now,
                now,
            ),
        )

        self.connection.commit()

    def user_exists(
        self,
        telegram_id: int,
    ) -> bool:

        row = self.connection.execute(
            """
            SELECT telegram_id
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()

        return row is not None

    def due_period_reminders(self, today: str) -> list[tuple[int, str]]:
        rows = self.connection.execute(
            """
            SELECT telegram_id, period_activation_date FROM state
            WHERE period_status = 'scheduled'
              AND period_activation_date <= ?
              AND (period_reminder_sent_for IS NULL OR period_reminder_sent_for != period_activation_date)
            """,
            (today,),
        ).fetchall()
        return [(int(row["telegram_id"]), str(row["period_activation_date"])) for row in rows]

    def mark_period_reminder_sent(self, telegram_id: int, activation_date: str) -> None:
        self.connection.execute(
            "UPDATE state SET period_reminder_sent_for = ? WHERE telegram_id = ?",
            (activation_date, telegram_id),
        )
        self.connection.commit()

    # ========================================================
    # СОХРАНЕНИЕ НАСТРОЕК
    # ========================================================

    def save_settings(
        self,
        telegram_id: int,
        settings: UserSettings,
    ):
        """
        Полностью сохраняет настройки пользователя.

        Перед сохранением старые цели и кредиты удаляются
        и записываются заново.

        Это проще и надёжнее для нашего первого варианта.
        """

        self.ensure_user(
            telegram_id
        )

        cursor = self.connection.cursor()

        # ----------------------------------------------------
        # Основные настройки
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO settings (
                telegram_id,

                has_debts,
                employment_type,

                critical_life,
                base_critical_life,
                automatic_life_obligations,
                household_reserve,
                average_income,

                tax_rate,

                taxable_income_types,

                minimum_reserve_months,
                force_majeure_months,

                bracket_a,
                bracket_b,
                bracket_c,
                bracket_d,
                bracket_e,

                goals_share_c,
                pillow_share_c,
                protective_stage_c_strategy,
                use_contract_obligations_fund,

                life_categories,
                life_category_ids,

                debt_strategy,

                calculate_interest_savings,
                developer_mode
            )

            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?,
                ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?,
                ?,
                ?, ?, ?
            )

            ON CONFLICT(telegram_id)
            DO UPDATE SET

                has_debts =
                    excluded.has_debts,

                employment_type =
                    excluded.employment_type,

                critical_life =
                    excluded.critical_life,

                base_critical_life =
                    excluded.base_critical_life,

                automatic_life_obligations =
                    excluded.automatic_life_obligations,

                household_reserve =
                    excluded.household_reserve,

                average_income =
                    excluded.average_income,

                tax_rate =
                    excluded.tax_rate,

                taxable_income_types =
                    excluded.taxable_income_types,

                minimum_reserve_months =
                    excluded.minimum_reserve_months,

                force_majeure_months =
                    excluded.force_majeure_months,

                bracket_a =
                    excluded.bracket_a,

                bracket_b =
                    excluded.bracket_b,

                bracket_c =
                    excluded.bracket_c,

                bracket_d =
                    excluded.bracket_d,

                bracket_e =
                    excluded.bracket_e,

                goals_share_c =
                    excluded.goals_share_c,

                pillow_share_c =
                    excluded.pillow_share_c,

                protective_stage_c_strategy =
                    excluded.protective_stage_c_strategy,

                use_contract_obligations_fund =
                    excluded.use_contract_obligations_fund,

                life_categories =
                    excluded.life_categories,

                life_category_ids =
                    excluded.life_category_ids,

                debt_strategy =
                    excluded.debt_strategy,

                calculate_interest_savings =
                    excluded.calculate_interest_savings,

                developer_mode =
                    excluded.developer_mode
            """,
            (
                telegram_id,

                int(settings.has_debts),
                settings.employment_type,

                decimal_to_string(
                    settings.critical_life
                ),

                decimal_to_string(settings.base_critical_life),

                serialize_json(settings.automatic_life_obligations),

                decimal_to_string(
                    settings.household_reserve
                ),

                decimal_to_string(
                    settings.average_income
                ),

                decimal_to_string(
                    settings.tax_rate
                ),

                serialize_income_types(settings),

                decimal_to_string(
                    settings.minimum_reserve_months
                ),

                decimal_to_string(
                    settings.force_majeure_months
                ),

                decimal_to_string(
                    settings.bracket_a
                ),

                decimal_to_string(
                    settings.bracket_b
                ),

                decimal_to_string(
                    settings.bracket_c
                ),

                decimal_to_string(
                    settings.bracket_d
                ),

                decimal_to_string(
                    settings.bracket_e
                ),

                decimal_to_string(
                    settings.goals_share_c
                ),

                decimal_to_string(
                    settings.pillow_share_c
                ),

                settings.protective_stage_c_strategy,

                int(settings.use_contract_obligations_fund),

                serialize_json(
                    settings.life_categories
                ),

                serialize_json(
                    settings.life_category_ids
                ),

                settings.debt_strategy,

                int(
                    settings.calculate_interest_savings
                ),

                int(
                    settings.developer_mode
                ),
            ),
        )

        # ----------------------------------------------------
        # Цели
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM goals
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

        for order_index, goal in enumerate(settings.goals):

            cursor.execute(
                """
                INSERT INTO goals (
                    telegram_id,
                    name,
                    uid,
                    is_system_chest,
                    percentage,
                    balance,
                    position_type,
                    order_index,
                    is_auto_percentage,
                    currency_code,
                    target_amount,
                    deadline,
                    buffer_enabled,
                    buffer_percent,
                    status,
                    created_at,
                    updated_at,
                    completed_at,
                    archived_at,
                    previous_percentage
                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_id,

                    goal.name,

                    goal.uid,

                    int(goal.is_system_chest),

                    decimal_to_string(
                        goal.percentage
                    ),

                    decimal_to_string(
                        goal.balance
                    ),
                    goal.position_type,
                    order_index,
                    int(goal.is_auto_percentage),
                    goal.currency_code,
                    (
                        decimal_to_string(goal.target_amount)
                        if goal.target_amount is not None else None
                    ),
                    goal.deadline,
                    int(goal.buffer_enabled),
                    decimal_to_string(goal.buffer_percent),
                    goal.status,
                    goal.created_at,
                    goal.updated_at,
                    goal.completed_at,
                    goal.archived_at,
                    (
                        decimal_to_string(goal.previous_percentage)
                        if goal.previous_percentage is not None else None
                    ),
                ),
            )

        # ----------------------------------------------------
        # Кредиты
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM credits
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

        for credit in settings.credits:

            full_repayment = None

            if (
                credit.full_repayment_amount
                is not None
            ):
                full_repayment = (
                    decimal_to_string(
                        credit.full_repayment_amount
                    )
                )

            cursor.execute(
                """
                INSERT INTO credits (
                    telegram_id,

                    name,

                    principal_balance,
                    full_repayment_amount,

                    annual_rate,
                    minimum_payment,

                    payment_type,
                    early_repayment_action,

                    status
                )

                VALUES (
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?
                )
                """,
                (
                    telegram_id,

                    credit.name,

                    decimal_to_string(
                        credit.principal_balance
                    ),

                    full_repayment,

                    decimal_to_string(
                        credit.annual_rate
                    ),

                    decimal_to_string(
                        credit.minimum_payment
                    ),

                    credit.payment_type,
                    credit.early_repayment_action,

                    credit.status,
                ),
            )

        self.connection.commit()

    # ========================================================
    # ЗАГРУЗКА НАСТРОЕК
    # ========================================================

    def load_settings(
        self,
        telegram_id: int,
    ) -> Optional[UserSettings]:

        row = self.connection.execute(
            """
            SELECT *
            FROM settings
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()

        if row is None:
            return None

        # ----------------------------------------------------
        # Цели
        # ----------------------------------------------------

        goal_rows = self.connection.execute(
            """
            SELECT *
            FROM goals
            WHERE telegram_id = ?
            ORDER BY order_index, id
            """,
            (telegram_id,),
        ).fetchall()

        goals = []

        for goal_row in goal_rows:

            goals.append(
                Goal(
                    name=goal_row["name"],

                    uid=goal_row["uid"],

                    is_system_chest=bool(goal_row["is_system_chest"]),

                    percentage=
                        string_to_decimal(
                            goal_row["percentage"]
                        ),

                    balance=
                        string_to_decimal(
                            goal_row["balance"]
                        ),
                    position_type=goal_row["position_type"],
                    order_index=goal_row["order_index"],
                    is_auto_percentage=bool(goal_row["is_auto_percentage"]),
                    currency_code=goal_row["currency_code"],
                    target_amount=(
                        string_to_decimal(goal_row["target_amount"])
                        if goal_row["target_amount"] is not None else None
                    ),
                    deadline=goal_row["deadline"],
                    buffer_enabled=bool(goal_row["buffer_enabled"]),
                    buffer_percent=string_to_decimal(goal_row["buffer_percent"]),
                    status=goal_row["status"],
                    created_at=goal_row["created_at"],
                    updated_at=goal_row["updated_at"],
                    completed_at=goal_row["completed_at"],
                    archived_at=goal_row["archived_at"],
                    previous_percentage=(
                        string_to_decimal(goal_row["previous_percentage"])
                        if goal_row["previous_percentage"] is not None else None
                    ),
                )
            )

        # ----------------------------------------------------
        # Кредиты
        # ----------------------------------------------------

        credit_rows = self.connection.execute(
            """
            SELECT *
            FROM credits
            WHERE telegram_id = ?
            ORDER BY id
            """,
            (telegram_id,),
        ).fetchall()

        credits = []

        for credit_row in credit_rows:

            full_repayment = (
                None
                if credit_row[
                    "full_repayment_amount"
                ] is None
                else string_to_decimal(
                    credit_row[
                        "full_repayment_amount"
                    ]
                )
            )

            credits.append(
                Credit(
                    name=credit_row["name"],

                    principal_balance=
                        string_to_decimal(
                            credit_row[
                                "principal_balance"
                            ]
                        ),

                    full_repayment_amount=
                        full_repayment,

                    annual_rate=
                        string_to_decimal(
                            credit_row[
                                "annual_rate"
                            ]
                        ),

                    minimum_payment=
                        string_to_decimal(
                            credit_row[
                                "minimum_payment"
                            ]
                        ),

                    payment_type=
                        credit_row[
                            "payment_type"
                        ],

                    early_repayment_action=
                        credit_row[
                            "early_repayment_action"
                        ],

                    status=
                        credit_row[
                            "status"
                        ],
                )
            )

        # ----------------------------------------------------
        # Создание UserSettings
        # ----------------------------------------------------

        legacy_tax_rate = string_to_decimal(row["tax_rate"])
        taxable_income_types, income_type_tax_rates = deserialize_income_types(
            row["taxable_income_types"],
            legacy_tax_rate,
        )
        income_cycle = deserialize_income_rhythm(
            row["taxable_income_types"]
        )

        settings = UserSettings(

            has_debts=bool(
                row["has_debts"]
            ),

            employment_type=
                row["employment_type"],

            critical_life=
                string_to_decimal(
                    row["critical_life"]
                ),

            base_critical_life=(
                string_to_decimal(row["base_critical_life"])
                if row["base_critical_life"] is not None else None
            ),

            automatic_life_obligations=
                deserialize_json(row["automatic_life_obligations"]),

            household_reserve=
                string_to_decimal(
                    row["household_reserve"]
                ),

            average_income=
                string_to_decimal(
                    row["average_income"]
                ),

            **income_cycle,

            tax_rate=legacy_tax_rate,

            taxable_income_types=taxable_income_types,

            income_type_tax_rates=income_type_tax_rates,

            minimum_reserve_months=
                string_to_decimal(
                    row[
                        "minimum_reserve_months"
                    ]
                ),

            force_majeure_months=
                string_to_decimal(
                    row[
                        "force_majeure_months"
                    ]
                ),

            bracket_a=
                string_to_decimal(
                    row["bracket_a"]
                ),

            bracket_b=
                string_to_decimal(
                    row["bracket_b"]
                ),

            bracket_c=
                string_to_decimal(
                    row["bracket_c"]
                ),

            bracket_d=
                string_to_decimal(
                    row["bracket_d"]
                ),

            bracket_e=
                string_to_decimal(
                    row["bracket_e"]
                ),

            goals_share_c=
                string_to_decimal(
                    row["goals_share_c"]
                ),

            pillow_share_c=
                string_to_decimal(
                    row["pillow_share_c"]
                ),

            protective_stage_c_strategy=
                row["protective_stage_c_strategy"],

            use_contract_obligations_fund=
                bool(row["use_contract_obligations_fund"]),

            life_categories=
                deserialize_json(
                    row["life_categories"]
                ),

            life_category_ids=
                deserialize_json(
                    row["life_category_ids"]
                ),

            goals=goals,

            credits=credits,

            debt_strategy=
                row["debt_strategy"],

            calculate_interest_savings=
                bool(
                    row[
                        "calculate_interest_savings"
                    ]
                ),

            developer_mode=
                bool(
                    row[
                        "developer_mode"
                    ]
                ),
        )

        return settings

    # ========================================================
    # СОХРАНЕНИЕ STATE
    # ========================================================

    def save_state(
        self,
        telegram_id: int,
        state: AllocatorState,
    ):

        self.ensure_user(
            telegram_id
        )

        self.connection.execute(
            """
            INSERT INTO state (

                telegram_id,

                life_balance,
                accumulated_minimum_payments,

                pillow_minimum,
                intercontract_reserve,
                intercontract_months_remaining,
                intercontract_break_active,
                current_cycle_phase,
                current_phase_months_remaining,
                contract_obligations_reserve,
                pillow_force_majeure,
                pillow_stabilizer,

                investments,
                early_repayment,

                goal_balances,
                period_life_topups,

                period_income,
                cycle_income,
                period_tax,

                period_started_at,
                period_ends_at,
                period_anchor_day,
                period_status,
                period_activation_date,
                period_reminder_sent_for,
                initial_distribution_completed,
                break_period_salary_paid,
                fund_salary_currencies,
                fund_salary_period_rates,
                fund_salary_start_reserves,
                fund_salary_rates_locked_at
            )

            VALUES (
                ?,
                ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )

            ON CONFLICT(telegram_id)
            DO UPDATE SET

                life_balance =
                    excluded.life_balance,

                accumulated_minimum_payments =
                    excluded.accumulated_minimum_payments,

                pillow_minimum =
                    excluded.pillow_minimum,

                intercontract_reserve =
                    excluded.intercontract_reserve,

                intercontract_months_remaining =
                    excluded.intercontract_months_remaining,

                intercontract_break_active =
                    excluded.intercontract_break_active,

                current_cycle_phase =
                    excluded.current_cycle_phase,

                current_phase_months_remaining =
                    excluded.current_phase_months_remaining,

                contract_obligations_reserve =
                    excluded.contract_obligations_reserve,

                pillow_force_majeure =
                    excluded.pillow_force_majeure,

                pillow_stabilizer =
                    excluded.pillow_stabilizer,

                investments =
                    excluded.investments,

                early_repayment =
                    excluded.early_repayment,

                goal_balances =
                    excluded.goal_balances,

                period_life_topups =
                    excluded.period_life_topups,

                period_income =
                    excluded.period_income,

                cycle_income =
                    excluded.cycle_income,

                period_tax =
                    excluded.period_tax,

                period_started_at =
                    excluded.period_started_at,

                period_ends_at =
                    excluded.period_ends_at,

                period_anchor_day =
                    excluded.period_anchor_day,

                period_status =
                    excluded.period_status,

                period_activation_date =
                    excluded.period_activation_date,

                period_reminder_sent_for = excluded.period_reminder_sent_for,
                initial_distribution_completed = excluded.initial_distribution_completed,
                break_period_salary_paid = excluded.break_period_salary_paid,
                fund_salary_currencies = excluded.fund_salary_currencies,
                fund_salary_period_rates = excluded.fund_salary_period_rates,
                fund_salary_start_reserves = excluded.fund_salary_start_reserves,
                fund_salary_rates_locked_at = excluded.fund_salary_rates_locked_at
            """,
            (
                telegram_id,

                decimal_to_string(
                    state.life_balance
                ),

                decimal_to_string(
                    state.accumulated_minimum_payments
                ),

                decimal_to_string(
                    state.pillow_minimum
                ),

                decimal_to_string(
                    state.intercontract_reserve
                ),

                decimal_to_string(
                    state.intercontract_months_remaining
                ),

                int(state.intercontract_break_active),

                state.current_cycle_phase,

                decimal_to_string(state.current_phase_months_remaining),

                decimal_to_string(
                    state.contract_obligations_reserve
                ),

                decimal_to_string(
                    state.pillow_force_majeure
                ),

                decimal_to_string(
                    state.pillow_stabilizer
                ),

                decimal_to_string(
                    state.investments
                ),

                decimal_to_string(
                    state.early_repayment
                ),

                serialize_json(
                    state.goal_balances
                ),

                serialize_json(
                    state.period_life_topups
                ),

                decimal_to_string(
                    state.period_income
                ),

                decimal_to_string(
                    state.cycle_income
                ),

                decimal_to_string(
                    state.period_tax
                ),

                state.period_started_at,
                state.period_ends_at,
                state.period_anchor_day,
                state.period_status,
                state.period_activation_date,
                state.period_reminder_sent_for,
                int(state.initial_distribution_completed),
                int(state.break_period_salary_paid),
                serialize_json(state.fund_salary_currencies),
                serialize_json(state.fund_salary_period_rates),
                serialize_json(state.fund_salary_start_reserves),
                state.fund_salary_rates_locked_at,
            ),
        )

        self.connection.commit()

    # ========================================================
    # ЗАГРУЗКА STATE
    # ========================================================

    def load_state(
        self,
        telegram_id: int,
    ) -> Optional[AllocatorState]:

        row = self.connection.execute(
            """
            SELECT *
            FROM state
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()

        if row is None:
            return None

        state = AllocatorState(

            life_balance=
                string_to_decimal(
                    row["life_balance"]
                ),

            accumulated_minimum_payments=
                string_to_decimal(
                    row[
                        "accumulated_minimum_payments"
                    ]
                ),

            pillow_minimum=
                string_to_decimal(
                    row["pillow_minimum"]
                ),

            intercontract_reserve=
                string_to_decimal(
                    row["intercontract_reserve"]
                ),

            intercontract_months_remaining=
                string_to_decimal(
                    row["intercontract_months_remaining"]
                ),

            intercontract_break_active=
                bool(row["intercontract_break_active"]),

            current_cycle_phase=
                row["current_cycle_phase"],

            current_phase_months_remaining=
                string_to_decimal(row["current_phase_months_remaining"]),

            contract_obligations_reserve=
                string_to_decimal(row["contract_obligations_reserve"]),

            pillow_force_majeure=
                string_to_decimal(
                    row["pillow_force_majeure"]
                ),

            pillow_stabilizer=
                string_to_decimal(
                    row["pillow_stabilizer"]
                ),

            investments=
                string_to_decimal(
                    row["investments"]
                ),

            early_repayment=
                string_to_decimal(
                    row["early_repayment"]
                ),

            goal_balances=
                deserialize_json(
                    row["goal_balances"]
                ),

            period_life_topups=
                deserialize_json(
                    row[
                        "period_life_topups"
                    ]
                ),

            period_income=
                string_to_decimal(
                    row["period_income"]
                ),

            cycle_income=
                string_to_decimal(
                    row["cycle_income"]
                ),

            period_tax=
                string_to_decimal(
                    row["period_tax"]
                ),

            period_started_at=
                row["period_started_at"],

            period_ends_at=
                row["period_ends_at"],

            period_anchor_day=
                row["period_anchor_day"],

            period_status=
                row["period_status"],

            period_activation_date=
                row["period_activation_date"],

            period_reminder_sent_for=row["period_reminder_sent_for"],
            initial_distribution_completed=bool(row["initial_distribution_completed"]),
            break_period_salary_paid=bool(row["break_period_salary_paid"]),
            fund_salary_currencies=deserialize_json(row["fund_salary_currencies"]),
            fund_salary_period_rates=deserialize_json(row["fund_salary_period_rates"]),
            fund_salary_start_reserves=deserialize_json(row["fund_salary_start_reserves"]),
            fund_salary_rates_locked_at=row["fund_salary_rates_locked_at"],
        )

        return state

    # ========================================================
    # СОХРАНЕНИЕ ОПЕРАЦИИ
    # ========================================================

    def save_operation(
        self,
        telegram_id: int,
        operation_type: str,
        payload: dict,
    ):

        self.ensure_user(
            telegram_id
        )

        self.connection.execute(
            """
            INSERT INTO operation_log (
                telegram_id,
                operation_type,
                created_at,
                payload
            )

            VALUES (?, ?, ?, ?)
            """,
            (
                telegram_id,

                operation_type,

                datetime.utcnow().isoformat(),

                serialize_json(
                    payload
                ),
            ),
        )

        self.connection.commit()

    # ========================================================
    # ЗАГРУЗКА ОПЕРАЦИЙ
    # ========================================================

    def record_envelope_rename(self, telegram_id, allocator, prefix, old, new, entity_id=""):
        """Record a changed label while retaining the envelope's immutable ID."""
        old_key, new_key = prefix + old, prefix + new
        flows = allocator.state.period_allocations
        if old_key in flows:
            flows[new_key] = flows.get(new_key, Decimal('0')) + flows.pop(old_key)
        self.save_operation(
            telegram_id,
            'envelope_rename',
            {'old': old_key, 'new': new_key, 'entity_id': str(entity_id or '')},
        )

    def normalize_envelope_names(self, telegram_id: int, allocator: FinancialAllocator) -> None:
        """Merge stale envelope aliases into their newest names everywhere in state.

        A renamed category can exist in older income records, in current-period
        aggregates and in goal balances. Reports must never treat those aliases
        as separate envelopes.
        """
        rows = self.connection.execute(
            "SELECT payload FROM operation_log WHERE telegram_id = ? "
            "AND operation_type = 'envelope_rename' ORDER BY id",
            (telegram_id,),
        ).fetchall()
        redirects = {}
        for row in rows:
            payload = deserialize_json(row["payload"])
            old, new = payload.get("old"), payload.get("new")
            if isinstance(old, str) and isinstance(new, str) and old != new:
                redirects[old] = new

        def current_name(name: str) -> str:
            seen = set()
            while name in redirects and name not in seen:
                seen.add(name)
                name = redirects[name]
            return name

        def merge(items: dict[str, Decimal], prefix: str = "") -> dict[str, Decimal]:
            result = {}
            for name, amount in items.items():
                full_name = prefix + str(name)
                resolved = current_name(full_name)
                final_name = resolved[len(prefix):] if prefix and resolved.startswith(prefix) else resolved
                result[final_name] = result.get(final_name, Decimal("0")) + Decimal(str(amount))
            return result

        allocator.state.period_allocations = merge(
            allocator.state.period_allocations,
        )
        allocator.state.period_life_topups = merge(
            allocator.state.period_life_topups,
            "КЖ:",
        )
        allocator.state.goal_balances = merge(
            allocator.state.goal_balances,
            "Цели:",
        )

        # Неизвестные старые подписи здесь не удаляем: данные до миграции
        # могут ещё нуждаться в однократном связывании с новой категорией.
        # Отчёты выводят только актуальные ID, поэтому эти записи не создают
        # отдельного сектора до восстановления.

    def load_operations(
        self,
        telegram_id: int,
        limit: int = 100,
    ) -> list[dict]:

        rows = self.connection.execute(
            """
            SELECT
                id,
                operation_type,
                created_at,
                payload

            FROM operation_log

            WHERE telegram_id = ?

            ORDER BY id DESC

            LIMIT ?
            """,
            (
                telegram_id,
                limit,
            ),
        ).fetchall()

        result = []

        for row in rows:

            result.append({
                "id": row["id"],

                "type":
                    row["operation_type"],

                "created_at":
                    row["created_at"],

                "payload":
                    deserialize_json(
                        row["payload"]
                    ),
            })

        renames = self.connection.execute(
            "SELECT id, payload FROM operation_log WHERE telegram_id = ? "
            "AND operation_type = 'envelope_rename' ORDER BY id", (telegram_id,)
        ).fetchall()
        for operation in result:
            allocations = operation['payload'].get('allocations')
            if not isinstance(allocations, dict):
                continue
            for rename in renames:
                names = deserialize_json(rename['payload'])
                old, new = names['old'], names['new']
                if old in allocations:
                    allocations[new] = str(Decimal(str(allocations.get(new, 0)))
                                           + Decimal(str(allocations.pop(old))))
        return result

    def update_income_note(
        self,
        telegram_id: int,
        operation_id: int,
        note: str,
    ) -> bool:
        """Update only a user's note; financial data stays immutable."""
        row = self.connection.execute(
            """
            SELECT payload
            FROM operation_log
            WHERE id = ?
              AND telegram_id = ?
              AND operation_type = 'income_distribution'
            """,
            (operation_id, telegram_id),
        ).fetchone()
        if row is None:
            return False

        payload = deserialize_json(row["payload"])
        payload["note"] = note
        self.connection.execute(
            "UPDATE operation_log SET payload = ? WHERE id = ? AND telegram_id = ?",
            (serialize_json(payload), operation_id, telegram_id),
        )
        self.connection.commit()
        return True

    def delete_income_operation(
        self,
        telegram_id: int,
        operation_id: int,
    ) -> bool:
        """Delete one owned income record after its balances have been rolled back."""
        cursor = self.connection.execute(
            """
            DELETE FROM operation_log
            WHERE id = ?
              AND telegram_id = ?
              AND operation_type = 'income_distribution'
            """,
            (operation_id, telegram_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    # ========================================================
    # ЗАГРУЗКА ВСЕГО АЛЛОКАТОРА
    # ========================================================

    def load_allocator(
        self,
        telegram_id: int,
    ) -> Optional[FinancialAllocator]:

        settings = self.load_settings(
            telegram_id
        )

        if settings is None:
            return None

        existing_automatic_keys = set(settings.automatic_life_obligations)
        planned_taxes, track_payments = self.load_tax_configuration(telegram_id)
        all_tax_obligations = self.load_tax_obligations(
            telegram_id, active_only=False,
        )
        active_tax_obligations = [
            item for item in all_tax_obligations if item["active"]
        ]
        active_tax_keys = {
            f"{item['tax_type']} · {item['object_name']}"
            for item in active_tax_obligations
        }
        inactive_tax_keys = {
            f"{item['tax_type']} · {item['object_name']}"
            for item in all_tax_obligations
            if not item["active"]
        }
        settings.planned_taxes = {
            name: string_to_decimal(amount)
            for name, amount in planned_taxes.items()
            if name not in inactive_tax_keys or name in active_tax_keys
        }
        valid_tax_automatic_keys = {
            f"tax:{name}" for name in settings.planned_taxes
        }
        for key in list(settings.automatic_life_obligations):
            if key.startswith("tax:") and key not in valid_tax_automatic_keys:
                settings.automatic_life_obligations.pop(key, None)
        settings.track_tax_payments = track_payments

        # В старой модели monthly_amount для имущественного налога был
        # срочным взносом до ближайшего 1 декабря. При первом чтении новой
        # версии отделяем от него годовую норму, чтобы КМ и резервы сразу
        # перестали учитывать временное ускорение накопления.
        annual_property_taxes = {
            "Налог на имущество", "Транспортный налог", "Земельный налог",
        }
        # Patent and custom dated payments used to be persisted as recurring
        # parts of KМ. They are one-time catch-ups and must not increase the
        # user's cost of life or reserve targets.
        one_time_tax_keys = {
            f"{item['tax_type']} · {item['object_name']}"
            for item in active_tax_obligations
            if item["tax_type"] not in annual_property_taxes
        }
        for name in one_time_tax_keys:
            settings.planned_taxes.pop(name, None)
            settings.automatic_life_obligations.pop(f"tax:{name}", None)
        for item in active_tax_obligations:
            if (
                item["tax_type"] not in annual_property_taxes
                and item["annual_monthly_amount"] != Decimal("0")
            ):
                self.update_tax_obligation_annual_monthly(
                    telegram_id, item["id"], Decimal("0"),
                )
        for item in active_tax_obligations:
            if item["tax_type"] not in annual_property_taxes:
                continue
            annual_monthly = item["annual_monthly_amount"]
            if annual_monthly <= Decimal("0"):
                annual_monthly = (item["target_amount"] / Decimal("12")).quantize(
                    Decimal("0.01"), rounding=ROUND_CEILING,
                )
                self.update_tax_obligation_annual_monthly(
                    telegram_id, item["id"], annual_monthly,
                )
            settings.planned_taxes[
                f"{item['tax_type']} · {item['object_name']}"
            ] = annual_monthly

        # load_settings создаёт UserSettings до tax_configuration. Поэтому
        # для старого профиля отделяем налоговые взносы здесь, после загрузки
        # реальных обязательств, а не позволяем им попасть в постоянный КМ.
        tax_total = sum(settings.planned_taxes.values(), Decimal("0"))
        if getattr(settings, "_base_critical_life_inferred", False):
            unaccounted_tax_total = sum(
                (
                    amount for name, amount in settings.planned_taxes.items()
                    if f"tax:{name}" not in existing_automatic_keys
                ),
                Decimal("0"),
            )
            settings.base_critical_life = max(
                Decimal("0"), settings.base_critical_life - unaccounted_tax_total,
            )
        for name, amount in settings.planned_taxes.items():
            settings.set_automatic_life_obligation(f"tax:{name}", amount)
        if tax_total > Decimal("0"):
            settings.ensure_life_category_id("Налоги")
            settings.life_categories["Налоги"] = tax_total
        else:
            settings.life_categories.pop("Налоги", None)

        # Старые профили хранили временные платежи прямо внутри КМ. Один раз
        # отделяем их от скрытой постоянной основы и дальше пересчитываем КМ
        # только из активных обязательств.
        planned_payments = self.load_planned_payments(telegram_id)
        valid_payment_automatic_keys = {
            f"payment:{item['id']}" for item in planned_payments
        }
        for key in list(settings.automatic_life_obligations):
            if key.startswith("payment:") and key not in valid_payment_automatic_keys:
                settings.automatic_life_obligations.pop(key, None)
        if getattr(settings, "_base_critical_life_inferred", False):
            unaccounted_payment_total = sum(
                (
                    string_to_decimal(item["monthly_amount"])
                    for item in planned_payments
                    if f"payment:{item['id']}" not in existing_automatic_keys
                ),
                Decimal("0"),
            )
            settings.base_critical_life = max(
                Decimal("0"), settings.base_critical_life - unaccounted_payment_total,
            )
        for item in planned_payments:
            settings.set_automatic_life_obligation(
                f"payment:{item['id']}",
                string_to_decimal(item["monthly_amount"]),
            )
        settings.recalculate_critical_life()

        state = self.load_state(
            telegram_id
        )

        if state is None:
            state = AllocatorState()

        allocator = FinancialAllocator(
            settings=settings,
            state=state,
        )

        self.normalize_envelope_names(telegram_id, allocator)

        return allocator

    # ========================================================
    # СОХРАНЕНИЕ ВСЕГО АЛЛОКАТОРА
    # ========================================================

    def save_allocator(
        self,
        telegram_id: int,
        allocator: FinancialAllocator,
    ):

        self.ensure_user(
            telegram_id
        )

        # Сохраняем настройки.
        self.save_settings(
            telegram_id,
            allocator.settings,
        )

        self.save_tax_configuration(telegram_id, allocator.settings)

        # Сохраняем состояние.
        self.save_state(
            telegram_id,
            allocator.state,
        )

        # Отдельно сохраняем последние операции
        # из operation_log ядра.
        #
        # Для защиты от повторной записи здесь
        # используется отдельная логика ниже.

        operations = (
            allocator.state.operation_log
        )

        if operations:

            latest = operations[-1]

            operation_type = latest.get(
                "type",
                "unknown",
            )

            self.save_operation(
                telegram_id,
                operation_type,
                latest,
            )

    def save_tax_configuration(self, telegram_id: int, settings: UserSettings):
        self.connection.execute(
            """
            INSERT INTO tax_configuration (telegram_id, planned_taxes, track_payments)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                planned_taxes = excluded.planned_taxes,
                track_payments = excluded.track_payments
            """,
            (
                telegram_id,
                serialize_json(settings.planned_taxes),
                int(settings.track_tax_payments),
            ),
        )
        self.connection.commit()

    def load_tax_configuration(self, telegram_id: int) -> tuple[dict, bool]:
        row = self.connection.execute(
            "SELECT planned_taxes, track_payments FROM tax_configuration WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row is None:
            return {}, False
        return deserialize_json(row["planned_taxes"]), bool(row["track_payments"])

    def save_tax_payment(
        self,
        telegram_id: int,
        tax_name: str,
        amount: Decimal,
        *,
        obligation_id: int | None = None,
        tax_due_year: int | None = None,
    ) -> int:
        self.ensure_user(telegram_id)
        cursor = self.connection.execute(
            """
            INSERT INTO tax_payments (
                telegram_id, tax_name, amount, paid_at, obligation_id, tax_due_year
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                tax_name,
                decimal_to_string(amount),
                datetime.utcnow().isoformat(),
                obligation_id,
                tax_due_year,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def load_tax_payments(self, telegram_id: int, year: int | None = None) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT id, tax_name, amount, paid_at, obligation_id, tax_due_year
            FROM tax_payments
            WHERE telegram_id = ? ORDER BY id DESC
            """,
            (telegram_id,),
        ).fetchall()
        result = []
        for row in rows:
            paid_at = datetime.fromisoformat(row["paid_at"])
            if year is not None and paid_at.year != year:
                continue
            result.append({
                "id": int(row["id"]),
                "tax_name": row["tax_name"],
                "amount": string_to_decimal(row["amount"]),
                "paid_at": row["paid_at"],
                "obligation_id": row["obligation_id"],
                "tax_due_year": row["tax_due_year"],
            })
        return result

    def tax_obligation_paid_amount(self, telegram_id: int, obligation_id: int) -> Decimal:
        rows = self.connection.execute(
            """
            SELECT amount FROM tax_payments
            WHERE telegram_id = ? AND obligation_id = ?
            """,
            (telegram_id, obligation_id),
        ).fetchall()
        return sum((string_to_decimal(row["amount"]) for row in rows), Decimal("0"))

    def add_tax_obligation(
        self,
        telegram_id: int,
        tax_type: str,
        object_name: str,
        target_amount: Decimal,
        saved_before: Decimal,
        months: int,
        monthly_amount: Decimal,
        due_date: str | None = None,
        annual_monthly_amount: Decimal | None = None,
        notice_received: bool = False,
        opening_amount: Decimal | None = None,
    ) -> int:
        self.ensure_user(telegram_id)
        if annual_monthly_amount is None:
            if tax_type in {
                "Налог на имущество", "Транспортный налог", "Земельный налог",
            }:
                annual_monthly_amount = (target_amount / Decimal("12")).quantize(
                    Decimal("0.01"), rounding=ROUND_CEILING,
                )
            else:
                annual_monthly_amount = monthly_amount
        cursor = self.connection.execute(
            """
            INSERT INTO tax_obligations (
                telegram_id, tax_type, object_name, target_amount,
                opening_amount, saved_before, months, monthly_amount, annual_monthly_amount,
                notice_received, due_date, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                telegram_id,
                tax_type,
                object_name,
                decimal_to_string(target_amount),
                decimal_to_string(
                    saved_before if opening_amount is None else opening_amount
                ),
                decimal_to_string(saved_before),
                months,
                decimal_to_string(monthly_amount),
                decimal_to_string(annual_monthly_amount),
                int(notice_received),
                due_date,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def load_tax_obligations(self, telegram_id: int, active_only: bool = True) -> list[dict]:
        query = "SELECT * FROM tax_obligations WHERE telegram_id = ?"
        params: tuple = (telegram_id,)
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY id"
        rows = self.connection.execute(query, params).fetchall()
        return [
            {
                "id": row["id"],
                "tax_type": row["tax_type"],
                "object_name": row["object_name"],
                "target_amount": string_to_decimal(row["target_amount"]),
                "opening_amount": string_to_decimal(row["opening_amount"]),
                "saved_before": string_to_decimal(row["saved_before"]),
                "months": row["months"],
                "monthly_amount": string_to_decimal(row["monthly_amount"]),
                "annual_monthly_amount": string_to_decimal(row["annual_monthly_amount"]),
                "notice_received": bool(row["notice_received"]),
                "due_date": row["due_date"],
                "ready_reminder_sent_at": row["ready_reminder_sent_at"],
                "last_notice_reminder_at": row["last_notice_reminder_at"],
                "last_payment_reminder_at": row["last_payment_reminder_at"],
                "next_payment_reminder_date": row["next_payment_reminder_date"],
                "active": bool(row["active"]),
            }
            for row in rows
        ]

    def add_planned_payment(
        self,
        telegram_id: int,
        category: str,
        envelope_name: str,
        payment_name: str,
        target_amount: Decimal,
        monthly_amount: Decimal,
        due_date: str,
    ) -> int:
        self.ensure_user(telegram_id)
        cursor = self.connection.execute(
            """
            INSERT INTO planned_payments (
                telegram_id, category, envelope_name, payment_name,
                target_amount, saved_amount, monthly_amount, due_date, active
            ) VALUES (?, ?, ?, ?, ?, '0', ?, ?, 1)
            """,
            (
                telegram_id,
                category,
                envelope_name,
                payment_name,
                decimal_to_string(target_amount),
                decimal_to_string(monthly_amount),
                due_date,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def load_planned_payments(self, telegram_id: int, active_only: bool = True) -> list[dict]:
        query = "SELECT * FROM planned_payments WHERE telegram_id = ?"
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY id"
        rows = self.connection.execute(query, (telegram_id,)).fetchall()
        return [
            {
                "id": row["id"],
                "category": row["category"],
                "envelope_name": row["envelope_name"],
                "payment_name": row["payment_name"],
                "target_amount": string_to_decimal(row["target_amount"]),
                "saved_amount": string_to_decimal(row["saved_amount"]),
                "monthly_amount": string_to_decimal(row["monthly_amount"]),
                "due_date": row["due_date"],
                "active": bool(row["active"]),
            }
            for row in rows
        ]

    def update_planned_payment_saved(
        self,
        telegram_id: int,
        payment_id: int,
        saved_amount: Decimal,
        active: bool,
    ) -> None:
        self.connection.execute(
            """
            UPDATE planned_payments
            SET saved_amount = ?, active = ?
            WHERE telegram_id = ? AND id = ?
            """,
            (decimal_to_string(saved_amount), int(active), telegram_id, payment_id),
        )
        self.connection.commit()

    def update_planned_payment_monthly(
        self, telegram_id: int, payment_id: int, monthly_amount: Decimal
    ) -> None:
        self.connection.execute(
            """
            UPDATE planned_payments SET monthly_amount = ?
            WHERE telegram_id = ? AND id = ?
            """,
            (decimal_to_string(monthly_amount), telegram_id, payment_id),
        )
        self.connection.commit()

    def deactivate_planned_payment(self, telegram_id: int, payment_id: int) -> None:
        self.connection.execute(
            "UPDATE planned_payments SET active = 0 WHERE telegram_id = ? AND id = ?",
            (telegram_id, payment_id),
        )
        self.connection.commit()

    def update_planned_payment_details(
        self, telegram_id: int, payment_id: int, *,
        target_amount: Decimal | None = None, due_date: str | None = None,
    ) -> None:
        if target_amount is not None:
            self.connection.execute(
                "UPDATE planned_payments SET target_amount = ? WHERE telegram_id = ? AND id = ?",
                (decimal_to_string(target_amount), telegram_id, payment_id),
            )
        if due_date is not None:
            self.connection.execute(
                "UPDATE planned_payments SET due_date = ? WHERE telegram_id = ? AND id = ?",
                (due_date, telegram_id, payment_id),
            )
        self.connection.commit()

    def deactivate_all_planned_payments(self, telegram_id: int) -> None:
        self.connection.execute(
            "UPDATE planned_payments SET active = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        self.connection.commit()

    def deactivate_tax_obligation(self, telegram_id: int, obligation_id: int) -> None:
        self.connection.execute(
            "UPDATE tax_obligations SET active = 0 WHERE telegram_id = ? AND id = ?",
            (telegram_id, obligation_id),
        )
        self.connection.commit()

    def rename_tax_obligation(
        self,
        telegram_id: int,
        tax_type: str,
        old_name: str,
        new_name: str,
    ) -> None:
        """Rename one tax object without splitting its historical balance."""
        old_key = f"{tax_type} · {old_name}"
        new_key = f"{tax_type} · {new_name}"
        rows = self.connection.execute(
            "SELECT id, payload FROM operation_log WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchall()
        for row in rows:
            payload = deserialize_json(row["payload"])
            details = payload.get("planned_tax_details")
            if not isinstance(details, dict) or old_key not in details:
                continue
            old_value = string_to_decimal(details.pop(old_key))
            new_value = string_to_decimal(details.get(new_key, "0"))
            details[new_key] = decimal_to_string(old_value + new_value)
            self.connection.execute(
                "UPDATE operation_log SET payload = ? WHERE id = ? AND telegram_id = ?",
                (serialize_json(payload), row["id"], telegram_id),
            )
        self.connection.execute(
            """
            UPDATE tax_obligations SET object_name = ?
            WHERE telegram_id = ? AND tax_type = ? AND object_name = ?
            """,
            (new_name, telegram_id, tax_type, old_name),
        )
        self.connection.execute(
            "UPDATE tax_payments SET tax_name = ? WHERE telegram_id = ? AND tax_name = ?",
            (new_key, telegram_id, old_key),
        )
        self.connection.commit()

    def update_tax_obligation_plan(
        self,
        telegram_id: int,
        obligation_id: int,
        *,
        target_amount: Decimal,
        months: int,
        monthly_amount: Decimal,
        annual_monthly_amount: Decimal,
    ) -> None:
        self.connection.execute(
            """
            UPDATE tax_obligations
            SET target_amount = ?, months = ?, monthly_amount = ?,
                annual_monthly_amount = ?
            WHERE telegram_id = ? AND id = ? AND active = 1
            """,
            (
                decimal_to_string(target_amount),
                int(months),
                decimal_to_string(monthly_amount),
                decimal_to_string(annual_monthly_amount),
                telegram_id,
                obligation_id,
            ),
        )
        self.connection.commit()

    def update_tax_obligation_saved(
        self,
        telegram_id: int,
        obligation_id: int,
        saved_amount: Decimal,
        active: bool,
    ) -> None:
        self.connection.execute(
            """
            UPDATE tax_obligations
            SET saved_before = ?, active = ?
            WHERE telegram_id = ? AND id = ?
            """,
            (
                decimal_to_string(saved_amount),
                int(active),
                telegram_id,
                obligation_id,
            ),
        )
        self.connection.commit()

    def update_tax_obligation_monthly(
        self, telegram_id: int, obligation_id: int, monthly_amount: Decimal
    ) -> None:
        self.connection.execute(
            """
            UPDATE tax_obligations SET monthly_amount = ?
            WHERE telegram_id = ? AND id = ?
            """,
            (decimal_to_string(monthly_amount), telegram_id, obligation_id),
        )
        self.connection.commit()

    def update_tax_obligation_annual_monthly(
        self, telegram_id: int, obligation_id: int, annual_monthly_amount: Decimal
    ) -> None:
        self.connection.execute(
            """
            UPDATE tax_obligations SET annual_monthly_amount = ?
            WHERE telegram_id = ? AND id = ?
            """,
            (decimal_to_string(annual_monthly_amount), telegram_id, obligation_id),
        )
        self.connection.commit()

    def update_tax_obligation_notice(
        self,
        telegram_id: int,
        obligation_id: int,
        *,
        target_amount: Decimal,
        saved_before: Decimal,
        months: int,
        monthly_amount: Decimal,
        annual_monthly_amount: Decimal,
        due_date: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE tax_obligations
            SET target_amount = ?, saved_before = ?, months = ?, monthly_amount = ?,
                annual_monthly_amount = ?, due_date = ?, notice_received = 1,
                ready_reminder_sent_at = NULL, last_notice_reminder_at = NULL
            WHERE telegram_id = ? AND id = ? AND active = 1
            """,
            (
                decimal_to_string(target_amount),
                decimal_to_string(saved_before),
                int(months),
                decimal_to_string(monthly_amount),
                decimal_to_string(annual_monthly_amount),
                due_date,
                telegram_id,
                obligation_id,
            ),
        )
        self.connection.commit()

    def due_tax_readiness_reminders(self, today: str) -> list[dict]:
        """Return annual taxes due for the Nov 1/15/25 notice reminders."""
        rows = self.connection.execute(
            """
            SELECT * FROM tax_obligations
            WHERE active = 1
              AND tax_type IN ('Налог на имущество', 'Транспортный налог', 'Земельный налог')
              AND due_date IS NOT NULL
              AND notice_received = 0
            ORDER BY telegram_id, id
            """,
        ).fetchall()
        today_date = date.fromisoformat(today)
        result = []
        for row in rows:
            due = date.fromisoformat(row["due_date"])
            stages = (
                date(due.year, 11, 1),
                date(due.year, 11, 15),
                date(due.year, 11, 25),
            )
            reached = [stage for stage in stages if stage <= today_date < due]
            if not reached:
                continue
            last_raw = row["last_notice_reminder_at"] or row["ready_reminder_sent_at"]
            last_date = date.fromisoformat(last_raw[:10]) if last_raw else None
            if last_date is not None and last_date >= reached[-1]:
                continue
            result.append({
                "id": int(row["id"]),
                "telegram_id": int(row["telegram_id"]),
                "tax_type": row["tax_type"],
                "object_name": row["object_name"],
                "target_amount": string_to_decimal(row["target_amount"]),
                "saved_before": string_to_decimal(row["saved_before"]),
                "due_date": row["due_date"],
            })
        return result

    def mark_tax_readiness_reminder_sent(
        self, telegram_id: int, obligation_id: int, sent_on: str | None = None,
    ) -> None:
        sent_on = sent_on or date.today().isoformat()
        self.connection.execute(
            """
            UPDATE tax_obligations
            SET ready_reminder_sent_at = ?, last_notice_reminder_at = ?
            WHERE telegram_id = ? AND id = ?
            """,
            (
                datetime.utcnow().isoformat(),
                sent_on,
                telegram_id,
                obligation_id,
            ),
        )
        self.connection.commit()

    def due_tax_payment_reminders(self, today: str) -> list[dict]:
        """Return unpaid annual taxes due today or overdue.

        The first reminders are sent on Dec 1 and Dec 2. After that they repeat
        weekly, unless the user explicitly asks to be reminded in three days.
        """
        rows = self.connection.execute(
            """
            SELECT * FROM tax_obligations
            WHERE active = 1
              AND tax_type IN ('Налог на имущество', 'Транспортный налог', 'Земельный налог')
              AND due_date IS NOT NULL
            ORDER BY telegram_id, id
            """
        ).fetchall()
        today_date = date.fromisoformat(today)
        result = []
        for row in rows:
            due = date.fromisoformat(row["due_date"])
            if today_date < due:
                continue
            paid = self.tax_obligation_paid_amount(int(row["telegram_id"]), int(row["id"]))
            target = string_to_decimal(row["target_amount"])
            remaining = max(Decimal("0"), target - paid)
            if remaining <= Decimal("0"):
                continue

            next_raw = row["next_payment_reminder_date"]
            last_raw = row["last_payment_reminder_at"]
            if next_raw:
                should_send = date.fromisoformat(next_raw) <= today_date
            elif not last_raw:
                should_send = True
            else:
                last_date = date.fromisoformat(last_raw[:10])
                first_followup = due + timedelta(days=1)
                if last_date <= due and today_date >= first_followup:
                    should_send = True
                else:
                    should_send = today_date >= last_date + timedelta(days=7)
            if not should_send:
                continue
            result.append({
                "id": int(row["id"]),
                "telegram_id": int(row["telegram_id"]),
                "tax_type": row["tax_type"],
                "object_name": row["object_name"],
                "target_amount": target,
                "paid_amount": paid,
                "remaining_amount": remaining,
                "due_date": row["due_date"],
            })
        return result

    def mark_tax_payment_reminder_sent(
        self, telegram_id: int, obligation_id: int, sent_on: str | None = None,
    ) -> None:
        sent_on = sent_on or date.today().isoformat()
        self.connection.execute(
            """
            UPDATE tax_obligations
            SET last_payment_reminder_at = ?, next_payment_reminder_date = NULL
            WHERE telegram_id = ? AND id = ?
            """,
            (sent_on, telegram_id, obligation_id),
        )
        self.connection.commit()

    def snooze_tax_payment_reminders(self, telegram_id: int, days: int = 3) -> None:
        next_date = (date.today() + timedelta(days=max(1, days))).isoformat()
        self.connection.execute(
            """
            UPDATE tax_obligations
            SET next_payment_reminder_date = ?
            WHERE telegram_id = ? AND active = 1
              AND tax_type IN ('Налог на имущество', 'Транспортный налог', 'Земельный налог')
            """,
            (next_date, telegram_id),
        )
        self.connection.commit()

    # ========================================================
    # КЭШ АВТОМАТИЧЕСКИХ ВАЛЮТНЫХ КУРСОВ
    # ========================================================

    def load_exchange_rate(self, currency_code: str) -> Optional[dict]:
        row = self.connection.execute(
            """
            SELECT currency_code, rub_per_unit, rate_date, fetched_at, source
            FROM exchange_rates
            WHERE currency_code = ?
            """,
            (str(currency_code).strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        return {
            "currency_code": row["currency_code"],
            "rub_per_unit": string_to_decimal(row["rub_per_unit"]),
            "rate_date": row["rate_date"],
            "fetched_at": row["fetched_at"],
            "source": row["source"],
        }

    def save_exchange_rate(
        self,
        currency_code: str,
        rub_per_unit: Decimal,
        rate_date: str,
        fetched_at: str,
        source: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO exchange_rates (
                currency_code, rub_per_unit, rate_date, fetched_at, source
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(currency_code) DO UPDATE SET
                rub_per_unit = excluded.rub_per_unit,
                rate_date = excluded.rate_date,
                fetched_at = excluded.fetched_at,
                source = excluded.source
            """,
            (
                str(currency_code).strip().upper(),
                decimal_to_string(rub_per_unit),
                rate_date,
                fetched_at,
                source,
            ),
        )
        self.connection.commit()

    # ========================================================
    # ПОЛНОЕ УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ
    # ========================================================

    def initial_distribution_available(self, telegram_id: int) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM operation_log WHERE telegram_id = ? AND operation_type IN "
            "('income_distribution', 'initial_distribution_closed') LIMIT 1",
            (telegram_id,),
        ).fetchone() is None

    def close_initial_distribution(self, telegram_id: int) -> None:
        if self.initial_distribution_available(telegram_id):
            self.save_operation(telegram_id, "initial_distribution_closed", {})

    def clear_accounting_history(self, telegram_id: int):
        """Clear persisted history and funded amounts, retaining configured targets."""
        with self.connection:
            for table in ("operation_log", "tax_payments"):
                self.connection.execute(f"DELETE FROM {table} WHERE telegram_id = ?", (telegram_id,))
            self.connection.execute(
                "UPDATE tax_obligations SET opening_amount = '0', saved_before = '0', "
                "ready_reminder_sent_at = NULL, last_notice_reminder_at = NULL, "
                "last_payment_reminder_at = NULL, next_payment_reminder_date = NULL "
                "WHERE telegram_id = ?", (telegram_id,))
            self.connection.execute(
                "UPDATE planned_payments SET saved_amount = '0' WHERE telegram_id = ?", (telegram_id,))

    def delete_user(
        self,
        telegram_id: int,
    ):

        self.connection.execute(
            """
            DELETE FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

        self.connection.commit()

    # ========================================================
    # СТАТИСТИКА
    # ========================================================

    def operation_count(
        self,
        telegram_id: int,
    ) -> int:

        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM operation_log
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()

        return int(
            row["count"]
        )

    # ========================================================
    # ЗАКРЫТИЕ
    # ========================================================

    def close(self):

        if self.connection:
            self.connection.close()


# ============================================================
# ГЛОБАЛЬНЫЙ ОБЪЕКТ БАЗЫ
# ============================================================

db = Database()
