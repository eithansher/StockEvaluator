"""Team-written definitions table (proposal §1.2-1.3): companies, per-company XBRL tag map,
metrics, formulas and documented defaults. Step 2 rejects any plan that is not expressible here."""

COMPANIES = {
    "AAPL": {"cik": 320193, "name": "Apple Inc."},
    "NVDA": {"cik": 1045810, "name": "NVIDIA Corporation"},
    "TSLA": {"cik": 1318605, "name": "Tesla, Inc."},
    "MSFT": {"cik": 789019, "name": "Microsoft Corporation"},
    "META": {"cik": 1326801, "name": "Meta Platforms, Inc."},
}

_ASC606 = "RevenueFromContractWithCustomerExcludingAssessedTax"
_CASH = "CashAndCashEquivalentsAtCarryingValue"

# Per-company tag map, audited against SEC companyfacts on 2026-11-16.
# debt = [(concept, role, required)]. An optional component that a balance sheet does not
# report at a date is treated as 0 and the assumption is shown on the claim.
TAGS = {
    "AAPL": {"revenue": _ASC606, "operating_income": "OperatingIncomeLoss", "cash": _CASH, "debt": [
        ("CommercialPaper", "short-term borrowings", False),
        ("LongTermDebtCurrent", "current portion of long-term debt", False),
        ("LongTermDebtNoncurrent", "noncurrent long-term debt", True)]},
    "NVDA": {"revenue": "Revenues", "operating_income": "OperatingIncomeLoss", "cash": _CASH, "debt": [
        ("LongTermDebtCurrent", "current portion of long-term debt", False),
        ("LongTermDebtNoncurrent", "noncurrent long-term debt", True)]},
    # Tesla tags the current and long-term columns of its debt table (finance leases excluded)
    # as DebtCurrent / LongTermDebt; here LongTermDebt is the noncurrent portion only.
    "TSLA": {"revenue": "Revenues", "operating_income": "OperatingIncomeLoss", "cash": _CASH, "debt": [
        ("DebtCurrent", "current portion of debt", False),
        ("LongTermDebt", "noncurrent debt", True)]},
    "MSFT": {"revenue": _ASC606, "operating_income": "OperatingIncomeLoss", "cash": _CASH, "debt": [
        ("CommercialPaper", "short-term borrowings", False),
        ("LongTermDebtCurrent", "current portion of long-term debt", False),
        ("LongTermDebtNoncurrent", "noncurrent long-term debt", True)]},
    "META": {"revenue": _ASC606, "operating_income": "OperatingIncomeLoss", "cash": _CASH, "debt": [
        ("LongTermDebtCurrent", "current portion of long-term debt", False),
        ("LongTermDebtNoncurrent", "noncurrent long-term debt", True)]},
}

FORMULAS = {
    "reported_revenue": "R(t), as reported",
    "revenue_growth": "g = [R(t) − R(b)] ÷ R(b); valid only when R(b) > 0",
    "operating_margin": "m(t) = OI(t) ÷ R(t); valid only when R(t) > 0",
    "margin_change": "Δm = [m(t) − m(b)] × 100, in percentage points",
    "total_debt": "D = short-term borrowings + current portion of long-term debt + noncurrent long-term debt",
    "net_debt": "ND = D − cash and cash equivalents",
    "debt_change": "ΔD = D(t) − D(b); %ΔD = ΔD ÷ D(b) only when D(b) > 0",
}

# metric -> measure -> (formula id, units a condition may use)
METRICS = {
    "revenue": {"name": "revenue", "level": ("reported_revenue", ("usd",)), "change": ("revenue_growth", ("pct",))},
    "operating_margin": {"name": "GAAP operating margin", "level": ("operating_margin", ("pct",)), "change": ("margin_change", ("pp",))},
    "total_debt": {"name": "total debt", "level": ("total_debt", ("usd",)), "change": ("debt_change", ("usd", "pct"))},
    "net_debt": {"name": "net debt", "level": ("net_debt", ("usd",)), "change": ("debt_change", ("usd", "pct"))},
}

# Documented defaults (§1.2), printed as assumptions whenever the planner applied one.
ASSUMPTIONS = {
    "comparison_yoy": "Comparison not stated: year-over-year (income-statement default; seasonality distorts QoQ).",
    "comparison_qoq": "Comparison not stated: latest quarter-end vs. prior quarter-end (QoQ).",
    "period": "Period not stated: the latest fiscal quarter in the corpus.",
    "condition": "Threshold not stated: any change in the stated direction (vs. 0).",
    "debt_definition": "'Debt' read as total debt (carrying amounts; operating-lease liabilities excluded).",
    "margin_definition": "'Margin'/'profitability' read as consolidated GAAP operating margin.",
    "net_debt": "Net debt = total debt − cash and cash equivalents; marketable securities excluded.",
    "all": "'Consistently' read as: every corpus comparison meets the condition.",
}

VERDICTS = ("Supported", "Contradicted", "Lacking sufficient evidence")
