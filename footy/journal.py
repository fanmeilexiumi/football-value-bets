"""Prediction journal and the daily review (复盘).

Each run keeps the latest pre-kick-off prediction of every upcoming game in
journal/predictions.json and the lottery tickets in journal/lottery.json; the
workflow commits the folder back to the repository so nothing is lost between
runs. Finished games are settled from football-data (the three leagues), The
Odds API scores (whatever is still open) and 500.com's period page (lottery),
then reviewed: which picks hit, what they returned, and every upset with what
the market and the model gave it beforehand.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from . import config, zucai
from .odds_api import CL_SPORT_KEYS, SPORT_KEYS
from .teams import canonical, cl_canonical_api

log = logging.getLogger(__name__)

SCORES_URL = "https://api.the-odds-api.com/v4/sports/{sport}/scores"
SEL = ("H", "D", "A")
LABEL = {"H": "主胜", "D": "平局", "A": "客胜", "O25": "大 2.5", "U25": "小 2.5"}


def load(name: str, default):
    path = config.JOURNAL_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(name: str, obj) -> None:
    config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    (config.JOURNAL_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def now_bj() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def match_day(bj_date: str, bj_time: str | None) -> str:
    """Games kicking off before 08:00 Beijing belong to the previous evening's round."""
    if bj_time and bj_time < "08:00":
        return (datetime.strptime(bj_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return bj_date


def _kickoff(rec: dict) -> datetime:
    return datetime.strptime(f'{rec["bj_date"]} {rec.get("bj_time") or "23:59"}', "%Y-%m-%d %H:%M")


def final_probs(model: dict | None, market: dict | None) -> dict | None:
    """1X2 used for 'our call' and the lottery: the model, pulled towards the market by ZC_MARKET_SHARE."""
    if not model:
        return {k: market[k] for k in SEL} if market and all(k in market for k in SEL) else None
    m = config.ZC_MARKET_SHARE if market and all(k in market for k in SEL) else 0.0
    return {k: (1 - m) * model[k] + m * (market[k] if m else 0.0) for k in SEL}


# ----------------------------------------------------------------- record

def _r(v):
    return None if v is None else round(float(v), 4)


def record(records: list[dict], predictions: list[dict], now: datetime) -> None:
    """Upsert every game that has not kicked off; earlier snapshots of started games stay as they were."""
    by_id = {r["id"]: i for i, r in enumerate(records)}
    for p in predictions:
        rec_id = zucai.pred_id(p)
        if rec_id in by_id and _kickoff(records[by_id[rec_id]]) <= now:
            continue
        model = {k: _r(p["model"][k]) for k in (*SEL, "O25") if k in p["model"]} if p.get("model") else None
        market = {k: _r(v) for k, v in (p.get("market") or {}).items()}
        fp = final_probs(model, market)
        rec = {
            "id": rec_id, "league": p["league"], "league_name": p["league_name"], "date": p["date"],
            "home": p["home"], "away": p["away"], "bj_date": p["bj_date"], "bj_time": p.get("bj_time"),
            "day": match_day(p["bj_date"], p.get("bj_time")),
            "model": model, "market": market or None, "call": max(fp, key=fp.get) if fp else None,
            "picks": [{"selection": b["selection"], "odds": _r(b["odds"]), "edge": _r(b["edge"]),
                       "tags": ["价值"] if kind == "bets" else list(b["tags"])}
                      for kind in ("bets", "longshots") for b in p.get(kind, [])],
            "context": _context_flags(p.get("context")),
            "result": None,
        }
        if rec_id in by_id:
            records[by_id[rec_id]] = rec
        else:
            by_id[rec_id] = len(records)
            records.append(rec)
    cutoff = (now - timedelta(days=config.JOURNAL_KEEP_DAYS)).strftime("%Y-%m-%d")
    records[:] = [r for r in records if r["bj_date"] >= cutoff]


def _context_flags(c: dict | None) -> dict | None:
    """What the weekly tuning may later test: per pick, the factors for and against."""
    if not c:
        return None
    return {sel: {"pro": len(v["pro"]), "con": len(v["con"]), "lean": v["lean"], "why": v["pro"] + ["−" + x for x in v["con"]]}
            for sel, v in c.get("verdicts", {}).items()}


def record_lottery(periods: list[dict], lottery: list[dict]) -> None:
    """Store the tickets of every period still on sale (the last version before the deadline wins)."""
    by_exp = {p["expect"]: i for i, p in enumerate(periods)}
    for lp in lottery:
        if lp.get("simulated"):
            continue
        entry = {
            "expect": lp["expect"], "deadline": lp["deadline"], "t14": lp["t14"]["code"], "r9": lp["r9"]["code"],
            "games": [{"no": g["no"], "comp": g["comp"], "kickoff": g["kickoff"], "home_cn": g["home_cn"],
                       "away_cn": g["away_cn"], "pred_id": g.get("pred_id"),
                       "p": {k: _r(v) for k, v in g["p"].items()},
                       "fav": max(g["market"], key=g["market"].get) if g.get("market") else max(g["p"], key=g["p"].get),
                       "t14": g["t14"]["picks"], "r9": g["r9"]["picks"] if g["r9"] else None,
                       "result": None} for g in lp["games"]],
            "settled": False,
        }
        if lp["expect"] in by_exp:
            periods[by_exp[lp["expect"]]] = entry
        else:
            periods.append(entry)
    periods.sort(key=lambda p: p["expect"])
    del periods[:-60]


# ----------------------------------------------------------------- settle

def settle(records: list[dict], history: pd.DataFrame, now: datetime, api_key: str | None = None) -> None:
    open_ = [r for r in records if r["result"] is None and _kickoff(r) + timedelta(hours=2, minutes=30) <= now]
    if not open_:
        return
    hist = history.dropna(subset=["hg", "ag"])
    idx = {(r.league, r.home, r.away): r for r in hist.itertuples()}
    for rec in open_:
        h = idx.get((rec["league"], rec["home"], rec["away"]))
        if h is not None and abs((h.date - pd.Timestamp(rec["date"])).days) <= 1:
            rec["result"] = {"hg": int(h.hg), "ag": int(h.ag), "source": "football-data"}
    still = [r for r in open_ if r["result"] is None]
    api_key = os.environ.get("THE_ODDS_API_KEY") if api_key is None else api_key  # "" switches the API off
    if still and api_key:
        _settle_from_scores(still, api_key)
    for rec in open_:
        if rec["result"] is None and _kickoff(rec) + timedelta(days=config.JOURNAL_VOID_DAYS) <= now:
            rec["result"] = {"void": True}


def _settle_from_scores(open_: list[dict], api_key: str) -> None:
    """The Odds API scores, last three days; 2 credits per competition, only for competitions with open games."""
    sports = {**SPORT_KEYS, **CL_SPORT_KEYS}
    for league in sorted({r["league"] for r in open_}):
        mine = [r for r in open_ if r["league"] == league]
        known = {r["home"] for r in mine} | {r["away"] for r in mine}
        name = cl_canonical_api if league == "CL" else canonical
        try:
            resp = requests.get(SCORES_URL.format(sport=sports[league]),
                                params={"apiKey": api_key, "daysFrom": 3}, timeout=30)
            resp.raise_for_status()
            events = resp.json()
            log.info("The Odds API scores %s: %d events, %s credits left", league, len(events),
                     resp.headers.get("x-requests-remaining"))
        except Exception as e:
            log.warning("The Odds API scores %s failed: %s", league, e)
            continue
        for ev in events:
            if not ev.get("completed") or not ev.get("scores"):
                continue
            score = {s.get("name"): s.get("score") for s in ev["scores"]}
            try:
                hg, ag = int(score[ev["home_team"]]), int(score[ev["away_team"]])
            except (KeyError, TypeError, ValueError):
                continue
            home, away = name(ev["home_team"], known), name(ev["away_team"], known)
            when = pd.to_datetime(ev.get("commence_time"), utc=True).tz_convert(None)
            for rec in mine:
                if rec["result"] is None and (rec["home"], rec["away"]) == (home, away) \
                        and abs((when.normalize() - pd.Timestamp(rec["date"])).days) <= 1:
                    rec["result"] = {"hg": hg, "ag": ag, "source": "The Odds API"}


def settle_lottery(periods: list[dict], records: list[dict], now: datetime, fetch=None) -> None:
    """Fill lottery results from 500.com's period page, falling back to our own settled games."""
    fetch = fetch or (lambda e: zucai.parse_period(zucai._get(f"{config.ZC_SOURCE_URL}?expect={e}"), now.date()))
    by_id = {r["id"]: r for r in records}
    for per in periods:
        if per["settled"]:
            continue
        kicks = [datetime.strptime(g["kickoff"], "%Y-%m-%d %H:%M") for g in per["games"] if g.get("kickoff")]
        if not kicks or min(kicks) + timedelta(hours=2, minutes=30) > now:
            continue
        try:
            scores = {g["no"]: g.get("score") for g in fetch(per["expect"])["games"]}
        except Exception as e:
            log.warning("500.com results for %s failed: %s", per["expect"], e)
            scores = {}
        for g in per["games"]:
            if g["result"]:
                continue
            sc = scores.get(g["no"])
            rec = by_id.get(g.get("pred_id") or "")
            if sc is None and rec and rec["result"] and not rec["result"].get("void"):
                sc = (rec["result"]["hg"], rec["result"]["ag"])
            if sc is not None:
                g["result"] = "3" if sc[0] > sc[1] else "1" if sc[0] == sc[1] else "0"
                g["score"] = f"{sc[0]}:{sc[1]}"
        if all(g["result"] for g in per["games"]) or max(kicks) + timedelta(days=config.JOURNAL_VOID_DAYS) <= now:
            per["settled"] = True


# ----------------------------------------------------------------- review

def _res(rec: dict) -> str | None:
    r = rec.get("result") or {}
    if "hg" not in r:
        return None
    return "H" if r["hg"] > r["ag"] else "D" if r["hg"] == r["ag"] else "A"


def _won(sel: str, rec: dict) -> bool:
    r = rec["result"]
    goals = r["hg"] + r["ag"]
    return {"O25": goals > 2, "U25": goals < 3}.get(sel, sel == _res(rec))


def review_game(rec: dict, lottery_cover: dict | None = None) -> dict:
    res = _res(rec)
    mkt, model = rec.get("market") or {}, rec.get("model") or {}
    fav = max(SEL, key=lambda k: mkt[k]) if all(k in mkt for k in SEL) else None
    picks = [{**pk, "label": LABEL[pk["selection"]], "won": _won(pk["selection"], rec),
              "profit": (pk["odds"] - 1) if _won(pk["selection"], rec) else -1.0} for pk in rec["picks"]]
    upset = None
    if fav and res != fav and mkt[res] < config.REVIEW_UPSET_PROB:
        flagged = [pk for pk in picks if pk["selection"] == res]
        if flagged:
            cause = "已提示：" + "/".join(dict.fromkeys(t for pk in flagged for t in pk["tags"]))
        elif not model:
            cause = "无模型，只有市场概率"
        elif model[res] - mkt[res] >= 0.02:
            cause = "模型看高冷门，但没到推荐门槛"
        else:
            cause = "模型和市场一样低估"
        upset = {"market": mkt[res], "model": model.get(res), "cause": cause, "flagged": bool(flagged),
                 "lottery": lottery_cover.get(rec["id"]) if lottery_cover else None}
    return {
        "id": rec["id"], "league_name": rec["league_name"], "home": rec["home"], "away": rec["away"],
        "bj_date": rec["bj_date"], "bj_time": rec.get("bj_time"),
        "score": f'{rec["result"]["hg"]}:{rec["result"]["ag"]}', "res": res, "res_label": LABEL[res],
        "call": rec.get("call"), "call_label": LABEL.get(rec.get("call")), "call_hit": rec.get("call") == res,
        "fav": fav, "fav_hit": fav == res if fav else None,
        "p_res": {"market": mkt.get(res), "model": model.get(res)}, "picks": picks, "upset": upset,
    }


def _stats(picks: list[dict]) -> dict:
    if not picks:
        return {"n": 0, "hit": None, "roi": None, "profit": 0.0}
    profit = float(sum(p["profit"] for p in picks))
    return {"n": len(picks), "hit": float(np.mean([p["won"] for p in picks])), "roi": profit / len(picks), "profit": profit}


def _summary(games: list[dict]) -> dict:
    called = [g for g in games if g["call"]]
    with_fav = [g for g in games if g["fav"]]
    picks = [pk for g in games for pk in g["picks"]]
    upsets = [g for g in games if g["upset"]]
    return {
        "n": len(games),
        "call_hit": sum(g["call_hit"] for g in called), "n_call": len(called),
        "fav_hit": sum(g["fav_hit"] for g in with_fav), "n_fav": len(with_fav),
        "value": _stats([p for p in picks if "价值" in p["tags"]]),
        "upset_picks": _stats([p for p in picks if "冷门" in p["tags"]]),
        "long_picks": _stats([p for p in picks if "高赔" in p["tags"]]),
        "long_any": _stats([p for p in picks if {"冷门", "高赔"} & set(p["tags"])]),
        "upsets": len(upsets), "upsets_flagged": sum(g["upset"]["flagged"] for g in upsets),
    }


def _lottery_review(per: dict) -> dict:
    games = per["games"]
    done = [g for g in games if g["result"]]
    t14 = sum(g["result"] in g["t14"] for g in done)
    r9 = [g for g in done if g["r9"]]
    return {
        "expect": per["expect"], "t14": per["t14"], "r9": per["r9"], "n_done": len(done),
        "result": ",".join(g["result"] or "?" for g in games),
        "t14_hits": t14, "fav_hits": sum(g["result"] == g["fav"] for g in done),
        "r9_hits": sum(g["result"] in g["r9"] for g in r9), "r9_n": sum(1 for g in games if g["r9"]),
        "r9_all": len(r9) == 9 and all(g["result"] in g["r9"] for g in r9),
        "games": [{"no": g["no"], "home_cn": g["home_cn"], "away_cn": g["away_cn"], "score": g.get("score"),
                   "result": g["result"], "t14": "".join(g["t14"]), "r9": "".join(g["r9"]) if g["r9"] else "-",
                   "hit": g["result"] in g["t14"] if g["result"] else None,
                   "upset": bool(g["result"] and g["result"] != g["fav"] and g["p"][g["result"]] < config.REVIEW_UPSET_PROB)}
                  for g in games],
    }


def build_review(records: list[dict], periods: list[dict], now: datetime) -> dict:
    """Latest finished match day in detail, recent days in brief, and running totals since the journal began."""
    cover = {}
    for per in periods:
        for g in per["games"]:
            if g.get("pred_id") and g["result"]:
                cover[g["pred_id"]] = f'{per["expect"]}期 {"".join(g["t14"])}' + (" 命中" if g["result"] in g["t14"] else " 未中")
    done = [r for r in records if _res(r)]
    games = [review_game(r, cover) for r in done]
    days = sorted({r["day"] for r in done}, reverse=True)
    by_day = {d: [g for g, r in zip(games, done) if r["day"] == d] for d in days}
    for d in days:
        by_day[d].sort(key=lambda g: (g["bj_date"], g["bj_time"] or "", g["home"]))
    settled = [p for p in periods if any(g["result"] for g in p["games"])]
    by_comp = {}
    for g in games:
        by_comp.setdefault(g["league_name"], []).append(g)
    return {
        "since": min((r["bj_date"] for r in records), default=None),
        "pending": sum(1 for r in records if r["result"] is None and _kickoff(r) <= now),
        "latest": {"day": days[0], "games": by_day[days[0]], "summary": _summary(by_day[days[0]])} if days else None,
        "recent": [{"day": d, "summary": _summary(by_day[d])} for d in days[1:14]],
        "total": _summary(games),
        "by_comp": [(k, _summary(v)) for k, v in sorted(by_comp.items())],
        "lottery": [_lottery_review(p) for p in sorted(settled, key=lambda p: p["expect"], reverse=True)[:6]],
    }
