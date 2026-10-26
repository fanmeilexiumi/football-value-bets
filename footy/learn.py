"""Weekly self-tuning: re-pick a few rule parameters from the results so far.

Every Monday (Beijing time) each parameter in PARAMS is re-chosen, one at a
time with the others held, on the walk-forward backtest rows (which grow by
one week of real results each week) plus settled Champions League games from
the journal. The grid value that scores best on the older rows is adopted
only if it also beats the current value on the most recent TUNE_VALID_DAYS by
TUNE_MIN_GAIN; otherwise the current value stays. Every check is logged in
journal/learned.json and shown on the page, adopted or not.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date

import numpy as np
import pandas as pd

from . import adjust, config, journal, zucai
from .value import find_longshots, find_value

log = logging.getLogger(__name__)

# name: (grid, objective, what it controls)
PARAMS: dict[str, tuple[tuple[float, ...], str, str]] = {
    "ELO_BLEND": ((0.0, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5), "logloss", "联赛胜平负里 Elo 模型的权重"),
    "ZC_MARKET_SHARE": ((0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9), "logloss", "“最可能结果”和足彩向市场概率靠拢的比例"),
    "MIN_EDGE": ((0.03, 0.05, 0.07, 0.10), "roi", "价值推荐的最低优势"),
    "UPSET_FAV_PROB": ((0.55, 0.60, 0.65, 0.70), "roi", "冷门推荐：热门胜率至少多少才算“有热门”"),
    "UPSET_MIN_GAP": ((0.02, 0.03, 0.05, 0.08), "roi", "冷门推荐：模型比市场多给冷门的最小概率差"),
    "LONG_MIN_EDGE": ((0.03, 0.05, 0.08, 0.12), "roi", "高赔推荐的最低优势"),
    "ZC_DOUBLE_MAX_P": ((0.50, 0.55, 0.60, 0.65), "hits", "足彩：首选概率低于多少才考虑双选"),
    "CTX_WEIGHT": ((0.0, 0.5, 1.0, 1.5, 2.0), "logloss", "19:00 更新：场外因素调整概率的力度（0 为不调整）"),
}
MIN_BETS = {"MIN_EDGE": 60, "UPSET_FAV_PROB": 25, "UPSET_MIN_GAP": 25, "LONG_MIN_EDGE": 40, "CTX_WEIGHT": 40}


def defaults() -> dict:
    return {k: getattr(config, k) for k in PARAMS}


DEFAULTS = defaults()


def load_and_apply() -> dict:
    state = journal.load("learned.json", {})
    for k, v in state.get("params", {}).items():
        if k in PARAMS:
            setattr(config, k, v)
    return state


def due(state: dict, today: date) -> bool:
    return not state.get("updated") or (today.weekday() == config.TUNE_WEEKDAY and state["updated"] != today.isoformat())


@contextmanager
def _patched(**kw):
    old = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(config, k, v)


# ------------------------------------------------------------- objectives

def _logloss(p: np.ndarray, res: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(p[np.arange(len(res)), res], 1e-12, 1))))


def _probs(row: dict) -> dict:
    w = config.ELO_BLEND
    p = {k: (1 - w) * row["dc"][i] + w * row["elo"][i] for i, k in enumerate(("H", "D", "A"))}
    return {**p, "O25": row["dc_over"], "U25": 1 - row["dc_over"]}


def _won(sel: str, row: dict) -> bool:
    return {"H": row["res"] == 0, "D": row["res"] == 1, "A": row["res"] == 2,
            "O25": row["over"] == 1, "U25": row["over"] == 0}[sel]


def _roi(rows: list[dict], kind: str) -> tuple[float | None, int]:
    """Flat-stake return at the average price, as the backtest's conservative bound."""
    profit, n = 0.0, 0
    for row in rows:
        odds = {k: (v if v == v else None) for k, v in row["odds"].items()}
        p = _probs(row)
        picks = find_value(odds, p, price="avg") if kind == "value" else \
            [b for b in find_longshots(odds, p, price="avg") if kind in b["tags"]]
        for b in picks:
            n += 1
            profit += b["odds"] - 1 if _won(b["selection"], row) else -1
    return (profit / n if n else None), n


def score(name: str, rows: list[dict], cl_rows: list[dict], ctx_rows: list[dict] = ()) -> tuple[float | None, int]:
    """Higher is better for every objective (log-loss is negated)."""
    if name == "CTX_WEIGHT":
        if not ctx_rows:
            return None, 0
        loss = 0.0
        for r in ctx_rows:
            p = adjust.apply(r["model"], r["adj"])
            loss -= np.log(max(1e-12, p[r["res"]]))
            if "O25" in p and r["over"] is not None:
                loss -= np.log(max(1e-12, p["O25"] if r["over"] else 1 - p["O25"]))
        return -loss / len(ctx_rows), len(ctx_rows)
    if name == "ELO_BLEND":
        p = np.array([[(1 - config.ELO_BLEND) * d + config.ELO_BLEND * e for d, e in zip(r["dc"], r["elo"])] for r in rows])
        return (-_logloss(p, np.array([r["res"] for r in rows])) if rows else None), len(rows)
    if name == "ZC_MARKET_SHARE":
        m = config.ZC_MARKET_SHARE
        both = [(_probs(r), r["mkt"], r["res"]) for r in rows if None not in r["mkt"]]
        both += [(r["model"], [r["market"][k] for k in ("H", "D", "A")], r["res"]) for r in cl_rows]
        if not both:
            return None, 0
        p = np.array([[(1 - m) * mo[k] + m * mk[i] for i, k in enumerate(("H", "D", "A"))] for mo, mk, _ in both])
        return -_logloss(p, np.array([y for *_, y in both])), len(both)
    if name == "ZC_DOUBLE_MAX_P":
        sim = zucai.simulate([{**r, "blend": [_probs(r)[k] for k in ("H", "D", "A")]} for r in rows], draws=8)
        return (sim["hits14"] if sim.get("n") else None), sim.get("n", 0)
    kind = {"MIN_EDGE": "value", "UPSET_FAV_PROB": "冷门", "UPSET_MIN_GAP": "冷门", "LONG_MIN_EDGE": "高赔"}[name]
    return _roi(rows, kind)


# ------------------------------------------------------------------ tune

def _cl_rows(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        res = journal._res(r)
        if r["league"] == "CL" and res and r.get("model") and all(k in (r.get("market") or {}) for k in ("H", "D", "A")):
            out.append({"date": pd.Timestamp(r["date"]), "model": r["model"], "market": r["market"],
                        "res": ("H", "D", "A").index(res)})
    return out


def _ctx_rows(records: list[dict]) -> list[dict]:
    """Settled journal games whose raw model and off-pitch shifts were stored (any league)."""
    out = []
    for r in records:
        res = journal._res(r)
        if res and r.get("model_raw") and r.get("ctx_adj") and any(r["ctx_adj"].values()):
            goals = r["result"]["hg"] + r["result"]["ag"]
            out.append({"model": r["model_raw"], "adj": r["ctx_adj"], "res": res, "over": goals > 2})
    return out


def tune(preds: list[dict], records: list[dict], today: date, state: dict) -> dict:
    split = pd.Timestamp(today) - pd.Timedelta(days=config.TUNE_VALID_DAYS)
    rows = [p for p in preds if all(k in p for k in ("odds", "dc", "elo"))]
    train, valid = [r for r in rows if r["date"] < split], [r for r in rows if r["date"] >= split]
    cl = _cl_rows(records)
    cl_train, cl_valid = [r for r in cl if r["date"] < split], [r for r in cl if r["date"] >= split]
    # the off-pitch shifts exist only in the journal, which is young: scored on all of it (in-sample)
    ctx = _ctx_rows(records)
    params = {k: getattr(config, k) for k in PARAMS}
    checks = []
    for name, (grid, objective, _) in PARAMS.items():
        need = MIN_BETS.get(name, 1)
        trial = {}
        for v in grid:
            with _patched(**{name: v}):
                s, n = score(name, train, cl_train, ctx)
            if s is not None and n >= need:
                trial[v] = s
        if not trial:
            checks.append({"param": name, "current": params[name], "candidate": None, "valid_current": None, "valid_candidate": None, "adopted": False, "note": "样本不足"})
            continue
        best = max(trial, key=trial.get)
        with _patched(**{name: params[name]}):
            cur_v, n_cur = score(name, valid, cl_valid, ctx)
        with _patched(**{name: best}):
            best_v, n_best = score(name, valid, cl_valid, ctx)
        gain = config.TUNE_MIN_GAIN[objective]
        adopted = (best != params[name] and cur_v is not None and best_v is not None
                   and n_best >= need and best_v - cur_v >= gain)
        checks.append({
            "param": name, "objective": objective, "current": params[name], "candidate": best,
            "train_current": trial.get(params[name]), "train_candidate": trial[best],
            "valid_current": cur_v, "valid_candidate": best_v, "n_valid": n_best, "adopted": adopted,
        })
        if adopted:
            params[name] = best
            setattr(config, name, best)
        log.info("tune %s: current %s, candidate %s, valid %s -> %s, adopted %s",
                 name, checks[-1]["current"], best, cur_v, best_v, adopted)
    entry = {"date": today.isoformat(), "train_rows": len(train), "valid_rows": len(valid),
             "cl_rows": len(cl), "ctx_rows": len(ctx), "checks": checks}
    return {"updated": today.isoformat(), "params": params, "log": ([entry] + state.get("log", []))[:26]}
