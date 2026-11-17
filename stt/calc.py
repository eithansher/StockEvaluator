"""Step 5: deterministic Decimal formulas (§1.3). Values are used as reported; rounding is display-only."""
import operator
from decimal import Decimal


class Gap(Exception):
    """An evidence gap: no calculation is made and the verdict is 'Lacking sufficient evidence'."""


def revenue_growth(rt, rb):
    if rb <= 0:
        raise Gap("base-period revenue is zero or negative, so growth is undefined")
    return (rt - rb) / rb


def operating_margin(oi, r):
    if r <= 0:
        raise Gap("revenue is zero or negative, so operating margin is undefined")
    return oi / r  # operating income may be negative


def margin_change_pp(mt, mb):
    return (mt - mb) * 100


def relative_margin_change(mt, mb):
    return (mt - mb) / mb if mb > 0 else None  # shown only when the base margin is > 0


def total_debt(parts):
    return sum(parts, Decimal(0))


def net_debt(debt, cash):
    return debt - cash


def debt_change(dt, db):
    return dt - db, ((dt - db) / db if db > 0 else None)


def q4_from_fy(fy, ytd9):
    return fy - ytd9  # Q4 = FY − nine-month YTD, always flagged "derived"


OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def meets(result, op, threshold):
    """Step 6: the only place a quantitative condition is decided."""
    return OPS[op](result, Decimal(str(threshold)))
