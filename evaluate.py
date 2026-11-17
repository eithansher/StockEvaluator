"""Evaluation harness (Part 4): score the agent or the no-tools baseline against a gold file.

    python evaluate.py eval/gold_seed.jsonl                 # agent
    python evaluate.py eval/gold_seed.jsonl --baseline      # same model, full filing text, one turn, no tools

Gold file: one thesis per line,
    {"id", "ticker", "thesis", "claims": [{"metric", "verdict", "value"?, "tolerance"?}]}
A reference claim the system does not produce counts as incorrect. Citation support and coverage need a
human judge and are not scored here."""
import argparse
import json
import statistics
import sys
import time
from decimal import Decimal

from stt import ingest, model, pipeline
from stt.definitions import COMPANIES, VERDICTS

LACKING = VERDICTS[2]
BASELINE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["claims"], "properties": {
    "claims": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["claim_text", "metric", "verdict", "value", "quotes"],
        "properties": {"claim_text": {"type": "string"},
                       "metric": {"type": "string", "enum": ["revenue", "operating_margin", "total_debt", "net_debt", "out_of_scope"]},
                       "verdict": {"type": "string", "enum": list(VERDICTS)},
                       "value": {"type": "number"}, "quotes": {"type": "array", "items": {"type": "string"}}}}}}}
BASELINE_SYSTEM = f"""Evaluate each claim in the investment thesis using only the SEC filings provided.
Verdicts: {VERDICTS[0]} (sufficient comparable evidence meets the claim's condition), {VERDICTS[1]} (sufficient \
comparable evidence conflicts with it), {VERDICTS[2]} (evidence is missing, ambiguous, incompatible or inadequate).
Defaults: revenue and margin claims compare the latest quarter with the same quarter a year earlier; "margin" is \
GAAP operating margin, change in percentage points; "debt" is total debt, latest quarter-end vs. prior quarter-end.
For each claim return the metric, the verdict, the computed value (revenue growth in %, margin change in pp, debt \
change in USD; 0 if none) and verbatim quotes from the filings that support it."""


def baseline(db, ticker, thesis):
    cik = COMPANIES[ticker]["cik"]
    docs = [f"<filing form=\"{f['form']}\" accession=\"{f['accession']}\">\n{ingest.doc_text(f['accession'], f['document'])}\n</filing>"
            for f in db.execute("SELECT * FROM filings WHERE cik=? AND doc_sha256 IS NOT NULL ORDER BY filed", (cik,))]
    if not docs:
        sys.exit("The baseline needs filing text: set SEC_USER_AGENT and re-run `python -m stt ingest`.")
    out = model._claude(BASELINE_SYSTEM, "\n".join(docs) + f"\n\nCompany: {ticker}\n<thesis>\n{thesis}\n</thesis>",
                        BASELINE_SCHEMA, "baseline")
    return [{"metric": c["metric"], "verdict": c["verdict"], "value": c["value"]} for c in out["claims"]]


def agent(db, ticker, thesis):
    out = []
    for r in pipeline.run_thesis(db, ticker, thesis):
        c = r["comparisons"][-1] if r["comparisons"] else {}
        out.append({"metric": r["metric"], "verdict": r["verdict"], "value": c.get("result")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    db, system = ingest.connect(), baseline if args.baseline else agent
    pairs, values, times, done = [], [], [], 0  # pairs: (gold verdict, predicted verdict or None)
    with open(args.gold, encoding="utf-8") as f:
        theses = [json.loads(line) for line in f if line.strip()]
    for t in theses:
        started = time.time()
        try:
            produced = system(db, t["ticker"], t["thesis"])
            done += time.time() - started <= 120
        except Exception as err:  # a failed thesis scores as nothing produced
            print(f"  {t['id']}: failed ({type(err).__name__}: {err})")
            produced = []
        times.append(time.time() - started)
        for ref in t["claims"]:
            got = next((p for p in produced if p["metric"] == ref["metric"]), None)
            if got:
                produced.remove(got)
            pairs.append((ref["verdict"], got and got["verdict"]))
            if "value" in ref:
                ok = got is not None and got["value"] is not None and abs(
                    Decimal(str(got["value"])) - Decimal(str(ref["value"]))) <= Decimal(str(ref.get("tolerance", "0.1")))
                values.append(ok)
            mark = "ok " if got and got["verdict"] == ref["verdict"] else "MISS"
            print(f"  {mark} {t['id']} {ref['metric']:17} gold={ref['verdict']} got={got and got['verdict']}")

    def f1(v):
        tp = sum(g == p == v for g, p in pairs)
        fp, fn = sum(p == v != g for g, p in pairs), sum(g == v != p for g, p in pairs)
        return 2 * tp / (2 * tp + fp + fn) if tp else 0.0

    answerable = [(g, p) for g, p in pairs if g != LACKING]
    insufficient = [(g, p) for g, p in pairs if g == LACKING]
    pct = lambda xs: f"{100 * sum(xs) / len(xs):.1f}% ({sum(xs)}/{len(xs)})" if xs else "n/a"
    print(f"\nSystem: {'baseline' if args.baseline else 'agent'} | planner: "
          f"{'Claude ' + model.MODEL if model.use_claude() else 'rule-based'} | snapshot {ingest.snapshot_id(db)}")
    print("Verdict accuracy:      ", pct([g == p for g, p in pairs]), "(target ≥ 85%)")
    print("Macro-F1:              ", f"{statistics.mean(f1(v) for v in VERDICTS):.3f}")
    print("Numerical accuracy:    ", pct(values), "(target ≥ 95%)")
    print("Abstention recall:     ", pct([p == LACKING for _, p in insufficient]), "(target ≥ 80%)")
    print("Over-abstention:       ", pct([p == LACKING for _, p in answerable]), "(target ≤ 10%)")
    print("End-to-end completion: ", f"{100 * done / len(theses):.1f}% within 120 s (target ≥ 95%)")
    print("Latency:               ", f"median {statistics.median(times):.2f} s, max {max(times):.2f} s (target median ≤ 60 s)")
    print("Token cost per call is logged in data/runs.jsonl.")


if __name__ == "__main__":
    main()
