import math

import numpy as np
import pandas as pd

from footy import config
from footy.backtest import _won
from footy.model import DixonColes, Elo, EloOutcome, add_pre_match_elo, result_code
from footy.odds import fair_probs, merge_fixtures, parse_oddsharvester
from footy.teams import canonical
from footy.value import find_value, kelly_stake


def _league(n_seasons=3, seed=0):
    """Synthetic round-robin league where team strength decides goals."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(10)]
    strength = {t: 0.1 * i for i, t in enumerate(teams)}
    rows, day = [], pd.Timestamp("2022-08-01")
    for _ in range(n_seasons * 2):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                lam_h = math.exp(0.2 + strength[h] - strength[a] * 0.5)
                lam_a = math.exp(strength[a] - strength[h] * 0.5)
                rows.append(dict(league="E0", date=day, home=h, away=a, hg=rng.poisson(lam_h), ag=rng.poisson(lam_a)))
                day += pd.Timedelta(hours=8)
    return pd.DataFrame(rows)


def test_canonical_names():
    assert canonical("Manchester United") == "Man United"
    assert canonical("Nottm Forest") == "Nott'm Forest"
    assert canonical("Atlético Madrid") == "Ath Madrid"
    assert canonical("Bournemouth AFC", {"Bournemouth", "Brentford"}) == "Bournemouth"


def test_fair_probs_sum_to_one_and_remove_margin():
    p = fair_probs([2.1, 3.4, 3.6])
    assert abs(sum(p) - 1) < 1e-9
    assert all(pi < 1 / o for pi, o in zip(p, [2.1, 3.4, 3.6]) if o < 3)  # favourite loses margin
    assert fair_probs([2.0, float("nan"), 3.0]) is None


def test_kelly_is_capped_and_never_negative():
    assert kelly_stake(0.3, 2.0) == 0.0
    assert kelly_stake(0.9, 2.0) == config.MAX_STAKE


def test_find_value_respects_thresholds():
    row = pd.Series(dict(avg_h=2.0, avg_d=3.5, avg_a=4.0, max_h=2.2, max_d=3.6, max_a=4.2,
                         avg_over=1.9, avg_under=1.9, max_over=2.0, max_under=2.0))
    probs = dict(H=0.52, D=0.25, A=0.23, O25=0.40, U25=0.60)
    bets = {b["selection"]: b for b in find_value(row, probs)}
    assert "H" in bets and abs(bets["H"]["edge"] - (0.52 * 2.2 - 1)) < 1e-9
    assert "U25" in bets
    assert "A" not in bets  # 0.23 * 4.2 - 1 < 5%
    huge = find_value(row, dict(H=0.9, D=0.05, A=0.05))
    assert all(b["selection"] != "H" for b in huge)  # edge above MAX_EDGE is treated as model error


def test_dixon_coles_ranks_stronger_team_higher():
    hist = _league()
    dc = DixonColes(hist, hist["date"].max() + pd.Timedelta(days=1))
    p = dc.predict("E0", "T9", "T0")
    assert p["H"] > p["A"]
    assert abs(p["H"] + p["D"] + p["A"] - 1) < 1e-6
    assert dc.predict("E0", "T9", "Unknown FC") is None


def test_elo_is_pre_match_and_outcome_model_is_monotonic():
    hist, elo = add_pre_match_elo(_league())
    assert hist["elo_diff"].iloc[0] == config.ELO_HOME_ADV  # nobody has played yet
    assert elo.get("E0", "T9") > elo.get("E0", "T0")
    eo = EloOutcome(hist["elo_diff"].to_numpy(), [result_code(h, a) for h, a in zip(hist["hg"], hist["ag"])])
    lo, hi = eo.predict(-200), eo.predict(200)
    assert hi["H"] > lo["H"] and hi["A"] < lo["A"]
    assert abs(sum(hi.values()) - 1) < 1e-9


def test_settlement():
    assert _won("H", 2, 1) and _won("D", 1, 1) and _won("A", 0, 3)
    assert _won("O25", 2, 1) and _won("U25", 1, 1) and not _won("U25", 2, 1)


def test_parse_oddsharvester_record():
    rec = {
        "league": "england-premier-league", "match_date": "2026-10-24 14:00:00 UTC",
        "home_team": "Manchester City", "away_team": "Tottenham Hotspur",
        "1x2_market": [
            {"bookmaker_name": "A", "1": "1.50", "X": "4.50", "2": "6.00"},
            {"bookmaker_name": "B", "1": "1.55", "X": "4.40", "2": "6.50", "blocked_outcomes": ["2"]},
        ],
        "over_under_2_5_market": [{"bookmaker_name": "A", "odds_over": "1.60", "odds_under": "2.30"}],
    }
    df = parse_oddsharvester([rec], {"Man City", "Tottenham"})
    r = df.iloc[0]
    assert (r["league"], r["home"], r["away"]) == ("E0", "Man City", "Tottenham")
    assert r["max_h"] == 1.55 and r["max_a"] == 6.00  # blocked 6.50 ignored
    assert abs(r["avg_h"] - 1.525) < 1e-9 and r["max_over"] == 1.60


def test_merge_prefers_live_odds():
    cols = ["league", "date", "home", "away", "avg_h"]
    fd = pd.DataFrame([["E0", pd.Timestamp("2026-10-24"), "A", "B", 2.0]], columns=cols)
    op = pd.DataFrame([["E0", pd.Timestamp("2026-10-24"), "A", "B", 2.1]], columns=cols)
    from footy.data import COLUMNS
    fd, op = fd.reindex(columns=COLUMNS), op.reindex(columns=COLUMNS)
    m = merge_fixtures(("OddsPortal", op), ("football-data", fd))
    assert len(m) == 1 and m.iloc[0]["avg_h"] == 2.1 and m.iloc[0]["odds_source"] == "OddsPortal"


def test_parse_odds_api_event():
    from footy.odds_api import parse
    ev = {
        "commence_time": "2026-10-24T14:00:00Z", "home_team": "Manchester City", "away_team": "Brighton and Hove Albion",
        "bookmakers": [
            {"markets": [
                {"key": "h2h", "outcomes": [{"name": "Manchester City", "price": 1.4}, {"name": "Brighton and Hove Albion", "price": 7.5}, {"name": "Draw", "price": 5.0}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": 1.6, "point": 2.5}, {"name": "Under", "price": 2.4, "point": 2.5},
                                                {"name": "Over", "price": 3.0, "point": 3.5}]},
            ]},
            {"key": "betfair_ex_eu", "markets": [{"key": "h2h", "outcomes": [{"name": "Manchester City", "price": 1.6}]}]},
            {"markets": [{"key": "h2h", "outcomes": [{"name": "Manchester City", "price": 1.45}, {"name": "Brighton and Hove Albion", "price": 7.0}, {"name": "Draw", "price": 5.2}]}]},
        ],
    }
    df = parse([("E0", ev)], {"Man City", "Brighton"})
    r = df.iloc[0]
    assert (r["home"], r["away"]) == ("Man City", "Brighton")
    assert r["max_h"] == 1.45 and r["max_a"] == 7.5 and abs(r["avg_d"] - 5.1) < 1e-9
    assert r["max_over"] == 1.6  # the 3.5 line is ignored


def test_find_longshots_tags_upsets_and_long_prices():
    from footy.value import find_longshots
    # market: strong home favourite; model rates the draw and away side higher
    row = pd.Series(dict(avg_h=1.40, avg_d=4.6, avg_a=8.0, max_h=1.45, max_d=4.8, max_a=8.5,
                         avg_over=1.9, avg_under=1.9, max_over=1.95, max_under=1.95))
    picks = {b["selection"]: b for b in find_longshots(row, {"H": 0.60, "D": 0.25, "A": 0.15})}
    assert set(picks) == {"D", "A"}
    assert "冷门" in picks["A"]["tags"] and "高赔" in picks["A"]["tags"]
    assert all(0 < b["stake"] <= config.LONG_MAX_STAKE for b in picks.values())
    # model agrees with the market: nothing
    assert find_longshots(row, {"H": 0.68, "D": 0.20, "A": 0.12}) == []


def test_likely_scores():
    from footy.model import likely_scores
    grid = np.zeros((7, 7))
    grid[1, 0], grid[1, 1], grid[0, 2], grid[2, 1] = 0.3, 0.25, 0.1, 0.2
    s = likely_scores(grid)
    assert s["top"][0][:2] == [1, 0] and s["D"][:2] == [1, 1] and s["A"][:2] == [0, 2]
