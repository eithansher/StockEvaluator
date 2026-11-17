# Stock Thesis Tracker

Checks the claims in an investment thesis ("revenue is growing", "debt is falling") against SEC filings and
returns **Supported**, **Contradicted** or **Lacking sufficient evidence**, with the values, formula,
assumptions and filing citations behind each verdict. Code decides every verdict; the model only interprets
language. Scope: AAPL, NVDA, TSLA, MSFT, META; revenue, GAAP operating margin, total and net debt; the four
latest fiscal quarters of each company. Not investment advice.

## Run it

```bash
pip install -r requirements.txt
python -m stt ingest --hold-back
streamlit run app.py
```

`ingest --hold-back` freezes the corpus in `data/corpus.json` and loads everything except each company's
newest filing, which is the demo setup: check a thesis, then open **Filings** and ingest
the held-back filing to see stored claims re-run and verdicts change. `python -m stt ingest` loads the full
corpus.

Copy `.env.example` to `.env` to enable two optional things:

- `SEC_USER_AGENT` (name and contact email). SEC serves XBRL facts without it but refuses filing documents,
  so without it verdicts work and management-commentary quotes are skipped.
- `ANTHROPIC_API_KEY`. With it, Claude (`claude-sonnet-5-5`) splits the thesis into claim specifications and
  writes caveats. Without it, a rule-based planner handles the documented default phrasings.

Command line (the UI fallback):

```bash
python -m stt assess AAPL "Revenue is growing and margins are improving. Debt is falling."
python -m stt add-filing AAPL
python -m stt history AAPL
python -m pytest tests -q
python evaluate.py eval/gold_seed.jsonl
```

`evaluate.py` runs each gold thesis through the normal pipeline, so its claims are stored in the claim
store like any other; the seed answers assume the full corpus (`python -m stt ingest`).

## Where each step lives

| Proposal step | Code |
|---|---|
| 1 Decompose, 2 Plan (model) | `stt/model.py` `plan` (Claude, schema-constrained) or `rule_plan` |
| 2 Plan check against the definitions table | `stt/pipeline.py` `validate_plan`, `stt/definitions.py` |
| 3 Retrieve | `pipeline.get_fact` (SQL), `model.retrieve` (FTS5 BM25) |
| 4 Validate | `pipeline.validate_fact`, `fiscal_calendar`, `classify` |
| 5 Calculate | `stt/calc.py` (Decimal) |
| 6 Verdict rules | `calc.meets`, `pipeline.evaluate` |
| 7 Assess (model) | `model.assess` |
| 8 Citation check | `model.check_citations` |
| 9 Report, claim store, reassessment | `pipeline.run_thesis`, `reassess`, `app.py`, `stt/__main__.py` |
| Ingestion, tag map, snapshot ID | `stt/ingest.py`, `definitions.TAGS` |
| Evaluation harness and baseline | `evaluate.py` |

Everything is one Python process and one SQLite file (`data/stt.sqlite`); raw SEC responses are cached under
`data/raw` and `data/filings`. Model calls and citation-check removals are logged to `data/runs.jsonl`.

## Differences from the proposal

- **edgartools is not used.** Facts come from the SEC `companyfacts` API (the proposal's stated fallback),
  which only carries consolidated, non-dimensional facts. Filing text is split with a small regex splitter.
  `companyfacts` does not expose XBRL `decimals`, so that record field is null.
- **Structured outputs instead of forced tool use.** `claude-sonnet-5-5` rejects forced `tool_choice`;
  `output_config.format` gives the same schema guarantee for the plan JSON.
- **No clarifying questions yet.** The planner always applies a documented default and shows it as an
  assumption.
- **Tesla's debt tags** (`DebtCurrent` + `LongTermDebt`) exclude finance leases; see `definitions.TAGS`.
- **`eval/gold_seed.jsonl` is a 13-claim seed**, hand-derived from raw SEC values to exercise the scorer. It is
  not the frozen 60-claim, two-annotator gold set; Cohen's kappa, Wilson intervals, the McNemar test and
  human citation judging are not implemented.
- Automatic filing monitoring, segment and non-GAAP claims, and embedding retrieval remain stretch goals.
