"""Champions League: results, a cross-league Elo and its match probabilities.

Domestic Dixon-Coles models cannot compare clubs from different leagues, so
Champions League games use one Elo run over the three leagues plus every
European game. European games are the only link between leagues, so on top of
the ratings we fit one strength offset per country on those games.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize
from scipy.stats import poisson

from . import config
from .model import _sig, result_code
from .teams import cl_canonical

log = logging.getLogger(__name__)

CL_COLUMNS = ["date", "home", "away", "hg", "ag", "home_cc", "away_cc", "neutral"]

_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_DAY = re.compile(r"^\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) (\d{1,2})(?: (\d{4}))?\s*$")
_MATCH = re.compile(r"^\s+(?:\d{1,2}:\d{2}\s+)?(.+?) \((\w{3})\)\s+v\s+(.+?) \((\w{3})\)\s+(\d+)-(\d+)(.*)$")
_PAREN = re.compile(r"\((\d+)-(\d+)")

LAST_CL_SEASONS: list[str] = []


def cl_seasons(today: date, n_completed: int = config.HISTORY_SEASONS) -> list[str]:
    start = today.year if today.month >= 7 else today.year - 1
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(start - n_completed, start + 1)]


def parse_results(text: str, start_year: int, domestic: dict[str, set[str]]) -> pd.DataFrame:
    """openfootball Football.TXT -> one row per played game, 90-minute score."""
    rows, day, stage = [], None, ""
    for line in text.splitlines():
        if line.startswith("▪"):
            stage = line[1:].strip()
            continue
        m = _DAY.match(line)
        if m:
            mon = _MONTHS[m[1]]
            year = int(m[3]) if m[3] else (start_year if mon >= 7 else start_year + 1)
            day = pd.Timestamp(year, mon, int(m[2]))
            continue
        m = _MATCH.match(line)
        if not m or day is None:
            continue
        hg, ag, rest = int(m[5]), int(m[6]), m[7]
        if "a.e.t" in rest:  # extra time: the first bracket holds the 90-minute score
            p = _PAREN.search(rest)
            if not p:
                continue
            hg, ag = int(p[1]), int(p[2])
        rows.append({
            "date": day,
            "home": cl_canonical(m[1], domestic.get(m[2])), "away": cl_canonical(m[3], domestic.get(m[4])),
            "hg": hg, "ag": ag, "home_cc": m[2], "away_cc": m[4],
            "neutral": stage.endswith("Final") and "Quarter" not in stage and "Semi" not in stage,
        })
    return pd.DataFrame(rows, columns=CL_COLUMNS)


def load_results(today: date, history: pd.DataFrame) -> pd.DataFrame:
    """All Champions League games of the last seasons; seasons not yet published are skipped."""
    domestic = {
        cc: set(history.loc[history["league"] == lg, "home"]) | set(history.loc[history["league"] == lg, "away"])
        for lg, cc in config.COUNTRY.items()
    }
    frames, got = [], []
    for season in cl_seasons(today):
        try:
            r = requests.get(config.CL_RESULTS_URL.format(season=season), timeout=30)
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except Exception as e:
            log.warning("Champions League %s results failed: %s", season, e)
            continue
        df = parse_results(r.text, int(season[:4]), domestic)
        if len(df):
            frames.append(df)
            got.append(season)
    LAST_CL_SEASONS[:] = got
    if not frames:
        return pd.DataFrame(columns=CL_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- rating

@dataclass
class CrossElo:
    """One rating per club across the domestic leagues and European games."""

    ratings: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)

    def get(self, team: str, start: float = 1500.0) -> float:
        return self.ratings.get(team, start)

    def update(self, home: str, away: str, hg: int, ag: int, k: float, home_adv: float, starts=(1500.0, 1500.0)) -> None:
        rh, ra = self.get(home, starts[0]), self.get(away, starts[1])
        exp_h = 1 / (1 + 10 ** (-(rh + home_adv - ra) / 400))
        score = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        gd = abs(hg - ag)
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = k * mult * (score - exp_h)
        self.ratings[home], self.ratings[away] = rh + delta, ra - delta
        self.games[home] = self.games.get(home, 0) + 1
        self.games[away] = self.games.get(away, 0) + 1


def _combined(history: pd.DataFrame, cl: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    dom = history[history["date"] < as_of][["date", "league", "home", "away", "hg", "ag"]].copy()
    dom["home_cc"] = dom["away_cc"] = dom["league"].map(config.COUNTRY)
    dom["neutral"] = False
    dom["comp"] = "dom"
    eu = cl[cl["date"] < as_of].copy()
    eu["comp"] = "CL"
    both = pd.concat([dom.drop(columns="league"), eu], ignore_index=True)
    return both.sort_values("date", kind="stable").reset_index(drop=True)


class CLModel:
    """Cross-league Elo + country offsets -> ordered-logit 1X2 and Poisson goals."""

    def __init__(self, history: pd.DataFrame, cl: pd.DataFrame, as_of: pd.Timestamp):
        games = _combined(history, cl, as_of)
        first_day = games["date"].min() if len(games) else as_of
        self.elo = CrossElo()
        self.country: dict[str, str] = {}
        x, home, cc_h, cc_a, y, hg, ag = [], [], [], [], [], [], []
        for r in games.itertuples(index=False):
            self.country.setdefault(r.home, r.home_cc)
            self.country.setdefault(r.away, r.away_cc)
            starts = tuple(self._start(t, cc, r.date, first_day) for t, cc in ((r.home, r.home_cc), (r.away, r.away_cc)))
            h_adv = 0.0 if r.neutral else config.ELO_HOME_ADV
            if r.comp == "CL":
                x.append(self.elo.get(r.home, starts[0]) - self.elo.get(r.away, starts[1]))
                home.append(0.0 if r.neutral else 1.0)
                cc_h.append(r.home_cc)
                cc_a.append(r.away_cc)
                y.append(result_code(r.hg, r.ag))
                hg.append(r.hg)
                ag.append(r.ag)
            k = config.CL_ELO_K if r.comp == "CL" else config.ELO_K
            self.elo.update(r.home, r.away, int(r.hg), int(r.ag), k, h_adv, starts)
        self.n_train = len(y)
        self._fit(np.array(x) / 100, np.array(home), cc_h, cc_a, np.array(y), np.array(hg), np.array(ag))

    def _start(self, team: str, cc: str, day, first_day) -> float:
        if team in self.elo.ratings:
            return self.elo.ratings[team]
        if cc not in config.COUNTRY.values():
            return config.CL_ELO_START
        # domestic sides seen only after the first weeks are promoted ones
        return 1500.0 if (day - first_day).days < 60 else config.ELO_NEW_TEAM

    def _fit(self, x, home, cc_h, cc_a, y, hg, ag) -> None:
        self.countries = sorted(set(cc_h) | set(cc_a))
        idx = {c: i for i, c in enumerate(self.countries)}
        ih, ia = np.array([idx[c] for c in cc_h], dtype=int), np.array([idx[c] for c in cc_a], dtype=int)
        n_c = len(self.countries)

        def strength(off):
            return x + off[ih] - off[ia]

        def nll(theta):
            b, c1, gap, h = theta[:4]
            off = theta[4:]
            z = b * (strength(off) + h * home)
            c2 = c1 + math.exp(gap)
            pa, pna = _sig(c1 - z), _sig(c2 - z)
            p = np.choose(y, [pa, pna - pa, 1 - pna])
            return -np.sum(np.log(np.clip(p, 1e-12, 1))) + config.CL_OFFSET_RIDGE * np.sum(off ** 2)

        res = minimize(nll, x0=np.r_[0.8, -0.8, 0.0, 0.6, np.zeros(n_c)], method="L-BFGS-B")
        self.b, self.c1, gap, self.h = res.x[:4]
        self.c2 = self.c1 + math.exp(gap)
        self.offset = {c: float(v) for c, v in zip(self.countries, res.x[4:])}
        s = strength(res.x[4:])

        def pois(theta):
            a, hh, g = theta
            lh, la = np.exp(a + hh * home + g * s), np.exp(a - g * s)
            return -np.sum(poisson.logpmf(hg, lh) + poisson.logpmf(ag, la))

        self.a, self.hh, self.g = minimize(pois, x0=[0.3, 0.2, 0.2], method="Nelder-Mead").x

    def rating(self, team: str) -> float | None:
        """Rating on the common scale: club Elo plus its country's offset."""
        if team not in self.elo.ratings:
            return None
        return self.elo.ratings[team] + 100 * self.offset.get(self.country.get(team, ""), 0.0)

    def predict(self, home: str, away: str, neutral: bool = False) -> dict | None:
        if min(self.elo.games.get(home, 0), self.elo.games.get(away, 0)) < config.CL_MIN_MATCHES:
            return None
        s = (self.rating(home) - self.rating(away)) / 100
        hm = 0.0 if neutral else 1.0
        z = self.b * (s + self.h * hm)
        pa, pna = _sig(self.c1 - z), _sig(self.c2 - z)
        lo = {"H": 1 - pna, "D": pna - pa, "A": pa}
        lh, la = math.exp(self.a + self.hh * hm + self.g * s), math.exp(self.a - self.g * s)
        g = np.arange(11)
        grid = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
        po = {"H": np.tril(grid, -1).sum(), "D": np.trace(grid), "A": np.triu(grid, 1).sum()}
        under = sum(grid[i, j] for i in range(3) for j in range(3 - i))
        w = config.CL_POISSON_SHARE
        out = {k: float(w * po[k] / sum(po.values()) + (1 - w) * lo[k]) for k in ("H", "D", "A")}
        out.update(O25=float(1 - under), U25=float(under), xg_h=lh, xg_a=la)
        return out
