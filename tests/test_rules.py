import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from stock_analyzer.csvio import import_companies_csv
from stock_analyzer.models import Company, PortfolioPosition
from stock_analyzer.moex import BLUE_CHIP_SECTORS, DailyTechnicalSnapshot, IntradayTechnicalSnapshot, companies_from_iss, intraday_from_candles, technical_from_candles, _rows
from stock_analyzer.rules import analyze_company
from stock_analyzer.smartlab import fundamental_from_html
from stock_analyzer.classification import classify_bank, classify_nonbank
from stock_analyzer.smartlab import annual_series_from_html
from unittest.mock import patch

from stock_analyzer.official_reports import OfficialDebtEvidence, _pdf_link_from_page, official_report_source, short_debt_from_text
from stock_analyzer.service import AnalyzerService, _manual_short_debt_ebitda


class RulesTests(unittest.TestCase):
    def valid_company(self, **changes):
        values = dict(ticker="TEST", name="Тест", sector="Нефть", category=3,
                      fundamental_passed=True, d1_confirmed=True, h4_confirmed=True,
                      volume_profile_confirmed=True)
        values.update(changes)
        return Company(**values)

    def test_category_three_has_recommended_range(self):
        result = analyze_company(self.valid_company(), proposed_position_pct=5)
        self.assertEqual(result.decision, "candidate")
        self.assertEqual((result.position_min_pct, result.position_max_pct), (4.0, 6.0))

    def test_missing_volume_profile_requires_manual_review(self):
        result = analyze_company(self.valid_company(volume_profile_confirmed=None), proposed_position_pct=5)
        self.assertEqual(result.decision, "manual_review")

    def test_sector_cap_rejects_candidate(self):
        result = analyze_company(self.valid_company(), [PortfolioPosition("OLD", "Нефть", 17)], 5)
        self.assertEqual(result.decision, "reject")
        self.assertEqual(result.projected_sector_pct, 22)

    def test_bank_uses_bank_metrics(self):
        company = self.valid_company(is_bank=True, bank_metrics_passed=False, fundamental_passed=True)
        self.assertEqual(analyze_company(company, proposed_position_pct=5).decision, "reject")

    def test_csv_rejects_duplicate_ticker(self):
        data = "ticker,name,sector\nAAA,А,Тест\nAAA,Б,Тест\n"
        result = import_companies_csv(data)
        self.assertEqual(len(result.companies), 1)
        self.assertEqual(len(result.errors), 1)

    def test_moex_payload_creates_manual_review_draft(self):
        payload = {"securities": {"columns": ["SECID", "SECNAME", "STATUS", "INSTRID"], "data": [["MOEX", "Мосбиржа", "A", "EQIN"], ["AKBC", "Пай фонда", "A", "IFTF"]]}, "marketdata": {"columns": ["SECID", "LAST", "MARKETPRICE"], "data": [["MOEX", 191.2, 190.0], ["AKBC", 86.3, 86.1]]}}
        company = companies_from_iss(payload)[0]
        self.assertEqual(company.ticker, "MOEX")
        self.assertEqual(company.last_price, 191.2)
        self.assertIsNone(company.category)
        self.assertEqual(len(companies_from_iss(payload)), 1)

    def test_moex_index_rows_keep_security_ids(self):
        rows = _rows({"columns": ["secids", "weight"], "data": [["SBER", 15.12]]})
        self.assertEqual(rows[0]["secids"], "SBER")

    def test_daily_rule_requires_price_above_sma_and_rising_high(self):
        closes = [100.0] * 49 + [110.0]
        highs = [101.0] * 10 + [102.0] * 10 + [103.0] * 20 + [110.0] * 10
        payload = {"candles": {"columns": ["begin", "close", "high", "volume"], "data": [
            [f"2026-01-{index + 1:02d}", closes[index], highs[index], 1000 + index]
            for index in range(50)
        ]}}
        snapshot = technical_from_candles("TEST", payload)
        self.assertTrue(snapshot.price_above_sma50)
        self.assertTrue(snapshot.rising_high)
        self.assertTrue(snapshot.trend_confirmed)

    def test_intraday_proxy_groups_candles_and_marks_h4_trend(self):
        rows = []
        for block in range(6):
            for minute in range(6):
                value = 100 + block * 2 + minute / 10
                rows.append([f"2026-09-0{block + 1}T08:{minute}0:00", value + 1, value - 1, value, 1000])
        payload = {"candles": {"columns": ["begin", "high", "low", "close", "volume"], "data": rows}}
        snapshot = intraday_from_candles("TEST", payload)
        self.assertTrue(snapshot.h4_trend_confirmed)
        self.assertEqual(len(snapshot.volume_zones), 2)

    def test_smartlab_parser_keeps_latest_public_facts(self):
        html = """<table>
        <tr><td>Дата отчета</td><td>2026-03-01</td><td>2026-06-01</td></tr>
        <tr><td>Чистая прибыль, млрд руб</td><td>100</td><td>120</td></tr>
        <tr><td>ROE, %</td><td>20%</td><td>22%</td></tr>
        <tr><td>P/E</td><td>5.0</td><td>4.5</td></tr>
        </table>"""
        snapshot = fundamental_from_html("SBER", html)
        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.report_date, "2026-06-01")
        self.assertEqual(snapshot.metrics["Чистая прибыль"], "120")
        self.assertEqual(snapshot.metrics["ROE"], "22%")

    def test_blue_chip_sector_map_marks_banks(self):
        self.assertEqual(BLUE_CHIP_SECTORS["SBER"], "Банки")
        self.assertEqual(BLUE_CHIP_SECTORS["LKOH"], "Нефть и газ")

    def test_blue_chip_import_replaces_general_registry(self):
        service = AnalyzerService()
        service.import_csv("ticker,name,sector\nOTHER,Другая,Тест\n")
        company = Company(ticker="TEST", name="Тест", sector="Нефть и газ")
        with patch("stock_analyzer.service.fetch_blue_chip_companies", return_value=[company]):
            result = service.import_blue_chips()
        self.assertEqual(result["total"], 1)
        self.assertEqual([item["ticker"] for item in service.companies()], ["TEST"])

    def test_annual_parser_excludes_ltm_column(self):
        html = """<table><tr><td>Компания</td><td>2023</td><td>2024</td><td>LTM</td></tr>
        <tr><td>Выручка</td><td>100</td><td>120</td><td>130</td></tr>
        <tr><td>Долг/EBITDA</td><td>3,5</td><td>2,4</td><td>2,0</td></tr></table>"""
        series = annual_series_from_html(html)
        self.assertEqual(series["revenue"], [100.0, 120.0])
        self.assertEqual(series["debt_ebitda"], [3.5, 2.4])

    def test_nonbank_category_one_requires_full_evidence(self):
        series = {"revenue": [100, 120], "debt_ebitda": [2.8, 2.4], "equity": [50, 60], "operating_profit": [20, 25], "fcf": [5, 6], "short_debt_ebitda": [1, .8]}
        result = classify_nonbank(series, 35)
        self.assertEqual(result.category, 1)
        self.assertTrue(result.fundamental_passed)

    def test_manual_short_debt_ratio_requires_two_verified_years(self):
        self.assertEqual(_manual_short_debt_ebitda("0,8; 0,7"), [0.8, 0.7])
        with self.assertRaises(ValueError):
            _manual_short_debt_ebitda("0,8")

    def test_bank_capital_below_floor_blocks(self):
        series = {"core_capital": [9, 7.9], "provisions": [10, 9], "loan_book": [10, 11], "deposits": [10, 11], "operating_income": [10, 11]}
        self.assertFalse(classify_bank(series).bank_metrics_passed)

    def test_official_report_extractor_keeps_source_and_amount(self):
        evidence = short_debt_from_text("Краткосрочные кредиты и займы 1 250,5 млн руб", "https://issuer.example/report.pdf")
        self.assertEqual(evidence.amount, 1250.5)
        self.assertEqual(evidence.status, "found")

    def test_uses_issuer_disclosure_page_not_aggregator(self):
        source = official_report_source("PLZL")
        self.assertIsNotNone(source)
        self.assertEqual(source["status"], "page_verified")
        self.assertIn("polyus.com", source["page_url"])
        self.assertIn("novatek.ru", official_report_source("NVTK")["page_url"])

    def test_batch_debt_collection_keeps_evidence_outside_categories(self):
        service = AnalyzerService()
        service.import_csv("ticker,name,sector\nPLZL,Полюс,Металлы\nSBER,Сбербанк,Банки\n")
        evidence = OfficialDebtEvidence(1250.5, "краткосрочные займы", "https://issuer.example/report.pdf", "found", "Строка найдена")
        with patch("stock_analyzer.service.short_debt_from_official_source", return_value=evidence):
            result = service.collect_official_short_debt()
        self.assertEqual([item["ticker"] for item in result["items"]], ["PLZL"])
        self.assertEqual(result["found"], 1)
        self.assertIn("не подставляется", result["note"])
        self.assertEqual(service.companies()[0]["short_debt"]["status"], "found")

    def test_pdf_discovery_prefers_requested_year(self):
        html = b'<a href="old.pdf">Report 2024</a><a href="fresh.pdf">Report 2025</a>'
        with patch("stock_analyzer.official_reports._download", return_value=html):
            report_url = _pdf_link_from_page("https://issuer.example/reports/", 2025)
        self.assertEqual(report_url, "https://issuer.example/reports/fresh.pdf")

    def test_blue_chip_scan_queues_complete_d1_for_manual_checks(self):
        company = Company(ticker="TEST", name="Тест", sector="Нефть и газ")
        technical = DailyTechnicalSnapshot("TEST", "2026-09-05", 110, 100, 110, 100, 1000, 1100, True, True, True)
        intraday = IntradayTechnicalSnapshot("TEST", 111, 105, 112, 108, True, [104, 109])
        series = {
            "revenue": [100, 120], "debt_ebitda": [2.8, 2.4], "equity": [50, 60],
            "operating_profit": [20, 25], "fcf": [5, 6],
        }
        with patch("stock_analyzer.service.fetch_blue_chip_companies", return_value=[company]), \
             patch("stock_analyzer.service.fetch_daily_technical", return_value=technical), \
             patch("stock_analyzer.service.fetch_intraday_technical", return_value=intraday), \
             patch("stock_analyzer.service.fetch_annual_series", return_value=(series, "https://issuer.example/report")):
            service = AnalyzerService()
            result = service.scan_blue_chips()
        self.assertEqual(result["items"][0]["status"], "review")
        self.assertTrue(result["items"][0]["base_series_ready"])
        checked = service.companies()[0]
        self.assertEqual(checked["category"], result["items"][0]["category"])
        self.assertEqual(checked["fundamental_passed"], result["items"][0]["fundamental_passed"])
        self.assertTrue(checked["d1_confirmed"])
        self.assertTrue(checked["intraday"]["h4_trend_confirmed"])
        self.assertEqual(checked["annual_coverage"], {"available": 5, "required": 6, "missing": ["short_debt_ebitda"]})
        self.assertEqual(service.latest_blue_chip_scan()["items"][0]["ticker"], "TEST")

    def test_automatic_check_keeps_subjective_steps_manual(self):
        service = AnalyzerService()
        scan = {"items": [
            {"ticker": "GOOD", "status": "review"},
            {"ticker": "BAD", "status": "exclude_now"},
            {"ticker": "WAIT", "status": "watch"},
        ]}
        debt = {"found": 1}
        with patch.object(service, "scan_blue_chips", return_value=scan), \
             patch.object(service, "collect_official_short_debt", return_value=debt):
            result = service.automatic_blue_chip_check()
        self.assertEqual(result["summary"]["checked"], 3)
        self.assertEqual(result["summary"]["d1_and_base_data"], 1)
        self.assertEqual(result["summary"]["d1_blocker"], 1)
        self.assertEqual(result["summary"]["official_debt_found"], 1)
        self.assertEqual(result["summary"]["h4_hints_available"], 0)
        self.assertTrue(result["completed_at"].endswith("+00:00"))
        self.assertEqual(service.latest_automatic_check()["completed_at"], result["completed_at"])
        self.assertIn("H4: зона входа", result["summary"]["manual_steps"])

    def test_automatic_check_keeps_scan_when_official_debt_source_fails(self):
        service = AnalyzerService()
        scan = {"items": [{"ticker": "TEST", "status": "review", "h4_trend_hint": True}]}
        with patch.object(service, "scan_blue_chips", return_value=scan), \
             patch.object(service, "collect_official_short_debt", side_effect=RuntimeError("temporary parser failure")):
            result = service.automatic_blue_chip_check()
        self.assertEqual(result["summary"]["checked"], 1)
        self.assertEqual(result["summary"]["official_debt_found"], 0)
        self.assertIn("временно недоступен", result["debt"]["note"])

    def test_last_auto_check_survives_service_restart(self):
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "last_auto_check.json"
            service = AnalyzerService(state_path=state_path)
            scan = {"items": [{"ticker": "TEST", "status": "review", "h4_trend_hint": True}]}
            with patch.object(service, "scan_blue_chips", return_value=scan), \
                 patch.object(service, "collect_official_short_debt", return_value={"found": 0}):
                result = service.automatic_blue_chip_check()
            restarted = AnalyzerService(state_path=state_path)
            self.assertEqual(restarted.latest_automatic_check()["completed_at"], result["completed_at"])


if __name__ == "__main__":
    unittest.main()
