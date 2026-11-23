"""Stage 1: find the facts and red flags in each company record. No model is used here.

Every threshold is in THRESHOLDS. CASE_STUDY.md gives the reason for each one.

Usage (shows the risk levels and flag counts for all companies):
  uv run python flags.py
  uv run python flags.py path/to/companies.jsonl   (default: data/companies.jsonl)
"""

import json
from datetime import date
from pathlib import Path

RECORD_DATE = date(2025, 12, 31)  # the records are as of 31 December 2025

THRESHOLDS = {
    "quick_ratio_min": 1.0,  # less than 1.0: quick assets do not cover current liabilities
    "interest_cover_min": 1.5,  # less than 1.5: operating profit barely pays the interest
    "recent_months": 24,  # name change or director appointment in the last 24 months
    "stale_fin_months": 15,  # latest accounts older than 15 months at the record date
    "young_company_years": 3,  # companies younger than this get "young company" context
}

OPEN_STATUSES = {"Pending", "Judgment entered"}  # cases not finished, or decided against the company

# "high" -> "High concern", "review" -> "Review", "info" -> context only (no effect on the risk level)
SEVERITY = {
    "status_not_active": "high",
    "audit_disclaimer_latest": "high",
    "going_concern_latest": "high",
    "negative_equity": "high",
    "winding_up_petition_open": "high",
    "judgment_against": "high",
    "no_financials": "review",
    "audit_issue_earlier": "review",
    "net_loss": "review",
    "negative_operating_cash_flow": "review",
    "low_quick_ratio": "review",
    "low_interest_cover": "review",
    "open_defendant_cases": "review",
    "recent_name_change": "review",
    "stale_financials": "info",
    "recent_directors": "info",
    "corporate_shareholder": "info",
    "young_company": "info",
}
CONTEXT_FLAGS = {k for k, v in SEVERITY.items() if v == "info"}


def load_companies(path: str | Path = "data/companies.jsonl") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _pct_change(new, old):
    if not old or new is None:
        return None
    return (new - old) / abs(old)


def extract_facts(rec: dict) -> dict:
    """Return the compact facts and flags for one company. The LLM gets only this, not the full record."""
    t = THRESHOLDS
    p = rec["profile"]
    years = sorted(rec["financials"]["years"], key=lambda y: y["financialYear"])
    cases = rec["litigation"]["cases"]
    directors = rec["relationships"]["directors"]
    holders = rec["relationships"]["shareholders"]
    flags: dict[str, str] = {}  # flag id -> short evidence text

    age_years = _months_between(_d(p["incorporationDate"]), RECORD_DATE) / 12

    # --- Profile
    if p["status"] != "Active":
        flags["status_not_active"] = f"Status: {p['status']}"
    for fn in p["formerNames"]:
        if _months_between(_d(fn["changedOn"]), RECORD_DATE) <= t["recent_months"]:
            flags["recent_name_change"] = f"Former name {fn['name']} (changed {fn['changedOn']})"
    if age_years < t["young_company_years"]:
        flags["young_company"] = f"Incorporated {p['incorporationDate']} ({age_years:.1f} years old)"

    # --- Financials
    fin = None
    if not years:
        flags["no_financials"] = "No financial statements in the record"
    else:
        latest = years[-1]
        s = latest["statements"]
        rm = latest["ratiosAndMetrics"]
        # prior year = the year directly before the latest. Some records skip a year (2022, 2024):
        # then the change is against the last year available, and the summary must name that year.
        prev_y = years[-2] if len(years) > 1 else None
        prev = prev_y["statements"] if prev_y else None
        fin = {
            "latest_year": latest["financialYear"],
            "year_end": latest["financialYearEnd"],
            "years_available": [y["financialYear"] for y in years],
            "revenue": s["revenue"],
            "revenue_change": _pct_change(s["revenue"], prev["revenue"]) if prev else None,
            "revenue_change_vs_year": prev_y["financialYear"] if prev_y else None,
            "accounts_age_months": _months_between(_d(latest["financialYearEnd"]), RECORD_DATE),
            "profit_after_tax": s["profitAfterTax"],
            "net_margin": rm["profitability"]["netProfitMargin"],
            "total_equity": s["totalEquity"],
            "borrowings": s["borrowings"],
            "operating_cash_flow": s["operatingCashFlow"],
            "quick_ratio": rm["liquidityAndCashFlow"]["quickRatio"],
            "interest_cover": rm["capitalStructure"]["interestCoverageRatio"],
            "audit_opinion": latest["audit"]["opinion"],
            "audit_firm": latest["audit"]["firm"],
        }
        op = latest["audit"]["opinion"]
        if "disclaimer" in op.lower() or "adverse" in op.lower():
            flags["audit_disclaimer_latest"] = f"FY{latest['financialYear']} audit: {op}"
        elif "going concern" in op.lower():
            flags["going_concern_latest"] = f"FY{latest['financialYear']} audit: {op}"
        earlier = [y for y in years[:-1] if y["audit"]["opinion"] != "Unqualified"]
        if earlier:
            flags["audit_issue_earlier"] = "; ".join(
                f"FY{y['financialYear']} audit: {y['audit']['opinion']}" for y in earlier
            )
        if s["profitAfterTax"] < 0:
            flags["net_loss"] = f"FY{latest['financialYear']} net loss RM{abs(s['profitAfterTax']):,.0f}"
        if s["totalEquity"] < 0:
            flags["negative_equity"] = f"Negative equity of -RM{abs(s['totalEquity']):,.0f}"
        if s["operatingCashFlow"] < 0:
            flags["negative_operating_cash_flow"] = f"Negative operating cash flow of -RM{abs(s['operatingCashFlow']):,.0f}"
        q = fin["quick_ratio"]
        if q is not None and q < t["quick_ratio_min"]:
            flags["low_quick_ratio"] = f"Quick ratio {q:.2f}"
        ic = fin["interest_cover"]
        if ic is not None and ic < t["interest_cover_min"] and s["interestExpense"] > 0:
            flags["low_interest_cover"] = f"Interest cover {ic:.2f}x"
        stale = _months_between(_d(latest["financialYearEnd"]), RECORD_DATE)
        if stale > t["stale_fin_months"]:
            flags["stale_financials"] = (
                f"Latest accounts are for year ended {latest['financialYearEnd']} ({stale} months old)"
            )

    # --- Litigation (defendant = main risk, plaintiff = context)
    as_def = [c for c in cases if c["role"] == "defendant"]
    open_def = [c for c in as_def if c["status"] in OPEN_STATUSES]
    if open_def:
        total = sum(c["claimAmount"] for c in open_def)
        flags["open_defendant_cases"] = (
            f"{len(open_def)} pending or adverse case(s) as defendant, total claims RM{total:,.0f}"
        )
    wu_open = [c for c in as_def if c["caseType"] == "Winding-up petition" and c["status"] in OPEN_STATUSES]
    if wu_open:
        flags["winding_up_petition_open"] = "; ".join(
            f"Winding-up petition {c['caseNumber']} ({c['status']}, filed {c['filedOn']})" for c in wu_open
        )
    judged = [c for c in as_def if c["status"] == "Judgment entered"]
    if judged:
        flags["judgment_against"] = f"{len(judged)} judgment(s) entered against the company"

    # --- People
    recent_dirs = [d for d in directors if _months_between(_d(d["appointedOn"]), RECORD_DATE) <= t["recent_months"]]
    if recent_dirs and "young_company" not in flags:
        flags["recent_directors"] = (
            f"{len(recent_dirs)} of {len(directors)} directors appointed in the last {t['recent_months']} months"
        )
    corp = [h for h in holders if h["type"] == "company"]
    if corp:
        flags["corporate_shareholder"] = "; ".join(f"{h['name']} holds {h['percentage']}%" for h in corp)

    top = max(holders, key=lambda h: h["percentage"]) if holders else None
    severities = {SEVERITY[f] for f in flags}
    risk = "High concern" if "high" in severities else "Review" if "review" in severities else "No major flags"

    return {
        "registrationNumber": rec["registrationNumber"],
        "name": p["companyName"],
        "status": p["status"],
        "industry": p["industry"],
        "state": p["state"],
        "nature_of_business": p["natureOfBusiness"],
        "incorporated": p["incorporationDate"],
        "age_years": round(age_years, 1),
        "directors": len(directors),
        "largest_shareholder": top,
        "financials": fin,
        "cases": {
            "total": len(cases),
            "as_defendant": len(as_def),
            "as_plaintiff": len(cases) - len(as_def),
            "open_as_defendant": len(open_def),
        },
        "risk_level": risk,
        "flags": flags,
    }


if __name__ == "__main__":
    import sys
    from collections import Counter

    path = sys.argv[1] if len(sys.argv) > 1 else "data/companies.jsonl"
    facts = [extract_facts(r) for r in load_companies(path)]
    print("Risk levels:", dict(Counter(f["risk_level"] for f in facts)))
    print("Flags:", Counter(k for f in facts for k in f["flags"]).most_common())
