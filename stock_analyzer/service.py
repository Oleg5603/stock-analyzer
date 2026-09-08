from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.error import URLError

from .csvio import import_companies_csv
from .classification import classify_bank, classify_nonbank
from .models import Company, PortfolioPosition
from .moex import fetch_blue_chip_companies, fetch_daily_technical, fetch_intraday_technical, fetch_tqbr_companies
from .official_reports import official_report_source, short_debt_from_official_source
from .rules import analyze_company
from .smartlab import fetch_annual_series, fetch_public_fundamentals


LAST_AUTO_CHECK_PATH = Path(__file__).resolve().parent.parent / "data" / "runtime" / "last_auto_check.json"


def _manual_short_debt_ebitda(value: Any) -> list[float] | None:
    """Accept two manually verified annual ratios, separated by semicolons."""
    if value is None or value == "":
        return None
    parts = value if isinstance(value, list) else str(value).split(";")
    if len(parts) != 2:
        raise ValueError("Укажите два значения краткосрочного долга/EBITDA через точку с запятой, например 0,8; 0,7")
    try:
        values = [float(str(item).strip().replace(",", ".")) for item in parts]
    except ValueError as exc:
        raise ValueError("Краткосрочный долг/EBITDA должен состоять из чисел") from exc
    if any(item < 0 for item in values):
        raise ValueError("Краткосрочный долг/EBITDA не может быть отрицательным")
    return values


class AnalyzerService:
    """Thread-safe in-memory orchestrator; it never sends broker transactions."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._companies: dict[str, Company] = {}
        self._short_debt: dict[str, dict[str, Any]] = {}
        self._annual_coverage: dict[str, dict[str, Any]] = {}
        self._intraday: dict[str, dict[str, Any]] = {}
        self._blue_chip_scan: dict[str, Any] = {"items": []}
        self._state_path = state_path or LAST_AUTO_CHECK_PATH
        self._last_auto_check = self._load_last_auto_check()
        self._lock = RLock()

    def _load_last_auto_check(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _save_last_auto_check(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._last_auto_check, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    def companies(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) | {
                "short_debt": self._short_debt.get(item.ticker),
                "annual_coverage": self._annual_coverage.get(item.ticker),
                "intraday": self._intraday.get(item.ticker),
            } for item in sorted(self._companies.values(), key=lambda x: x.ticker)]

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
            self._companies = {company.ticker: company for company in imported}
            self._short_debt = {ticker: item for ticker, item in self._short_debt.items() if ticker in self._companies}
        return {"accepted": len(imported), "total": len(self._companies), "source": "MOEX ISS / MOEXBC", "note": "Состав голубых фишек и цены загружены автоматически; методика требует ручной проверки."}

    def latest_blue_chip_scan(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._blue_chip_scan) | {"items": list(self._blue_chip_scan["items"])}

    def latest_automatic_check(self) -> dict[str, Any] | None:
        """Small safe status payload for local monitoring tools such as Gavrik."""
        with self._lock:
            return dict(self._last_auto_check) if self._last_auto_check else None

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
            try:
                evidence = short_debt_from_official_source(ticker, year, timeout_seconds=8)
                item = evidence.to_dict()
            except (URLError, TimeoutError, OSError, ValueError, RuntimeError):
                item = {
                    "amount": None, "matched_label": None, "source_url": source["page_url"],
                    "status": "unavailable", "note": "Официальный источник временно недоступен; повторите позже.",
                }
            item |= {
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

    def automatic_blue_chip_check(self, year: int = 2025) -> dict[str, Any]:
        """Run every check that has a public, machine-readable source.

        This deliberately stops before H4, Volume Profile, target price and
        converting a debt amount to Debt/EBITDA.  Those inputs have no safe
        universal interpretation in the public sources used by the MVP.
        """
        scan = self.scan_blue_chips()
        try:
            debt = self.collect_official_short_debt(year)
        except Exception:  # external issuer pages/PDF parsers must not cancel the scan
            debt = {
                "items": [], "year": year, "found": 0,
                "source": "Официальные страницы раскрытия эмитентов",
                "note": "Блок официального краткосрочного долга временно недоступен; D1 и H4 проверены отдельно.",
            }
        status_counts = {status: sum(item["status"] == status for item in scan["items"])
                         for status in ("review", "watch", "exclude_now", "unavailable")}
        h4_hints_available = sum(item.get("h4_trend_hint") is not None for item in scan["items"])
        candidates = [
            {
                "ticker": item["ticker"],
                "name": item.get("name", item["ticker"]),
                "sector": item.get("sector", "Не указан"),
                "d1_confirmed": item.get("d1_confirmed"),
                "h4_trend_hint": item.get("h4_trend_hint"),
                "pending": item.get("reasons", []),
            }
            for item in scan["items"]
            if item["status"] == "review"
        ]
        response = {
            "scan": scan,
            "debt": debt,
            "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "summary": {
                "checked": len(scan["items"]),
                "d1_and_base_data": status_counts["review"],
                "d1_blocker": status_counts["exclude_now"],
                "needs_data": status_counts["watch"] + status_counts["unavailable"],
                "official_debt_found": debt["found"],
                "h4_hints_available": h4_hints_available,
                "manual_steps": [
                    "H4: зона входа",
                    "Volume Profile: две объёмные зоны",
                    "целевая цена",
                    "сопоставление краткосрочного долга с EBITDA за тот же период",
                ],
            },
            "candidates": candidates,
            "note": "Автоматически проверены только публичные и однозначно вычислимые данные. Результат не является торговой рекомендацией.",
        }
        with self._lock:
            self._last_auto_check = {
                "completed_at": response["completed_at"],
                "summary": dict(response["summary"]),
                "candidates": list(response["candidates"]),
                "note": response["note"],
            }
        self._save_last_auto_check()
        return response

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
                # The short intraday window is an aid for the chart review.
                # Do not discard an otherwise usable D1/fundamental result if
                # MOEX has not exposed enough recent H4 data for this ticker.
                try:
                    intraday = fetch_intraday_technical(company.ticker)
                except (URLError, TimeoutError, OSError, ValueError):
                    intraday = None
                annual_series, source_url = fetch_annual_series(company.ticker)
                official = short_debt_from_official_source(company.ticker, timeout_seconds=8)
                if official.ratios:
                    annual_series = dict(annual_series) | {"short_debt_ebitda": official.ratios}
                    with self._lock:
                        self._short_debt[company.ticker] = official.to_dict() | {"ticker": company.ticker}
                classification = classify_bank(annual_series) if company.is_bank else classify_nonbank(annual_series, None)
                fundamental = classification.bank_metrics_passed if company.is_bank else classification.fundamental_passed
                required_keys = ("core_capital", "provisions", "loan_book", "deposits", "operating_income") if company.is_bank else ("revenue", "debt_ebitda", "equity", "operating_profit", "fcf", "short_debt_ebitda")
                missing_keys = [key for key in required_keys if len(annual_series.get(key, [])) < 2]
                coverage = {"available": len(required_keys) - len(missing_keys), "required": len(required_keys), "missing": missing_keys}
                checked_company = replace(
                    company,
                    category=classification.category,
                    fundamental_passed=classification.fundamental_passed,
                    bank_metrics_passed=classification.bank_metrics_passed,
                    d1_confirmed=technical.trend_confirmed,
                )
                with self._lock:
                    self._companies[company.ticker] = checked_company
                    self._annual_coverage[company.ticker] = coverage
                    if intraday is not None:
                        self._intraday[company.ticker] = intraday.to_dict()
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
                    "h4_trend_hint": intraday.h4_trend_confirmed if intraday else None,
                    "volume_zones": intraday.volume_zones if intraday else [],
                    "base_series_ready": base_series_ready,
                    "category": classification.category, "reasons": classification.reasons,
                    "annual_source_url": source_url,
                })
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                items.append({"ticker": company.ticker, "name": company.name, "sector": company.sector, "status": "unavailable", "title": "Данные временно недоступны", "reasons": [str(exc)]})
        ranks = {"review": 0, "watch": 1, "exclude_now": 2, "unavailable": 3}
        items.sort(key=lambda item: (ranks[item["status"]], item["ticker"]))
        response = {"items": items, "source": "MOEX ISS + публичные годовые МСФО Smart-Lab", "note": "Это предварительная очередь: H4, Volume Profile, цель и лимит портфеля не проверялись."}
        with self._lock:
            self._blue_chip_scan = response
        return response

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
        intraday = fetch_intraday_technical(company.ticker)
        fundamentals = fetch_public_fundamentals(company.ticker)
        annual_series, annual_source_url = fetch_annual_series(company.ticker)
        official = short_debt_from_official_source(company.ticker, timeout_seconds=8)
        if official.ratios:
            annual_series = dict(annual_series) | {"short_debt_ebitda": official.ratios}
        manual_short_debt = _manual_short_debt_ebitda(payload.get("short_debt_ebitda"))
        if manual_short_debt is not None:
            annual_series = dict(annual_series) | {"short_debt_ebitda": manual_short_debt}
        target = payload.get("target_price")
        potential = round((float(target) / technical.close - 1) * 100, 2) if target else None
        target_source = str(payload.get("target_source", "")).strip()
        target_as_of = str(payload.get("target_as_of", "")).strip()
        classification = classify_bank(annual_series) if company.is_bank else classify_nonbank(annual_series, potential)
        company = replace(company, category=classification.category, fundamental_passed=classification.fundamental_passed, bank_metrics_passed=classification.bank_metrics_passed, d1_confirmed=technical.trend_confirmed, h4_confirmed=payload.get("h4_confirmed", company.h4_confirmed), volume_profile_confirmed=payload.get("volume_profile_confirmed", company.volume_profile_confirmed))
        portfolio = [PortfolioPosition.from_mapping(row) for row in payload.get("portfolio", [])]
        proposed = payload.get("proposed_position_pct")
        result = analyze_company(company, portfolio, float(proposed) if proposed is not None else None)
        response = result.to_dict()
        response["technical"] = technical.to_dict()
        response["intraday"] = intraday.to_dict()
        response["fundamentals"] = fundamentals.to_dict()
        response["classification"] = classification.to_dict() | {
            "annual_source_url": annual_source_url,
            "annual_series": annual_series,
            "official_report_source": official_report_source(company.ticker),
            "manual_short_debt_ebitda": manual_short_debt,
            "official_short_debt_ebitda": official.ratios,
            "target_evidence": {
                "price": float(target) if target else None,
                "source": target_source or None,
                "as_of": target_as_of or None,
            },
        }
        if not technical.trend_confirmed:
            recommendation = ("exclude_now", "Не рассматривать сейчас", "Дневной тренд не подтверждён.")
        elif any(value is None for value in (company.category, company.h4_confirmed, company.volume_profile_confirmed, target)) or not target_source or not target_as_of:
            recommendation = ("watch", "Наблюдать", "Нужно подтвердить категорию, H4, Volume Profile, целевую цену и её источник.")
        elif result.decision == "candidate" and (potential is None or potential >= 10):
            recommendation = ("consider", "Можно рассматривать", "Все заданные фильтры пройдены; проверьте план входа.")
        else:
            recommendation = ("watch", "Наблюдать", "В цепочке правил остались блокеры или уточнения.")
        response["recommendation"] = {"status": recommendation[0], "title": recommendation[1], "message": recommendation[2], "potential_pct": potential}
        return response
