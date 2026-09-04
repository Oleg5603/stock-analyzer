from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .models import Company


@dataclass
class ImportResult:
    companies: list[Company] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": len(self.companies),
            "rejected": len(self.errors),
            "errors": self.errors,
        }


def import_companies_csv(text: str) -> ImportResult:
    """Parse UTF-8/decoded CSV text without changing application state."""
    result = ImportResult()
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), dialect=dialect)
    required = {"ticker", "name", "sector"}
    if not reader.fieldnames or not required.issubset({x.strip() for x in reader.fieldnames}):
        missing = sorted(required - set(reader.fieldnames or []))
        result.errors.append({"row": 1, "error": f"Отсутствуют колонки: {', '.join(missing)}"})
        return result
    seen: set[str] = set()
    for line_number, row in enumerate(reader, start=2):
        try:
            normalized = {str(k).strip(): v for k, v in row.items() if k is not None}
            company = Company.from_mapping(normalized)
            if company.ticker in seen:
                raise ValueError(f"Дубликат ticker: {company.ticker}")
            seen.add(company.ticker)
            result.companies.append(company)
        except (TypeError, ValueError) as exc:
            result.errors.append({"row": line_number, "error": str(exc)})
    return result
