"""Walk-forward backtest: refit every Monday, predict that week, settle bets.

Each week only uses matches played before it, so no result leaks into its own
prediction. Bets are settled at the pre-match average price (conservative) and
at the best price across bookmakers (optimistic) so both bounds are visible.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from . import config, zucai
from .model import DixonColes, EloOutcome, OUTCOMES, add_pre_match_elo, blend, result_code
from .value import find_longshots, find_value, market_probs

log = logging.getLogger(__name__)


def _won(sel: str, hg: int, ag: int) -> bool:
    return {
        "H": hg > ag, "D": hg == ag, "A": hg < ag,
        "O25": hg + ag > 2, "U25": hg + ag < 3,
    }[sel]


def run(history: pd.DataFrame, today: date | None = None) -> dict:
    today = today or date.today()
    start_year = (today.year if today.month >= 7 else today.year - 1) - config.BACKTEST_SEASONS
    start = pd.Timestamp(f"{start_year}-07-01")
    hist, _ = add_pre_match_elo(history)
    test = hist[hist["date"] >= start]

    preds, bets, longs = [], {"avg": [], "max": []}, {"avg": [], "max": []}
    for week in pd.date_range(start, pd.Timestamp(today), freq="W-MON"):
        games = test[(test["date"] >= week) & (test["date"] < week + pd.Timedelta(days=7))]
        if games.empty:
            continue
        dc = DixonColes(hist, week)
        train = hist[(hist["date"] < week) & (hist["date"] >= week - pd.Timedelta(days=config.TRAIN_DAYS))]
        eo = EloOutcome(train["elo_diff"].to_numpy(), [result_code(h, a) for h, a in zip(train["hg"], train["ag"])])
        for _, r in games.iterrows():
            p_dc = dc.predict(r["league"], r["home"], r["away"])
            if p_dc is None:
                continue
            p_elo = eo.predict(r["elo_diff"])
            p = blend(p_dc, p_elo)
            res = result_code(r["hg"], r["ag"])
            preds.append({
                "date": r["date"], "league": r["league"], "res": 2 - res,  # index into OUTCOMES (H, D, A)
                "over": int(r["hg"] + r["ag"] > 2),
                "dc": [p_dc[k] for k in OUTCOMES], "elo": [p_elo[k] for k in OUTCOMES],
                "blend": [p[k] for k in OUTCOMES], "mkt": [market_probs(r).get(k) for k in OUTCOMES],
                "dc_over": p_dc["O25"], "mkt_over": market_probs(r).get("O25"),
            })
            for price in ("avg", "max"):
                for b in find_value(r, p, price=price):
                    b.update(date=r["date"], league=r["league"], won=_won(b["selection"], r["hg"], r["ag"]))
                    bets[price].append(b)
                for b in find_longshots(r, p, price=price):
                    b.update(date=r["date"], league=r["league"], won=_won(b["selection"], r["hg"], r["ag"]))
                    longs[price].append(b)
    out = summarise(preds, bets, start)
    if preds:
        out["long"] = summarise_long(longs)
        out["zucai"] = zucai.simulate(preds)
    return out


def _logloss(probs: list[list[float]], res: list[int]) -> float:
    p = np.array([pr[y] for pr, y in zip(probs, res)], dtype=float)
    return float(-np.mean(np.log(np.clip(p, 1e-12, 1))))


def _bet_stats(bets: list[dict]) -> dict:
    if not bets:
        return {"n": 0, "hit": None, "roi": None, "profit": 0.0, "avg_odds": None, "avg_edge": None}
    won = np.array([b["won"] for b in bets])
    odds = np.array([b["odds"] for b in bets])
    profit = float(np.sum(np.where(won, odds - 1, -1)))
    return {
        "n": len(bets), "hit": float(won.mean()), "roi": profit / len(bets), "profit": profit,
        "avg_odds": float(odds.mean()), "avg_edge": float(np.mean([b["edge"] for b in bets])),
    }


def _bankroll(bets: list[dict], start: float = 100.0) -> list[tuple[str, float]]:
    """Fractional-Kelly bankroll, stakes sized on the bankroll at the start of each day."""
    bank, curve = start, []
    for day, group in pd.DataFrame(bets).groupby("date", sort=True):
        base = bank
        for b in group.itertuples():
            stake = base * b.stake
            bank += stake * (b.odds - 1) if b.won else -stake
        curve.append((day.strftime("%Y-%m-%d"), round(bank, 2)))
    return curve


def summarise(preds: list[dict], bets: dict[str, list[dict]], start: pd.Timestamp) -> dict:
    if not preds:
        return {"n_matches": 0}
    res = [p["res"] for p in preds]
    with_mkt = [i for i, p in enumerate(preds) if None not in p["mkt"]]
    ll = {k: _logloss([preds[i][k] for i in with_mkt], [res[i] for i in with_mkt]) for k in ("dc", "elo", "blend", "mkt")}
    ou_idx = [i for i, p in enumerate(preds) if p["mkt_over"] is not None]
    ou_res = [preds[i]["over"] for i in ou_idx]
    ll_ou = {
        "dc": _logloss([[1 - preds[i]["dc_over"], preds[i]["dc_over"]] for i in ou_idx], ou_res),
        "mkt": _logloss([[1 - preds[i]["mkt_over"], preds[i]["mkt_over"]] for i in ou_idx], ou_res),
    }
    out = {
        "start": start.strftime("%Y-%m-%d"),
        "end": max(p["date"] for p in preds).strftime("%Y-%m-%d"),
        "n_matches": len(preds),
        "logloss_1x2": ll, "logloss_ou": ll_ou, "n_scored": len(with_mkt),
        "bets": {},
    }
    for price, bl in bets.items():
        df = pd.DataFrame(bl)
        block = {"all": _bet_stats(bl), "by_league": {}, "by_market": {}, "by_season": {}}
        if not df.empty:
            for lg, g in df.groupby("league"):
                block["by_league"][lg] = _bet_stats(g.to_dict("records"))
            df["market"] = df["selection"].map(lambda s: "1X2" if s in OUTCOMES else "O/U 2.5")
            for mk, g in df.groupby("market"):
                block["by_market"][mk] = _bet_stats(g.to_dict("records"))
            df["season"] = df["date"].map(lambda d: f"{d.year if d.month >= 7 else d.year - 1}/{(d.year + 1 if d.month >= 7 else d.year) % 100:02d}")
            for s, g in df.groupby("season"):
                block["by_season"][s] = _bet_stats(g.to_dict("records"))
            block["bankroll"] = _bankroll(bl)
        out["bets"][price] = block
    return out


def summarise_long(longs: dict[str, list[dict]]) -> dict:
    """Long-odds and upset picks, split by tag, league and season."""
    out = {}
    for price, bl in longs.items():
        block = {"all": _bet_stats(bl), "rows": []}
        if bl:
            df = pd.DataFrame(bl)
            for tag in ("冷门", "高赔"):
                block["rows"].append((f"{tag}标签", _bet_stats([b for b in bl if tag in b["tags"]])))
            for sel, g in df.groupby("label"):
                block["rows"].append((sel, _bet_stats(g.to_dict("records"))))
            for lg, g in df.groupby("league"):
                block["rows"].append((config.LEAGUES[lg]["name"], _bet_stats(g.to_dict("records"))))
            df["season"] = df["date"].map(lambda d: f"{d.year if d.month >= 7 else d.year - 1}/{(d.year + 1 if d.month >= 7 else d.year) % 100:02d}")
            for se, g in df.groupby("season"):
                block["rows"].append((f"{se} 赛季", _bet_stats(g.to_dict("records"))))
            block["bankroll"] = _bankroll(bl)
        out[price] = block
    return out
