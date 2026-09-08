"""Read-only extraction of selected facts from public Smart-Lab report tables."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from time import sleep
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


def _download_html(source_url: str, timeout_seconds: int) -> str:
    """Retry one transient public-source connection failure without inventing data."""
    request = Request(source_url, headers={"Accept": "text/html", "User-Agent": "StockAnalyzer/0.2"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: fixed public HTTPS source
                return response.read().decode("utf-8", errors="replace")
        except OSError:
            if attempt:
                raise
            sleep(0.4)
    raise RuntimeError("Недостижимый код")

ANNUAL_METRICS: dict[str, tuple[str, ...]] = {
    "revenue": ("Выручка",),
    "debt_ebitda": ("Долг/EBITDA",),
    "equity": ("Капитал",),
    "operating_profit": ("Операционная прибыль", "Опер. прибыль"),
    "fcf": ("Свободный денежный поток", "FCF"),
    "short_debt_ebitda": ("Краткосрочный долг/EBITDA", "Краткосрочные обязательства/EBITDA"),
    "core_capital": ("Дост.осн капитала", "Достаточность основного капитала"),
    "provisions": ("Создание резервов",),
    "loan_book": ("Кредитный портфель",),
    "deposits": ("Депозиты",),
    "operating_income": ("Чистый операц доход", "Чистый операционный доход"),
}


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


def _number(value: str) -> float | None:
    cleaned = value.replace("\u00a0", " ").replace("%", "").replace(" ", "").replace(",", ".")
    cleaned = "".join(character for character in cleaned if character in "0123456789.-")
    try:
        return float(cleaned) if cleaned not in {"", "-", "."} else None
    except ValueError:
        return None


def annual_series_from_html(html: str) -> dict[str, list[float]]:
    """Extract only columns whose headers are explicit calendar years, never LTM."""
    parser = _TableParser()
    parser.feed(html)
    year_columns: list[int] = []
    for row in parser.rows:
        current = [index for index, cell in enumerate(row) if len(cell) == 4 and cell.isdigit() and 2000 <= int(cell) <= 2100]
        if len(current) >= 2:
            year_columns = current
            break
    result: dict[str, list[float]] = {}
    if not year_columns:
        return result
    for key, aliases in ANNUAL_METRICS.items():
        row = next((_matching_row(parser.rows, alias) for alias in aliases if _matching_row(parser.rows, alias)), None)
        if not row:
            continue
        # Smart-Lab has a presentation-only empty column before financial rows,
        # so cell offsets differ between the header and data rows.  Take the
        # first N numeric values: they correspond to N explicit year headers;
        # the following numeric value is LTM and is deliberately excluded.
        numeric = [value for value in (_number(cell) for cell in row[1:]) if value is not None]
        numeric = numeric[:len(year_columns)]
        if numeric:
            result[key] = numeric
    return result


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
    html = _download_html(source_url, timeout_seconds)
    return fundamental_from_html(normalized, html)


def fetch_annual_series(ticker: str, timeout_seconds: int = 20) -> tuple[dict[str, list[float]], str]:
    normalized = ticker.strip().upper()
    if not normalized.isalnum():
        raise ValueError("Тикер может содержать только буквы и цифры")
    source_url = f"https://smart-lab.ru/q/{normalized}/f/y/MSFO/"
    html = _download_html(source_url, timeout_seconds)
    return annual_series_from_html(html), source_url
