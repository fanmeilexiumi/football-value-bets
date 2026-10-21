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

# Champions League: priced with one cross-league Elo, not per-league Dixon-Coles
CL = {"code": "CL", "name": "欧冠", "en": "Champions League"}
COUNTRY = {"E0": "ENG", "I1": "ITA", "SP1": "ESP"}  # league -> country tag used in the CL results
CL_RESULTS_URL = "https://raw.githubusercontent.com/openfootball/champions-league/master/{season}/cl.txt"
CL_ELO_K = 30.0  # European games move ratings more: they are the only link between leagues
CL_ELO_START = 1450.0  # clubs outside the three leagues; their country offset is fitted on top
CL_OFFSET_RIDGE = 1.0  # shrinks country offsets towards zero (units: 100 Elo points)
CL_MIN_MATCHES = 6  # rated games a club needs before we price it
CL_POISSON_SHARE = 0.5  # 1X2 = this share Elo-Poisson + the rest ordered logit

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

# Long odds and upsets (1X2 only): a separate, smaller-staked list
LONG_MIN_ODDS = 3.0
LONG_MAX_ODDS = 10.0
LONG_MIN_EDGE = 0.05
LONG_MAX_EDGE = 0.40  # long prices swing more, so allow a wider gap before calling it model error
UPSET_FAV_PROB = 0.60  # market favourite at least this likely to win
UPSET_MIN_GAP = 0.03  # model gives the upset at least 3 points more than the market
LONG_KELLY_FRACTION = 0.125
LONG_MAX_STAKE = 0.01

# 传统足彩 (China Sports Lottery 14-match and pick-9)
ZC_SOURCE_URL = "https://trade.500.com/sfc/"
ZC_COMPETITIONS = {"英超": "E0", "意甲": "I1", "西甲": "SP1", "欧冠": "CL"}
ZC_UPSET_SHRINK = 0.10  # games we do not model: pull market probabilities this far towards base rates
ZC_BASE_RATES = (0.45, 0.27, 0.28)  # home, draw, away in the three leagues
ZC_DOUBLE_MAX_P = 0.60  # only double a game whose top pick is below this
ZC_MAX_DOUBLES_14 = 2  # 4 bets = 8 yuan
ZC_MAX_DOUBLES_R9 = 1  # 2 bets = 4 yuan
ZC_UPSET_BONUS = 0.10  # log-score bonus for doubling on an outcome the model rates above the market

ZC_MARKET_SHARE = 0.0  # games we model: pull the model this far towards the market (tuned weekly)

# 推荐理由 (context.py): shown and journaled only, never used to move a probability
CONTEXT_HOURS = 48  # games with a pick kicking off this soon get context
API_FOOTBALL_BUDGET = 90  # of the free plan's 100 requests a day
CTX_WEIGHT = 1.0  # evening run: scale of the off-pitch shifts in adjust.py (tuned weekly; 0 switches them off)
EVENING_FROM_HOUR = 15  # runs starting at or after this Beijing hour are the evening update

# Journal and daily review (复盘)
JOURNAL_DIR = Path(os.environ.get("FOOTY_JOURNAL_DIR", ROOT / "journal"))  # committed back by the workflow
JOURNAL_KEEP_DAYS = 400
JOURNAL_VOID_DAYS = 6  # no result this long after kick-off: postponed or abandoned
REVIEW_UPSET_PROB = 0.30  # an upset: not the market favourite, and the market gave it less than this

# Weekly self-tuning (learn.py): a change must win on the held-out recent months
TUNE_WEEKDAY = 0  # Monday, Beijing time
TUNE_VALID_DAYS = 365
TUNE_MIN_GAIN = {"logloss": 0.001, "roi": 0.02, "hits": 0.03}

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
