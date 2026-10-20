"""Live bookmaker odds (OddsPortal via OddsHarvester) and margin removal."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path

import goto_conversion
import numpy as np
import pandas as pd

from . import config
from .data import COLUMNS
from .teams import canonical

log = logging.getLogger(__name__)

MARKETS = "1x2,over_under_2_5"


def fair_probs(odds: list[float]) -> list[float] | None:
    """Bookmaker odds -> margin-free probabilities (goto_conversion).

    goto_conversion removes the margin in proportion to each price's standard
    error, which corrects the favourite-longshot bias better than simple
    normalisation.
    """
    if any(o is None or not np.isfinite(o) or o <= 1.0 for o in odds):
        return None
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # the library prints on imprudent odds
            return [float(p) for p in goto_conversion.goto_conversion(list(odds))]
    except Exception:
        total = sum(1 / o for o in odds)
        return [(1 / o) / total for o in odds]


def fetch_oddsportal(known_teams: set[str], timeout: int = 1500) -> pd.DataFrame:
    """Scrape upcoming 1X2 and O/U 2.5 odds for the configured leagues.

    Returns an empty frame on any failure: the pipeline then falls back to the
    football-data fixtures file.
    """
    leagues = ",".join(v["oddsportal"] for v in config.LEAGUES.values())
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "odds.json"
        cmd = [
            "oddsharvester", "upcoming", "-s", "football", "-l", leagues,
            "-m", MARKETS, "--headless", "-f", "json", "-o", str(out),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
            records = json.loads(out.read_text()) if out.exists() else []
        except FileNotFoundError:
            log.warning("oddsharvester is not installed; skipping live odds")
            return pd.DataFrame(columns=COLUMNS)
        except subprocess.CalledProcessError as e:
            log.warning("oddsharvester failed (exit %s):\n%s\n%s", e.returncode, (e.stdout or "")[-3000:], (e.stderr or "")[-6000:])
            return pd.DataFrame(columns=COLUMNS)
        except Exception as e:
            log.warning("oddsharvester failed: %s", e)
            return pd.DataFrame(columns=COLUMNS)
    return parse_oddsharvester(records, known_teams)


def _num(v) -> float:
    try:
        f = float(v)
        return f if f > 1.0 else math.nan
    except (TypeError, ValueError):
        return math.nan


def _summarise(rows: list[dict], keys: tuple[str, ...]) -> tuple[list[float], list[float]]:
    """Average and best price per outcome across bookmakers, skipping struck-through prices."""
    cols: list[list[float]] = [[] for _ in keys]
    for r in rows or []:
        blocked = set(r.get("blocked_outcomes") or [])
        for i, k in enumerate(keys):
            if k in blocked:
                continue
            v = _num(r.get(k))
            if np.isfinite(v):
                cols[i].append(v)
    avg = [float(np.mean(c)) if c else math.nan for c in cols]
    best = [float(np.max(c)) if c else math.nan for c in cols]
    return avg, best


_SLUG_TO_DIV = {v["oddsportal"]: k for k, v in config.LEAGUES.items()}


def parse_oddsharvester(records: list[dict], known_teams: set[str]) -> pd.DataFrame:
    out = []
    for rec in records:
        league = _SLUG_TO_DIV.get(str(rec.get("league", "")).lower())
        if league is None:
            # some versions report the league name rather than the slug
            name = str(rec.get("league", "")).lower()
            link = str(rec.get("match_link") or rec.get("match_url") or "")
            league = next((k for k, v in config.LEAGUES.items() if v["en"].lower() in name or v["oddsportal"] in link), None)
        when = pd.to_datetime(rec.get("match_date") or rec.get("kickoff"), errors="coerce", utc=True)
        if league is None or pd.isna(when):
            continue
        avg1x2, best1x2 = _summarise(rec.get("1x2_market"), ("1", "X", "2"))
        ou = rec.get("over_under_2_5_market") or rec.get("over_under_2.5_market")
        avg_ou, best_ou = _summarise(ou, ("odds_over", "odds_under"))
        out.append({
            "league": league,
            "date": when.tz_convert(None).normalize(), "kickoff": when.tz_convert(None),
            "home": canonical(rec.get("home_team", ""), known_teams),
            "away": canonical(rec.get("away_team", ""), known_teams),
            "hg": math.nan, "ag": math.nan,
            "avg_h": avg1x2[0], "avg_d": avg1x2[1], "avg_a": avg1x2[2],
            "max_h": best1x2[0], "max_d": best1x2[1], "max_a": best1x2[2],
            "avg_over": avg_ou[0], "avg_under": avg_ou[1],
            "max_over": best_ou[0], "max_under": best_ou[1],
        })
    return pd.DataFrame(out, columns=COLUMNS)


def merge_fixtures(*sources: tuple[str, pd.DataFrame]) -> pd.DataFrame:
    """Combine (label, frame) sources; earlier sources win where several have a match."""
    key = ["league", "home", "away"]
    merged = None
    for label, df in sources:
        if df is None or df.empty:
            continue
        df = df.assign(odds_source=label).drop_duplicates(subset=key).set_index(key)
        merged = df if merged is None else merged.combine_first(df)
    if merged is None:
        return pd.DataFrame(columns=COLUMNS + ["odds_source"])
    return merged.reset_index()[COLUMNS + ["odds_source"]].sort_values(["date", "league"]).reset_index(drop=True)
