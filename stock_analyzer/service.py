from __future__ import annotations

from dataclasses import asdict
from threading import RLock
from typing import Any

from .csvio import import_companies_csv
from .models import Company, PortfolioPosition
from .moex import fetch_blue_chip_companies, fetch_tqbr_companies
from .rules import analyze_company


class AnalyzerService:
    """Thread-safe in-memory orchestrator; it never sends broker transactions."""

    def __init__(self) -> None:
        self._companies: dict[str, Company] = {}
        self._lock = RLock()

    def companies(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) for item in sorted(self._companies.values(), key=lambda x: x.ticker)]

    def import_csv(self, text: str) -> dict[str, Any]:
        imported = import_companies_csv(text)
        with self._lock:
            for company in imported.companies:
                self._companies[company.ticker] = company
        response = imported.to_dict()
        response["total"] = len(self._companies)
        return response

    def import_moex(self) -> dict[str, Any]:
        imported = fetch_tqbr_companies()
        with self._lock:
            for company in imported:
                self._companies[company.ticker] = company
        return {"accepted": len(imported), "total": len(self._companies), "source": "MOEX ISS / TQBR", "note": "Цена и список загружены автоматически; категория, сектор и признаки методики требуют ручной проверки."}

    def import_blue_chips(self) -> dict[str, Any]:
        imported = fetch_blue_chip_companies()
        with self._lock:
            for company in imported:
                self._companies[company.ticker] = company
        return {"accepted": len(imported), "total": len(self._companies), "source": "MOEX ISS / MOEXBC", "note": "Состав голубых фишек и цены загружены автоматически; методика требует ручной проверки."}

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        company_data = payload.get("company")
        if company_data:
            company = Company.from_mapping(company_data)
        else:
            ticker = str(payload.get("ticker", "")).upper()
            with self._lock:
                company = self._companies.get(ticker)
            if company is None:
                raise KeyError(f"Инструмент {ticker!r} не найден")
        portfolio = [PortfolioPosition.from_mapping(row) for row in payload.get("portfolio", [])]
        proposed = payload.get("proposed_position_pct")
        result = analyze_company(company, portfolio, float(proposed) if proposed is not None else None)
        return result.to_dict()
