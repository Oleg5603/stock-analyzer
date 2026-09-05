"""Explainable category calculation from public annual financial series."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Classification:
    category: int | None
    fundamental_passed: bool | None
    bank_metrics_passed: bool | None
    status: str
    reasons: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _latest(series: dict[str, list[float]], key: str) -> tuple[float, float] | None:
    values = series.get(key, [])
    return (values[-1], values[-2]) if len(values) >= 2 else None


def _complete(series: dict[str, list[float]], keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if len(series.get(key, [])) < 2]


def classify_nonbank(series: dict[str, list[float]], potential_pct: float | None) -> Classification:
    keys = ("revenue", "debt_ebitda", "equity", "operating_profit", "fcf", "short_debt_ebitda")
    missing = _complete(series, keys)
    if potential_pct is None:
        missing.append("потенциал по целевой цене")
    if missing:
        return Classification(None, None, None, "incomplete", ["Не хватает: " + ", ".join(missing)])
    latest = {key: _latest(series, key) for key in keys}
    assert all(item is not None for item in latest.values())
    values = {key: item for key, item in latest.items() if item is not None}
    revenue_up = values["revenue"][0] > values["revenue"][1]
    debt_down = values["debt_ebitda"][0] <= values["debt_ebitda"][1]
    equity_up = values["equity"][0] > values["equity"][1]
    profit_up = values["operating_profit"][0] > values["operating_profit"][1]
    fcf_positive = values["fcf"][0] >= 0
    ratio = values["debt_ebitda"][0]
    short_ratio = values["short_debt_ebitda"][0]
    reasons = [
        f"Выручка: {values['revenue'][1]:g} → {values['revenue'][0]:g}",
        f"Долг/EBITDA: {ratio:g}",
        f"Потенциал: {potential_pct:g}%",
    ]
    if revenue_up and debt_down and equity_up and profit_up and fcf_positive and ratio <= 2.5 and short_ratio <= 1 and potential_pct >= 30:
        return Classification(1, True, None, "ready", reasons + ["Все условия категории 1 выполнены."])
    if revenue_up and equity_up and profit_up and fcf_positive and ratio <= 3 and short_ratio <= 1 and 10 <= potential_pct < 30:
        return Classification(2, True, None, "ready", reasons + ["Все условия категории 2 выполнены."])
    revenue_3y = series["revenue"][-3:] if len(series["revenue"]) >= 3 else []
    equity_3y = series["equity"][-3:] if len(series["equity"]) >= 3 else []
    profit_3y = series["operating_profit"][-3:] if len(series["operating_profit"]) >= 3 else []
    three_year_ready = all(len(item) == 3 and item[0] != 0 for item in (revenue_3y, equity_3y, profit_3y))
    if three_year_ready:
        revenue_drop = (revenue_3y[-1] / revenue_3y[0] - 1) * 100
        equity_drop = (equity_3y[-1] / equity_3y[0] - 1) * 100
        profit_drop = (profit_3y[-1] / profit_3y[0] - 1) * 100
        if revenue_drop >= -30 and equity_drop >= -10 and profit_drop >= -30 and 3 < ratio <= 4 and potential_pct >= 50:
            return Classification(3, True, None, "ready", reasons + ["Все условия категории 3 выполнены."])
    return Classification(4, False, None, "ready", reasons + ["Условия категорий 1–3 не выполнены."])


def classify_bank(series: dict[str, list[float]]) -> Classification:
    keys = ("core_capital", "provisions", "loan_book", "deposits", "operating_income")
    missing = _complete(series, keys)
    if missing:
        return Classification(None, None, None, "incomplete", ["Не хватает: " + ", ".join(missing)])
    latest = {key: _latest(series, key) for key in keys}
    assert all(item is not None for item in latest.values())
    values = {key: item for key, item in latest.items() if item is not None}
    capital = values["core_capital"][0]
    if capital < 8:
        return Classification(None, None, False, "blocked", [f"Достаточность основного капитала {capital:g}% ниже жёсткого минимума 8%."])
    positive = [
        capital >= values["core_capital"][1],
        values["provisions"][0] <= values["provisions"][1],
        values["loan_book"][0] >= values["loan_book"][1],
        values["deposits"][0] >= values["deposits"][1],
        values["operating_income"][0] >= values["operating_income"][1],
    ]
    score = sum(positive)
    label = "надёжны" if score >= 3 else "ненадёжны"
    return Classification(None, None, score >= 3, "ready", [f"Банковский счёт: {score}/5 — показатели {label}.", "Рост резервов считается отрицательным фактором.", "Категории 1–3 для банков в методике не формализованы."])
