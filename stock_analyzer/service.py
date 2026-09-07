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
        self._short_debt: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def companies(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) | {"short_debt": self._short_debt.get(item.ticker)} for item in sorted(self._companies.values(), key=lambda x: x.ticker)]

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

    def collect_official_short_debt(self, year: int = 2025) -> dict[str, Any]:
        """Collect auditable debt evidence only for tickers with issuer sources.

        The extracted amount is deliberately not used as a debt/EBITDA ratio:
        PDF units and the matching EBITDA period must be checked first.
        """
        with self._lock:
            tickers = sorted(self._companies)
        items: list[dict[str, Any]] = []
        for ticker in tickers:
            source = official_report_source(ticker)
            if not source:
                continue
            evidence = short_debt_from_official_source(ticker, year, timeout_seconds=8)
            item = evidence.to_dict() | {
                "ticker": ticker,
                "issuer": source.get("issuer", ticker),
                "page_url": source["page_url"],
            }
            items.append(item)
            with self._lock:
                self._short_debt[ticker] = item
        found = sum(item["status"] == "found" for item in items)
        return {
            "items": items,
            "year": year,
            "found": found,
            "source": "Официальные страницы раскрытия эмитентов",
            "note": "Сумма не подставляется в категорию до проверки единиц измерения и EBITDA за тот же период.",
        }

    def scan_blue_chips(self) -> dict[str, Any]:
        """Run the automatable part of the checklist for the MOEX blue chips.

        H4, Volume Profile and target price intentionally stay outside this
        batch run, so it produces a review queue rather than buy signals.
        """
        imported = fetch_blue_chip_companies()
        with self._lock:
            self._companies.update({company.ticker: company for company in imported})
        items: list[dict[str, Any]] = []
        for company in imported:
            try:
                technical = fetch_daily_technical(company.ticker)
                annual_series, source_url = fetch_annual_series(company.ticker)
                classification = classify_bank(annual_series) if company.is_bank else classify_nonbank(annual_series, None)
                fundamental = classification.bank_metrics_passed if company.is_bank else classification.fundamental_passed
                checked_company = replace(
                    company,
                    category=classification.category,
                    fundamental_passed=classification.fundamental_passed,
                    bank_metrics_passed=classification.bank_metrics_passed,
                    d1_confirmed=technical.trend_confirmed,
                )
                with self._lock:
                    self._companies[company.ticker] = checked_company
                base_series_ready = all(len(annual_series.get(key, [])) >= 2 for key in ("revenue", "debt_ebitda", "equity", "operating_profit", "fcf"))
                if not technical.trend_confirmed:
                    status, title = "exclude_now", "Не рассматривать сейчас"
                elif fundamental is True or (not company.is_bank and base_series_ready):
                    status, title = "review", "Проверить долг, H4 и объёмные зоны"
                else:
                    status, title = "watch", "Наблюдать: данных недостаточно"
                items.append({
                    "ticker": company.ticker, "name": company.name, "sector": company.sector,
                    "last_price": company.last_price, "status": status, "title": title,
                    "d1_confirmed": technical.trend_confirmed, "fundamental_passed": fundamental,
                    "base_series_ready": base_series_ready,
                    "category": classification.category, "reasons": classification.reasons,
                    "annual_source_url": source_url,
                })
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                items.append({"ticker": company.ticker, "name": company.name, "sector": company.sector, "status": "unavailable", "title": "Данные временно недоступны", "reasons": [str(exc)]})
        ranks = {"review": 0, "watch": 1, "exclude_now": 2, "unavailable": 3}
        items.sort(key=lambda item: (ranks[item["status"]], item["ticker"]))
        return {"items": items, "source": "MOEX ISS + публичные годовые МСФО Smart-Lab", "note": "Это предварительная очередь: H4, Volume Profile, цель и лимит портфеля не проверялись."}

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
