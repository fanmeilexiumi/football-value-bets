"""Turn model probabilities and market prices into value bets."""

from __future__ import annotations

import math

from . import config
from .odds import fair_probs

# market key -> (model prob key, avg odds column, best odds column, label)
SELECTIONS = {
    "H": ("H", "avg_h", "max_h", "主胜"),
    "D": ("D", "avg_d", "max_d", "平局"),
    "A": ("A", "avg_a", "max_a", "客胜"),
    "O25": ("O25", "avg_over", "max_over", "大 2.5"),
    "U25": ("U25", "avg_under", "max_under", "小 2.5"),
}


def market_probs(row) -> dict:
    """Margin-free market probabilities from the average price."""
    out = {}
    one = fair_probs([row["avg_h"], row["avg_d"], row["avg_a"]])
    if one:
        out.update(zip(("H", "D", "A"), one))
    ou = fair_probs([row["avg_over"], row["avg_under"]])
    if ou:
        out.update(zip(("O25", "U25"), ou))
    return out


def kelly_stake(p: float, odds: float) -> float:
    """Fractional Kelly share of bankroll, capped."""
    if odds <= 1:
        return 0.0
    f = (p * odds - 1) / (odds - 1)
    return max(0.0, min(config.MAX_STAKE, config.KELLY_FRACTION * f))


def find_value(row, probs: dict, price: str = "max") -> list[dict]:
    """Selections where the model beats the offered price by MIN_EDGE.

    price="max" uses the best price across bookmakers (what a bettor with
    several accounts gets); "avg" is the conservative market average.
    """
    bets = []
    mkt = market_probs(row)
    for sel, (pk, avg_col, max_col, label) in SELECTIONS.items():
        p = probs.get(pk)
        odds = (_price(row, max_col) if price == "max" else None) or _price(row, avg_col)
        if p is None or odds is None:
            continue
        edge = p * odds - 1
        if config.MIN_EDGE <= edge <= config.MAX_EDGE and p >= config.MIN_PROB and odds <= config.MAX_ODDS:
            bets.append({
                "selection": sel, "label": label, "prob": p, "odds": odds,
                "market_prob": mkt.get(sel), "edge": edge, "stake": kelly_stake(p, odds),
            })
    return bets


def _price(row, col: str) -> float | None:
    try:
        v = float(row.get(col))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None
