import math

import numpy as np
import pandas as pd

from footy import odds_api
from footy.teams import cl_canonical, cl_canonical_api
from footy.ucl import CL_COLUMNS, CLModel, parse_results

SAMPLE = """= UEFA Champions League 2025/26

▪ League, Matchday 1
  Tue Sep 16 2025
    18:45  Athletic Club (ESP)     v Arsenal FC (ENG)         0-2 (0-0)
    21:00  FC Bayern München (GER) v Chelsea FC (ENG)         3-1 (2-1)
  Wed Sep 17
           FK Bodø/Glimt (NOR)     v FC København (DEN)       2-2

▪ Finals, Final
  Sat May 30
    18:00  Paris Saint-Germain FC (FRA) v Arsenal FC (ENG)         4-3 pen. 1-1 a.e.t. (1-1, 0-1)
"""


def test_parse_results_names_dates_and_90_minute_score():
    domestic = {"ENG": {"Arsenal", "Chelsea"}, "ESP": {"Ath Bilbao"}}
    df = parse_results(SAMPLE, 2025, domestic)
    assert list(df["home"]) == ["Ath Bilbao", "Bayern Munich", "Bodo/Glimt", "Paris SG"]
    assert list(df["away"]) == ["Arsenal", "Chelsea", "Copenhagen", "Arsenal"]
    assert df["date"].iloc[2] == pd.Timestamp("2025-09-17")
    assert df["date"].iloc[3] == pd.Timestamp("2026-05-30")
    assert (df["hg"].iloc[3], df["ag"].iloc[3]) == (1, 1)  # penalties and extra time ignored
    assert list(df["neutral"]) == [False, False, False, True]


def test_bookmaker_names_map_to_rated_clubs():
    known = {"Bayern Munich", "Paris SG", "Inter", "Ath Madrid", "Sporting CP", "Celtic", "Celta"}
    assert cl_canonical_api("Bayern Munich", known) == "Bayern Munich"
    assert cl_canonical_api("Paris Saint Germain", known) == "Paris SG"
    assert cl_canonical_api("Inter Milan", known) == "Inter"
    assert cl_canonical_api("Atlético Madrid", known) == "Ath Madrid"
    assert cl_canonical_api("Sporting Lisbon", known) == "Sporting CP"
    assert cl_canonical_api("Celtic", known) == "Celtic"
    assert cl_canonical("Manchester City FC", {"Man City", "Man United"}) == "Man City"


def _world(seed=0):
    """Two domestic leagues of 6 clubs plus European games; league A is stronger."""
    rng = np.random.default_rng(seed)
    a = [f"A{i}" for i in range(6)]
    b = [f"B{i}" for i in range(6)]
    s = {t: 0.4 + 0.08 * i for i, t in enumerate(a)} | {t: 0.08 * i for i, t in enumerate(b)}
    dom, eu, day = [], [], pd.Timestamp("2022-08-01")
    for _ in range(6):
        for lg, teams in (("E0", a), ("I1", b)):
            for h in teams:
                for w in teams:
                    if h != w:
                        dom.append(dict(league=lg, date=day, home=h, away=w,
                                        hg=rng.poisson(math.exp(0.25 + s[h] - s[w])), ag=rng.poisson(math.exp(s[w] - s[h]))))
                        day += pd.Timedelta(hours=6)
        for h in a:
            for w in b:
                for x, y in ((h, w), (w, h)):
                    eu.append(dict(date=day, home=x, away=y, hg=rng.poisson(math.exp(0.25 + s[x] - s[y])),
                                   ag=rng.poisson(math.exp(s[y] - s[x])), home_cc="ENG" if x in a else "ITA",
                                   away_cc="ENG" if y in a else "ITA", neutral=False))
                    day += pd.Timedelta(hours=6)
    return pd.DataFrame(dom), pd.DataFrame(eu, columns=CL_COLUMNS), day


def test_cross_league_model_sees_the_stronger_league():
    dom, eu, end = _world()
    m = CLModel(dom, eu, end)
    assert m.offset["ENG"] > m.offset["ITA"]
    p = m.predict("A0", "B0", neutral=True)  # equal domestic rank, stronger league
    assert p["H"] > p["A"]
    assert abs(p["H"] + p["D"] + p["A"] - 1) < 1e-9
    assert abs(p["O25"] + p["U25"] - 1) < 1e-9
    assert m.predict("A0", "Nobody") is None


def test_parse_odds_api_champions_league_names():
    ev = {"home_team": "Inter Milan", "away_team": "Bayern Munich", "commence_time": "2026-10-21T19:00:00Z",
          "bookmakers": [{"key": "x", "markets": [{"key": "h2h", "outcomes": [
              {"name": "Inter Milan", "price": 2.8}, {"name": "Bayern Munich", "price": 2.5}, {"name": "Draw", "price": 3.4}]}]}]}
    df = odds_api.parse([("CL", ev)], {"Inter", "Bayern Munich"})
    assert (df["home"].iloc[0], df["away"].iloc[0], df["league"].iloc[0]) == ("Inter", "Bayern Munich", "CL")
    assert df["max_h"].iloc[0] == 2.8
