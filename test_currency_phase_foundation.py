import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["ALLOCATOR_DATA_DIR"] = _TEST_DATA_DIR.name

from currency_rates import (
    CurrencyRateService,
    CurrencyRateUnavailable,
    convert_to_rub,
    parse_cbr_daily_rates,
)
from financial_engine import AllocatorState, FinancialAllocator, PhaseLifeBudget, UserSettings
from storage import Database, deserialize_income_rhythm, serialize_income_types, serialize_json


CBR_XML = b"""<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="24.08.2026" name="Foreign Currency Market">
  <Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>US Dollar</Name><Value>80,0000</Value></Valute>
  <Valute ID="R01270"><NumCode>356</NumCode><CharCode>INR</CharCode><Nominal>100</Nominal><Name>Indian Rupees</Name><Value>92,5000</Value></Valute>
</ValCurs>"""


class MemoryRateCache:
    def __init__(self):
        self.values = {}

    def load_exchange_rate(self, currency_code):
        return self.values.get(currency_code)

    def save_exchange_rate(self, currency_code, rub_per_unit, rate_date, fetched_at, source):
        self.values[currency_code] = {
            "rub_per_unit": rub_per_unit,
            "rate_date": rate_date,
            "fetched_at": fetched_at,
            "source": source,
        }


def cyclic_settings(**overrides):
    values = dict(
        has_debts=False,
        employment_type="Фрилансер",
        critical_life=Decimal("40000"),
        household_reserve=Decimal("13000"),
        average_income=Decimal("57000"),
        income_rhythm="cyclic",
        profile_type="cyclic",
        income_work_months=Decimal("5"),
        income_gap_months=Decimal("7"),
        life_categories={"Недвижимость": Decimal("4000")},
        household_reserve_categories={"Быт": Decimal("1000")},
    )
    values.update(overrides)
    return UserSettings(**values)


class CurrencyFoundationTests(unittest.TestCase):
    def test_cbr_parser_respects_nominal(self):
        rate_date, rates = parse_cbr_daily_rates(CBR_XML)
        self.assertEqual(rate_date, date(2026, 8, 24))
        self.assertEqual(rates["USD"], Decimal("80.0000"))
        self.assertEqual(rates["INR"], Decimal("0.925"))

    def test_service_uses_stale_cache_when_provider_is_unavailable(self):
        cache = MemoryRateCache()
        working = CurrencyRateService(cache, downloader=lambda: CBR_XML)
        fresh = working.get_rate("INR", force_refresh=True)
        self.assertFalse(fresh.stale)

        broken = CurrencyRateService(
            cache,
            downloader=lambda: (_ for _ in ()).throw(OSError("offline")),
        )
        fallback = broken.get_rate("INR", force_refresh=True)
        self.assertTrue(fallback.stale)
        self.assertEqual(fallback.rub_per_unit, Decimal("0.925"))

    def test_service_fails_clearly_without_provider_or_cache(self):
        service = CurrencyRateService(
            downloader=lambda: (_ for _ in ()).throw(OSError("offline"))
        )
        with self.assertRaises(CurrencyRateUnavailable):
            service.get_rate("INR")

    def test_rub_conversion_keeps_two_decimal_places(self):
        self.assertEqual(convert_to_rub(Decimal("40000"), Decimal("0.925")), Decimal("37000.00"))

    def test_phase_budgets_survive_settings_serialization(self):
        settings = cyclic_settings(
            phase_life_budgets={
                "break": PhaseLifeBudget(
                    critical_life="40000",
                    household_reserve="13000",
                    currency_code="RUB",
                    completed=True,
                ),
                "work": PhaseLifeBudget(
                    critical_life="30000",
                    household_reserve="10000",
                    currency_code="INR",
                    currency_symbol="₹",
                    exchange_rate_to_rub="0.925",
                    exchange_rate_mode="manual",
                    exchange_rate_updated_at="2026-08-24T12:00:00+00:00",
                    completed=True,
                ),
            }
        )
        restored = deserialize_income_rhythm(serialize_income_types(settings))
        work = restored["phase_life_budgets"]["work"]
        self.assertEqual(work.currency_code, "INR")
        self.assertEqual(work.exchange_rate_to_rub, Decimal("0.925"))
        self.assertEqual(work.household_life, Decimal("40000"))
        self.assertTrue(work.completed)

    def test_legacy_cyclic_profile_becomes_completed_break_life(self):
        legacy = serialize_json({
            "version": 6,
            "rates": {},
            "rhythm": "cyclic",
            "profile_type": "cyclic",
            "gap_months": "7",
            "work_months": "5",
            "household_reserve_categories": {},
        })
        restored = deserialize_income_rhythm(legacy)
        settings = cyclic_settings(**restored)
        break_life = settings.phase_life("break")
        self.assertIsNotNone(break_life)
        self.assertTrue(break_life.completed)
        self.assertEqual(break_life.critical_life, Decimal("40000"))
        self.assertIsNone(settings.phase_life("work"))

    def test_database_persists_phase_and_rate_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            settings = cyclic_settings()
            state = AllocatorState(
                current_cycle_phase="work",
                current_phase_months_remaining=Decimal("3"),
            )
            database.save_allocator(991001, FinancialAllocator(settings, state))
            restored = database.load_allocator(991001)
            self.assertEqual(restored.state.current_cycle_phase, "work")
            self.assertEqual(restored.state.current_phase_months_remaining, Decimal("3"))

            service = CurrencyRateService(database, downloader=lambda: CBR_XML)
            quote = service.get_rate("USD", force_refresh=True)
            cached = database.load_exchange_rate("USD")
            self.assertEqual(quote.rub_per_unit, Decimal("80.0000"))
            self.assertEqual(cached["rub_per_unit"], Decimal("80.0000"))
            database.close()

    def test_cycle_transitions_keep_generic_phase_state_in_sync(self):
        allocator = FinancialAllocator(cyclic_settings())
        started = allocator.start_intercontract_break()
        self.assertEqual(allocator.state.current_cycle_phase, "break")
        self.assertEqual(
            allocator.state.current_phase_months_remaining,
            started["months_remaining"],
        )
        allocator.state.intercontract_reserve = Decimal("53000")
        allocator.pay_intercontract_salary()
        self.assertEqual(
            allocator.state.current_phase_months_remaining,
            allocator.state.intercontract_months_remaining,
        )


if __name__ == "__main__":
    unittest.main()
