"""推荐理由: off-pitch context for games with a pick, set against the odds.

For each game with a value, upset or long-odds pick that kicks off within
CONTEXT_HOURS, collect:
- form, rest days and the next fixture, and the table (ESPN's public JSON);
- weather at kick-off at the stadium's city (Open-Meteo, no key);
- injuries, suspensions and last formations (API-Football, only when the
  API_FOOTBALL_KEY secret is set; the free plan allows 100 requests a day);
- how the market moved since our previous snapshot (the journal).
Each factor is then marked as supporting or weakening each pick by plain
rules. Nothing here changes a probability: the factors are shown and stored
in the journal so the weekly tuning can test them once enough games settle.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta

import requests

from . import config
from .teams import canonical, cl_canonical_api

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/{path}"
ESPN_LEAGUES = {"E0": "eng.1", "I1": "ita.1", "SP1": "esp.1", "CL": "uefa.champions"}
AF_URL = "https://v3.football.api-sports.io/{path}"
AF_LEAGUES = {"E0": 39, "I1": 135, "SP1": 140, "CL": 2}
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"
# fallback when Open-Meteo's geocoder is slow: cities that host our leagues and regular Champions League games
CITY_COORDS = {
    "London": (51.51, -0.13), "Manchester": (53.48, -2.24), "Liverpool": (53.41, -2.98), "Birmingham": (52.49, -1.89),
    "Newcastle upon Tyne": (54.98, -1.62), "Madrid": (40.42, -3.70), "Barcelona": (41.39, 2.17), "Sevilla": (37.39, -5.98),
    "Seville": (37.39, -5.98), "Valencia": (39.47, -0.38), "Manises": (39.49, -0.46), "Bilbao": (43.26, -2.93),
    "Villarreal": (39.94, -0.10), "San Sebastián": (43.32, -1.98), "Girona": (41.98, 2.82), "Milano": (45.46, 9.19),
    "Milan": (45.46, 9.19), "Torino": (45.07, 7.69), "Roma": (41.90, 12.50), "Napoli": (40.85, 14.27),
    "Bergamo": (45.70, 9.67), "Bologna": (44.49, 11.34), "Firenze": (43.77, 11.26), "Como": (45.81, 9.09),
    "München": (48.14, 11.58), "Munich": (48.14, 11.58), "Dortmund": (51.51, 7.47), "Leverkusen": (51.03, 6.98),
    "Leipzig": (51.34, 12.37), "Frankfurt": (50.11, 8.68), "Stuttgart": (48.78, 9.18), "Paris": (48.86, 2.35),
    "Marseille": (43.30, 5.37), "Lille": (50.63, 3.06), "Monaco": (43.74, 7.42), "Lisbon": (38.72, -9.14),
    "Porto": (41.15, -8.61), "Amsterdam": (52.37, 4.90), "Eindhoven": (51.44, 5.47), "Rotterdam": (51.92, 4.48),
    "Brugge": (51.21, 3.22), "Istanbul": (41.01, 28.98), "Glasgow": (55.86, -4.25), "Salzburg": (47.81, 13.06),
}


COMP_CN = [("Champions League", "欧冠"), ("Europa League", "欧联"), ("Conference League", "欧协联"),
           ("English Premier League", "英超"), ("FA Cup", "足总杯"), ("Carabao", "联赛杯"), ("EFL Cup", "联赛杯"),
           ("Serie A", "意甲"), ("Coppa Italia", "意大利杯"), ("LALIGA", "西甲"), ("LaLiga", "西甲"), ("Copa del Rey", "国王杯"),
           ("Bundesliga", "德甲"), ("DFB", "德国杯"), ("Ligue 1", "法甲"), ("Coupe de France", "法国杯"),
           ("Primeira Liga", "葡超"), ("Eredivisie", "荷甲"), ("Pro League", "比甲"), ("Super Lig", "土超"),
           ("Premiership", "苏超"), ("Eliteserien", "挪超"), ("Superliga", "丹超")]


def comp_cn(name: str | None) -> str | None:
    if not name:
        return name
    return next((cn for en, cn in COMP_CN if en.lower() in name.lower()), name)


def _get(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", **(headers or {})}, timeout=15)
    r.raise_for_status()
    return r.json()


def kickoff_utc(p: dict) -> datetime | None:
    if not p.get("bj_time"):
        return None
    return datetime.strptime(f'{p["bj_date"]} {p["bj_time"]}', "%Y-%m-%d %H:%M") - timedelta(hours=8)


def _name(league: str, name: str, known: set[str]) -> str:
    return (cl_canonical_api if league == "CL" else canonical)(name, known)


# ------------------------------------------------------------------ ESPN

def _espn_match(league: str, preds: list[dict], cache: dict) -> dict:
    """pred id -> ESPN event (scoreboard entry) for our games, matched on kick-off and either team."""
    out = {}
    for day in sorted({kickoff_utc(p).strftime("%Y%m%d") for p in preds}):
        key = (league, day)
        if key not in cache:
            try:
                cache[key] = _get(ESPN.format(lg=ESPN_LEAGUES[league], path="scoreboard"), {"dates": day}).get("events", [])
            except Exception as e:
                log.warning("ESPN scoreboard %s %s failed: %s", league, day, e)
                cache[key] = []
        for ev in cache[key]:
            comp = ev["competitions"][0]
            when = datetime.strptime(comp["date"][:16], "%Y-%m-%dT%H:%M")
            teams = {c["homeAway"]: c["team"] for c in comp["competitors"]}
            for p in preds:
                known = {p["home"], p["away"]}
                if abs((kickoff_utc(p) - when).total_seconds()) > 3600:
                    continue
                if _name(league, teams["home"]["displayName"], known) == p["home"] \
                        or _name(league, teams["away"]["displayName"], known) == p["away"]:
                    out[p["_id"]] = ev
    return out


def _form(summary: dict, team_id: str, kick: datetime) -> dict:
    for t in summary.get("lastFiveGames", []):
        if t["team"]["id"] != team_id:
            continue
        games = sorted((e for e in t.get("events", []) if e.get("gameDate")), key=lambda e: e["gameDate"])
        games = [e for e in games if datetime.strptime(e["gameDate"][:16], "%Y-%m-%dT%H:%M") < kick]
        if not games:
            return {}
        last = games[-1]
        return {"form": "".join(e.get("gameResult", "?") for e in games[-5:]),
                "last_date": last["gameDate"][:16], "last_id": last.get("id"), "last_comp": comp_cn(last.get("leagueName")),
                "rest_days": (kick - datetime.strptime(last["gameDate"][:16], "%Y-%m-%dT%H:%M")).total_seconds() / 86400}
    return {}


def _next_game(league: str, team_id: str, kick: datetime) -> dict:
    # the "all" slug lists fixtures in every competition; a league slug lists only that league's
    try:
        evs = _get(ESPN.format(lg="all", path=f"teams/{team_id}/schedule"), {"fixture": "true"}).get("events", [])
    except Exception as e:
        log.warning("ESPN schedule %s failed: %s", team_id, e)
        return {}
    later = sorted((e for e in evs if datetime.strptime(e["date"][:16], "%Y-%m-%dT%H:%M") > kick + timedelta(hours=3)),
                   key=lambda e: e["date"])
    if not later:
        return {}
    n = later[0]
    when = datetime.strptime(n["date"][:16], "%Y-%m-%dT%H:%M")
    comp = (n.get("league") or {}).get("name") or (n.get("season") or {}).get("displayName") or ""
    return {"next_date": n["date"][:16], "next_days": (when - kick).total_seconds() / 86400,
            "next_comp": comp_cn(re.sub(r"^\d{4}-\d{2}\s+", "", comp)), "next_name": n.get("name")}


def _lineup(event_id: str | None, team_id: str, cache: dict) -> dict:
    """Formation and starting XI of a finished game, from ESPN's match summary."""
    if not event_id:
        return {}
    if event_id not in cache:
        try:
            cache[event_id] = _get(ESPN.format(lg="all", path="summary"), {"event": event_id}).get("rosters", [])
        except Exception as e:
            log.warning("ESPN summary %s failed: %s", event_id, e)
            cache[event_id] = []
    for r in cache[event_id]:
        if (r.get("team") or {}).get("id") == team_id:
            xi = [{"name": x["athlete"]["displayName"], "pos": pos_group((x.get("position") or {}).get("abbreviation"))}
                  for x in r.get("roster", []) if x.get("starter")]
            return {"formation": r.get("formation"), "starters": xi}
    return {}


def pos_group(abbr: str | None) -> str | None:
    """ESPN position codes (G, CD-L, RB, DM, AM-R, LW, CF ...) to G/D/M/F."""
    if not abbr:
        return None
    a = abbr.upper().split("-")[0]
    if a in ("G", "GK"):
        return "G"
    if a in ("CD", "LB", "RB", "SW", "D", "LWB", "RWB", "WB", "CB"):
        return "D"
    if a in ("LW", "RW", "F", "CF", "ST", "FW", "SS"):
        return "F"
    return "M" if "M" in a else None


def _plain(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace(".", " ").replace("-", " ")


def same_player(af_name: str, full_name: str) -> bool:
    """API-Football writes "M. Rashford"; ESPN writes "Marcus Rashford"."""
    a, b = _plain(af_name).split(), _plain(full_name).split()
    if not a or not b or a[-1] != b[-1]:
        return False
    return len(a) == 1 or a[0][0] == b[0][0]


def _table(summary: dict) -> list[dict]:
    rows = []
    for g in (summary.get("standings") or {}).get("groups", [])[:1]:
        for e in g["standings"]["entries"]:
            st = {s["name"]: s.get("value") for s in e.get("stats", [])}
            rows.append({"team": e["team"], "id": e.get("id"), "rank": int(st.get("rank") or 0),
                         "points": int(st.get("points") or 0), "played": int(st.get("gamesPlayed") or 0)})
    return sorted(rows, key=lambda r: r["rank"])


def motivation(league: str, table: list[dict], team_id: str) -> dict | None:
    """Where the team stands and what it is playing for."""
    me = next((r for r in table if r["id"] == team_id), None)
    if not me or not table:
        return None
    n, pts = len(table), {r["rank"]: r["points"] for r in table}

    def gap(rank):
        return abs(me["points"] - pts.get(rank, me["points"]))

    if me["played"] < 5:
        pre = "欧冠" if league == "CL" else ""
        return {"rank": me["rank"], "points": me["points"], "level": 0, "text": f'{pre}第 {me["rank"]} 名，赛季初，排名参考有限'}
    if league == "CL":
        zone = "直接晋级区" if me["rank"] <= 8 else "附加赛区" if me["rank"] <= 24 else "淘汰区"
        edge = min(gap(8) if me["rank"] > 8 else gap(9), gap(24) if me["rank"] > 24 else gap(25))
        level = 2 if edge <= 3 else 1
        return {"rank": me["rank"], "points": me["points"], "level": level,
                "text": f'欧冠第 {me["rank"]} 名（{zone}），{"离分区线不超过 3 分，必须拿分" if level == 2 else "形势相对安稳"}'}
    if me["rank"] <= 3 and gap(1) <= 3:
        text, level = "争冠", 2
    elif me["rank"] <= 7 and gap(4) <= 3:
        text, level = "争欧冠资格", 2
    elif me["rank"] >= n - 5 and gap(n - 2) <= 3:
        text, level = "保级", 2
    else:
        text, level = "中游，压力一般", 1
    return {"rank": me["rank"], "points": me["points"], "level": level, "text": f'第 {me["rank"]} 名，{me["points"]} 分，{text}'}


# --------------------------------------------------------------- weather

def weather(city: str, country: str | None, kick: datetime, cache: dict) -> dict | None:
    key = (city, country)
    if key not in cache:
        cache[key] = CITY_COORDS.get(city)
        if cache[key] is None:
            try:
                res = _get(GEO_URL, {"name": city, "count": 5}).get("results", [])
                pick = next((r for r in res if country and r.get("country") == country), res[0] if res else None)
                cache[key] = (pick["latitude"], pick["longitude"]) if pick else None
            except Exception as e:
                log.warning("geocoding %s failed: %s", city, e)
    if not cache[key]:
        return None
    lat, lon = cache[key]
    try:
        d = _get(WX_URL, {"latitude": lat, "longitude": lon, "timezone": "UTC",
                          "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m",
                          "start_date": kick.strftime("%Y-%m-%d"), "end_date": kick.strftime("%Y-%m-%d")})["hourly"]
    except Exception as e:
        log.warning("weather %s failed: %s", city, e)
        return None
    i = min(range(len(d["time"])), key=lambda j: abs(datetime.strptime(d["time"][j], "%Y-%m-%dT%H:%M") - kick))
    w = {"city": city, "temp": d["temperature_2m"][i], "rain_prob": d["precipitation_probability"][i],
         "rain_mm": d["precipitation"][i], "wind": d["wind_speed_10m"][i]}
    bad = []
    if (w["rain_prob"] or 0) >= 60 or (w["rain_mm"] or 0) >= 1:
        bad.append("降雨")
    if (w["wind"] or 0) >= 30:
        bad.append("大风")
    if w["temp"] is not None and (w["temp"] <= 3 or w["temp"] >= 30):
        bad.append("低温" if w["temp"] <= 3 else "高温")
    w["bad"] = bad
    return w


# ----------------------------------------------------------- API-Football

class AF:
    """API-Football with a request budget; every call is optional."""

    def __init__(self, key: str, budget: int):
        self.key, self.left, self.cache = key, budget, {}

    def get(self, path: str, **params) -> list:
        k = (path, tuple(sorted(params.items())))
        if k in self.cache:
            return self.cache[k]
        if self.left <= 0:
            return []
        self.left -= 1
        try:
            d = _get(AF_URL.format(path=path), params, {"x-apisports-key": self.key})
        except Exception as e:
            log.warning("API-Football %s failed: %s", path, e)
            return []
        if d.get("errors"):
            log.warning("API-Football %s %s: %s", path, params, d["errors"])
        self.cache[k] = d.get("response") or []
        return self.cache[k]


def af_context(af: AF, p: dict, lineups: dict | None = None) -> dict:
    """Injuries and suspensions, marked against each side's starters in its last game (from ESPN)."""
    kick = kickoff_utc(p)
    # the free plan refuses current-season queries by league and season, the "last" parameter and dates
    # outside about yesterday to tomorrow; date-only queries for match day still work (all leagues,
    # one request each), so fixtures and injuries are fetched by date and filtered here
    day = kick.strftime("%Y-%m-%d")
    fx = [f for f in af.get("fixtures", date=day, timezone="UTC") if f["league"]["id"] == AF_LEAGUES[p["league"]]]
    known = {p["home"], p["away"]}
    me = next((f for f in fx if abs(f["fixture"]["timestamp"] - (kick - datetime(1970, 1, 1)).total_seconds()) <= 3600
               and (_name(p["league"], f["teams"]["home"]["name"], known) == p["home"]
                    or _name(p["league"], f["teams"]["away"]["name"], known) == p["away"])), None)
    if not me:
        return {}
    out = {}
    inj = [i for i in af.get("injuries", date=day) if i["fixture"]["id"] == me["fixture"]["id"]]
    for side in ("home", "away"):
        team = me["teams"][side]
        xi = ((lineups or {}).get(side) or {}).get("starters")
        seen = set()
        inj_team = [i for i in inj if i["team"]["id"] == team["id"] and not (i["player"]["id"] in seen or seen.add(i["player"]["id"]))]
        missing = []
        for i in inj_team:
            hit = next((x for x in xi or [] if same_player(i["player"]["name"], x["name"])), None)
            missing.append({"name": hit["name"] if hit else i["player"]["name"], "type": i["player"].get("type"),
                            "reason": i["player"].get("reason"), "starter": bool(hit), "pos": hit and hit["pos"]})
        out[side] = {"missing": missing, "starters_known": bool(xi),
                     "key_out": [m for m in missing if m["starter"] and m["type"] == "Missing Fixture"]}
    return out


POS = {"G": "门将", "D": "后卫", "M": "中场", "F": "前锋"}


def matchup(fh: str | None, fa: str | None) -> str | None:
    """Rule-of-thumb reading of two formations (experience, not tested)."""
    if not fh or not fa:
        return None
    parts = lambda f: [int(x) for x in f.split("-") if x.isdigit()]
    h, a = parts(fh), parts(fa)
    if len(h) < 3 or len(a) < 3:
        return None
    mid_h, mid_a = sum(h[1:-1]), sum(a[1:-1])
    notes = []
    if fh == fa:
        notes.append("阵型对称，主要比拼个人能力")
    if mid_h != mid_a:
        more = "主队" if mid_h > mid_a else "客队"
        notes.append(f"{more}中场人数多 {abs(mid_h - mid_a)} 人，控球占优的可能更大")
    if (h[0] == 3) != (a[0] == 3):
        three = "主队" if h[0] == 3 else "客队"
        notes.append(f"{three}三中卫，边路靠翼卫一人防一条边，对手边路一对一机会多")
    if h[-1] >= 2 and a[0] == 3 or a[-1] >= 2 and h[0] == 3:
        notes.append("双前锋对三中卫，前场人数难占优")
    return "；".join(notes) or "阵型没有明显的克制关系"


# ----------------------------------------------------------------- verdict

def _side_flags(c: dict, side: str) -> dict:
    """Negative points for one side: tiredness, likely rotation, key absences."""
    other = "away" if side == "home" else "home"
    t, o = c.get(side, {}), c.get(other, {})
    return {
        "tired": (t.get("rest_days") or 9) <= 3 and (o.get("rest_days") or 0) >= 5,
        "rotation": (t.get("next_days") or 9) <= 3.5,
        "absences": len((t.get("af") or {}).get("key_out", [])) - len((o.get("af") or {}).get("key_out", [])) >= 2,
        "motivation": ((t.get("motivation") or {}).get("level", 1) < (o.get("motivation") or {}).get("level", 1)),
    }


def verdict(pick: dict, c: dict) -> dict:
    """Which factors back the pick (+) and which work against it (−)."""
    sel = pick["selection"]
    pro, con = [], []
    names = {"home": c["home_name"], "away": c["away_name"]}
    for side in ("home", "away"):
        f = _side_flags(c, side)
        lab = names[side]
        reasons = [("tired", f"{lab}休息不足 3 天"), ("rotation", f"{lab}三天内还有比赛，可能轮换"),
                   ("absences", f"{lab}主力缺阵更多"), ("motivation", f"{lab}战意较弱")]
        for kind, text in reasons:
            if not f[kind]:
                continue
            if sel == ("H" if side == "home" else "A"):
                con.append(text)
            elif sel in ("H", "A"):
                pro.append(text)
            elif sel == "D" and kind in ("tired", "rotation"):
                pro.append(text)  # a weakened side makes a draw likelier only through fatigue, by rule of thumb
    bad = (c.get("weather") or {}).get("bad", [])
    if bad:
        if sel in ("U25", "D"):
            pro.append("天气（" + "、".join(bad) + "）通常压低进球")
        elif sel == "O25":
            con.append("天气（" + "、".join(bad) + "）通常压低进球")
    move = c.get("market_move", {}).get(sel)
    if move is not None and abs(move) >= 0.03:
        (pro if move > 0 else con).append(f"上次以来市场看好度{'上升' if move > 0 else '下降'} {abs(move) * 100:.0f} 个百分点")
    lean = "支持" if len(pro) > len(con) else "削弱" if len(con) > len(pro) else "中性"
    return {"pro": pro, "con": con, "lean": lean}


# ------------------------------------------------------------------ build

def collect(predictions: list[dict], previous: dict[str, dict], now_utc: datetime) -> dict[str, dict]:
    """Context per pred id for games with a pick in the next CONTEXT_HOURS."""
    from .zucai import pred_id

    targets = []
    for p in predictions:
        k = kickoff_utc(p)
        if (p.get("bets") or p.get("longshots")) and k and now_utc < k <= now_utc + timedelta(hours=config.CONTEXT_HOURS):
            targets.append({**p, "_id": pred_id(p)})
    if not targets:
        return {}
    af_key = (os.environ.get("API_FOOTBALL_KEY") or "").strip()  # a pasted key often carries a newline
    af = AF(af_key, config.API_FOOTBALL_BUDGET) if af_key else None
    log.info("API-Football key %s", "found" if af else "not set: no injuries or formations")
    sb_cache, geo_cache, lu_cache, out = {}, {}, {}, {}
    for league in sorted({p["league"] for p in targets}):
        mine = [p for p in targets if p["league"] == league]
        events = _espn_match(league, mine, sb_cache)
        for p in mine:
            kick = kickoff_utc(p)
            c = {"home_name": p["home"], "away_name": p["away"], "home": {}, "away": {}}
            ev = events.get(p["_id"])
            if ev:
                comp = ev["competitions"][0]
                ids = {x["homeAway"]: x["team"]["id"] for x in comp["competitors"]}
                try:
                    sm = _get(ESPN.format(lg=ESPN_LEAGUES[league], path="summary"), {"event": ev["id"]})
                except Exception as e:
                    log.warning("ESPN summary %s failed: %s", ev["id"], e)
                    sm = {}
                table = _table(sm)
                for side in ("home", "away"):
                    c[side].update(_form(sm, ids[side], kick))
                    c[side]["lineup"] = _lineup(c[side].get("last_id"), ids[side], lu_cache)
                    c[side].update(_next_game(league, ids[side], kick))
                    c[side]["motivation"] = motivation(league, table, ids[side])
                addr = (comp.get("venue") or {}).get("address") or {}
                if addr.get("city"):
                    c["venue"] = (comp.get("venue") or {}).get("fullName")
                    c["weather"] = weather(addr["city"], addr.get("country"), kick, geo_cache)
            if af:
                afc = af_context(af, p, {s: c[s].get("lineup") for s in ("home", "away")})
                for side in ("home", "away"):
                    if side in afc:
                        c[side]["af"] = afc[side]
            c["matchup"] = matchup(*((c[s].get("lineup") or {}).get("formation") for s in ("home", "away")))
            prev = (previous.get(p["_id"]) or {}).get("market") or {}
            c["market_move"] = {k: p["market"][k] - prev[k] for k in p.get("market", {}) if k in prev}
            picks = {b["selection"]: b for b in p.get("bets", []) + p.get("longshots", [])}
            c["verdicts"] = {sel: verdict(b, c) for sel, b in picks.items()}
            c["sources"] = [s for s, ok in (("ESPN", ev), ("Open-Meteo", c.get("weather")), ("API-Football", af)) if ok]
            out[p["_id"]] = c
    if af:
        log.info("API-Football requests left in today's budget: %d", af.left)
    log.info("context for %d of %d games with picks", sum(1 for c in out.values() if c["sources"]), len(targets))
    return out
