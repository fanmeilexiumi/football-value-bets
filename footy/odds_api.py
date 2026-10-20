"""Upcoming odds from The Odds API (https://the-odds-api.com).

Used when the THE_ODDS_API_KEY environment variable is set. The free plan
gives 500 credits a month; one run costs 2 credits per competition (two
markets, one region), so a daily run over the three leagues and the Champions
League uses about 240 a month.
"""

from __future__ import annotations

import logging
import math
import os

import numpy as np
import pandas as pd
import requests

from .data import COLUMNS
from .teams import canonical, cl_canonical_api

log = logging.getLogger(__name__)

API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
# Exchanges quote prices before commission, so their "best" odds are not really available.
EXCHANGES = {"betfair_ex_eu", "betfair_ex_uk", "matchbook", "smarkets", "betdaq"}

SPORT_KEYS = {"E0": "soccer_epl", "I1": "soccer_italy_serie_a", "SP1": "soccer_spain_la_liga"}
CL_SPORT_KEYS = {"CL": "soccer_uefa_champs_league"}


def fetch(known_teams: set[str], api_key: str | None = None, sports: dict[str, str] = SPORT_KEYS) -> pd.DataFrame:
    api_key = api_key or os.environ.get("THE_ODDS_API_KEY")
    if not api_key:
        return pd.DataFrame(columns=COLUMNS)
    events = []
    for league, sport in sports.items():
        try:
            r = requests.get(
                API_URL.format(sport=sport),
                params={"apiKey": api_key, "regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal"},
                timeout=30,
            )
            r.raise_for_status()
            log.info("The Odds API %s: %d events, %s credits left", sport, len(r.json()),
                     r.headers.get("x-requests-remaining"))
            events += [(league, e) for e in r.json()]
        except Exception as e:
            log.warning("The Odds API %s failed: %s", sport, e)
    return parse(events, known_teams)


def parse(events: list[tuple[str, dict]], known_teams: set[str]) -> pd.DataFrame:
    rows = []
    for league, ev in events:
        name = cl_canonical_api if league == "CL" else canonical
        home, away = ev.get("home_team"), ev.get("away_team")
        prices = {k: [] for k in ("h", "d", "a", "over", "under")}
        for bm in ev.get("bookmakers", []):
            if bm.get("key") in EXCHANGES:
                continue
            for m in bm.get("markets", []):
                for o in m.get("outcomes", []):
                    p = o.get("price")
                    if not isinstance(p, (int, float)) or p <= 1:
                        continue
                    if m.get("key") == "h2h":
                        key = "h" if o["name"] == home else "a" if o["name"] == away else "d" if o["name"] == "Draw" else None
                    elif m.get("key") == "totals" and o.get("point") == 2.5:
                        key = o["name"].lower() if o["name"] in ("Over", "Under") else None
                    else:
                        key = None
                    if key:
                        prices[key].append(float(p))
        when = pd.to_datetime(ev.get("commence_time"), errors="coerce", utc=True)
        if pd.isna(when):
            continue
        row = {
            "league": league, "date": when.tz_convert(None).normalize(), "kickoff": when.tz_convert(None),
            "home": name(home, known_teams), "away": name(away, known_teams),
            "hg": math.nan, "ag": math.nan,
        }
        for k, v in prices.items():
            row[f"avg_{k}"] = float(np.mean(v)) if v else math.nan
            row[f"max_{k}"] = float(np.max(v)) if v else math.nan
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)
