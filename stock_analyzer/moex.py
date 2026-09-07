"""Public, delayed MOEX ISS import. No credentials and no broker actions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean
from urllib.request import Request, urlopen

from .models import Company

MOEX_TQBR_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    "?iss.meta=off&iss.only=securities,marketdata"
    "&securities.columns=SECID,SHORTNAME,SECNAME,STATUS,INSTRID"
    "&marketdata.columns=SECID,LAST,MARKETPRICE,LASTTOPREVPRICE"
)
MOEX_BLUE_CHIPS_URL = "https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/MOEXBC.json?iss.meta=off&iss.only=analytics"

# Консервативная классификация состава MOEXBC; не является категорией стратегии.
BLUE_CHIP_SECTORS = {
    "GAZP": "Нефть и газ", "ROSN": "Нефть и газ", "NVTK": "Нефть и газ", "TATN": "Нефть и газ", "SNGS": "Нефть и газ",
    "LKOH": "Нефть и газ", "GMKN": "Металлы и добыча", "PLZL": "Металлы и добыча",
    "SBER": "Банки", "VTBR": "Банки", "T": "Финансовые технологии",
    "MOEX": "Финансовая инфраструктура", "OZON": "Потребительский интернет", "X5": "Потребительский сектор", "YDEX": "Технологии",
}
BANK_TICKERS = {"SBER", "VTBR"}


@dataclass(frozen=True)
class DailyTechnicalSnapshot:
    """Facts derived from public daily MOEX candles, not a trade recommendation."""

    ticker: str
    candle_date: str
    close: float
    sma50: float
    recent_high_20: float
    previous_high_20: float
    average_volume_20: float
    latest_volume: float
    price_above_sma50: bool
    rising_high: bool
    trend_confirmed: bool
    source: str = "MOEX ISS / дневные свечи TQBR"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _rows(block: dict[str, object]) -> list[dict[str, object]]:
    columns = block.get("columns", [])
    data = block.get("data", [])
    return [dict(zip(columns, row, strict=False)) for row in data]


def companies_from_iss(payload: dict[str, object]) -> list[Company]:
    securities = _rows(payload["securities"])
    prices = {str(row["SECID"]): row for row in _rows(payload["marketdata"])}
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    companies: list[Company] = []
    for row in securities:
        # TQBR also contains ETF/BPIF shares. The analyzer is intentionally
        # limited to ordinary/preferred shares, which MOEX labels EQIN.
        if row.get("STATUS") != "A" or row.get("INSTRID") != "EQIN":
            continue
        ticker = str(row["SECID"]).upper()
        price_row = prices.get(ticker, {})
        price = next((price_row.get(name) for name in ("LAST", "MARKETPRICE") if price_row.get(name) is not None), None)
        companies.append(Company(
            ticker=ticker,
            name=str(row.get("SECNAME") or row.get("SHORTNAME") or ticker),
            sector=BLUE_CHIP_SECTORS.get(ticker, "Не классифицировано"),
            is_bank=ticker in BANK_TICKERS,
            evidence="MOEX ISS: список и цена загружены автоматически; методика не подтверждена",
            last_price=float(price) if price is not None else None,
            price_updated_at=updated_at,
        ))
    return sorted(companies, key=lambda item: item.ticker)


def fetch_tqbr_companies(timeout_seconds: int = 20) -> list[Company]:
    request = Request(MOEX_TQBR_URL, headers={"Accept": "application/json", "User-Agent": "StockAnalyzer/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: fixed HTTPS public MOEX endpoint
        payload = json.loads(response.read().decode("utf-8"))
    return companies_from_iss(payload)


def _get_json(url: str, timeout_seconds: int) -> dict[str, object]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "StockAnalyzer/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: fixed HTTPS public MOEX endpoint
        return json.loads(response.read().decode("utf-8"))


def technical_from_candles(ticker: str, payload: dict[str, object]) -> DailyTechnicalSnapshot:
    """Apply the user-approved D1 rule to at least 50 completed daily candles."""
    candles = _rows(payload["candles"])
    if len(candles) < 50:
        raise ValueError(f"MOEX ISS вернул только {len(candles)} дневных свечей; нужно не менее 50")
    closes = [float(row["close"]) for row in candles]
    highs = [float(row["high"]) for row in candles]
    volumes = [float(row["volume"]) for row in candles]
    close = closes[-1]
    sma50 = fmean(closes[-50:])
    # "Растущий максимум": 20 последних торговых дней против предшествующих 20.
    recent_high = max(highs[-20:])
    previous_high = max(highs[-40:-20])
    price_above_sma50 = close > sma50
    rising_high = recent_high > previous_high
    return DailyTechnicalSnapshot(
        ticker=ticker.upper(),
        candle_date=str(candles[-1]["begin"]),
        close=round(close, 6),
        sma50=round(sma50, 6),
        recent_high_20=round(recent_high, 6),
        previous_high_20=round(previous_high, 6),
        average_volume_20=round(fmean(volumes[-20:]), 2),
        latest_volume=round(volumes[-1], 2),
        price_above_sma50=price_above_sma50,
        rising_high=rising_high,
        trend_confirmed=price_above_sma50 and rising_high,
    )


def fetch_daily_technical(ticker: str, timeout_seconds: int = 20) -> DailyTechnicalSnapshot:
    normalized = ticker.strip().upper()
    if not normalized.isalnum():
        raise ValueError("Тикер может содержать только буквы и цифры")
    date_from = (datetime.now(UTC).date() - timedelta(days=300)).isoformat()
    url = (
        "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/"
        f"securities/{normalized}/candles.json?iss.meta=off&iss.only=candles"
        "&candles.columns=begin,close,high,volume&interval=24"
        f"&from={date_from}"
    )
    return technical_from_candles(normalized, _get_json(url, timeout_seconds))


def fetch_blue_chip_companies(timeout_seconds: int = 20) -> list[Company]:
    index_rows = _rows(_get_json(MOEX_BLUE_CHIPS_URL, timeout_seconds)["analytics"])
    tickers = {str(row["secids"]).upper() for row in index_rows}
    return [company for company in fetch_tqbr_companies(timeout_seconds) if company.ticker in tickers]
