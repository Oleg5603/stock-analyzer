"""Read-only extraction of selected facts from public Smart-Lab report tables."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.request import Request, urlopen


SMARTLAB_METRICS = (
    "Выручка",
    "Чистая прибыль",
    "Долг/EBITDA",
    "ROE",
    "P/E",
    "Капитал",
    "Стоимость риска (CoR)",
    "Просроченные кредиты, NPL",
)


@dataclass(frozen=True)
class FundamentalSnapshot:
    ticker: str
    report_date: str | None
    metrics: dict[str, str]
    available: bool
    source_url: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _last_value(row: list[str]) -> str | None:
    for value in reversed(row[1:]):
        normalized = value.strip()
        if normalized and normalized not in {"—", "-", "?"}:
            return normalized
    return None


def _matching_row(rows: list[list[str]], label: str) -> list[str] | None:
    label_folded = label.casefold()
    return next((row for row in rows if row and label_folded in row[0].casefold()), None)


def fundamental_from_html(ticker: str, html: str) -> FundamentalSnapshot:
    parser = _TableParser()
    parser.feed(html)
    metrics: dict[str, str] = {}
    for label in SMARTLAB_METRICS:
        row = _matching_row(parser.rows, label)
        value = _last_value(row) if row else None
        if value:
            metrics[label] = value
    report_row = _matching_row(parser.rows, "Дата отчета")
    report_date = _last_value(report_row) if report_row else None
    source_url = f"https://smart-lab.ru/q/{ticker.upper()}/f/q/MSFO/"
    available = bool(metrics)
    return FundamentalSnapshot(
        ticker=ticker.upper(),
        report_date=report_date,
        metrics=metrics,
        available=available,
        source_url=source_url,
        note=("Публичные показатели загружены; пороги фундаментального фильтра не заданы."
              if available else "На публичной странице не найдены распознаваемые показатели МСФО."),
    )


def fetch_public_fundamentals(ticker: str, timeout_seconds: int = 20) -> FundamentalSnapshot:
    normalized = ticker.strip().upper()
    if not normalized.isalnum():
        raise ValueError("Тикер может содержать только буквы и цифры")
    source_url = f"https://smart-lab.ru/q/{normalized}/f/q/MSFO/"
    request = Request(source_url, headers={"Accept": "text/html", "User-Agent": "StockAnalyzer/0.2"})
    with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: fixed public HTTPS source
        html = response.read().decode("utf-8", errors="replace")
    return fundamental_from_html(normalized, html)
