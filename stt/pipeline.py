"""Steps 2-6 and 9: plan check, retrieve, validate, calculate, verdict rules, claim store, reassessment.
Everything here is deterministic; no model output can set a verdict."""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from . import calc, ingest, model
from .calc import Gap
from .definitions import ASSUMPTIONS, COMPANIES, FORMULAS, METRICS, TAGS, VERDICTS

SUPPORTED, CONTRADICTED, LACKING = VERDICTS
KINDS = {"quarter": (80, 100), "annual": (350, 380), "ytd9": (260, 290)}  # days; covers 52/53-week calendars
UNIT_SUFFIX = {"pct": "%", "pp": " pp", "usd": ""}


# ---------- display helpers (rounding happens only here) ----------
def usd(v):
    v = Decimal(v)
    for div, suffix in ((Decimal(10) ** 9, "B"), (Decimal(10) ** 6, "M")):
        if abs(v) >= div:
            return f"{'−' if v < 0 else ''}${abs(v) / div:,.2f}{suffix}"
    return f"{'−' if v < 0 else ''}${abs(v):,.0f}"


def show(v, unit, signed=False):
    if unit == "usd":
        return ("+" if signed and Decimal(v) > 0 else "") + usd(v)
    return f"{Decimal(v):{'+' if signed else ''},.2f}{UNIT_SUFFIX[unit]}".replace("-", "−")


def plabel(label):
    fy, fp = label
    return f"FY{fy}" if fp == "FY" else f"{fp} FY{fy}"


def condition_text(spec):
    c, name = spec["condition"], METRICS[spec["metric"]]["name"]
    what = name if spec["measure"] == "level" else {
        "revenue": "revenue growth", "operating_margin": "Δ operating margin"}.get(spec["metric"], f"Δ {name}")
    return f"{what} {c['op']} {show(c['threshold'], c['unit'])}"


# ---------- step 2: plan check ----------
def validate_plan(spec):
    """Reject metrics, formulas or units that are not in the definitions table. Returns a reason or None."""
    m = METRICS.get(spec.get("metric"))
    if m is None:
        return spec.get("out_of_scope_reason") or f"metric '{spec.get('metric')}' is not in the definitions table"
    if spec.get("measure") not in ("level", "change"):
        return "measure must be 'level' or 'change'"
    comparison, period, cond = spec.get("comparison"), spec.get("period", {}), spec.get("condition", {})
    if (spec["measure"] == "change") != (comparison in ("yoy", "qoq")) or comparison not in ("yoy", "qoq", "none"):
        return "a change claim needs a YoY or QoQ comparison; a level claim takes none"
    if period.get("fp") not in ("latest", "all", "Q1", "Q2", "Q3", "Q4", "FY"):
        return "period is not a fiscal quarter, fiscal year, 'latest' or 'all'"
    if period["fp"] == "FY" and (comparison == "qoq" or spec["metric"] in ("total_debt", "net_debt")):
        return "fiscal-year periods apply to YoY income-statement claims only; debt is a quarter-end value"
    if period["fp"] in ("Q1", "Q2", "Q3", "Q4", "FY") and not isinstance(period.get("fy"), int):
        return "an explicit period needs a fiscal year"
    if cond.get("op") not in calc.OPS or cond.get("unit") not in m[spec["measure"]][1]:
        return f"condition unit '{cond.get('unit')}' is not defined for {m['name']} ({spec['measure']})"
    try:
        Decimal(str(cond.get("threshold")))
    except ArithmeticError:
        return "condition threshold is not a number"
    return None


# ---------- periods: fiscal labels plus exact dates, never calendar quarters ----------
def classify(start, end):
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return next((k for k, (lo, hi) in KINDS.items() if lo <= days <= hi), "other")


def fiscal_calendar(db, ticker):
    """Map (fiscal year, period) -> exact dates, anchored on each filing's own fiscal-period focus.
    Returns (calendar, reporting periods of the ingested filings, oldest first)."""
    cik = COMPANIES[ticker]["cik"]
    anchors = db.execute("SELECT DISTINCT fy, fp, report_date FROM filings WHERE cik=? AND fy IS NOT NULL "
                         "ORDER BY report_date", (cik,)).fetchall()

    def label(end):
        for fy, fp, report_date in anchors:
            gap = (date.fromisoformat(report_date) - date.fromisoformat(end)).days
            years = round(gap / 365.25)
            if years >= 0 and abs(gap - years * 365.25) <= 12:
                return fy - years, fp
        return None

    names = {("quarter", "Q1"): "Q1", ("quarter", "Q2"): "Q2", ("quarter", "Q3"): "Q3", ("quarter", "FY"): "Q4",
             ("annual", "FY"): "FY", ("ytd9", "Q3"): "9M"}
    cal = {}
    for r in db.execute("SELECT DISTINCT pstart, pend FROM facts WHERE cik=? AND concept=? AND pstart IS NOT NULL",
                        (cik, TAGS[ticker]["revenue"])):
        lab, kind = label(r["pend"]), classify(r["pstart"], r["pend"])
        if lab and (kind, lab[1]) in names:
            cal[(lab[0], names[(kind, lab[1])])] = {"start": r["pstart"], "end": r["pend"], "kind": kind}
    for (fy, fp), p in list(cal.items()):  # Q4 = FY − nine-month YTD, when both share the fiscal-year start
        ytd = cal.get((fy, "9M"))
        if fp == "FY" and (fy, "Q4") not in cal and ytd and ytd["start"] == p["start"]:
            q4_start = (date.fromisoformat(ytd["end"]) + timedelta(days=1)).isoformat()
            cal[(fy, "Q4")] = {"start": q4_start, "end": p["end"], "kind": "derived_q4"}
    return cal, [(fy, "Q4" if fp == "FY" else fp) for fy, fp, _ in anchors]


def base_period(label, comparison):
    fy, fp = label
    if comparison == "yoy":
        return fy - 1, fp
    return (fy - 1, "Q4") if fp == "Q1" else (fy, f"Q{int(fp[1]) - 1}")


# ---------- steps 3-4: retrieve and validate one fact ----------
def validate_fact(row, kind, cik):
    """Identity, unit/currency and period-basis checks (§1.3). Raises Gap on any failure."""
    where = f"{row['concept']} ({row['pstart'] or 'at'} {row['pend']}, {row['accession']})"
    if row["cik"] != cik:
        raise Gap(f"{where} belongs to a different company")
    if row["unit"] != "USD":
        raise Gap(f"{where} is reported in {row['unit']}, not USD; currencies are never converted")
    if kind == "instant":
        if row["pstart"] is not None:
            raise Gap(f"{where} is a duration fact, not a period-end balance")
    elif row["pstart"] is None or classify(row["pstart"], row["pend"]) != kind:
        raise Gap(f"{where} is not a {kind} fact; year-to-date and other durations are rejected")


def get_fact(db, ticker, concept, start, end, kind, role, out):
    """Latest-filed value for one concept and exact period; flags revisions; appends the cited input."""
    cik = COMPANIES[ticker]["cik"]
    rows = db.execute(
        "SELECT f.*, fl.form, fl.filed, fl.url, fl.facts_sha256 FROM facts f JOIN filings fl USING(accession) "
        "WHERE f.cik=? AND concept=? AND pend=? AND pstart IS ? ORDER BY fl.filed DESC", (cik, concept, end, start),
    ).fetchall()
    if not rows:
        raise Gap(f"{concept} for {(start + ' to ') if start else ''}{end} is not in the corpus filings")
    row = rows[0]
    validate_fact(row, kind, cik)
    for old in rows[1:]:
        if Decimal(old["value"]) != Decimal(row["value"]):
            out["flags"].append(f"revised: {concept} for period ending {end} is {usd(row['value'])} in "
                                f"{row['accession']} (filed {row['filed']}, used) vs. {usd(old['value'])} in "
                                f"{old['accession']} (filed {old['filed']})")
            break
    out["inputs"].append({
        "role": role, "concept": concept, "value": row["value"], "unit": row["unit"], "currency": "USD",
        "decimals": None,  # not exposed by the companyfacts API
        "period_start": start, "period_end": end, "accession": row["accession"], "form": row["form"],
        "filed": row["filed"], "url": row["url"], "filing_facts_sha256": row["facts_sha256"]})
    return Decimal(row["value"])


def flow(db, ticker, cal, key, label, role, out):
    """Income-statement value for a fiscal period; derives Q4 = FY − 9M YTD (flagged)."""
    p = cal.get(label)
    if p is None:
        raise Gap(f"{plabel(label)} is not presented in the corpus filings (outside corpus)")
    concept, name = TAGS[ticker][key], key.replace("_", " ").capitalize()
    tag = f"{name}, {plabel(label)}"
    if p["kind"] != "derived_q4":
        return get_fact(db, ticker, concept, p["start"], p["end"], p["kind"], tag, out)
    fy, ytd = cal[(label[0], "FY")], cal[(label[0], "9M")]
    out["flags"].append(f"derived: {key.replace('_', ' ')} for {plabel(label)} = fiscal year − nine-month YTD")
    return calc.q4_from_fy(
        get_fact(db, ticker, concept, fy["start"], fy["end"], "annual", f"{name}, FY{label[0]}", out),
        get_fact(db, ticker, concept, ytd["start"], ytd["end"], "ytd9", f"{name}, first nine months of FY{label[0]}", out))


def period_dates(cal, metric, label):
    """Calendar entry for a fiscal label; debt is read at the period-end date (Q4 = fiscal year-end)."""
    if metric in ("total_debt", "net_debt") and label[1] == "Q4":
        return cal.get((label[0], "FY")) or cal.get(label)
    return cal.get(label)


def needed_text(spec):
    """The claim's condition in plain words, e.g. 'any increase' or 'a change above +10.00%'."""
    c, change = spec["condition"], spec["measure"] == "change"
    if change and Decimal(str(c["threshold"])) == 0:
        return {">": "any increase", ">=": "no decrease", "<": "any decrease", "<=": "no increase"}[c["op"]]
    words = {">": "above", ">=": "at least", "<": "below", "<=": "at most"}[c["op"]]
    return f"{'a change' if change else 'a value'} {words} {show(c['threshold'], c['unit'], change)}"


def _moved(name, delta, amount, how, up="rose"):
    return f"{name} was unchanged {how}" if delta == 0 else f"{name} {up if delta > 0 else 'fell'} {amount} {how}"


def metric_value(db, ticker, cal, metric, label, out):
    if metric == "revenue":
        return flow(db, ticker, cal, "revenue", label, "revenue", out)
    if metric == "operating_margin":
        revenue = flow(db, ticker, cal, "revenue", label, "revenue", out)
        return calc.operating_margin(flow(db, ticker, cal, "operating_income", label, "operating_income", out), revenue)
    p = period_dates(cal, metric, label)  # debt: point-in-time at period end
    if p is None:
        raise Gap(f"the {plabel(label)} period-end balance sheet is not in the corpus filings (outside corpus)")
    parts = []
    for concept, role, required in TAGS[ticker]["debt"]:
        try:
            parts.append(get_fact(db, ticker, concept, None, p["end"], "instant", f"{role.capitalize()}, {plabel(label)}", out))
        except Gap:
            if required:
                raise
            out["flags"].append(f"assumption: no {role} ({concept}) reported at {p['end']}; treated as 0")
    debt = calc.total_debt(parts)
    if metric == "net_debt":
        cash = get_fact(db, ticker, TAGS[ticker]["cash"], None, p["end"], "instant", f"Cash, {plabel(label)}", out)
        return calc.net_debt(debt, cash)
    return debt


# ---------- steps 5-6: calculate and apply the verdict rule ----------
def compare_one(db, ticker, cal, spec, current, base):
    metric, cond = spec["metric"], spec["condition"]
    name, margin = METRICS[metric]["name"], metric == "operating_margin"
    Name = name[0].upper() + name[1:]
    out = {"current": plabel(current), "base": plabel(base) if base else None, "inputs": [], "flags": [],
           "gaps": [], "result": None, "result_unit": cond["unit"], "met": None, "text": "", "headline": "",
           "sides": []}
    val = (lambda v: show(v * 100, "pct")) if margin else usd
    debt = metric in ("total_debt", "net_debt")
    how = "year over year" if spec["comparison"] == "yoy" else "from the prior quarter"

    def side(label):  # one period's value, with its dates and the inputs it was built from
        first, p = len(out["inputs"]), period_dates(cal, metric, label) or {}
        v = metric_value(db, ticker, cal, metric, label, out)
        out["sides"].append({"label": plabel(label), "start": None if debt else p.get("start"), "end": p.get("end"),
                             "value": val(v), "inputs": [first, len(out["inputs"])]})
        return v

    try:
        vt = side(current)
        if spec["measure"] == "level":
            result = vt * 100 if margin else vt
            out["text"] = f"{Name} for {plabel(current)} was {val(vt)}."
            out["headline"] = f"{Name} was {val(vt)} {'at the end of' if debt else 'in'} {plabel(current)}"
        else:
            vb = side(base)
            head = f"{Name} was {val(vt)} for {plabel(current)} vs. {val(vb)} for {plabel(base)}"
            if metric == "revenue":
                result = calc.revenue_growth(vt, vb) * 100
                out["text"] = f"{head}: growth {show(result, 'pct', True)}."
                out["headline"] = _moved(Name, result, show(abs(result), "pct"), how, "grew")
            elif margin:
                result, rel = calc.margin_change_pp(vt, vb), calc.relative_margin_change(vt, vb)
                out["text"] = f"{head}: change {show(result, 'pp', True)}" + (
                    f" (relative {show(rel * 100, 'pct', True)})." if rel is not None else ".")
                out["headline"] = _moved(Name, result, f"{abs(result):,.2f} percentage points", how)
            else:
                delta, pct = calc.debt_change(vt, vb)
                if cond["unit"] == "pct" and pct is None:
                    raise Gap("base-period debt is zero or negative, so a percentage change is undefined")
                result = pct * 100 if cond["unit"] == "pct" else delta
                out["text"] = f"{head}: change {show(delta, 'usd', True)}" + (
                    f" ({show(pct * 100, 'pct', True)})." if pct is not None else ".")
                out["headline"] = _moved(Name, delta, usd(abs(delta)), how)
        out["result"], out["met"] = str(result), calc.meets(result, cond["op"], cond["threshold"])
    except Gap as gap:
        out["gaps"].append(str(gap))
    return out


def evaluate(db, ticker, spec):
    """Steps 2-6 for one claim specification. Returns the deterministic part of the claim record."""
    rec = {"verdict": LACKING, "condition": None, "normalized_question": None, "formula_id": None, "formula": None,
           "comparisons": [], "gaps": [], "assumptions": []}
    problem = validate_plan(spec)
    if problem:
        rec["gaps"].append(f"Can't be checked: {problem}")
        return rec
    metric, comparison, fp = spec["metric"], spec["comparison"], spec["period"]["fp"]
    rec["formula_id"] = METRICS[metric][spec["measure"]][0]
    rec["formula"] = FORMULAS[rec["formula_id"]] + (
        f"; {FORMULAS['total_debt']}" if metric in ("total_debt", "net_debt") and rec["formula_id"] != "total_debt" else "")
    rec["condition"], rec["needed"] = condition_text(spec), needed_text(spec)
    assumed = set(spec.get("assumed", []))
    keys = [f"comparison_{comparison}" if "comparison" in assumed and comparison != "none" else None,
            "period" if "period" in assumed and fp == "latest" else None,
            "condition" if "condition" in assumed else None,
            "debt_definition" if "debt_definition" in assumed else None,
            "margin_definition" if "margin_definition" in assumed else None,
            "net_debt" if metric == "net_debt" else None, "all" if fp == "all" else None]
    rec["assumptions"] = [ASSUMPTIONS[k] for k in keys if k]

    cal, reported = fiscal_calendar(db, ticker)
    if not reported:
        rec["gaps"].append("No filings are ingested for this company.")
        return rec
    currents = [reported[-1]] if fp == "latest" else reported if fp == "all" else [(spec["period"]["fy"], fp)]
    pairs = [(c, base_period(c, comparison) if comparison != "none" else None) for c in currents]
    rec["comparisons"] = [compare_one(db, ticker, cal, spec, c, b) for c, b in pairs]
    periods = ("each corpus quarter" if fp == "all" else plabel(currents[0])) + (
        f" vs. {'the same period a year earlier' if comparison == 'yoy' else 'the prior quarter-end'}"
        if fp == "all" and comparison != "none" else f" vs. {plabel(pairs[0][1])}" if comparison != "none" else "")
    rec["normalized_question"] = (f"{COMPANIES[ticker]['name']}, consolidated GAAP figures, {periods}: "
                                  f"is {rec['condition']}?")
    met = [c["met"] for c in rec["comparisons"]]
    rec["verdict"] = CONTRADICTED if False in met else SUPPORTED if all(m is True for m in met) else LACKING
    rec["gaps"] = [g if len(pairs) == 1 else f"{c['current']}: {g}" for c in rec["comparisons"] for g in c["gaps"]]
    return rec


# ---------- step 9: claim record, store, reassessment ----------
def _finish(db, ticker, thesis, thesis_id, claim_id, spec, planner):
    """Run steps 3-9 for one planned claim and append the record to the claim store."""
    cik = COMPANIES[ticker]["cik"]
    rec = evaluate(db, ticker, spec)
    latest = db.execute("SELECT form, filed, accession FROM filings WHERE cik=? ORDER BY filed DESC", (cik,)).fetchone()
    caveats = list(dict.fromkeys(f for c in rec["comparisons"] for f in c["flags"]))
    if latest:
        caveats.append(f"Evidence through the {latest['form']} filed {latest['filed']} ({latest['accession']}); "
                       "later filings are not reflected")
    if ingest.pending(db, ticker):
        caveats.append("A newer filing is held back and not ingested yet, so this verdict may be stale")
    rec["explanation"] = " ".join(c["text"] for c in rec["comparisons"] if c["text"]) + (
        f" Condition ({rec['condition']}): {rec['verdict'].lower()}." if rec["condition"] else "")
    rec["passages"], model_caveats, rec["removed"] = model.assess(db, ticker, spec, rec)  # steps 7-8
    rec.update({
        "claim_id": claim_id, "thesis_id": thesis_id, "claim_text": spec.get("claim_text", ""), "spec": spec,
        "cik": cik, "company": COMPANIES[ticker]["name"], "ticker": ticker, "metric": spec.get("metric"),
        "comparison": spec.get("comparison"), "caveats": caveats + model_caveats,
        "run": {"model_id": planner["model_id"], "planner": planner["planner"],
                "prompt_version": model.PROMPT_VERSION, "corpus_snapshot_id": ingest.snapshot_id(db),
                "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}})
    db.execute("INSERT INTO claims(claim_id, thesis_id, cik, thesis, evaluated_at, verdict, record) VALUES(?,?,?,?,?,?,?)",
               (claim_id, thesis_id, cik, thesis, rec["run"]["evaluated_at"], rec["verdict"], json.dumps(rec)))
    db.commit()
    return rec


def run_thesis(db, ticker, thesis):
    """Steps 1-9: thesis text -> stored, cited claim records."""
    thesis = thesis.strip()[:model.MAX_THESIS_CHARS]
    specs, planner = model.plan(thesis, ticker)
    thesis_id = uuid.uuid4().hex[:8]
    return [_finish(db, ticker, thesis, thesis_id, f"{thesis_id}-{i}", spec, planner)
            for i, spec in enumerate(specs, 1)]


def history(db, ticker=None):
    """All stored records, oldest first (append-only, so each claim_id keeps its verdict history)."""
    sql = "SELECT seq, thesis, record FROM claims" + (" WHERE cik=?" if ticker else "") + " ORDER BY seq"
    return [{**json.loads(r["record"]), "seq": r["seq"], "thesis": r["thesis"]}
            for r in db.execute(sql, (COMPANIES[ticker]["cik"],) if ticker else ())]


def reassess(db, ticker):
    """Re-run the company's stored claims from their stored plan; return (old, new) record pairs."""
    latest = {r["claim_id"]: r for r in history(db, ticker)}
    planner = {"planner": "stored plan (reassessment)", "model_id": None}
    return [(old, _finish(db, ticker, old["thesis"], old["thesis_id"], cid, old["spec"],
                          {**planner, "model_id": old["run"]["model_id"]})) for cid, old in latest.items()]


def delete_thesis(db, thesis_id):
    db.execute("DELETE FROM claims WHERE thesis_id=?", (thesis_id,))
    db.commit()


def format_report(rec):
    """Fixed plain-text report template (CLI); every value carries its filing citation."""
    lines = [f"CLAIM {rec['claim_id']}: {rec['claim_text']}", f"  VERDICT: {rec['verdict']}"]
    if rec["condition"]:
        lines += [f"  Condition: {rec['condition']}", f"  Question: {rec['normalized_question']}",
                  f"  Formula: {rec['formula_id']}: {rec['formula']}"]
    lines += [f"  Assumption: {a}" for a in rec["assumptions"]]
    for c in rec["comparisons"]:
        lines.append(f"  - {c['text'] or c['current'] + ': no calculation'}")
        lines += [f"      {i['role']}: {usd(i['value'])} [{i['concept']}, {i['form']} {i['accession']}, "
                  f"filed {i['filed']}] {i['url']}" for i in c["inputs"]]
    lines += [f"  Evidence gap: {g}" for g in rec["gaps"]]
    lines += [f"  Management states ({p['form']} {p['accession']}, {p['section']}, chars {p['start']}-{p['end']}): "
              f"\"{p['quote']}\" [reported explanation, not a verified cause]" for p in rec["passages"]]
    lines += [f"  Caveat: {c}" for c in rec["caveats"]]
    run = rec["run"]
    lines.append(f"  Run: {run['planner']}, snapshot {run['corpus_snapshot_id']}, {run['evaluated_at']}")
    return "\n".join(lines)
