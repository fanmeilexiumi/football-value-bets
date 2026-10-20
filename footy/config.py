"""Project-wide settings. Edit here rather than scattering constants around."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FOOTY_DATA_DIR", ROOT / "data"))
SITE_DIR = Path(os.environ.get("FOOTY_SITE_DIR", ROOT / "site"))

# football-data.co.uk division code -> display name and OddsPortal league slug
LEAGUES: dict[str, dict[str, str]] = {
    "E0": {"name": "英超", "en": "Premier League", "oddsportal": "england-premier-league"},
    "I1": {"name": "意甲", "en": "Serie A", "oddsportal": "italy-serie-a"},
    "SP1": {"name": "西甲", "en": "La Liga", "oddsportal": "spain-laliga"},
}

HISTORY_SEASONS = 5  # completed seasons kept, plus the current one

# Dixon-Coles
DC_XI = 0.0018  # time-decay per day; ~0.0018 is the value Dixon & Coles found for weekly data
TRAIN_DAYS = 3 * 365  # matches older than this are dropped from each fit
MIN_TEAM_MATCHES = 10  # promoted sides need this many league games in the window before we price them

# Elo
ELO_K = 20.0
ELO_HOME_ADV = 60.0
ELO_NEW_TEAM = 1450.0  # promoted sides start below the league average of 1500
ELO_BLEND = 0.25  # share of the Elo model in the final 1X2 probability

# Value-bet rules
MIN_EDGE = 0.05  # model_prob * odds - 1 must reach this
MIN_PROB = 0.20  # skip long shots the model cannot price reliably
MAX_ODDS = 6.0
MAX_EDGE = 0.25  # larger gaps to the market are almost always model error, not value
KELLY_FRACTION = 0.25
MAX_STAKE = 0.02  # share of bankroll per bet

# Backtest
BACKTEST_SEASONS = 2  # completed seasons replayed, plus the current one so far


def season_code(d: date) -> str:
    """football-data.co.uk season folder, e.g. 2026-10-20 -> '2627'."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def season_codes(today: date, n_completed: int = HISTORY_SEASONS) -> list[str]:
    start = today.year if today.month >= 7 else today.year - 1
    years = range(start - n_completed, start + 1)
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in years]
