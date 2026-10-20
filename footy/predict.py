"""Probabilities and value bets for upcoming fixtures."""

from __future__ import annotations

import pandas as pd

from . import config
from .model import DixonColes, EloOutcome, add_pre_match_elo, blend, result_code
from .value import find_value, market_probs


def predict_fixtures(history: pd.DataFrame, fixtures: pd.DataFrame, as_of: pd.Timestamp) -> list[dict]:
    hist, elo = add_pre_match_elo(history)
    dc = DixonColes(hist, as_of)
    train = hist[hist["date"] >= as_of - pd.Timedelta(days=config.TRAIN_DAYS)]
    eo = EloOutcome(train["elo_diff"].to_numpy(), [result_code(h, a) for h, a in zip(train["hg"], train["ag"])])

    out = []
    for _, r in fixtures.iterrows():
        p_dc = dc.predict(r["league"], r["home"], r["away"])
        row = {
            "league": r["league"], "league_name": config.LEAGUES[r["league"]]["name"],
            "date": r["date"].strftime("%Y-%m-%d"), "home": r["home"], "away": r["away"],
            "odds_source": r.get("odds_source", "football-data"),
            "elo_home": round(elo.get(r["league"], r["home"])), "elo_away": round(elo.get(r["league"], r["away"])),
            "odds": {k: _f(r.get(k)) for k in ("avg_h", "avg_d", "avg_a", "max_h", "max_d", "max_a",
                                                "avg_over", "avg_under", "max_over", "max_under")},
            "market": market_probs(r), "model": None, "bets": [],
        }
        if p_dc is not None:
            p = blend(p_dc, eo.predict(elo.diff(r["league"], r["home"], r["away"])))
            row["model"] = {k: float(v) for k, v in p.items()}
            row["bets"] = find_value(r, p, price="max")
        out.append(row)
    return out


def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None
