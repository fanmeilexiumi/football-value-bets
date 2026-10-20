"""Historical results, upcoming fixtures and their bookmaker odds.

Primary source is football-data.co.uk (results since 1993, average and best
odds from ~40 bookmakers). If it cannot be reached, the xgabora Club Football
Match Data mirror on GitHub is used instead; it is built from the same files.
"""

from __future__ import annotations

import io
import logging
from datetime import date

import pandas as pd
import requests

from . import config
from .teams import canonical

log = logging.getLogger(__name__)

FD_SEASON_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
FD_FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
MIRROR_URL = (
    "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data-2000-2025/HEAD/data/Matches.csv"
)

COLUMNS = [
    "league", "date", "home", "away", "hg", "ag",
    "avg_h", "avg_d", "avg_a", "max_h", "max_d", "max_a",
    "avg_over", "avg_under", "max_over", "max_under",
]

_FD_MAP = {
    "Div": "league", "HomeTeam": "home", "AwayTeam": "away", "FTHG": "hg", "FTAG": "ag",
    "AvgH": "avg_h", "AvgD": "avg_d", "AvgA": "avg_a",
    "MaxH": "max_h", "MaxD": "max_d", "MaxA": "max_a",
    "Avg>2.5": "avg_over", "Avg<2.5": "avg_under",
    "Max>2.5": "max_over", "Max<2.5": "max_under",
}

_MIRROR_MAP = {
    "Division": "league", "HomeTeam": "home", "AwayTeam": "away", "FTHome": "hg", "FTAway": "ag",
    "OddHome": "avg_h", "OddDraw": "avg_d", "OddAway": "avg_a",
    "MaxHome": "max_h", "MaxDraw": "max_d", "MaxAway": "max_a",
    "Over25": "avg_over", "Under25": "avg_under",
    "MaxOver25": "max_over", "MaxUnder25": "max_under",
}

LAST_HISTORY_SOURCE = "football-data.co.uk"

_session = requests.Session()
_session.headers["User-Agent"] = "footy-value/1.0 (+https://github.com)"


def _get(url: str, timeout: int = 60) -> bytes:
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def _read_fd_csv(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    df = df.rename(columns=_FD_MAP).copy()
    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return _tidy(df)


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLUMNS].copy()
    df = df.dropna(subset=["date", "home", "away"])
    df["home"] = df["home"].map(canonical)
    df["away"] = df["away"].map(canonical)
    for c in COLUMNS[4:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def load_history(today: date | None = None) -> pd.DataFrame:
    """Finished matches for the configured leagues, last N seasons plus current."""
    today = today or date.today()
    seasons = config.season_codes(today)
    frames, missing = [], []
    for s in seasons:
        for div in config.LEAGUES:
            try:
                frames.append(_read_fd_csv(_get(FD_SEASON_URL.format(season=s, div=div))))
            except Exception as e:  # network, 404 before a season starts, parse error
                log.warning("football-data %s/%s unavailable: %s", s, div, e)
                missing.append((s, div))
    global LAST_HISTORY_SOURCE
    LAST_HISTORY_SOURCE = "football-data.co.uk"
    if missing:
        LAST_HISTORY_SOURCE = "GitHub 镜像" if len(missing) == len(seasons) * len(config.LEAGUES) else "football-data.co.uk + GitHub 镜像"
        log.warning("filling %d season files from the GitHub mirror", len(missing))
        frames.append(_mirror_history(today))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    df = df[df["league"].isin(config.LEAGUES)]
    df = df.dropna(subset=["hg", "ag"])
    first = pd.Timestamp(f"20{seasons[0][:2]}-07-01")
    df = df[(df["date"] >= first) & (df["date"] <= pd.Timestamp(today))]
    df = df.drop_duplicates(subset=["league", "date", "home", "away"], keep="first")
    df[["hg", "ag"]] = df[["hg", "ag"]].astype(int)
    return df.sort_values("date").reset_index(drop=True)


def _mirror_history(today: date) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(_get(MIRROR_URL, timeout=180)), low_memory=False)
    df = df[df["Division"].isin(config.LEAGUES)].rename(columns=_MIRROR_MAP)
    df["date"] = pd.to_datetime(df["MatchDate"], errors="coerce")
    return _tidy(df)


def load_fixtures(today: date | None = None, days_ahead: int = 10) -> pd.DataFrame:
    """Upcoming matches with pre-match odds from football-data.co.uk.

    football-data publishes this file a few days before each round, so it may
    be empty or stale midweek; OddsPortal odds (see odds.py) fill that gap.
    """
    today = today or date.today()
    try:
        df = _read_fd_csv(_get(FD_FIXTURES_URL))
    except Exception as e:
        log.warning("football-data fixtures unavailable: %s", e)
        return pd.DataFrame(columns=COLUMNS)
    log.info("fixtures file: %d rows, %d in our leagues, dates %s to %s", len(df),
             df["league"].isin(config.LEAGUES).sum(), df["date"].min(), df["date"].max())
    df = df[df["league"].isin(config.LEAGUES)]
    lo, hi = pd.Timestamp(today), pd.Timestamp(today) + pd.Timedelta(days=days_ahead)
    return df[(df["date"] >= lo) & (df["date"] <= hi)].reset_index(drop=True)
