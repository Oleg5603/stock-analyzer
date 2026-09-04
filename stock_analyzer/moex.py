"""Public, delayed MOEX ISS import. No credentials and no broker actions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.request import Request, urlopen

from .models import Company

MOEX_TQBR_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    "?iss.meta=off&iss.only=securities,marketdata"
    "&securities.columns=SECID,SHORTNAME,SECNAME,STATUS"
    "&marketdata.columns=SECID,LAST,MARKETPRICE,LASTTOPREVPRICE"
)
MOEX_BLUE_CHIPS_URL = "https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/MOEXBC.json?iss.meta=off&iss.only=analytics"


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
        if row.get("STATUS") != "A":
            continue
        ticker = str(row["SECID"]).upper()
        price_row = prices.get(ticker, {})
        price = next((price_row.get(name) for name in ("LAST", "MARKETPRICE") if price_row.get(name) is not None), None)
        companies.append(Company(
            ticker=ticker,
            name=str(row.get("SECNAME") or row.get("SHORTNAME") or ticker),
            sector="Не классифицировано",
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


def fetch_blue_chip_companies(timeout_seconds: int = 20) -> list[Company]:
    index_rows = _rows(_get_json(MOEX_BLUE_CHIPS_URL, timeout_seconds)["analytics"])
    tickers = {str(row["secids"]).upper() for row in index_rows}
    return [company for company in fetch_tqbr_companies(timeout_seconds) if company.ticker in tickers]
