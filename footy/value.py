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


def kelly_stake(p: float, odds: float, fraction: float = config.KELLY_FRACTION, cap: float = config.MAX_STAKE) -> float:
    """Fractional Kelly share of bankroll, capped."""
    if odds <= 1:
        return 0.0
    f = (p * odds - 1) / (odds - 1)
    return max(0.0, min(cap, fraction * f))


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


def find_longshots(row, probs: dict, price: str = "max") -> list[dict]:
    """1X2 picks at long odds: upsets against a clear favourite, and any long price the model rates higher.

    Tags: "冷门" when the market favourite is at least UPSET_FAV_PROB and the
    model gives the draw or the outsider UPSET_MIN_GAP more than the market;
    "高赔" when the price is in [LONG_MIN_ODDS, LONG_MAX_ODDS] with an edge of
    at least LONG_MIN_EDGE. Stakes are smaller than for value bets.
    """
    mkt = market_probs(row)
    if not all(k in mkt for k in ("H", "D", "A")):
        return []
    fav = "H" if mkt["H"] >= mkt["A"] else "A"
    has_fav = mkt[fav] >= config.UPSET_FAV_PROB
    picks = []
    for sel in ("H", "D", "A"):
        pk, avg_col, max_col, label = SELECTIONS[sel]
        p = probs.get(pk)
        odds = (_price(row, max_col) if price == "max" else None) or _price(row, avg_col)
        if p is None or odds is None or odds > config.LONG_MAX_ODDS:
            continue
        edge = p * odds - 1
        if edge > config.LONG_MAX_EDGE:
            continue
        tags = []
        if has_fav and sel != fav and p - mkt[sel] >= config.UPSET_MIN_GAP and edge > 0:
            tags.append("冷门")
        if odds >= config.LONG_MIN_ODDS and edge >= config.LONG_MIN_EDGE:
            tags.append("高赔")
        if tags:
            picks.append({
                "selection": sel, "label": label, "prob": p, "odds": odds, "market_prob": mkt[sel],
                "edge": edge, "tags": tags, "favourite": SELECTIONS[fav][3] if has_fav else None,
                "stake": kelly_stake(p, odds, config.LONG_KELLY_FRACTION, config.LONG_MAX_STAKE),
            })
    return picks


def _price(row, col: str) -> float | None:
    try:
        v = float(row.get(col))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None
