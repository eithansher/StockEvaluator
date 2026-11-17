"""Command line: python -m stt <command>. The CLI is the planned fallback for the Streamlit UI."""
import argparse
import sys

from . import ingest, pipeline
from .definitions import COMPANIES


def print_changes(pairs):
    for old, new in pairs:
        changed = "CHANGED" if old["verdict"] != new["verdict"] else "unchanged"
        print(f"[{changed}] {new['claim_text']}\n    before: {old['verdict']} | {old['explanation']}"
              f"\n    after:  {new['verdict']} | {new['explanation']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # reports use Δ, −, ÷
    ap = argparse.ArgumentParser(prog="python -m stt", description="Stock Thesis Tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="freeze the corpus (first run) and load filings from SEC EDGAR")
    p.add_argument("--hold-back", action="store_true", help="leave each company's newest filing for the add-filing demo")
    p.add_argument("--tickers", nargs="*", choices=COMPANIES)
    p = sub.add_parser("add-filing", help="ingest the next held-back filing, then reassess stored claims")
    p.add_argument("ticker", choices=COMPANIES)
    p.add_argument("accession", nargs="?")
    p.add_argument("--reason", help="documented scope-change reason, required for filings outside the frozen corpus")
    p = sub.add_parser("assess", help="evaluate a thesis and store its claim records")
    p.add_argument("ticker", choices=COMPANIES)
    p.add_argument("thesis")
    p = sub.add_parser("reassess", help="re-run a company's stored claims")
    p.add_argument("ticker", choices=COMPANIES)
    p = sub.add_parser("history", help="list stored claim records (append-only verdict history)")
    p.add_argument("ticker", nargs="?", choices=COMPANIES)
    p = sub.add_parser("delete", help="delete a stored thesis and its claim records")
    p.add_argument("thesis_id")
    args = ap.parse_args()
    db = ingest.connect()

    if args.cmd == "ingest":
        ingest.ingest_corpus(db, args.tickers, args.hold_back)
        print("corpus snapshot", ingest.snapshot_id(db))
    elif args.cmd == "add-filing":
        try:
            e = ingest.add_filing(db, args.ticker, args.accession, args.reason)
        except ValueError as err:
            sys.exit(str(err))
        print(f"Ingested {e['form']} for period {e['report_date']} ({e['accession']}). Reassessing stored claims:")
        print_changes(pipeline.reassess(db, args.ticker))
    elif args.cmd == "assess":
        records = pipeline.run_thesis(db, args.ticker, args.thesis)
        print("\n\n".join(pipeline.format_report(r) for r in records) or "No checkable claim found in the thesis.")
    elif args.cmd == "reassess":
        print_changes(pipeline.reassess(db, args.ticker))
    elif args.cmd == "history":
        for r in pipeline.history(db, args.ticker):
            print(f"{r['run']['evaluated_at']}  {r['claim_id']}  {r['ticker']:5} {r['verdict']:28} {r['claim_text']}")
    elif args.cmd == "delete":
        pipeline.delete_thesis(db, args.thesis_id)


if __name__ == "__main__":
    main()
