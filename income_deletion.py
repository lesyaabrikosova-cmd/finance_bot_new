"""Transactional removal and chronological replay of income operations.

Deleting an income is a correction of history. Later incomes must therefore
be calculated again from the state that existed before the deleted income.
New income rows carry enough snapshots to perform that replay. Old rows are
only removed when the existing delta rollback is provably sufficient; an
ambiguous legacy chain is rejected before SQLite is changed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

from financial_engine import (
    AllocatorState,
    Credit,
    FinancialAllocator,
    Goal,
    PhaseLifeBudget,
    UserSettings,
)
from time_utils import moscow_today


ZERO = Decimal("0")
REPLAY_CONTEXT_VERSION = 2

_DERIVED_STATE_FIELDS = {
    "operation_log",
    "distribution_history",
    "period_income",
    "period_tax",
    "period_allocations",
    "period_life_topups",
    "goal_balances",
}

_PAYMENT_STATIC_FIELDS = ("category", "envelope_name", "payment_name")
_TAX_STATIC_FIELDS = (
    "tax_type",
    "source_obligation_id",
    "tracking_started_operation_id",
)

_PAYMENT_REPLAY_DERIVED = {"monthly_amount"}
_TAX_REPLAY_DERIVED = {"saved_before", "months", "monthly_amount", "monthly_period"}


class IncomeDeletionError(ValueError):
    """The requested deletion cannot be proven safe."""


def _rows_by_id(rows: Iterable[dict]) -> dict[int, dict]:
    return {int(item["id"]): deepcopy(item) for item in rows}


def _payment_snapshot(database, telegram_id: int) -> list[dict]:
    return deepcopy(database.load_planned_payments(telegram_id, active_only=False))


def _tax_snapshot(database, telegram_id: int) -> list[dict]:
    return deepcopy(database.load_tax_obligations(telegram_id, active_only=False))


def _goal_balances_by_id(allocator: FinancialAllocator) -> dict[str, Decimal]:
    return {
        str(goal.uid): Decimal(str(allocator.state.goal_balances.get(goal.name, ZERO)))
        for goal in allocator.settings.goals
    }


def _settings_snapshot(settings: UserSettings) -> dict:
    """Return a JSON-safe dataclass snapshot used only by replay."""

    return asdict(settings)


def _settings_from_snapshot(snapshot: dict) -> UserSettings:
    try:
        data = deepcopy(snapshot)
        data["credits"] = [
            item if isinstance(item, Credit) else Credit(**item)
            for item in data.get("credits", [])
        ]
        data["goals"] = [
            item if isinstance(item, Goal) else Goal(**item)
            for item in data.get("goals", [])
        ]
        data["phase_life_budgets"] = {
            phase: budget if isinstance(budget, PhaseLifeBudget) else PhaseLifeBudget(**budget)
            for phase, budget in data.get("phase_life_budgets", {}).items()
        }
        return UserSettings(**data)
    except (TypeError, ValueError, KeyError) as error:
        raise IncomeDeletionError(
            "Снимок настроек этого поступления повреждён. "
            "Удаление остановлено без изменения балансов."
        ) from error


def _normalize_legacy_state_snapshot(
    snapshot: dict,
    settings_snapshot: dict,
) -> dict:
    """Add independent БР progress to snapshots saved by the old model."""

    result = deepcopy(snapshot)
    if "household_reserve_progress" in result:
        return result
    settings = _settings_from_snapshot(settings_snapshot)
    life = Decimal(str(result.get("life_balance", ZERO)))
    result["household_reserve_progress"] = min(
        settings.household_reserve,
        max(ZERO, life - settings.critical_life),
    )
    return result


def _merge_replayed_settings(
    replayed: UserSettings,
    expected_after: UserSettings,
    current: UserSettings,
) -> UserSettings:
    """Keep later manual settings edits, otherwise take the replayed value."""

    result = deepcopy(current)
    for item in fields(UserSettings):
        name = item.name
        if getattr(current, name) == getattr(expected_after, name):
            setattr(result, name, deepcopy(getattr(replayed, name)))
    return result


def capture_income_rollback_context(
    database,
    telegram_id: int,
    allocator: FinancialAllocator | None = None,
) -> dict:
    """Capture all state outside ``AllocatorState`` before an income."""

    context = {
        "version": REPLAY_CONTEXT_VERSION,
        "planned_payments_before": _payment_snapshot(database, telegram_id),
        "tax_obligations_before": _tax_snapshot(database, telegram_id),
    }
    if allocator is not None:
        context["goal_balances_before"] = _goal_balances_by_id(allocator)
        context["settings_before"] = _settings_snapshot(allocator.settings)
    return context


def attach_income_rollback_context(
    database,
    telegram_id: int,
    allocator: FinancialAllocator,
    context_before: dict,
) -> None:
    """Finish the replay record on the just-created income payload."""

    if not allocator.state.operation_log:
        raise RuntimeError("Не найдена операция дохода для защищённого снимка.")
    operation = allocator.state.operation_log[-1]
    if operation.get("type") != "income_distribution":
        raise RuntimeError("Последняя операция не является распределением дохода.")

    context = dict(context_before)
    context.update({
        "version": REPLAY_CONTEXT_VERSION,
        "state_after": allocator._income_rollback_snapshot(),
        "credits_after": {
            credit.name: {
                "principal_balance": credit.principal_balance,
                "status": credit.status,
            }
            for credit in allocator.settings.credits
        },
        "goal_balances_after": _goal_balances_by_id(allocator),
        "settings_after": _settings_snapshot(allocator.settings),
        "planned_payments_after": _payment_snapshot(database, telegram_id),
        "tax_obligations_after": _tax_snapshot(database, telegram_id),
    })
    operation["rollback_context"] = context


def _income_context(operation: dict) -> dict:
    context = (operation.get("payload") or {}).get("rollback_context") or {}
    return context if isinstance(context, dict) else {}


def _current_period_operation(
    operations: list[dict], operation_id: int,
) -> tuple[dict, int]:
    target_index = next(
        (index for index, item in enumerate(operations) if item.get("id") == operation_id),
        None,
    )
    if target_index is None:
        raise IncomeDeletionError("Это поступление уже недоступно в истории.")
    target = operations[target_index]
    if target.get("type") != "income_distribution":
        raise IncomeDeletionError("Можно удалить только поступление дохода.")

    target_id = int(target.get("id") or 0)
    latest_reset_id = max(
        (
            int(item.get("id") or 0)
            for item in operations
            if item.get("type") == "period_reset"
        ),
        default=0,
    )
    if latest_reset_id and target_id < latest_reset_id:
        raise IncomeDeletionError(
            "Этот доход относится к уже закрытому расчётному периоду. "
            "Удаление изменило бы сохранённые итоги прошлого периода."
        )
    return target, target_index


def _require_replay_context(operation: dict) -> dict:
    payload = operation.get("payload") or {}
    context = _income_context(operation)
    required = (
        payload.get("state_before"),
        context.get("state_after"),
        context.get("settings_before"),
        context.get("planned_payments_before") is not None,
        context.get("planned_payments_after") is not None,
        context.get("tax_obligations_before") is not None,
        context.get("tax_obligations_after") is not None,
    )
    if int(context.get("version") or 0) < REPLAY_CONTEXT_VERSION or not all(required):
        raise IncomeDeletionError(
            "Эта запись создана до появления полного защищённого пересчёта. "
            "Удалить её вместе с последующими операциями без риска нельзя."
        )
    return context


def _same_value(left, right) -> bool:
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        try:
            return Decimal(str(left or ZERO)) == Decimal(str(right or ZERO))
        except Exception:
            return False
    return left == right


def _state_manual_patch(expected: dict, actual: dict) -> dict:
    """Absolute changes outside income-derived period aggregates."""

    result = {}
    for item in fields(AllocatorState):
        name = item.name
        if name in _DERIVED_STATE_FIELDS:
            continue
        if not _same_value(expected.get(name), actual.get(name)):
            result[name] = deepcopy(actual.get(name))
    return result


def _apply_state_patch(state: AllocatorState, patch: dict) -> None:
    for name, value in patch.items():
        setattr(state, name, deepcopy(value))


def _goal_patch(expected: dict, actual: dict) -> dict[str, Decimal]:
    return {
        uid: Decimal(str(value))
        for uid, value in actual.items()
        if uid not in expected or not _same_value(expected.get(uid), value)
    }


def _apply_goal_patch(allocator: FinancialAllocator, patch: dict[str, Decimal]) -> None:
    goals = {str(goal.uid): goal for goal in allocator.settings.goals}
    for uid, amount in patch.items():
        goal = goals.get(str(uid))
        if goal is not None:
            allocator.state.goal_balances[goal.name] = Decimal(str(amount))


def _credit_snapshot(settings: UserSettings) -> dict[str, dict]:
    return {
        credit.name: {
            "principal_balance": Decimal(str(credit.principal_balance)),
            "status": str(credit.status),
        }
        for credit in settings.credits
    }


def _credit_patch(expected: dict, actual: dict) -> dict[str, dict]:
    return {
        name: deepcopy(value)
        for name, value in actual.items()
        if name not in expected or value != expected.get(name)
    }


def _apply_credit_patch(settings: UserSettings, patch: dict[str, dict]) -> None:
    credits = {credit.name: credit for credit in settings.credits}
    for name, value in patch.items():
        credit = credits.get(name)
        if credit is None:
            continue
        credit.principal_balance = Decimal(str(value["principal_balance"]))
        credit.status = str(value["status"])


def _validate_external_rows(
    current_rows: list[dict],
    snapshots: Iterable[list[dict]],
    static_fields: tuple[str, ...],
    label: str,
) -> None:
    current = _rows_by_id(current_rows)
    for rows in snapshots:
        for row_id, row in _rows_by_id(rows).items():
            existing = current.get(row_id)
            if existing is None:
                raise IncomeDeletionError(
                    f"Связанный {label} больше не найден. "
                    "Удаление остановлено без изменения балансов."
                )
            if any(not _same_value(existing.get(key), row.get(key)) for key in static_fields):
                raise IncomeDeletionError(
                    f"Параметры связанного {label} были изменены. "
                    "Исторический пересчёт остановлен без изменения балансов."
                )


def _apply_payment_snapshot(database, telegram_id: int, rows: list[dict]) -> None:
    desired = _rows_by_id(rows)
    current = _rows_by_id(database.load_planned_payments(telegram_id, active_only=False))
    for row_id, row in current.items():
        wanted = desired.get(row_id)
        if wanted is None:
            disabled = deepcopy(row)
            disabled["active"] = False
            if not database.restore_planned_payment_snapshot(telegram_id, disabled):
                raise IncomeDeletionError("Связанный плановый платёж больше не найден.")
            continue
        if not database.restore_planned_payment_snapshot(telegram_id, wanted):
            raise IncomeDeletionError("Связанный плановый платёж больше не найден.")


def _apply_tax_snapshot(database, telegram_id: int, rows: list[dict]) -> None:
    desired = _rows_by_id(rows)
    current = _rows_by_id(database.load_tax_obligations(telegram_id, active_only=False))
    for row_id, row in current.items():
        wanted = desired.get(row_id)
        if wanted is None:
            disabled = deepcopy(row)
            disabled["active"] = False
            if not database.restore_tax_obligation_snapshot(telegram_id, disabled):
                raise IncomeDeletionError("Связанный налоговый план больше не найден.")
            continue
        if not database.restore_tax_obligation_snapshot(telegram_id, wanted):
            raise IncomeDeletionError("Связанный налоговый план больше не найден.")


def _apply_snapshot_transition(
    database,
    telegram_id: int,
    expected_rows: list[dict],
    actual_rows: list[dict],
    *,
    kind: str,
) -> None:
    """Apply only changes that happened outside income distribution.

    Saved progress and the current-month quota are replay products. If the
    two historical snapshots agree on a field, the freshly replayed value is
    retained instead of restoring money contributed by the deleted income.
    """

    expected = _rows_by_id(expected_rows)
    actual = _rows_by_id(actual_rows)
    current_rows = (
        database.load_planned_payments(telegram_id, active_only=False)
        if kind == "payment"
        else database.load_tax_obligations(telegram_id, active_only=False)
    )
    current = _rows_by_id(current_rows)
    derived = _PAYMENT_REPLAY_DERIVED if kind == "payment" else _TAX_REPLAY_DERIVED
    restore = (
        database.restore_planned_payment_snapshot
        if kind == "payment"
        else database.restore_tax_obligation_snapshot
    )

    for row_id in set(expected) | set(actual):
        before = expected.get(row_id)
        after = actual.get(row_id)
        present = current.get(row_id)
        if after is None:
            if present is not None:
                disabled = deepcopy(present)
                disabled["active"] = False
                if not restore(telegram_id, disabled):
                    raise IncomeDeletionError("Связанная запись исчезла во время пересчёта.")
            continue
        if present is None:
            raise IncomeDeletionError(
                "Связанная запись больше не найдена. Пересчёт остановлен без изменений."
            )
        if before is None:
            # The row was created between the two incomes. Its opening balance
            # is an explicit fact supplied by the user, so restore it exactly.
            if not restore(telegram_id, after):
                raise IncomeDeletionError("Связанная запись исчезла во время пересчёта.")
            continue

        merged = deepcopy(present)
        config_changed = False
        for key, value in after.items():
            if key == "id" or key in derived:
                continue
            if not _same_value(before.get(key), value):
                merged[key] = deepcopy(value)
                config_changed = True
        if kind == "payment":
            # An explicit activation/deactivation is not a monetary replay
            # product and must survive.
            if bool(before.get("active")) != bool(after.get("active")):
                merged["active"] = bool(after.get("active"))
        elif config_changed and any(
            not _same_value(before.get(key), after.get(key))
            for key in ("target_amount", "due_date", "notice_received")
        ):
            # Force refresh_planned_tax_targets to calculate a fresh quota
            # from the replayed balance for the changed plan.
            merged["monthly_period"] = ""
        if not restore(telegram_id, merged):
            raise IncomeDeletionError("Связанная запись исчезла во время пересчёта.")


def _restore_final_payment_configuration(
    database,
    telegram_id: int,
    original_current: list[dict],
    expected_after: list[dict],
) -> None:
    expected = _rows_by_id(expected_after)
    for row in original_current:
        row_id = int(row["id"])
        database.update_planned_payment_details(
            telegram_id,
            row_id,
            target_amount=Decimal(str(row["target_amount"])),
            due_date=str(row["due_date"]),
        )
        prior = expected.get(row_id)
        if prior is None or (
            not _same_value(prior.get("saved_amount"), row.get("saved_amount"))
            or bool(prior.get("active")) != bool(row.get("active"))
        ):
            database.update_planned_payment_saved(
                telegram_id,
                row_id,
                Decimal(str(row["saved_amount"])),
                bool(row["active"]),
            )


def _restore_final_tax_lifecycle(
    database,
    telegram_id: int,
    original_current: list[dict],
    expected_after: list[dict],
) -> None:
    expected = _rows_by_id(expected_after)
    for row in original_current:
        prior = expected.get(int(row["id"]))
        # Saved progress is rebuilt from the ledger. Only lifecycle changes
        # made outside income distribution are restored here.
        if prior is None or bool(prior.get("active")) != bool(row.get("active")):
            database.update_tax_obligation_saved(
                telegram_id,
                int(row["id"]),
                Decimal(str(row["saved_before"])),
                bool(row["active"]),
            )


def _income_type_for_settings(payload: dict, settings: UserSettings) -> str:
    income_type_id = str(payload.get("income_type_id") or "")
    if income_type_id:
        for name, uid in settings.income_type_ids.items():
            if str(uid) == income_type_id:
                return name
    return str(payload.get("income_type") or "Без типа")


def _operation_date(payload: dict) -> date:
    try:
        return date.fromisoformat(str(payload["date"]))
    except (KeyError, TypeError, ValueError) as error:
        raise IncomeDeletionError(
            "В одной из последующих записей нет корректной даты. "
            "Исторический пересчёт остановлен."
        ) from error


def _legacy_delete_latest(
    database,
    telegram_id: int,
    target: dict,
    allocator: FinancialAllocator,
    *,
    reconcile_taxes: Callable[[int, object], None],
    rebuild_period_analytics: Callable[[object, int], None],
) -> FinancialAllocator:
    payload = target.get("payload") or {}
    context = _income_context(target)
    if not context.get("state_after"):
        allocations = payload.get("allocations") or {}
        planned_envelopes = {
            str(item["envelope_name"])
            for item in database.load_planned_payments(telegram_id, active_only=False)
        }
        if any(
            str(key).startswith("КЖ:")
            and str(key)[3:] in planned_envelopes
            and Decimal(str(amount)) > ZERO
            for key, amount in allocations.items()
        ):
            raise IncomeDeletionError(
                "Эта старая запись не содержит защищённого снимка планового "
                "платежа. Удалить её автоматически без риска нельзя."
            )
        if any(
            Decimal(str(allocations.get(name, ZERO))) > ZERO
            for name in ("Подушка", "Фонд Зарплаты", "Стабилизатор дохода", "Досрочное")
        ):
            raise IncomeDeletionError(
                "Эта старая запись не содержит защищённого снимка резервов. "
                "Удалить её автоматически без риска нельзя."
            )
    restored = deepcopy(allocator)
    try:
        restored.rollback_income_operation(payload, restore_snapshot=False)
    except ValueError as error:
        raise IncomeDeletionError(str(error)) from error
    with database.transaction():
        if not database.delete_income_operation(telegram_id, int(target["id"])):
            raise RuntimeError("Запись дохода исчезла во время удаления.")
        reconcile_taxes(telegram_id, restored)
        rebuild_period_analytics(restored, telegram_id)
        database.save_allocator(telegram_id, restored)
    return restored


def delete_income_safely(
    database,
    telegram_id: int,
    operation_id: int,
    *,
    reconcile_taxes: Callable[[int, object], None],
    rebuild_period_analytics: Callable[[object, int], None],
    today: date | None = None,
) -> FinancialAllocator:
    """Delete one current-period income and replay every later income."""

    operations = database.load_operations(telegram_id, limit=-1)
    target, target_index = _current_period_operation(operations, operation_id)
    allocator = database.load_allocator(telegram_id)
    if allocator is None:
        raise IncomeDeletionError("Финансовый профиль не найден.")
    if allocator.state.period_status == "active" and allocator.state.period_started_at:
        try:
            period_start = date.fromisoformat(
                str(allocator.state.period_started_at).split("T", 1)[0]
            )
            target_date = _operation_date(target.get("payload") or {})
        except (TypeError, ValueError, IncomeDeletionError):
            raise IncomeDeletionError(
                "Не удалось подтвердить расчётный период этого дохода. "
                "Удаление остановлено без изменения балансов."
            )
        if target_date < period_start:
            raise IncomeDeletionError(
                "Этот доход относится к уже закрытому расчётному периоду. "
                "Удаление изменило бы сохранённые итоги прошлого периода."
            )

    later_incomes = [
        item for item in reversed(operations[:target_index])
        if item.get("type") == "income_distribution"
    ]
    target_context = _income_context(target)
    if not later_incomes and int(target_context.get("version") or 0) < REPLAY_CONTEXT_VERSION:
        return _legacy_delete_latest(
            database,
            telegram_id,
            target,
            allocator,
            reconcile_taxes=reconcile_taxes,
            rebuild_period_analytics=rebuild_period_analytics,
        )

    replay_chain = [target, *later_incomes]
    contexts = [_require_replay_context(item) for item in replay_chain]
    for operation, context in zip(replay_chain, contexts):
        payload = operation.get("payload") or {}
        payload["state_before"] = _normalize_legacy_state_snapshot(
            payload["state_before"], context["settings_before"],
        )
        context["state_after"] = _normalize_legacy_state_snapshot(
            context["state_after"], context["settings_after"],
        )
    current_payments = _payment_snapshot(database, telegram_id)
    current_taxes = _tax_snapshot(database, telegram_id)
    _validate_external_rows(
        current_payments,
        (
            rows
            for context in contexts
            for rows in (
                context["planned_payments_before"],
                context["planned_payments_after"],
            )
        ),
        _PAYMENT_STATIC_FIELDS,
        "плановый платёж",
    )
    _validate_external_rows(
        current_taxes,
        (
            rows
            for context in contexts
            for rows in (
                context["tax_obligations_before"],
                context["tax_obligations_after"],
            )
        ),
        _TAX_STATIC_FIELDS,
        "налоговый план",
    )

    # Compute every manual discontinuity before touching SQLite.
    state_patches: list[dict] = []
    goal_patches: list[dict[str, Decimal]] = []
    credit_patches: list[dict[str, dict]] = []
    previous_context = contexts[0]
    for operation, context in zip(later_incomes, contexts[1:]):
        state_patches.append(_state_manual_patch(
            previous_context["state_after"],
            (operation.get("payload") or {})["state_before"],
        ))
        goal_patches.append(_goal_patch(
            previous_context.get("goal_balances_after") or {},
            context.get("goal_balances_before") or {},
        ))
        next_settings = _settings_from_snapshot(context["settings_before"])
        credit_patches.append(_credit_patch(
            previous_context.get("credits_after") or {},
            _credit_snapshot(next_settings),
        ))
        previous_context = context

    current_state_snapshot = allocator._income_rollback_snapshot()
    final_state_patch = _state_manual_patch(
        contexts[-1]["state_after"], current_state_snapshot,
    )
    final_goal_patch = _goal_patch(
        contexts[-1].get("goal_balances_after") or {},
        _goal_balances_by_id(allocator),
    )
    final_credit_patch = _credit_patch(
        contexts[-1].get("credits_after") or {},
        _credit_snapshot(allocator.settings),
    )

    baseline_settings = _settings_from_snapshot(target_context["settings_before"])
    baseline_state = AllocatorState(**deepcopy((target.get("payload") or {})["state_before"]))
    replayed = FinancialAllocator(baseline_settings, baseline_state)
    original_current_settings = deepcopy(allocator.settings)
    replay_today = today or moscow_today()

    # Lazy imports avoid coupling the pure financial engine to Telegram modules.
    from planned_payments import (
        apply_planned_payment_allocation,
        refresh_planned_payment_targets,
    )
    from taxes import apply_planned_tax_allocation, refresh_planned_tax_targets

    with database.transaction():
        _apply_payment_snapshot(
            database, telegram_id, target_context["planned_payments_before"],
        )
        _apply_tax_snapshot(
            database, telegram_id, target_context["tax_obligations_before"],
        )
        if not database.delete_income_operation(telegram_id, operation_id):
            raise RuntimeError("Запись дохода исчезла во время удаления.")

        for index, operation in enumerate(later_incomes):
            context = contexts[index + 1]
            _apply_state_patch(replayed.state, state_patches[index])
            _apply_goal_patch(replayed, goal_patches[index])
            _apply_credit_patch(replayed.settings, credit_patches[index])

            _apply_snapshot_transition(
                database,
                telegram_id,
                contexts[index]["planned_payments_after"],
                context["planned_payments_before"],
                kind="payment",
            )
            _apply_snapshot_transition(
                database,
                telegram_id,
                contexts[index]["tax_obligations_after"],
                context["tax_obligations_before"],
                kind="tax",
            )

            historical_settings = _settings_from_snapshot(context["settings_before"])
            replayed_credit_state = _credit_snapshot(replayed.settings)
            _apply_credit_patch(historical_settings, replayed_credit_state)
            replayed = FinancialAllocator(historical_settings, replayed.state)
            payload = operation.get("payload") or {}
            income_date = _operation_date(payload)
            refresh_planned_payment_targets(telegram_id, replayed, income_date)
            refresh_planned_tax_targets(telegram_id, replayed, income_date)
            context_before = capture_income_rollback_context(
                database, telegram_id, replayed,
            )
            original_strategy = replayed.settings.protective_stage_c_strategy
            replayed.settings.protective_stage_c_strategy = str(
                payload.get("distribution_strategy") or original_strategy
            )
            result = replayed.process_income(
                Decimal(str(payload["income"])),
                _income_type_for_settings(payload, replayed.settings),
                income_date=income_date,
                tax_override=Decimal(str(payload.get("tax", ZERO))),
                note=payload.get("note"),
            )
            replayed.settings.protective_stage_c_strategy = original_strategy
            if not result.checks["ok"]:
                raise IncomeDeletionError(
                    "Повторный расчёт не прошёл проверку контрольной суммы. "
                    "Удаление отменено."
                )
            apply_planned_tax_allocation(
                telegram_id,
                replayed,
                result.allocations.get("КЖ:Налоги", ZERO),
            )
            for envelope_name in {
                item["envelope_name"]
                for item in database.load_planned_payments(telegram_id)
            }:
                apply_planned_payment_allocation(
                    telegram_id,
                    replayed,
                    envelope_name,
                    result.allocations.get(f"КЖ:{envelope_name}", ZERO),
                )
            attach_income_rollback_context(
                database, telegram_id, replayed, context_before,
            )
            new_payload = deepcopy(replayed.state.operation_log[-1])
            if payload.get("income_type_id"):
                new_payload["income_type_id"] = payload["income_type_id"]
            if not database.update_income_operation_payload(
                telegram_id, int(operation["id"]), new_payload,
            ):
                raise RuntimeError("Не удалось обновить последующее поступление.")
            replayed.state.operation_log.clear()
            replayed.state.distribution_history.clear()

        # Return to today's configuration while retaining replayed balances.
        expected_final_settings = _settings_from_snapshot(
            contexts[-1]["settings_after"]
        )
        merged_settings = _merge_replayed_settings(
            replayed.settings,
            expected_final_settings,
            original_current_settings,
        )
        replayed_credit_state = _credit_snapshot(replayed.settings)
        final_allocator = FinancialAllocator(merged_settings, replayed.state)
        _apply_credit_patch(final_allocator.settings, replayed_credit_state)
        database.normalize_envelope_names(telegram_id, final_allocator)
        _apply_state_patch(final_allocator.state, final_state_patch)
        _apply_goal_patch(final_allocator, final_goal_patch)
        _apply_credit_patch(final_allocator.settings, final_credit_patch)

        _apply_snapshot_transition(
            database,
            telegram_id,
            contexts[-1]["planned_payments_after"],
            current_payments,
            kind="payment",
        )
        _apply_snapshot_transition(
            database,
            telegram_id,
            contexts[-1]["tax_obligations_after"],
            current_taxes,
            kind="tax",
        )
        refresh_planned_payment_targets(telegram_id, final_allocator, replay_today)
        # Rebuild the cached virtual tax balances from the rewritten ledger,
        # while retaining the already-consumed quota of this month.
        from taxes import tax_obligation_key, virtual_tax_balance
        for item in database.load_tax_obligations(telegram_id):
            actual = min(
                Decimal(str(item["target_amount"])),
                virtual_tax_balance(
                    telegram_id,
                    tax_obligation_key(item["tax_type"], item["object_name"]),
                    int(item["id"]),
                ),
            )
            database.update_tax_obligation_saved(
                telegram_id, int(item["id"]), actual, True,
            )
        refresh_planned_tax_targets(
            telegram_id, final_allocator, replay_today,
            reset_current_period=False,
        )
        rebuild_period_analytics(final_allocator, telegram_id)
        database.save_allocator(telegram_id, final_allocator)
    return final_allocator
