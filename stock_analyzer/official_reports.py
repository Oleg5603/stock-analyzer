"""Evidence extraction for short-term debt from an issuer's official report text."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


DEBT_PATTERNS = (
    r"краткосрочн\w* кредиты и займы",
    r"краткосрочн\w* займы",
    r"текущ\w* часть долгосрочн\w* кредит\w* и займ\w*",
)
NUMBER = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[,.]\d+)?"


@dataclass(frozen=True)
class OfficialDebtEvidence:
    amount: float | None
    matched_label: str | None
    source_url: str
    status: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: str) -> float:
    return float(value.replace("\u00a0", " ").replace(" ", "").replace(",", "."))


def short_debt_from_text(text: str, source_url: str) -> OfficialDebtEvidence:
    """Return the first amount after a recognised official-report line label.

    The caller records the original URL, so every automated result remains
    auditable and an ambiguous PDF is never silently converted into a number.
    """
    normalized = " ".join(text.split())
    for pattern in DEBT_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        fragment = normalized[match.end():match.end() + 200]
        value = re.search(NUMBER, fragment)
        if value:
            return OfficialDebtEvidence(_number(value.group()), match.group(), source_url, "found", "Строка найдена в тексте официального отчёта; единицы измерения проверяются по документу.")
        return OfficialDebtEvidence(None, match.group(), source_url, "needs_review", "Строка найдена, но число рядом не распознано.")
    return OfficialDebtEvidence(None, None, source_url, "not_found", "В тексте отчёта не найдена однозначная строка краткосрочного долга.")
