"""Probabilities and value bets for upcoming fixtures."""

from __future__ import annotations

import pandas as pd

from . import config
from .model import DixonColes, EloOutcome, add_pre_match_elo, blend, result_code
from .value import find_longshots, find_value, market_probs


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
            "date": r["date"].strftime("%Y-%m-%d"), "home": r["home"], "away": r["away"], **_beijing(r),
            "odds_source": r.get("odds_source", "football-data"),
            "elo_home": round(elo.get(r["league"], r["home"])), "elo_away": round(elo.get(r["league"], r["away"])),
            "odds": {k: _f(r.get(k)) for k in ("avg_h", "avg_d", "avg_a", "max_h", "max_d", "max_a",
                                                "avg_over", "avg_under", "max_over", "max_under")},
            "market": market_probs(r), "model": None, "bets": [], "longshots": [],
        }
        if p_dc is not None:
            p = blend(p_dc, eo.predict(elo.diff(r["league"], r["home"], r["away"])))
            row["model"] = {k: (v if k == "scores" else float(v)) for k, v in p.items()}
            row["bets"] = find_value(r, p, price="max")
            row["longshots"] = find_longshots(r, p, price="max")
        out.append(row)
    return out


def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def predict_cl(cl_model, fixtures: pd.DataFrame) -> list[dict]:
    """Champions League fixtures priced by the cross-league model (see ucl.py)."""
    out = []
    for _, r in fixtures.iterrows():
        rh, ra = cl_model.rating(r["home"]), cl_model.rating(r["away"])
        row = {
            "league": "CL", "league_name": config.CL["name"],
            "date": r["date"].strftime("%Y-%m-%d"), "home": r["home"], "away": r["away"], **_beijing(r),
            "odds_source": r.get("odds_source", "The Odds API"),
            "elo_home": None if rh is None else round(rh), "elo_away": None if ra is None else round(ra),
            "odds": {k: _f(r.get(k)) for k in ("avg_h", "avg_d", "avg_a", "max_h", "max_d", "max_a",
                                                "avg_over", "avg_under", "max_over", "max_under")},
            "market": market_probs(r), "model": None, "bets": [], "longshots": [],
        }
        p = cl_model.predict(r["home"], r["away"])
        if p is not None:
            row["model"] = {k: (v if k == "scores" else float(v)) for k, v in p.items()}
            row["bets"] = find_value(r, p, price="max")
            row["longshots"] = find_longshots(r, p, price="max")
        out.append(row)
    return out


def _beijing(r) -> dict:
    """Kick-off in Beijing time; falls back to the fixture date when the source gives no time."""
    k = pd.to_datetime(r.get("kickoff"), errors="coerce")
    if pd.isna(k):
        return {"bj_date": r["date"].strftime("%Y-%m-%d"), "bj_time": None}
    bj = k + pd.Timedelta(hours=8)
    return {"bj_date": bj.strftime("%Y-%m-%d"), "bj_time": bj.strftime("%H:%M")}
