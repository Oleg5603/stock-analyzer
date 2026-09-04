from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "да", "passed", "confirmed"}:
        return True
    if text in {"0", "false", "no", "нет", "failed", "not_confirmed"}:
        return False
    raise ValueError(f"Ожидалось логическое значение, получено: {value!r}")


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value).replace(",", "."))


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    sector: str
    category: int | None = None
    is_bank: bool = False
    fundamental_passed: bool | None = None
    bank_metrics_passed: bool | None = None
    d1_confirmed: bool | None = None
    h4_confirmed: bool | None = None
    volume_profile_confirmed: bool | None = None
    evidence: str = ""
    last_price: float | None = None
    price_updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise ValueError("ticker обязателен")
        if not self.name.strip():
            raise ValueError("name обязателен")
        if not self.sector.strip():
            raise ValueError("sector обязателен")
        if self.category is not None and self.category not in {1, 2, 3, 4}:
            raise ValueError("category должна быть от 1 до 4")

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "Company":
        category = row.get("category")
        return cls(
            ticker=str(row.get("ticker", "")).strip().upper(),
            name=str(row.get("name", "")).strip(),
            sector=str(row.get("sector", "")).strip(),
            category=int(category) if category not in {None, ""} else None,
            is_bank=bool(_optional_bool(row.get("is_bank")) or False),
            fundamental_passed=_optional_bool(row.get("fundamental_passed")),
            bank_metrics_passed=_optional_bool(row.get("bank_metrics_passed")),
            d1_confirmed=_optional_bool(row.get("d1_confirmed")),
            h4_confirmed=_optional_bool(row.get("h4_confirmed")),
            volume_profile_confirmed=_optional_bool(row.get("volume_profile_confirmed")),
            evidence=str(row.get("evidence", "")).strip(),
            last_price=_optional_float(row.get("last_price")),
            price_updated_at=str(row.get("price_updated_at", "")).strip(),
        )


@dataclass(frozen=True)
class PortfolioPosition:
    ticker: str
    sector: str
    weight_pct: float

    def __post_init__(self) -> None:
        if self.weight_pct < 0:
            raise ValueError("weight_pct не может быть отрицательным")

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "PortfolioPosition":
        value = _optional_float(row.get("weight_pct"))
        if value is None:
            raise ValueError("weight_pct обязателен")
        return cls(str(row.get("ticker", "")).strip().upper(), str(row.get("sector", "")).strip(), value)


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    status: str
    message: str
    actual: Any = None
    expected: Any = None


@dataclass
class AnalysisResult:
    ticker: str
    category: int | None
    decision: str
    position_min_pct: float | None
    position_max_pct: float | None
    proposed_position_pct: float | None
    projected_sector_pct: float | None
    confidence: str
    outcomes: list[RuleOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
