from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional, Protocol
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import certifi


CBR_DAILY_RATES_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

CURRENCY_SYMBOLS = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "INR": "₹",
    "AED": "د.إ",
    "CNY": "¥",
    "TRY": "₺",
    "KZT": "₸",
    "GEL": "₾",
    "AMD": "֏",
    "RSD": "дин.",
}


class RateCache(Protocol):
    def load_exchange_rate(self, currency_code: str) -> Optional[dict]: ...

    def save_exchange_rate(
        self,
        currency_code: str,
        rub_per_unit: Decimal,
        rate_date: str,
        fetched_at: str,
        source: str,
    ) -> None: ...


class CurrencyRateUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RateQuote:
    currency_code: str
    rub_per_unit: Decimal
    rate_date: date
    fetched_at: datetime
    source: str = "CBR"
    stale: bool = False


def currency_symbol(currency_code: str) -> str:
    code = str(currency_code or "RUB").strip().upper()
    return CURRENCY_SYMBOLS.get(code, code)


def parse_cbr_daily_rates(payload: bytes) -> tuple[date, dict[str, Decimal]]:
    root = ElementTree.fromstring(payload)
    raw_date = root.attrib.get("Date", "")
    try:
        rate_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
    except ValueError as error:
        raise CurrencyRateUnavailable("Банк России вернул курс без корректной даты.") from error

    rates: dict[str, Decimal] = {"RUB": Decimal("1")}
    for node in root.findall("Valute"):
        code = (node.findtext("CharCode") or "").strip().upper()
        nominal_text = (node.findtext("Nominal") or "").strip()
        value_text = (node.findtext("Value") or "").strip().replace(",", ".")
        if not code:
            continue
        try:
            nominal = Decimal(nominal_text)
            value = Decimal(value_text)
        except InvalidOperation:
            continue
        if nominal > 0 and value > 0:
            rates[code] = value / nominal
    return rate_date, rates


def _download_cbr_rates(timeout: float = 5.0) -> bytes:
    request = Request(
        CBR_DAILY_RATES_URL,
        headers={"User-Agent": "RichAlchemistBot/1.0"},
    )
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout, context=ssl_context) as response:
        return response.read()


class CurrencyRateService:
    """Автоматический ориентир ЦБ с локальным кэшем и безопасным fallback."""

    def __init__(
        self,
        cache: Optional[RateCache] = None,
        downloader: Callable[[], bytes] = _download_cbr_rates,
    ):
        self.cache = cache
        self.downloader = downloader

    def _cached_quote(self, code: str, *, stale: bool) -> Optional[RateQuote]:
        if self.cache is None:
            return None
        raw = self.cache.load_exchange_rate(code)
        if not raw:
            return None
        return RateQuote(
            currency_code=code,
            rub_per_unit=Decimal(str(raw["rub_per_unit"])),
            rate_date=date.fromisoformat(str(raw["rate_date"])),
            fetched_at=datetime.fromisoformat(str(raw["fetched_at"])),
            source=str(raw.get("source") or "CBR"),
            stale=stale,
        )

    def get_rate(self, currency_code: str, *, force_refresh: bool = False) -> RateQuote:
        code = str(currency_code or "RUB").strip().upper()
        now = datetime.now(timezone.utc)
        if code == "RUB":
            return RateQuote("RUB", Decimal("1"), now.date(), now, "RUB", False)

        cached = self._cached_quote(code, stale=False)
        if cached and cached.rate_date >= now.date() and not force_refresh:
            return cached

        try:
            rate_date, rates = parse_cbr_daily_rates(self.downloader())
            if code not in rates:
                raise CurrencyRateUnavailable(
                    f"Банк России не публикует ежедневный курс {code}."
                )
            fetched_at = datetime.now(timezone.utc)
            quote = RateQuote(code, rates[code], rate_date, fetched_at)
            if self.cache is not None:
                self.cache.save_exchange_rate(
                    code,
                    quote.rub_per_unit,
                    quote.rate_date.isoformat(),
                    quote.fetched_at.isoformat(),
                    quote.source,
                )
            return quote
        except Exception as error:
            fallback = self._cached_quote(code, stale=True)
            if fallback is not None:
                return fallback
            if isinstance(error, CurrencyRateUnavailable):
                raise
            raise CurrencyRateUnavailable(
                f"Не удалось получить автоматический курс {code}."
            ) from error

    async def get_rate_async(
        self,
        currency_code: str,
        *,
        force_refresh: bool = False,
    ) -> RateQuote:
        return await asyncio.to_thread(
            self.get_rate,
            currency_code,
            force_refresh=force_refresh,
        )


def convert_to_rub(amount: Decimal, rub_per_unit: Decimal) -> Decimal:
    return (Decimal(str(amount)) * Decimal(str(rub_per_unit))).quantize(Decimal("0.01"))
