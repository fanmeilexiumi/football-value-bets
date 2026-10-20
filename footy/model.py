"""Match models: Dixon-Coles goals model, Elo ratings, and their blend."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import penaltyblog as pb
from scipy.optimize import minimize

from . import config

OUTCOMES = ("H", "D", "A")


# ---------------------------------------------------------------- Dixon-Coles

class DixonColes:
    """One Dixon-Coles model per league, fitted on time-decayed recent matches."""

    def __init__(self, history: pd.DataFrame, as_of: pd.Timestamp):
        self.models: dict[str, pb.models.DixonColesGoalModel] = {}
        self.games: dict[tuple[str, str], int] = {}
        lo = as_of - pd.Timedelta(days=config.TRAIN_DAYS)
        for league, g in history[(history["date"] < as_of) & (history["date"] >= lo)].groupby("league"):
            if len(g) < 100:
                continue
            counts = pd.concat([g["home"], g["away"]]).value_counts()
            self.games.update({(league, t): int(n) for t, n in counts.items()})
            w = pb.models.dixon_coles_weights(g["date"], config.DC_XI, base_date=as_of)
            m = pb.models.DixonColesGoalModel(g["hg"], g["ag"], g["home"], g["away"], weights=w)
            m.fit()
            self.models[league] = m

    def predict(self, league: str, home: str, away: str) -> dict | None:
        m = self.models.get(league)
        if m is None:
            return None
        if min(self.games.get((league, home), 0), self.games.get((league, away), 0)) < config.MIN_TEAM_MATCHES:
            return None  # too few games in this league to rate the side
        try:
            g = m.predict(home, away)
        except ValueError:  # promoted side with no matches in the window
            return None
        return {
            "H": g.home_win, "D": g.draw, "A": g.away_win,
            "O25": g.total_goals("over", 2.5), "U25": g.total_goals("under", 2.5),
            "xg_h": float(g.home_goal_expectation), "xg_a": float(g.away_goal_expectation),
        }


# ---------------------------------------------------------------------- Elo

@dataclass
class Elo:
    """Per-league Elo with a goal-difference multiplier (World Football Elo style)."""

    ratings: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, league: str, team: str) -> float:
        return self.ratings.get((league, team), config.ELO_NEW_TEAM)

    def diff(self, league: str, home: str, away: str) -> float:
        return self.get(league, home) + config.ELO_HOME_ADV - self.get(league, away)

    def update(self, league: str, home: str, away: str, hg: int, ag: int) -> None:
        d = self.diff(league, home, away)
        exp_h = 1 / (1 + 10 ** (-d / 400))
        score = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        gd = abs(hg - ag)
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = config.ELO_K * mult * (score - exp_h)
        self.ratings[(league, home)] = self.get(league, home) + delta
        self.ratings[(league, away)] = self.get(league, away) - delta


def add_pre_match_elo(history: pd.DataFrame) -> tuple[pd.DataFrame, Elo]:
    """Annotate each match with the Elo difference known *before* kick-off."""
    elo = Elo()
    diffs = np.empty(len(history))
    for i, r in enumerate(history.itertuples(index=False)):
        diffs[i] = elo.diff(r.league, r.home, r.away)
        elo.update(r.league, r.home, r.away, r.hg, r.ag)
    return history.assign(elo_diff=diffs), elo


class EloOutcome:
    """Ordered-logit map from Elo difference to home/draw/away probabilities."""

    def __init__(self, elo_diff: np.ndarray, result: np.ndarray):
        # result: 0 away, 1 draw, 2 home
        x = np.asarray(elo_diff) / 100.0
        y = np.asarray(result)

        def nll(theta):
            b, c1, gap = theta
            c2 = c1 + math.exp(gap)
            p_away = _sig(c1 - b * x)
            p_not_home = _sig(c2 - b * x)
            p = np.choose(y, [p_away, p_not_home - p_away, 1 - p_not_home])
            return -np.sum(np.log(np.clip(p, 1e-12, 1)))

        res = minimize(nll, x0=[0.5, -1.0, 0.0], method="Nelder-Mead", options={"maxiter": 2000})
        self.b, self.c1, gap = res.x
        self.c2 = self.c1 + math.exp(gap)

    def predict(self, elo_diff: float) -> dict:
        x = elo_diff / 100.0
        pa = _sig(self.c1 - self.b * x)
        pna = _sig(self.c2 - self.b * x)
        return {"H": 1 - pna, "D": pna - pa, "A": pa}


def _sig(z):
    return 1 / (1 + np.exp(-z))


def result_code(hg: int, ag: int) -> int:
    return 2 if hg > ag else 1 if hg == ag else 0


def blend(dc: dict, elo: dict, w: float = config.ELO_BLEND) -> dict:
    out = dict(dc)
    for k in OUTCOMES:
        out[k] = (1 - w) * dc[k] + w * elo[k]
    return out
