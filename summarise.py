"""Stage 2: write the summaries.

Two writers:
  rules  - a fixed template, no model. This is the baseline (v0).
  llm    - a model through an OpenAI-compatible chat API. The project used gemini-3.5-flash-lite.

Prompt versions for the llm writer (--prompt):
  v1  first prompt
  v2  stricter prompt: exact template, only high/review flags in the risk line, max 100 words
  v3  the v2 prompt, but the code formats every number before the model sees it

Every model response is saved in runs/<run>/raw/. A saved response is used again, so a re-run
makes no new model calls.

Usage (the runs in CASE_STUDY.md, on the 10 review companies):
  IDS=SYN-000545,SYN-001212,SYN-000451,SYN-000217,SYN-001158,SYN-000302,SYN-000267,SYN-000029,SYN-000381,SYN-001970
  uv run python summarise.py --run v0_rules --writer rules --ids $IDS
  uv run python summarise.py --run v1_llm --writer llm --prompt v1 --ids $IDS
  uv run python summarise.py --run v2_llm --writer llm --prompt v2 --ids $IDS
  uv run python summarise.py --run v3_llm --writer llm --prompt v3 --ids $IDS

Other options:
  --limit N      only the first N companies (--ids overrides it)
  --model NAME   model for the llm writer (default: gemini-3.5-flash-lite)

To run a version again with new model calls, delete runs/<run>/raw/ or use a new run name.

Environment for the llm writer (keep the key out of the repo):
  LLM_API_KEY   your API key
  LLM_BASE_URL  for Gemini: https://generativelanguage.googleapis.com/v1beta/openai/
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

from flags import CONTEXT_FLAGS, SEVERITY, extract_facts, load_companies


def rm(x: float) -> str:
    """One money format (RM1.23m / RM450k), so that evaluate.py can read it."""
    a, sign = abs(x), "-" if x < 0 else ""
    if a >= 1_000_000:
        return f"{sign}RM{a / 1_000_000:.2f}m"
    if a >= 1_000:
        return f"{sign}RM{a / 1_000:.0f}k"
    return f"{sign}RM{a:.0f}"


def rules_summary(f: dict) -> str:
    reasons = [v for k, v in f["flags"].items() if k not in CONTEXT_FLAGS]
    lines = [f"**Risk: {f['risk_level']}**" + (" — " + "; ".join(reasons[:3]) + "." if reasons else ".")]
    lines.append(
        f"- **Profile:** {f['status']} private company, {f['industry']}, {f['state']}, "
        f"incorporated {f['incorporated'][:4]}. {f['nature_of_business']}."
    )
    fin = f["financials"]
    if fin:
        chg = ""
        if fin["revenue_change"] is not None:
            base = fin["revenue_change_vs_year"]
            vs = "on prior year" if base == fin["latest_year"] - 1 else f"vs FY{base}, the previous accounts in the record"
            chg = f", {fin['revenue_change'] * 100:+.0f}% {vs}"
        pat = fin["profit_after_tax"]
        lines.append(
            f"- **Financials (FY{fin['latest_year']}):** Revenue {rm(fin['revenue'])}{chg}. "
            f"Net {'profit' if pat >= 0 else 'loss'} {rm(abs(pat))}. "
            f"Equity {rm(fin['total_equity'])}. Audit: {fin['audit_opinion']}."
        )
    else:
        lines.append("- **Financials:** No financial statements in the record.")
    c = f["cases"]
    lines.append(
        f"- **Litigation:** {c['total']} case(s): {c['as_defendant']} as defendant "
        f"({c['open_as_defendant']} pending or adverse), {c['as_plaintiff']} as plaintiff."
        if c["total"]
        else "- **Litigation:** No court cases."
    )
    top = f["largest_shareholder"]
    own = f" Largest shareholder: {top['name']} ({top['percentage']}%)." if top else ""
    lines.append(f"- **People:** {f['directors']} director(s).{own}")
    return "\n".join(lines)


PROMPT_V1 = """You write a short summary of a company for a team that vets suppliers and project partners.
The team reads your summary first, to decide if the company needs a closer look.

Rules:
- Use ONLY the facts in the JSON. Do not add facts, opinions or numbers that are not in the JSON.
- Start with one line: **Risk: <risk_level>** — then the 1 to 3 most serious reasons.
- Then 4 bullets: **Profile**, **Financials**, **Litigation**, **People**.
- Mention EVERY item in "flags". If a flag gives context (for example a young company), use it to explain the risk.
- Write money as RM1.23m or RM450k. Write ratios with two decimals. Write percentages as whole numbers.
- Maximum 120 words. Markdown only, no preamble.

Company facts (JSON):
{facts}"""

# v2: a stricter prompt. The model also gets the severity of each flag (to order the risk line),
# plus revenue_change_vs_year and accounts_age_months, which v1 did not have.
# Problems found in v1: too long (mean 104 words, max 146), info flags in the risk line,
# different bullet formats, ratios that triggered no flag, opinions that are not in the record.
PROMPT_V2 = """You write a risk-screening summary of a Malaysian company for a team that vets suppliers
and project partners. The team decides from your FIRST LINE if the company needs a closer look.

Use ONLY the facts in the JSON. Copy numbers from the JSON; do not calculate new ones.

Write EXACTLY this Markdown: 5 lines, no blank lines, no preamble, no extra text.

**Risk: <risk_level>** — <reasons>.
- **Profile:** <status> private company, <industry>, <state>, incorporated <year>. <nature_of_business in English>.
- **Financials (FY<latest_year>):** <financials>
- **Litigation:** <litigation>
- **People:** <N> director(s). Largest shareholder: <name> (<percentage>%). <people flags>

Rules:
1. <risk_level> is exactly "risk_level" in the JSON.
2. <reasons>: only flags with severity "high" or "review" (see "severity"), most serious first,
   at most 3. NEVER put an "info" flag in the risk line. If there are none, write "no high or review flags".
3. Mention EVERY flag once: in the risk line or in its section line. Keep the key words of the
   flag text (for example "months old", "appointed", "defendant", "judgment", "interest cover").
4. <financials>: revenue and its change, net profit or net loss, equity, audit opinion. Add a ratio
   or cash flow ONLY if it has a flag. If "revenue_change_vs_year" is not latest_year - 1, write
   "vs FY<that year>", not "on prior year". If the stale_financials flag exists, write
   "Accounts <accounts_age_months> months old." If financials is null, write
   "No financial statements in the record." and leave out "(FY<latest_year>)".
5. <litigation>: "<total> case(s): <as_defendant> as defendant (<open_as_defendant> pending or adverse),
   <as_plaintiff> as plaintiff." plus any litigation flags. If total is 0, write "No court cases."
6. Do not write the company name or registration number.
7. Money: RM1.23m or RM450k. Ratios: two decimals. Percentages: whole numbers.
   Write "net loss RM1.08m", not "negative ... -RM1.08m".
8. Maximum 100 words. Remove detail before you remove a flag.

Company facts (JSON):
{facts}"""
# v3: one change from v2 - the code formats every number, and the prompt tells the model to copy them.
# v2 problem: the model rounded money itself and cut digits twice (RM275,562 -> "RM275k", RM944,841 -> "RM944k").
PROMPT_V3 = PROMPT_V2.replace(
    "Copy numbers from the JSON; do not calculate new ones.",
    "Every number in the JSON is already formatted. Copy it exactly; do not round, convert or calculate.",
)
PROMPTS = {"v1": PROMPT_V1, "v2": PROMPT_V2, "v3": PROMPT_V3}

MONEY_KEYS = ("revenue", "profit_after_tax", "total_equity", "borrowings", "operating_cash_flow")


def display_facts(f: dict) -> dict:
    """The same facts, with numbers as the summary must show them. Code does the rounding, not the model."""
    out = json.loads(json.dumps(f, default=str))
    fin = out["financials"]
    if fin:
        for k in MONEY_KEYS:
            fin[k] = rm(fin[k])
        if fin["revenue_change"] is not None:
            fin["revenue_change"] = f"{fin['revenue_change'] * 100:+.0f}%"
        fin["net_margin"] = f"{fin['net_margin'] * 100:.0f}%" if fin["net_margin"] is not None else None
        for k in ("quick_ratio", "interest_cover"):
            fin[k] = f"{fin[k]:.2f}" if fin[k] is not None else None
    # flag text: "RM275,562" -> "RM276k"
    out["flags"] = {k: re.sub(r"RM([\d,]+)", lambda m: rm(float(m.group(1).replace(",", ""))), v)
                    for k, v in out["flags"].items()}
    return out


def build_prompt(f: dict, prompt_version: str) -> str:
    """The exact prompt for one company. Each version gets the same facts as when it was run,
    so that the saved responses still match (see the check in llm_summary)."""
    # age_years is for evaluate.py only. The young_company flag already gives the age to the model.
    f = {k: v for k, v in f.items() if k != "age_years"}
    if prompt_version == "v1":
        # v1 was run before these two fields were added to the facts
        fin = f["financials"] and {k: v for k, v in f["financials"].items()
                                   if k not in ("revenue_change_vs_year", "accounts_age_months")}
        facts = {**f, "financials": fin}
    else:  # v2 and v3: plus the severity of each flag, so the model can order the risk line
        if prompt_version == "v3":
            f = display_facts(f)
        facts = {**f, "severity": {k: SEVERITY[k] for k in f["flags"]}}
    return PROMPTS[prompt_version].format(facts=json.dumps(facts, indent=1, default=str))


def llm_summary(f: dict, model: str, raw_dir: Path, prompt_version: str = "v1") -> str:
    raw_path = raw_dir / f"{f['registrationNumber']}.json"
    prompt = build_prompt(f, prompt_version)
    if raw_path.exists():  # saved response: no new model call
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        if data.get("_prompt") != prompt:
            print(f"  WARNING {f['registrationNumber']}: saved response is for a different prompt. "
                  f"Use a new --run name, or delete {raw_path}.")
        saved_model = str(data.get("model", "")).removeprefix("models/")
        if model and saved_model and saved_model != model:
            print(f"  WARNING {f['registrationNumber']}: saved response is from {saved_model}, not {model}. "
                  f"Use a new --run name, or delete {raw_path}.")
        return data["choices"][0]["message"]["content"].strip()

    if not model:
        raise SystemExit("the llm writer needs --model")
    from openai import OpenAI  # imported here, so that the rules writer does not need it

    if not os.environ.get("LLM_API_KEY"):
        raise SystemExit("set LLM_API_KEY (and LLM_BASE_URL) before you use the llm writer")
    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL"))
    for attempt in range(5):
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0, messages=[{"role": "user", "content": prompt}]
            )
            break
        except Exception as e:  # free tiers often return 429 (rate limit): wait and try again
            wait = 2**attempt * 5
            print(f"  {f['registrationNumber']}: {e.__class__.__name__}, retry in {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError(f"No response for {f['registrationNumber']}")
    data = resp.model_dump()
    data["_prompt"] = prompt
    raw_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data["choices"][0]["message"]["content"].strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run name, also the summaries/<run>.jsonl file name")
    ap.add_argument("--writer", choices=["rules", "llm"], default="rules")
    ap.add_argument("--model", default="gemini-3.5-flash-lite", help="model name for the llm writer")
    ap.add_argument("--data", default="data/companies.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="only the first N companies (0 = all)")
    ap.add_argument("--ids", default="", help="comma-separated registration numbers (overrides --limit)")
    ap.add_argument("--prompt", choices=sorted(PROMPTS), default="v1", help="prompt version for the llm writer")
    a = ap.parse_args()

    companies = load_companies(a.data)
    if a.ids:
        wanted = [x.strip() for x in a.ids.split(",") if x.strip()]
        by_id = {c["registrationNumber"]: c for c in companies}
        missing = [x for x in wanted if x not in by_id]
        if missing:
            raise SystemExit(f"unknown registration numbers: {missing}")
        companies = [by_id[x] for x in wanted]
    elif a.limit:
        companies = companies[: a.limit]
    run_dir = Path("runs") / a.run
    raw_dir = run_dir / "raw"
    run_dir.mkdir(parents=True, exist_ok=True)
    if a.writer == "llm":
        raw_dir.mkdir(exist_ok=True)
    Path("summaries").mkdir(exist_ok=True)

    out = Path("summaries") / f"{a.run}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for rec in companies:
            f = extract_facts(rec)
            text = rules_summary(f) if a.writer == "rules" else llm_summary(f, a.model, raw_dir, a.prompt)
            row = {"registrationNumber": f["registrationNumber"], "summary": text}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    config = vars(a)
    if a.writer == "rules":  # the rules writer uses no model and no prompt
        config = {k: v for k, v in config.items() if k not in ("model", "prompt")}
    (run_dir / "config.json").write_text(json.dumps(config, indent=1))
    print("wrote", out, f"({len(companies)} companies)")


if __name__ == "__main__":
    main()
