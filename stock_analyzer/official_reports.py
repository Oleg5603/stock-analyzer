"""Evidence extraction for short-term debt from an issuer's official report text."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEBT_PATTERNS = (
    r"краткосрочн\w* кредиты и займы",
    r"краткосрочн\w* займы",
    r"текущ\w* часть долгосрочн\w* кредит\w* и займ\w*",
    r"current loans and borrowings",
)
NUMBER = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[,.]\d+)?"
SOURCES_PATH = Path(__file__).resolve().parent.parent / "data" / "official_report_sources.json"
MAX_REPORT_BYTES = 25_000_000


class _PdfLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None


def _download(url: str, timeout_seconds: int = 20) -> bytes:
    request = Request(url, headers={"User-Agent": "StockAnalyzer/0.1 (local research)"})
    try:
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        context = None
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        data = response.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise ValueError("Официальный PDF больше лимита 25 МБ")
    return data


def _pdf_link_from_page(page_url: str, year: int | None = None, timeout_seconds: int = 20) -> str | None:
    parser = _PdfLinks()
    parser.feed(_download(page_url, timeout_seconds).decode("utf-8", errors="ignore"))
    candidates = [(urljoin(page_url, href), label) for href, label in parser.links if ".pdf" in href.lower()]
    if year:
        matches = [item for item in candidates if str(year) in item[0] or str(year) in item[1]]
        if matches:
            return matches[0][0]
    return candidates[0][0] if candidates else None


def _pdf_text(pdf: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Для чтения PDF установите пакет pypdf") from exc
    return " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)


def _report_text(document: bytes) -> str:
    """Read an issuer PDF or its official HTML annual-report page."""
    if document.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return re.sub(r"<[^>]+>", " ", document.decode("utf-8", errors="ignore"))
    return _pdf_text(document)


@dataclass(frozen=True)
class OfficialDebtEvidence:
    amount: float | None
    matched_label: str | None
    source_url: str
    status: str
    note: str
    ratios: list[float] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: str) -> float:
    normalized = value.replace("\u00a0", " ").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", normalized):
        return float(normalized.replace(",", ""))
    return float(normalized.replace(",", "."))


def _nornickel_current_debt_ebitda(text: str) -> list[float] | None:
    """Return 2024, 2025 current-debt/EBITDA from one official table.

    The report presents debt as 2025 then 2024, while EBITDA is 2024 then
    2025. It is deliberately a source-specific parser, not a guess for other
    issuers or differently structured reports.
    """
    normalized = " ".join(text.split())
    debt = re.search(r"Current loans and borrowings\s+(%s)\s+(%s)" % (NUMBER, NUMBER), normalized, re.IGNORECASE)
    ebitda = re.search(r"EBITDA,?\s+USD million.*?EBITDA\s+(%s)\s+(%s)" % (NUMBER, NUMBER), normalized, re.IGNORECASE)
    if not debt or not ebitda:
        return None
    debt_2025, debt_2024 = (_number(value) for value in debt.groups())
    ebitda_2024, ebitda_2025 = (_number(value) for value in ebitda.groups())
    if not ebitda_2024 or not ebitda_2025:
        return None
    return [round(debt_2024 / ebitda_2024, 4), round(debt_2025 / ebitda_2025, 4)]


def official_report_source(ticker: str) -> dict[str, str] | None:
    """Return a verified issuer disclosure page, never a third-party mirror."""
    try:
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source = sources.get(ticker.upper())
    return source if isinstance(source, dict) else None


def short_debt_from_official_source(ticker: str, year: int = 2025, timeout_seconds: int = 20) -> OfficialDebtEvidence:
    """Load only an issuer-hosted PDF and return auditable debt evidence.

    A missing PDF, scanned report or unknown amount stays `needs_review`; the
    scanner never invents a debt number or applies it to a category silently.
    """
    source = official_report_source(ticker)
    if not source:
        return OfficialDebtEvidence(None, None, "", "needs_review", "Для тикера пока нет подтверждённой страницы отчётности эмитента.")
    page_url = source["page_url"]
    report_url = source.get("report_url") if source.get("report_year") == str(year) else None
    if not report_url:
        try:
            report_url = _pdf_link_from_page(page_url, year, timeout_seconds)
        except Exception:  # Network/WAF errors become a review state.
            return OfficialDebtEvidence(None, None, page_url, "needs_review", "Защищённое соединение с сайтом эмитента не прошло проверку или доступ временно ограничен. Откройте страницу вручную и повторите позже.")
    if not report_url:
        return OfficialDebtEvidence(None, None, page_url, "needs_review", f"На странице эмитента не найдена PDF-ссылка за {year} год.")
    try:
        report_text = _report_text(_download(report_url, timeout_seconds))
        evidence = short_debt_from_text(report_text, report_url)
    except Exception:  # PDF may be a scan or protected by the issuer.
        return OfficialDebtEvidence(None, None, report_url, "needs_review", "PDF недоступен для автоматического чтения или является сканом. Значение нужно проверить в документе вручную.")
    if source.get("ratio_parser") == "nornickel_current_debt_ebitda":
        ratios = _nornickel_current_debt_ebitda(report_text)
        if ratios:
            return replace(evidence, ratios=ratios, note="В официальной таблице совпали единицы и периоды; рассчитан ряд краткосрочный долг/EBITDA за 2024–2025.")
    return evidence


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
