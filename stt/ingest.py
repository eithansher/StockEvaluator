"""Ingestion: SEC EDGAR -> one SQLite file (facts, filings, FTS5 passage index) + raw files on disk."""
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from collections import Counter
from decimal import Decimal
from html import unescape

from . import DATA
from .definitions import COMPANIES

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings(accession TEXT PRIMARY KEY, cik INT, form TEXT, filed TEXT, report_date TEXT,
  fy INT, fp TEXT, document TEXT, url TEXT, facts_sha256 TEXT, doc_sha256 TEXT);
CREATE TABLE IF NOT EXISTS facts(cik INT, accession TEXT, concept TEXT, unit TEXT, pstart TEXT, pend TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS facts_lookup ON facts(cik, concept, pend);
CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text, cik UNINDEXED, accession UNINDEXED,
  section UNINDEXED, char_start UNINDEXED);
CREATE TABLE IF NOT EXISTS claims(seq INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, thesis_id TEXT, cik INT,
  thesis TEXT, evaluated_at TEXT, verdict TEXT, record TEXT);
"""
MANIFEST = DATA / "corpus.json"
PERIODIC = ("10-Q", "10-K")


def connect(path=None):
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path or DATA / "stt.sqlite", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def http_get(url):
    time.sleep(0.15)  # SEC fair access: stay well under 10 requests/s
    ua = os.environ.get("SEC_USER_AGENT", "StockThesisTracker-prototype (course project)")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=60) as r:
        return r.read()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _recent_filings(cik):
    r = json.loads(http_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))["filings"]["recent"]
    return [{"accession": r["accessionNumber"][i], "form": r["form"][i], "filed": r["filingDate"][i],
             "report_date": r["reportDate"][i], "primary_doc": r["primaryDocument"][i]}
            for i in range(len(r["form"])) if r["form"][i].removesuffix("/A") in PERIODIC]  # newest first


def load_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def freeze_corpus(tickers):
    """Frozen corpus = the four latest fiscal periods (three 10-Qs, one 10-K) plus their amendments.
    Written once to corpus.json; later runs reuse it, so the corpus only grows by a scope change."""
    manifest = load_manifest()
    for t in tickers:
        if t in manifest:
            continue
        rows = _recent_filings(COMPANIES[t]["cik"])
        periods = {x["report_date"] for x in [x for x in rows if x["form"] in PERIODIC][:4]}
        manifest[t] = sorted((x for x in rows if x["report_date"] in periods), key=lambda x: x["filed"])
    DATA.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=1))
    return manifest


_refreshed, _warned = set(), []


def _company_facts(cik, accession):
    path = DATA / "raw" / f"companyfacts_{cik}.json"
    if not path.exists() or (accession not in path.read_text() and cik not in _refreshed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(http_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
        _refreshed.add(cik)
    return json.loads(path.read_text(), parse_float=Decimal)["facts"].get("us-gaap", {})


def html_to_text(html):
    # shortcut: regex tag stripping instead of a real HTML parser; upgrade to edgartools if section
    # boundaries or tables need to be reliable.
    html = re.sub(r"(?is)<(script|style|ix:header)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h\d|table)>|<br\s*/?>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip() + "\n"


_ITEM = re.compile(r"\s*Item\s+\d+[A-C]?\.", re.I)


def split_passages(text, size=900):
    """Yield (section, start, end) chunks; text[start:end] is the passage, so offsets stay verifiable."""
    section, start, pos = "Front matter", 0, 0
    for line in text.splitlines(keepends=True):
        if _ITEM.match(line):
            if pos > start:
                yield section, start, pos
            section, start = " ".join(line.split())[:70], pos
        pos += len(line)
        if pos - start >= size:
            yield section, start, pos
            start = pos
    if pos > start:
        yield section, start, pos


def doc_text(accession, document):
    return (DATA / "filings" / accession / (document + ".txt")).read_bytes().decode("utf-8")


def _ingest_text(db, cik, e, url):
    """Download the primary document, store raw + plain text, index passages. Returns the SHA-256 or None."""
    try:
        raw = http_get(url)
    except urllib.error.HTTPError as err:
        if not _warned:
            print(f"  ! Filing text not downloaded (HTTP {err.code}). SEC's archive requires a declared "
                  "User-Agent: set SEC_USER_AGENT='Name contact@email' in .env and re-run ingest. "
                  "Numeric verdicts are unaffected; management-commentary quotes are unavailable.")
            _warned.append(err.code)
        return None
    folder = DATA / "filings" / e["accession"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / e["primary_doc"]).write_bytes(raw)
    text = html_to_text(raw.decode("utf-8", "replace"))
    (folder / (e["primary_doc"] + ".txt")).write_bytes(text.encode("utf-8"))
    for section, a, b in split_passages(text):
        chunk = text[a:b]
        if len(chunk) > 80 and sum(c.isdigit() for c in chunk) < 0.15 * len(chunk):  # skip tables
            db.execute("INSERT INTO passages VALUES(?,?,?,?,?)", (chunk, cik, e["accession"], section, a))
    return sha256(raw)


def ingest_filing(db, ticker, e):
    """Load one filing's XBRL facts and text. Idempotent; a missing text download is retried on re-run."""
    cik, acc = COMPANIES[ticker]["cik"], e["accession"]
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{e['primary_doc']}"
    row = db.execute("SELECT doc_sha256 FROM filings WHERE accession=?", (acc,)).fetchone()
    if row is None:
        # companyfacts only carries facts without dimensional qualifiers, i.e. consolidated values.
        rows, focus = [], Counter()
        for concept, c in _company_facts(cik, acc).items():
            for unit, vals in c["units"].items():
                for v in vals:
                    if v["accn"] == acc:
                        rows.append((cik, acc, concept, unit, v.get("start"), v["end"], str(v["val"])))
                        focus[(v["fy"], v["fp"])] += 1
        fy, fp = focus.most_common(1)[0][0] if focus else (None, None)
        db.executemany("INSERT INTO facts VALUES(?,?,?,?,?,?,?)", rows)
        db.execute("INSERT INTO filings VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                   (acc, cik, e["form"], e["filed"], e["report_date"], fy, fp, e["primary_doc"], url,
                    sha256(json.dumps(sorted(str(r[2:]) for r in rows)).encode())))
    if row is None or row["doc_sha256"] is None:
        db.execute("UPDATE filings SET doc_sha256=? WHERE accession=?", (_ingest_text(db, cik, e, url), acc))
    db.commit()
    return row is None


def ingest_corpus(db, tickers=None, hold_back=False):
    """hold_back=True leaves each company's newest periodic filing for the add-filing demo."""
    tickers = tickers or list(COMPANIES)
    for t, entries in freeze_corpus(tickers).items():
        if t not in tickers:
            continue
        if hold_back:
            entries = entries[:max(i for i, e in enumerate(entries) if e["form"] in PERIODIC)]
        for e in entries:
            print(f"{t} {e['form']:6} period {e['report_date']} filed {e['filed']} {e['accession']}"
                  + ("" if ingest_filing(db, t, e) else " (already loaded)"))


def pending(db, ticker):
    """Frozen-corpus filings not ingested yet (the held-back filing in the demo)."""
    have = {r[0] for r in db.execute("SELECT accession FROM filings")}
    return [e for e in load_manifest().get(ticker, []) if e["accession"] not in have]


def add_filing(db, ticker, accession=None, reason=None):
    """Manual add-filing command (M3). An accession outside the frozen corpus needs a written reason."""
    manifest = load_manifest()
    entries = manifest.setdefault(ticker, [])
    if accession is None:
        todo = pending(db, ticker)
        if not todo:
            raise ValueError(f"No held-back filing for {ticker}; pass an accession number and a scope-change reason.")
        e = todo[0]
    else:
        e = next((x for x in entries if x["accession"] == accession), None)
    if e is None:
        if not reason:
            raise ValueError("That filing is outside the frozen corpus; a scope change needs a documented reason.")
        e = next((x for x in _recent_filings(COMPANIES[ticker]["cik"]) if x["accession"] == accession), None)
        if e is None:
            raise ValueError(f"{accession} is not a recent 10-Q/10-K filing of {ticker}.")
        entries.append({**e, "scope_change": reason})
        MANIFEST.write_text(json.dumps(manifest, indent=1))
    ingest_filing(db, ticker, e)
    return e


def snapshot_id(db):
    rows = db.execute("SELECT accession, facts_sha256, doc_sha256 FROM filings ORDER BY accession").fetchall()
    return sha256("|".join(":".join(str(x) for x in r) for r in rows).encode())[:12]
