"""Streamlit UI: streamlit run app.py

Reading order on every screen: outcome first (summary, verdict, the deciding figure), then the two period
values with footnote links to their SEC filings, then the method folded under "How this was checked"."""
import re
from datetime import date, datetime
from html import escape as e

import streamlit as st

from stt import ingest, model, pipeline
from stt.definitions import COMPANIES, VERDICTS

st.set_page_config(page_title="Stock Thesis Tracker", layout="wide")

KIND = dict(zip(VERDICTS, "scl"))  # css class per verdict
SHARED = ("Evidence cutoff", "Evidence through", "A newer filing")  # caveats that apply to every claim: shown once, in the summary
EXAMPLES = ["Revenue is growing and margins are improving", "Debt is falling", "Operating margin is above 30%"]
LEGEND = [("Supported", "The filings meet the claim's condition"),
          ("Contradicted", "The filings conflict with it"),
          ("Lacking sufficient evidence", "The data is missing, not comparable, or the claim is out of scope")]
CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap');
:root{--card:#19191b;--line:rgba(237,237,237,.08);--t1:#ededed;--t2:#a1a1a6;--t3:#85858b;--ok:#4cc38a;--bad:#e5484d;
  --unk:#d9a441;--accent:#8fb0ff;--mono:'IBM Plex Mono',ui-monospace,monospace}
.stMainBlockContainer{max-width:1200px;padding-top:48px}
.stApp button[kind="primary"]{background:var(--t1);border-color:var(--t1);color:#141415}
.stApp button[kind="primary"]:hover{background:#fff;border-color:#fff;color:#141415}
.stt{color:var(--t1);font-size:15px;line-height:22px}
.stt a{color:var(--accent);text-decoration:none}
.stt a:hover{text-decoration:underline}
.stt a:focus-visible,.stt summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px;margin-bottom:16px}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:16px}
.meta{font:400 12px/16px var(--mono);color:var(--t3);white-space:nowrap}
.t2{color:var(--t2);font-size:13px;line-height:20px}
.verdict{display:inline-flex;align-items:center;gap:8px;font-size:13px;line-height:20px;font-weight:500;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;flex:none}
.s{color:var(--ok)}.s .dot{background:var(--ok)}.c{color:var(--bad)}.c .dot{background:var(--bad)}
.l{color:var(--unk)}.l .dot{background:var(--unk)}
.claim{font-size:18px;line-height:26px;margin:8px 0 20px}
.result{display:flex;align-items:center;gap:24px;flex-wrap:wrap}
.figure{font:500 32px/1 var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.ledger{display:grid;grid-template-columns:1fr max-content;gap:8px 24px;border-top:1px solid var(--line);
  margin-top:20px;padding-top:16px;font-size:14px;line-height:20px}
.ledger .sub{grid-column:1/-1;color:var(--t2);font-size:13px}
.ledger .sub:not(:first-child){margin-top:8px}
.ledger .d{color:var(--t3);font-size:13px;margin-left:12px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.stt sup{font:400 11px/1 var(--mono);margin-left:4px}
.note{color:var(--t2);font-size:13px;line-height:20px;margin-top:12px}
.stt details{margin-top:20px}
.stt summary{cursor:pointer;color:var(--t2);font-size:13px;line-height:20px;width:max-content}
.stt summary:hover{color:var(--t1)}
.stt h4{font-size:12px;line-height:16px;font-weight:400;color:var(--t3);margin:20px 0 8px;padding:0}
.stt ul,.stt ol{margin:0;padding-left:20px;font-size:14px;line-height:22px;color:var(--t2)}
.stt ol.src li::marker{font-family:var(--mono);font-size:12px;color:var(--t3)}
.stt ol.src b{font-weight:400;color:var(--t1)}
.stt code{font:400 12px/20px var(--mono);color:var(--t3);background:none;padding:0}
.stt blockquote{margin:0 0 8px;padding-left:12px;border-left:2px solid var(--line);font-size:14px;color:var(--t1)}
.stats{display:flex;gap:48px;flex-wrap:wrap;margin-bottom:20px}
.stats .figure{margin-bottom:8px}.stats .zero{color:var(--t3)}
.tbl{width:100%;border-collapse:collapse;font-size:14px;line-height:20px}
.tbl th{font-size:12px;font-weight:400;color:var(--t3);text-align:left;padding:0 16px 8px 0}
.tbl td{padding:10px 16px 10px 0;border-top:1px solid var(--line);vertical-align:top}
.tbl th:last-child,.tbl td:last-child{padding-right:0}
.tbl td.num{font-size:13px}
.brand{font-size:17px;font-weight:500;margin-bottom:4px}
.changed{color:var(--accent);font-size:12px;margin-left:8px}
</style>"""


@st.cache_resource
def get_db():
    return ingest.connect()


def day(iso):
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def span(start, end):
    """Exact dates behind a fiscal label: fiscal calendars differ, so 'Q3' alone says little."""
    if not start:
        return f"at {day(end)}"
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return f"{a:%b} {a.day}{'' if a.year == b.year else f', {a.year}'} – {day(end)}"


def note(text):
    """A stored note as interface text: sentence case, one date format, no trailing period. Escaped."""
    # lookarounds keep accession numbers (0000320193-26-000026) from being read as dates
    text = re.sub(r"(?<![\d-])\d{4}-\d\d-\d\d(?![\d-])", lambda m: day(m.group()), text.rstrip("."))
    return e(text[0].upper() + text[1:])


def verdict(v):
    return f'<span class="verdict {KIND[v]}"><i class="dot"></i>{e(v)}</span>'


def key_figure(rec):
    comps = rec["comparisons"]
    if len(comps) > 1:
        return f"{sum(c['met'] is True for c in comps)} of {len(comps)} quarters"
    if not comps or comps[0]["result"] is None:
        return "–"
    return pipeline.show(comps[0]["result"], comps[0]["result_unit"], rec["spec"].get("measure") == "change")


def said(rec):
    """One plain sentence for the outcome."""
    comps = rec["comparisons"]
    if rec["verdict"] == VERDICTS[2]:
        return (rec["gaps"] or ["No comparable evidence was found"])[0]
    if len(comps) > 1:
        return "quarters met the condition"
    return comps[0].get("headline") or comps[0]["text"]


def card(rec, position=""):
    comps, n, ledger = rec["comparisons"], 0, ""
    for c in comps:  # footnote numbers run across the whole claim; each marker opens the filing
        if len(comps) > 1:
            ledger += f'<span class="sub">{e(c.get("headline") or c["text"] or c["current"] + ": " + "; ".join(c["gaps"]))}</span>'
        for side in c.get("sides", []):
            marks = "".join(f'<sup><a href="{e(i["url"])}" target="_blank" rel="noopener" title="{e(i["role"])}">{n + k}</a></sup>'
                            for k, i in enumerate(c["inputs"][slice(*side["inputs"])], side["inputs"][0] + 1))
            ledger += (f'<span>{e(side["label"])}<span class="d">{e(span(side["start"], side["end"]))}</span></span>'
                       f'<span class="num">{e(side["value"])}{marks}</span>')
        n += len(c["inputs"])
    lacking = rec["verdict"] == VERDICTS[2]
    result = (f'<span>{e(said(rec))}</span>' if lacking else
              f'<span class="figure">{e(key_figure(rec).removesuffix(" quarters"))}</span><span>{e(said(rec))}'
              f'<br><span class="t2">The claim needed {e(rec.get("needed") or rec["condition"])}</span></span>')
    notes = [c for c in rec["caveats"] if not c.startswith(SHARED + ("derived",))]
    if any(c.startswith("derived") for c in rec["caveats"]):  # one line instead of one per input
        notes.insert(0, "Q4 values are derived: fiscal year minus the first nine months")
    sources = "".join(
        f'<li><b>{e(i["role"])}</b> · <span class="num">{e(pipeline.usd(i["value"]))}</span> · '
        f'<a href="{e(i["url"])}" target="_blank" rel="noopener">{e(i["form"])} filed {day(i["filed"])}</a> '
        f'<code>{e(i["concept"])} · {e(i["accession"])}</code></li>' for c in comps for i in c["inputs"])
    quotes = "".join(
        f'<blockquote>“{e(p["quote"])}”<br><span class="t2">Management states, in the '
        f'<a href="{e(p["url"])}" target="_blank" rel="noopener">{e(p["form"])} {e(p["accession"])}</a>, {e(p["section"])}, '
        f'characters {p["start"]}–{p["end"]}. A reported explanation, not a verified cause</span></blockquote>'
        for p in rec["passages"])
    items = lambda xs: "<ul>" + "".join(f"<li>{note(x)}</li>" for x in xs) + "</ul>"
    run = rec["run"]
    method = "".join([
        f"<h4>Assumptions</h4>{items(rec['assumptions'])}" if rec["assumptions"] else "",
        f"<h4>Formula</h4><ul><li>{e(rec['formula'])}</li></ul>" if rec["formula"] else "",
        f'<h4>Sources</h4><ol class="src">{sources}</ol>' if sources else "",
        f"<h4>Management commentary</h4>{quotes}" if quotes else "",
        f"<h4>Other evidence gaps</h4>{items(rec['gaps'][1:])}" if len(rec["gaps"]) > 1 else "",
        f"<h4>Citation check</h4><ul><li>{len(rec['removed'])} unsupported statement(s) removed and logged</li></ul>"
        if rec.get("removed") else "",
        f'<h4>Run</h4><div class="meta">{e(rec["claim_id"])} · {e(run["planner"])} · corpus snapshot '
        f'{e(run["corpus_snapshot_id"])} · {e(run["evaluated_at"])}</div>'])
    return (f'<div class="stt card"><div class="row">{verdict(rec["verdict"])}<span class="meta">{e(position)}</span></div>'
            f'<div class="claim">“{e(rec["claim_text"])}”</div><div class="result">{result}</div>'
            + (f'<div class="ledger">{ledger}</div>' if ledger else "")
            + "".join(f'<div class="note">{note(x)}</div>' for x in notes)
            + f"<details><summary>How this was checked</summary>{method}</details></div>")


def claim_rows(rows):
    """Compact table: verdict, claim, deciding figure. rows = [(record, extra cell html)]."""
    return '<table class="tbl">' + "".join(
        f'<tr><td style="width:1%">{verdict(r["verdict"])}</td><td>{e(r["claim_text"])}{extra}</td>'
        f'<td class="num">{e(key_figure(r))}</td></tr>' for r, extra in rows) + "</table>"


def summary(records):
    """Outcome of the whole thesis, plus the notes that apply to every claim. One claim needs no counts."""
    shared = "".join(f'<div class="note">{note(c)}</div>' for c in dict.fromkeys(
        c for r in records for c in r["caveats"] if c.startswith(SHARED)))
    if len(records) == 1:
        return f'<div class="stt" style="margin-bottom:16px">{shared}</div>'
    stats = "".join(
        f'<div><div class="figure{"" if n else " zero"}">{n}</div>{verdict(v)}</div>'
        for v in VERDICTS for n in [sum(r["verdict"] == v for r in records)])
    return f'<div class="stt card"><div class="stats">{stats}</div>{claim_rows([(r, "") for r in records])}{shared}</div>'


db = get_db()
st.html(CSS)
with st.sidebar:
    st.html('<div class="stt brand">Stock Thesis Tracker</div>'
            '<div class="stt t2">Verdicts are computed from SEC filings in code, never by the model</div>')
    ticker = st.selectbox("Company", list(COMPANIES), format_func=lambda t: f"{t} · {COMPANIES[t]['name']}")
    cik = COMPANIES[ticker]["cik"]
    filings = db.execute("SELECT * FROM filings WHERE cik=? ORDER BY filed", (cik,)).fetchall()
    st.html(f'<div class="stt t2">Checks claims about revenue, GAAP operating margin and debt against the latest '
            f'four fiscal quarters. Not investment advice</div>'
            f'<h4 class="stt" style="font-size:12px;font-weight:400;color:var(--t3);margin:24px 0 8px">Run details</h4>'
            f'<div class="stt meta">CIK <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK={cik:010d}'
            f'&amp;type=10-" target="_blank" rel="noopener">{cik:010d}</a><br>'
            f'Planner {e("Claude " + model.MODEL if model.use_claude() else "rule-based, no API key")}<br>'
            f'Snapshot {ingest.snapshot_id(db)}</div>')

if not filings:
    st.info(f"No filings are loaded for {ticker} yet")
    if st.button("Load filings from SEC EDGAR", type="primary",
                 help="Loads every company and holds back each one's newest filing for the reassessment demo"):
        with st.spinner("Downloading XBRL facts from SEC EDGAR"):
            ingest.ingest_corpus(db, hold_back=True)
        st.rerun()
    st.stop()

tab_check, tab_history, tab_filings = st.tabs(["Check a thesis", "History", "Filings"])

with tab_check:
    thesis = st.text_area("Thesis", key="thesis", max_chars=model.MAX_THESIS_CHARS, height=96,
                          placeholder="Revenue is growing and margins are improving. Debt is falling",
                          help="Stored on this computer with the verdicts. Do not enter personal data")
    if st.button("Check thesis", type="primary", disabled=not thesis.strip()):
        with st.spinner("Checking against SEC filings"):
            st.session_state["last"] = (ticker, pipeline.run_thesis(db, ticker, thesis))
    last_ticker, records = st.session_state.get("last", (None, None))
    if last_ticker != ticker:
        st.html('<div class="stt t2" style="margin-top:8px">Or start from an example</div>')
        with st.container(horizontal=True):
            for example in EXAMPLES:
                st.button(example, on_click=st.session_state.__setitem__, args=("thesis", example))
        st.html('<div class="stt" style="margin-top:24px"><table class="tbl">' + "".join(
            f'<tr><td style="width:1%">{verdict(v)}</td><td class="t2">{e(why)}</td></tr>' for v, why in LEGEND)
            + "</table></div>")
    elif not records:
        st.warning("No checkable claim was found. Try a statement about revenue, operating margin or debt")
    else:
        st.html(summary(records) + "".join(
            card(r, f"Claim {i} of {len(records)}") for i, r in enumerate(records, 1)))

with tab_history:
    by_thesis = {}
    for r in pipeline.history(db, ticker):
        by_thesis.setdefault(r["thesis_id"], {}).setdefault(r["claim_id"], []).append(r)
    if not by_thesis:
        st.info("No stored claims for this company yet")
    for thesis_id, claims in reversed(by_thesis.items()):
        first = next(iter(claims.values()))[0]
        rows = [(versions[-1], f'<br><span class="meta">{e(" → ".join(v["verdict"] for v in versions))}</span>'
                 if len(versions) > 1 else "") for versions in claims.values()]
        st.html(f'<div class="stt card"><div class="row"><div class="claim" style="margin-top:0">“{e(first["thesis"])}”</div>'
                f'<span class="meta">{day(datetime.fromisoformat(first["run"]["evaluated_at"]).astimezone().date().isoformat())}</span></div>{claim_rows(rows)}</div>')
        left, right = st.columns([5, 1])
        with left.expander("Details"):
            st.html("".join(card(versions[-1]) for versions in claims.values()))
        if right.button("Delete thesis", key=f"del-{thesis_id}", width="stretch"):
            pipeline.delete_thesis(db, thesis_id)
            st.rerun()

with tab_filings:
    st.html('<div class="stt card"><table class="tbl"><tr><th>Form</th><th>Fiscal period</th><th>Period end</th>'
            '<th>Filed</th><th>Accession</th><th>Filing text</th></tr>' + "".join(
                f'<tr><td>{e(f["form"])}</td><td>{e(f"{f["fp"]} FY{f["fy"]}" if f["fy"] else "No XBRL facts")}</td>'
                f'<td>{day(f["report_date"])}</td><td>{day(f["filed"])}</td>'
                f'<td><a href="{e(f["url"])}" target="_blank" rel="noopener"><code style="color:inherit">{e(f["accession"])}</code></a></td>'
                f'<td class="t2">{"Indexed" if f["doc_sha256"] else "Not downloaded"}</td></tr>' for f in filings)
            + "</table>" + ("" if all(f["doc_sha256"] for f in filings) else
                            '<div class="note">Filing text is missing, so management commentary cannot be quoted. Set '
                            'SEC_USER_AGENT in .env (SEC requires a declared contact) and run <code>python -m stt ingest</code></div>')
            + "</div>")
    todo = ingest.pending(db, ticker)
    if todo:
        st.html(f'<div class="stt">Held back: the {e(todo[0]["form"])} for the period ending {day(todo[0]["report_date"])}, '
                f'filed {day(todo[0]["filed"])}</div>')
        if st.button("Ingest and re-check stored claims", type="primary"):
            with st.spinner("Ingesting the filing and re-running stored claims"):
                ingest.add_filing(db, ticker)
                st.session_state["changes"] = (ticker, pipeline.reassess(db, ticker))
            st.rerun()
    elif st.button("Re-check stored claims"):
        st.session_state["changes"] = (ticker, pipeline.reassess(db, ticker))
    changed_for, changes = st.session_state.get("changes", (None, []))
    if changed_for == ticker:
        if not changes:
            st.info("No stored claims to re-check. Check a thesis first, then ingest the held-back filing")
        else:
            cell = lambda r: f'{verdict(r["verdict"])}<br><span class="t2">{e(key_figure(r))} · {e(said(r))}</span>'
            st.html('<div class="stt card"><table class="tbl"><tr><th>Claim</th><th>Before</th><th>After</th></tr>' + "".join(
                f'<tr><td>{e(new["claim_text"])}{"<span class=changed>Verdict changed</span>" if old["verdict"] != new["verdict"] else ""}</td>'
                f'<td>{cell(old)}</td><td>{cell(new)}</td></tr>'
                for old, new in sorted(changes, key=lambda p: p[0]["verdict"] == p[1]["verdict"])) + "</table></div>")
