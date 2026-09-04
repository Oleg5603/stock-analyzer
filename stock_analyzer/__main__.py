from __future__ import annotations

import argparse
import json

from .csvio import import_companies_csv
from .models import Company, PortfolioPosition
from .rules import analyze_company
from .server import run_server


def self_test() -> None:
    company = Company(
        ticker="TEST", name="Тест", sector="Банки", category=2, is_bank=True,
        bank_metrics_passed=True, d1_confirmed=True, h4_confirmed=True,
        volume_profile_confirmed=True,
    )
    ok = analyze_company(company, [PortfolioPosition("OLD", "Банки", 10)], 8)
    assert ok.decision == "candidate" and ok.projected_sector_pct == 18
    rejected = analyze_company(company, [PortfolioPosition("OLD", "Банки", 15)], 8)
    assert rejected.decision == "reject" and rejected.projected_sector_pct == 23
    review = analyze_company(Company("UNK", "Нет данных", "Нефть"))
    assert review.decision == "manual_review"
    imported = import_companies_csv("ticker,name,sector,category\nSBER,Сбер,Банки,1\n")
    assert len(imported.companies) == 1 and not imported.errors
    print(json.dumps({"status": "ok", "checks": 7}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Local read-only stock analyzer")
    parser.add_argument("command", choices=["serve", "self-test"], nargs="?", default="serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
    else:
        run_server(args.host, args.port)


if __name__ == "__main__":
    main()
