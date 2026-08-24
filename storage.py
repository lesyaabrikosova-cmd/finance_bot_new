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
from datetime import datetime
from decimal import Decimal
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
        "version": 7,
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
        "household_reserve_categories": {
            name: decimal_to_string(amount)
            for name, amount in settings.household_reserve_categories.items()
        },
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
    if isinstance(raw, dict) and raw.get("version") in {2, 3, 4, 5, 6, 7}:
        rates = {
            str(name): string_to_decimal(rate)
            for name, rate in raw.get("rates", {}).items()
        }
        return [name for name, rate in rates.items() if rate > 0], rates
    legacy_types = [str(name) for name in raw] if isinstance(raw, list) else []
    return legacy_types, {name: legacy_rate for name in legacy_types}


def deserialize_income_rhythm(value) -> dict:
    raw = deserialize_json(value)
    if isinstance(raw, dict) and raw.get("version") in {3, 4, 5, 6, 7}:
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
            "household_reserve_categories": {
                str(name): string_to_decimal(amount)
                for name, amount in raw.get("household_reserve_categories", {}).items()
            },
            "phase_life_budgets": {
                str(phase): PhaseLifeBudget(
                    critical_life=budget.get("critical_life", "0"),
                    household_reserve=budget.get("household_reserve", "0"),
                    life_categories=budget.get("life_categories", {}),
                    household_reserve_categories=budget.get(
                        "household_reserve_categories", {}
                    ),
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

                life_categories TEXT NOT NULL,

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
                percentage TEXT NOT NULL,
                balance TEXT NOT NULL,

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

                life_categories,

                debt_strategy,

                calculate_interest_savings,
                developer_mode
            )

            VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?,
                ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?,
                ?,
                ?, ?
            )

            ON CONFLICT(telegram_id)
            DO UPDATE SET

                has_debts =
                    excluded.has_debts,

                employment_type =
                    excluded.employment_type,

                critical_life =
                    excluded.critical_life,

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

                life_categories =
                    excluded.life_categories,

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

                serialize_json(
                    settings.life_categories
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

        for goal in settings.goals:

            cursor.execute(
                """
                INSERT INTO goals (
                    telegram_id,
                    name,
                    percentage,
                    balance
                )

                VALUES (?, ?, ?, ?)
                """,
                (
                    telegram_id,

                    goal.name,

                    decimal_to_string(
                        goal.percentage
                    ),

                    decimal_to_string(
                        goal.balance
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
            ORDER BY id
            """,
            (telegram_id,),
        ).fetchall()

        goals = []

        for goal_row in goal_rows:

            goals.append(
                Goal(
                    name=goal_row["name"],

                    percentage=
                        string_to_decimal(
                            goal_row["percentage"]
                        ),

                    balance=
                        string_to_decimal(
                            goal_row["balance"]
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

            life_categories=
                deserialize_json(
                    row["life_categories"]
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

                period_started_at
            )

            VALUES (
                ?,
                ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?
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
                    excluded.period_started_at
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

        return result

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

        planned_taxes, track_payments = self.load_tax_configuration(telegram_id)
        settings.planned_taxes = {
            name: string_to_decimal(amount)
            for name, amount in planned_taxes.items()
        }
        settings.track_tax_payments = track_payments

        state = self.load_state(
            telegram_id
        )

        if state is None:
            state = AllocatorState()

        allocator = FinancialAllocator(
            settings=settings,
            state=state,
        )

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

    def save_tax_payment(self, telegram_id: int, tax_name: str, amount: Decimal):
        self.ensure_user(telegram_id)
        self.connection.execute(
            """
            INSERT INTO tax_payments (telegram_id, tax_name, amount, paid_at)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, tax_name, decimal_to_string(amount), datetime.utcnow().isoformat()),
        )
        self.connection.commit()

    def load_tax_payments(self, telegram_id: int, year: int | None = None) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT tax_name, amount, paid_at FROM tax_payments
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
                "tax_name": row["tax_name"],
                "amount": string_to_decimal(row["amount"]),
                "paid_at": row["paid_at"],
            })
        return result

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
    ) -> int:
        self.ensure_user(telegram_id)
        cursor = self.connection.execute(
            """
            INSERT INTO tax_obligations (
                telegram_id, tax_type, object_name, target_amount,
                opening_amount, saved_before, months, monthly_amount, due_date, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                telegram_id,
                tax_type,
                object_name,
                decimal_to_string(target_amount),
                decimal_to_string(saved_before),
                decimal_to_string(saved_before),
                months,
                decimal_to_string(monthly_amount),
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
                "due_date": row["due_date"],
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
