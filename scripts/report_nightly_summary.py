from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports" / "nightly_quote_report.json"


def best_price(result: dict) -> tuple[str, str]:
    best_amount = None
    best_currency = ""
    for platform in result.get("results", []):
        for offer in platform.get("offers", []):
            amount = offer.get("final_price")
            try:
                value = float(amount)
            except Exception:
                continue
            if best_amount is None or value < best_amount:
                best_amount = value
                best_currency = str(offer.get("currency", ""))
    if best_amount is None:
        return "-", "-"
    return f"{best_amount:.2f}", best_currency


def main() -> None:
    if not REPORT_PATH.exists():
        print(f"report not found: {REPORT_PATH}")
        return
    data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    print(f"report: {REPORT_PATH}")
    for item in data:
        scenario = item.get("scenario", "unknown")
        result = item.get("result", {})
        amount, currency = best_price(result)
        platform_rows = []
        for platform in result.get("results", []):
            platform_rows.append(
                f"{platform.get('platform')} offers={platform.get('offer_count', 0)} "
                f"structured={platform.get('structured_offer_count', 0)}"
            )
        joined = " | ".join(platform_rows)
        print(f"- {scenario}: best={amount} {currency} ; {joined}")


if __name__ == "__main__":
    main()
