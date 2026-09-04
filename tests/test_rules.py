import unittest

from stock_analyzer.csvio import import_companies_csv
from stock_analyzer.models import Company, PortfolioPosition
from stock_analyzer.moex import companies_from_iss, _rows
from stock_analyzer.rules import analyze_company


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
        payload = {"securities": {"columns": ["SECID", "SECNAME", "STATUS"], "data": [["MOEX", "Мосбиржа", "A"]]}, "marketdata": {"columns": ["SECID", "LAST", "MARKETPRICE"], "data": [["MOEX", 191.2, 190.0]]}}
        company = companies_from_iss(payload)[0]
        self.assertEqual(company.ticker, "MOEX")
        self.assertEqual(company.last_price, 191.2)
        self.assertIsNone(company.category)

    def test_moex_index_rows_keep_security_ids(self):
        rows = _rows({"columns": ["secids", "weight"], "data": [["SBER", 15.12]]})
        self.assertEqual(rows[0]["secids"], "SBER")


if __name__ == "__main__":
    unittest.main()
