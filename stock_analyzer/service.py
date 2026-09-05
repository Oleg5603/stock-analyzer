from __future__ import annotations

from dataclasses import asdict, replace
from threading import RLock
from typing import Any

from .csvio import import_companies_csv
from .classification import classify_bank, classify_nonbank
from .models import Company, PortfolioPosition
from .moex import fetch_blue_chip_companies, fetch_daily_technical, fetch_tqbr_companies
from .official_reports import official_report_source, short_debt_from_official_source
from .rules import analyze_company
from .smartlab import fetch_annual_series, fetch_public_fundamentals


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

    def official_short_debt(self, ticker: str, year: int = 2025) -> dict[str, Any]:
        evidence = short_debt_from_official_source(ticker, year)
        return evidence.to_dict() | {"ticker": ticker.upper(), "year": year}

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
        technical = fetch_daily_technical(company.ticker)
        fundamentals = fetch_public_fundamentals(company.ticker)
        annual_series, annual_source_url = fetch_annual_series(company.ticker)
        target = payload.get("target_price")
        potential = round((float(target) / technical.close - 1) * 100, 2) if target else None
        classification = classify_bank(annual_series) if company.is_bank else classify_nonbank(annual_series, potential)
        company = replace(company, category=classification.category, fundamental_passed=classification.fundamental_passed, bank_metrics_passed=classification.bank_metrics_passed, d1_confirmed=technical.trend_confirmed, h4_confirmed=payload.get("h4_confirmed", company.h4_confirmed), volume_profile_confirmed=payload.get("volume_profile_confirmed", company.volume_profile_confirmed))
        portfolio = [PortfolioPosition.from_mapping(row) for row in payload.get("portfolio", [])]
        proposed = payload.get("proposed_position_pct")
        result = analyze_company(company, portfolio, float(proposed) if proposed is not None else None)
        response = result.to_dict()
        response["technical"] = technical.to_dict()
        response["fundamentals"] = fundamentals.to_dict()
        response["classification"] = classification.to_dict() | {
            "annual_source_url": annual_source_url,
            "annual_series": annual_series,
            "official_report_source": official_report_source(company.ticker),
        }
        if not technical.trend_confirmed:
            recommendation = ("exclude_now", "Не рассматривать сейчас", "Дневной тренд не подтверждён.")
        elif any(value is None for value in (company.category, company.h4_confirmed, company.volume_profile_confirmed, target)):
            recommendation = ("watch", "Наблюдать", "Нужно подтвердить категорию, H4, Volume Profile и целевую цену.")
        elif result.decision == "candidate" and (potential is None or potential >= 10):
            recommendation = ("consider", "Можно рассматривать", "Все заданные фильтры пройдены; проверьте план входа.")
        else:
            recommendation = ("watch", "Наблюдать", "В цепочке правил остались блокеры или уточнения.")
        response["recommendation"] = {"status": recommendation[0], "title": recommendation[1], "message": recommendation[2], "potential_pct": potential}
        return response
