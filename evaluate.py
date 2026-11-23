"""Score every summaries/<run>.jsonl against the source records. No model calls.

Metrics per run:
  num_prec   share of numbers in the summaries that match a value in the record (rounding allowed)
  flag_rec   share of red flags from flags.py that the summary mentions (keyword match)
  hr_rec     the same, for "high" and "review" flags only (the flags that set the risk level)
  false_al   risk words in a summary when the record has no matching flag
  risk_ok    the first line has the correct risk level
  words/max  mean and maximum summary length in words

It scores every file in summaries/. Each version must have its own file, for example
summaries/v3_llm.jsonl.

Usage (the scores in CASE_STUDY.md, on the 10 review companies):
  IDS=SYN-000545,SYN-001212,SYN-000451,SYN-000217,SYN-001158,SYN-000302,SYN-000267,SYN-000029,SYN-000381,SYN-001970
  uv run python evaluate.py --ids $IDS
  uv run python evaluate.py --ids $IDS --detail v3_llm   (also list each problem for one run)

Without --ids, it scores all companies in each file. Use --ids when the files have different
companies, so that every run is scored on the same set.
"""

import argparse
import json
import re
from pathlib import Path

from flags import SEVERITY, THRESHOLDS, extract_facts, load_companies

# Words that show the summary mentions a flag. A keyword match is crude, so review a sample by hand.
# A keyword must start a word: "qualified" does not match "unqualified".
FLAG_WORDS = {
    "status_not_active": ["winding up", "not active", "dissolved", "struck off"],
    "audit_disclaimer_latest": ["disclaimer"],
    "going_concern_latest": ["going concern"],
    "audit_issue_earlier": ["disclaimer", "going concern", "qualified"],
    "negative_equity": ["negative equity", "equity -rm", "equity of -rm", "liabilities exceed", "deficit"],
    "winding_up_petition_open": ["winding-up petition", "winding up petition"],
    "judgment_against": ["judgment"],
    "no_financials": ["no financial"],
    "net_loss": ["loss"],
    "negative_operating_cash_flow": ["operating cash", "cash outflow", "negative cash"],
    "low_quick_ratio": ["quick ratio", "liquidity"],
    "low_interest_cover": ["interest cover"],
    "open_defendant_cases": ["defendant"],
    "recent_name_change": ["former", "formerly", "renamed", "name change", "changed its name"],
    "stale_financials": ["latest accounts", "months old", "stale", "old accounts"],  # not "fy2024": every heading has it
    "recent_directors": ["appointed", "new director"],
    "corporate_shareholder": ["holdings", "corporate shareholder", "company shareholder"],
    "young_company": ["young", "start-up", "startup", "incorporated 2023", "incorporated 2024", "years old"],
}
# If a summary uses one of these words, the record must have one of the matching flags.
ALARM_WORDS = {
    "winding": {"status_not_active", "winding_up_petition_open"},
    "disclaimer": {"audit_disclaimer_latest", "audit_issue_earlier"},
    "going concern": {"going_concern_latest", "audit_issue_earlier"},
    "negative equity": {"negative_equity"},
    "judgment": {"judgment_against"},
}

# "N months" and "N years" are checked on their own. They are correct when N is a threshold
# ("in the last 24 months"), the age of the latest accounts or the age of the company.
# These values come from the code, not from the record, so the general number check cannot find them.
MONTHS = re.compile(r"\b(\d+)\s*months?\b", re.I)
YEARS = re.compile(r"\b(\d+(?:\.\d+)?)\s*-?\s*years?\b", re.I)


def mentions(low: str, word: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(word), low) is not None


def months_ok(shown: str, f: dict) -> bool:
    ok = {v for k, v in THRESHOLDS.items() if k.endswith("_months")}
    if f["financials"]:
        ok.add(f["financials"]["accounts_age_months"])
    return int(shown) in ok


def years_ok(shown: str, f: dict) -> bool:
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    tol = 0.5 * 10**-decimals + 1e-9  # "2.3 years" matches 2.25 to 2.35
    targets = [f["age_years"]] + [v for k, v in THRESHOLDS.items() if k.endswith("_years")]
    return any(abs(float(shown) - t) <= tol for t in targets)


NUM = re.compile(r"(-?)(RM)?\s?(\d[\d,]*(?:\.\d+)?)\s?(million|m|k|%|x)?(?![\w-])", re.I)


def record_values(rec: dict) -> list[float]:
    vals: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            vals.append(float(x))
            if abs(x) < 50:  # ratios are decimals: also accept them as percent
                vals.append(float(x) * 100)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(rec)
    fin = extract_facts(rec)["financials"]
    if fin and fin["revenue_change"] is not None:
        vals.append(fin["revenue_change"] * 100)
    dates = [rec["profile"]["incorporationDate"]] + [c["filedOn"] for c in rec["litigation"]["cases"]]
    dates += [y["financialYearEnd"] for y in rec["financials"]["years"]]
    vals += [float(s[:4]) for s in dates]
    # totals that a summary can state: claims by role and by status
    cases = rec["litigation"]["cases"]
    for role in ("defendant", "plaintiff", None):
        group = [c for c in cases if role in (None, c["role"])]
        vals.append(sum(c["claimAmount"] for c in group))
        vals.append(sum(c["claimAmount"] for c in group if c["status"] in ("Pending", "Judgment entered")))
        vals.append(sum(c["claimAmount"] for c in group if c["status"] == "Pending"))
    return vals


def record_dates(rec: dict) -> set[str]:
    return set(re.findall(r"\d{4}-\d{2}-\d{2}", json.dumps(rec)))


def number_ok(m: re.Match, values: list[float]) -> bool | None:
    """True = matches the record, False = not in the record, None = not scored."""
    sign, cur, digits, unit = m.groups()
    v = float(digits.replace(",", ""))
    unit = (unit or "").lower()
    if not cur and not unit and v <= 20 and v.is_integer():
        return None  # small counts (3 directors, 2 cases)
    mult = {"m": 1e6, "million": 1e6, "k": 1e3}.get(unit, 1)
    v *= mult
    targets = [-v] if sign else [v, -v]  # "net loss RM1.2m" has no minus sign
    # allowed rounding error: half of the last shown digit (RM1.23m -> 5,000; RM450k -> 500; 12% -> 0.5)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    abs_tol = 0.5 * mult * 10**-decimals + 1e-9
    for t in targets:
        for x in values:
            if abs(x - t) <= abs_tol:
                return True
    return False


def score(summary: str, rec: dict) -> dict:
    f = extract_facts(rec)
    low = summary.lower()
    values = record_values(rec)
    known = record_dates(rec)
    checked = [(d, d in known) for d in re.findall(r"\d{4}-\d{2}-\d{2}", summary)]
    text = re.sub(r"\d{4}-\d{2}-\d{2}|(SYN|CASE)-[\d-]+|Director \d+|Shareholder \d+|Audit Firm \d+", "", summary)
    checked += [(m.group(0), months_ok(m.group(1), f)) for m in MONTHS.finditer(text)]
    text = MONTHS.sub("", text)
    checked += [(m.group(0), years_ok(m.group(1), f)) for m in YEARS.finditer(text)]
    text = YEARS.sub("", text)
    checked += [(m.group(0).strip(), number_ok(m, values)) for m in NUM.finditer(text)]
    checked = [(t, ok) for t, ok in checked if ok is not None]
    missed = [k for k in f["flags"] if not any(mentions(low, w) for w in FLAG_WORDS.get(k, []))]
    alarms = [w for w, allowed in ALARM_WORDS.items() if w in low and not (allowed & set(f["flags"]))]
    first = summary.strip().splitlines()[0] if summary.strip() else ""
    return {
        "numbers": len(checked),
        "numbers_ok": sum(ok for _, ok in checked),
        "bad_numbers": [t for t, ok in checked if not ok],
        "flags": len(f["flags"]),
        "flags_hit": len(f["flags"]) - len(missed),
        "missed_flags": missed,
        "hr_flags": sum(SEVERITY[k] != "info" for k in f["flags"]),
        "hr_hit": sum(SEVERITY[k] != "info" for k in f["flags"] if k not in missed),
        "false_alarms": alarms,
        "risk_line_ok": f["risk_level"].lower() in first.lower(),
        "words": len(re.sub(r"[*#`]", "", summary).split()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/companies.jsonl")
    ap.add_argument("--detail", default="")
    ap.add_argument("--ids", default="", help="score only these companies (comma-separated), for a fair comparison")
    a = ap.parse_args()
    recs = {r["registrationNumber"]: r for r in load_companies(a.data)}
    ids = {x.strip() for x in a.ids.split(",") if x.strip()}

    print(f"{'run':<16}{'n':>4}{'num_prec':>10}{'flag_rec':>10}{'hr_rec':>8}{'false_al':>10}{'risk_ok':>9}{'words':>7}{'max':>6}")
    for path in sorted(Path("summaries").glob("*.jsonl")):
        rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
        if ids:
            rows = [r for r in rows if r["registrationNumber"] in ids]
        if not rows:
            continue
        res = [(r["registrationNumber"], score(r["summary"], recs[r["registrationNumber"]])) for r in rows]
        s = [x for _, x in res]
        n_num = sum(x["numbers"] for x in s) or 1
        n_flag = sum(x["flags"] for x in s) or 1
        print(
            f"{path.stem:<16}{len(s):>4}"
            f"{sum(x['numbers_ok'] for x in s) / n_num:>10.1%}"
            f"{sum(x['flags_hit'] for x in s) / n_flag:>10.1%}"
            f"{sum(x['hr_hit'] for x in s) / (sum(x['hr_flags'] for x in s) or 1):>8.1%}"
            f"{sum(len(x['false_alarms']) for x in s):>10}"
            f"{sum(x['risk_line_ok'] for x in s) / len(s):>9.0%}"
            f"{sum(x['words'] for x in s) / len(s):>7.0f}"
            f"{max(x['words'] for x in s):>6}"
        )
        if a.detail == path.stem:
            for reg, x in res:
                probs = {k: x[k] for k in ("bad_numbers", "missed_flags", "false_alarms") if x[k]}
                if probs or not x["risk_line_ok"]:
                    print(f"   {reg}: {probs} risk_line_ok={x['risk_line_ok']}")


if __name__ == "__main__":
    main()
