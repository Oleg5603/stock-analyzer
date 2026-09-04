from __future__ import annotations

from .models import AnalysisResult, Company, PortfolioPosition, RuleOutcome

POSITION_RANGES: dict[int, tuple[float, float] | None] = {
    1: (8.0, 10.0),
    2: (6.0, 8.0),
    3: (4.0, 6.0),
    4: None,
}
SECTOR_LIMIT_PCT = 20.0


def _out(rule_id: str, status: str, message: str, actual=None, expected=None) -> RuleOutcome:
    return RuleOutcome(rule_id, status, message, actual, expected)


def analyze_company(
    company: Company,
    portfolio: list[PortfolioPosition] | None = None,
    proposed_position_pct: float | None = None,
) -> AnalysisResult:
    """Evaluate only explicit v0.1 rules; ambiguous inputs require manual review."""
    portfolio = portfolio or []
    outcomes: list[RuleOutcome] = []
    blockers = False
    reviews = False

    if company.category is None:
        outcomes.append(_out("CAT-01", "manual_review", "Категория 1–4 не назначена"))
        reviews = True
        position_range = None
    else:
        position_range = POSITION_RANGES[company.category]
        outcomes.append(_out("CAT-01", "passed", f"Назначена категория {company.category}", company.category, "1..4"))
        if company.category == 4:
            outcomes.append(_out("CAT-05", "manual_review", "Для категории 4 размер позиции автоматически не назначается"))
            reviews = True

    fundamental = company.bank_metrics_passed if company.is_bank else company.fundamental_passed
    fundamental_rule = "BANK-01" if company.is_bank else "FUND-01"
    if fundamental is None:
        label = "банковские показатели" if company.is_bank else "фундаментальные показатели"
        outcomes.append(_out(fundamental_rule, "manual_review", f"Не подтверждены {label}"))
        reviews = True
    elif fundamental:
        outcomes.append(_out(fundamental_rule, "passed", "Фундаментальный фильтр пройден"))
    else:
        outcomes.append(_out(fundamental_rule, "failed", "Фундаментальный фильтр не пройден"))
        blockers = True

    technical_values = (company.d1_confirmed, company.h4_confirmed, company.volume_profile_confirmed)
    if any(value is None for value in technical_values):
        outcomes.append(_out("TECH-01", "manual_review", "D1, H4 или Volume Profile требуют ручной проверки"))
        reviews = True
    elif all(technical_values):
        outcomes.append(_out("TECH-01", "passed", "D1 и H4 подтверждены, Volume Profile проверен"))
    else:
        outcomes.append(_out("TECH-01", "failed", "Техническое подтверждение отсутствует"))
        blockers = True

    min_pct = position_range[0] if position_range else None
    max_pct = position_range[1] if position_range else None
    if proposed_position_pct is not None:
        if proposed_position_pct < 0:
            raise ValueError("proposed_position_pct не может быть отрицательным")
        if max_pct is None:
            outcomes.append(_out("POS-01", "manual_review", "Нет подтверждённого диапазона позиции", proposed_position_pct))
            reviews = True
        elif proposed_position_pct > max_pct:
            outcomes.append(_out("POS-01", "failed", "Вес превышает максимум категории", proposed_position_pct, max_pct))
            blockers = True
        elif proposed_position_pct < min_pct:
            outcomes.append(_out("POS-01", "warning", "Вес ниже целевого диапазона", proposed_position_pct, min_pct))
        else:
            outcomes.append(_out("POS-01", "passed", "Вес находится в диапазоне категории", proposed_position_pct, [min_pct, max_pct]))

    current_sector = sum(p.weight_pct for p in portfolio if p.sector.casefold() == company.sector.casefold())
    existing_same_ticker = sum(p.weight_pct for p in portfolio if p.ticker == company.ticker)
    increment = max(0.0, (proposed_position_pct or existing_same_ticker) - existing_same_ticker)
    projected_sector = current_sector + increment
    if projected_sector > SECTOR_LIMIT_PCT:
        outcomes.append(_out("SEC-01", "failed", "Лимит сектора превышен", round(projected_sector, 4), SECTOR_LIMIT_PCT))
        blockers = True
    else:
        outcomes.append(_out("SEC-01", "passed", "Лимит сектора соблюдён", round(projected_sector, 4), SECTOR_LIMIT_PCT))

    if blockers:
        decision = "reject"
    elif reviews:
        decision = "manual_review"
    else:
        decision = "candidate"
    confidence = "high" if decision == "candidate" else ("low" if reviews else "medium")
    return AnalysisResult(
        ticker=company.ticker,
        category=company.category,
        decision=decision,
        position_min_pct=min_pct,
        position_max_pct=max_pct,
        proposed_position_pct=proposed_position_pct,
        projected_sector_pct=round(projected_sector, 4),
        confidence=confidence,
        outcomes=outcomes,
    )
