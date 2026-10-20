"""传统足彩: the China Sports Lottery 14-match game (胜负彩) and pick-9 (任选九).

The official fixture list comes from 500.com (the lottery's own site refuses
requests from outside China). Games in our leagues or the Champions League use
the same model probabilities and the same recommended result as the schedule
on the page, even when that result is an upset; other games use the market
probabilities 500.com publishes, pulled slightly towards base rates.

One ticket per game: 14-match picks every game with at most two doubles
(4 bets, 8 yuan); pick-9 keeps the nine surest games with at most one double
(2 bets, 4 yuan). Doubles go to the least certain games, preferring the
outcome the model rates above the market (an upset the market underprices).
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np
import requests

from . import config

log = logging.getLogger(__name__)

PICKS = ("3", "1", "0")  # home win, draw, away win in lottery notation
LABELS = {"3": "主胜", "1": "平", "0": "客胜"}
_KEYS = {"3": "H", "1": "D", "0": "A"}
BET_PRICE = 2

# Chinese names used by 500.com, full and shortened, per our canonical name
ZH_NAMES: dict[str, tuple[str, ...]] = {
    # Premier League
    "Arsenal": ("阿森纳",), "Aston Villa": ("阿斯顿维拉", "维拉"), "Bournemouth": ("伯恩茅斯", "伯恩茅"),
    "Brentford": ("布伦特福德", "布伦特"), "Brighton": ("布莱顿",), "Burnley": ("伯恩利",), "Chelsea": ("切尔西",),
    "Crystal Palace": ("水晶宫",), "Everton": ("埃弗顿",), "Fulham": ("富勒姆",), "Leeds": ("利兹联", "利兹"),
    "Liverpool": ("利物浦",), "Man City": ("曼城", "曼彻斯特城"), "Man United": ("曼联", "曼彻斯特联"),
    "Newcastle": ("纽卡斯尔", "纽卡斯", "纽卡"), "Nott'm Forest": ("诺丁汉森林", "诺丁汉", "诺森林"),
    "Sunderland": ("桑德兰",), "Tottenham": ("热刺", "托特纳姆"), "West Ham": ("西汉姆联", "西汉姆"),
    "Wolves": ("狼队",), "Ipswich": ("伊普斯维奇", "伊普斯"), "Coventry": ("考文垂",), "Hull": ("赫尔城", "赫尔"),
    "Leicester": ("莱斯特城", "莱斯特"), "Southampton": ("南安普顿", "南安普"), "Sheffield United": ("谢菲尔德联", "谢菲联"),
    "Norwich": ("诺维奇",), "Middlesbrough": ("米德尔斯堡", "米堡"), "Luton": ("卢顿",),
    # Serie A
    "Inter": ("国际米兰", "国米"), "Milan": ("AC米兰",), "Juventus": ("尤文图斯", "尤文"), "Napoli": ("那不勒斯", "那不勒"),
    "Roma": ("罗马",), "Lazio": ("拉齐奥",), "Atalanta": ("亚特兰大", "亚特兰"), "Fiorentina": ("佛罗伦萨", "佛罗伦"),
    "Bologna": ("博洛尼亚", "博洛尼"), "Torino": ("都灵",), "Genoa": ("热那亚",), "Udinese": ("乌迪内斯", "乌迪内"),
    "Sassuolo": ("萨索洛",), "Cagliari": ("卡利亚里", "卡利亚"), "Lecce": ("莱切",), "Verona": ("维罗纳",),
    "Parma": ("帕尔马",), "Como": ("科莫",), "Cremonese": ("克雷莫纳", "克雷莫"), "Pisa": ("比萨",),
    "Empoli": ("恩波利",), "Monza": ("蒙扎",), "Venezia": ("威尼斯",), "Frosinone": ("弗罗西诺内", "弗罗西"),
    "Palermo": ("巴勒莫",), "Sampdoria": ("桑普多利亚", "桑普多"), "Salernitana": ("萨勒尼塔纳", "萨勒尼"),
    # La Liga
    "Real Madrid": ("皇家马德里", "皇马"), "Barcelona": ("巴塞罗那", "巴萨"), "Ath Madrid": ("马德里竞技", "马竞技", "马竞"),
    "Ath Bilbao": ("毕尔巴鄂", "毕尔巴", "毕巴"), "Sociedad": ("皇家社会", "皇社"), "Betis": ("皇家贝蒂斯", "贝蒂斯"),
    "Sevilla": ("塞维利亚", "塞维利"), "Villarreal": ("比利亚雷亚尔", "比利亚"), "Valencia": ("瓦伦西亚", "瓦伦西"),
    "Celta": ("塞尔塔",), "Osasuna": ("奥萨苏纳", "奥萨苏"), "Getafe": ("赫塔费",), "Mallorca": ("马略卡",),
    "Alaves": ("阿拉维斯", "阿拉维"), "Girona": ("赫罗纳",), "Vallecano": ("巴列卡诺", "巴列卡"),
    "Espanol": ("西班牙人", "西班牙"), "Levante": ("莱万特",), "Elche": ("埃尔切",), "Oviedo": ("奥维耶多", "奥维耶"),
    "Las Palmas": ("拉斯帕尔马斯", "拉斯帕"), "Leganes": ("莱加内斯", "莱加内"), "Valladolid": ("巴拉多利德", "巴拉多"),
    "Cadiz": ("加的斯",), "Granada": ("格拉纳达", "格拉纳"), "Almeria": ("阿尔梅里亚", "阿尔梅"),
    # other Champions League clubs
    "Bayern Munich": ("拜仁慕尼黑", "拜仁"), "Dortmund": ("多特蒙德", "多特"), "Leverkusen": ("勒沃库森", "勒沃"),
    "RB Leipzig": ("莱比锡红牛", "莱比锡"), "Stuttgart": ("斯图加特", "斯图加"), "Eintracht Frankfurt": ("法兰克福", "法兰克"),
    "Paris SG": ("巴黎圣日耳曼", "巴黎", "日尔曼", "大巴黎"), "Marseille": ("马赛",), "Monaco": ("摩纳哥",),
    "Lille": ("里尔",), "Lens": ("朗斯",), "Brest": ("布雷斯特", "布雷斯"), "Benfica": ("本菲卡",), "Porto": ("波尔图",),
    "Sporting CP": ("葡萄牙体育", "里斯本竞技", "葡体"), "Braga": ("布拉加",), "PSV": ("埃因霍温", "埃因霍"),
    "Ajax": ("阿贾克斯", "阿贾克"), "Feyenoord": ("费耶诺德", "费耶诺"), "Club Brugge": ("布鲁日",),
    "Union SG": ("圣吉罗斯", "圣吉"), "Galatasaray": ("加拉塔萨雷", "加拉塔"), "Fenerbahce": ("费内巴切", "费内巴"),
    "Besiktas": ("贝西克塔斯", "贝西克"), "Celtic": ("凯尔特人", "凯尔特"), "Rangers": ("流浪者",),
    "Salzburg": ("萨尔茨堡", "萨尔茨"), "Sturm Graz": ("格拉茨",), "Dinamo Zagreb": ("萨格勒布迪纳摩", "萨格勒"),
    "Crvena Zvezda": ("贝尔格莱德红星", "红星"), "Copenhagen": ("哥本哈根", "哥本哈"), "Bodo/Glimt": ("博德闪耀", "博德"),
    "Olympiakos": ("奥林匹亚科斯", "奥林匹"), "Qarabag": ("卡拉巴赫", "卡拉巴"), "Pafos": ("帕福斯",), "Kairat": ("凯拉特",),
    "Slavia Praha": ("布拉格斯拉维亚", "斯拉维"), "Sparta Praha": ("布拉格斯巴达", "斯巴达"), "Young Boys": ("年轻人",),
    "Shakhtar": ("顿涅茨克矿工", "矿工"), "Slovan Bratislava": ("布拉迪斯拉发", "布拉迪"),
}


# ------------------------------------------------------------------ fetch

def fetch_periods(today: date | None = None) -> list[dict]:
    """On-sale periods from 500.com: [{"expect", "deadline", "games": [...]}]. Empty on failure."""
    today = today or date.today()
    try:
        first = _get(config.ZC_SOURCE_URL)
    except Exception as e:
        log.warning("500.com lottery page failed: %s", e)
        return []
    expects = sorted(set(re.findall(r'data-expect="(\d+)"[^>]*>\s*<i class="ico-chk"></i>[^<]*(?:当前|在售)', first)))
    periods = []
    for e in expects or [None]:
        html = first if e is None or f'class="chked" data-expect="{e}"' in first else None
        if html is None:
            try:
                html = _get(f"{config.ZC_SOURCE_URL}?expect={e}")
            except Exception as ex:
                log.warning("500.com period %s failed: %s", e, ex)
                continue
        p = parse_period(html, today)
        if p["games"] and len(p["games"]) == 14 and not p["closed"]:
            periods.append(p)
    log.info("lottery periods on sale: %s", [p["expect"] for p in periods])
    return periods


def _get(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126"})
    r.raise_for_status()
    return r.content.decode("gb18030", errors="replace")


def parse_period(html: str, today: date) -> dict:
    expect = re.search(r'class="chked" data-expect="(\d+)"', html)
    dl = re.search(r'截止时间：(\d{2})-(\d{2}) (\d{2}):(\d{2})', html)
    deadline = _md_time(today, *dl.groups()) if dl else None
    games = []
    for row in re.findall(r'<tr class="bet-tb-tr.*?</tr>', html, re.S):
        def grab(pat):
            m = re.search(pat, row)
            return m.group(1).strip() if m else None
        no, t = grab(r'td-no">(\d+)'), grab(r'td-endtime">([^<]*)')
        pj = grab(r'data-pjgl="([^"]*)"')
        try:
            mkt = [float(x) for x in pj.split(",")]
            mkt = [x / sum(mkt) for x in mkt]
        except (AttributeError, ValueError, ZeroDivisionError):
            mkt = None
        kick = _md_time(today, *re.match(r"(\d{2})-(\d{2}) (\d{2}):(\d{2})", t).groups()) if t and re.match(r"\d{2}-\d{2} \d{2}:\d{2}", t) else None
        games.append({
            "no": int(no) if no else len(games) + 1,
            "comp": grab(r'td-evt"><a[^>]*>([^<]*)') or "",
            "kickoff": kick.strftime("%Y-%m-%d %H:%M") if kick else None,
            "home_cn": grab(r'class="team-l">([^<]*)</a>') or "", "away_cn": grab(r'class="team-r">([^<]*)</a>') or "",
            "market": mkt,
        })
    now_bj = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    closed = bool(deadline and deadline < now_bj)
    return {"expect": expect.group(1) if expect else "", "deadline": deadline.strftime("%m-%d %H:%M") if deadline else None,
            "closed": closed, "games": games}


def _md_time(today: date, mm, dd, hh, mi) -> datetime:
    """'MM-DD HH:MM' in Beijing time; the year rolls over around New Year."""
    year = today.year + (1 if today.month == 12 and int(mm) == 1 else 0) - (1 if today.month == 1 and int(mm) == 12 else 0)
    return datetime(year, int(mm), int(dd), int(hh), int(mi))


# ----------------------------------------------------------------- match

def _zh_match(cn: str, en: str) -> bool:
    return any(len(v) >= 2 and (cn.startswith(v) or v.startswith(cn)) for v in ZH_NAMES.get(en, ())) if cn else False


def attach_predictions(period: dict, predictions: list[dict]) -> None:
    """Link each lottery game to our prediction of it, when we have one."""
    for g in period["games"]:
        g["pred"] = None
        league = config.ZC_COMPETITIONS.get(g["comp"])
        if not league or not g["kickoff"]:
            continue
        day = datetime.strptime(g["kickoff"][:10], "%Y-%m-%d").date()
        cands = [p for p in predictions if p["league"] == league
                 and abs((datetime.strptime(p["bj_date"], "%Y-%m-%d").date() - day).days) <= 1]
        both = [p for p in cands if _zh_match(g["home_cn"], p["home"]) and _zh_match(g["away_cn"], p["away"])]
        if not both:  # one side matched at the same kick-off is still unambiguous
            both = [p for p in cands if (p.get("bj_time") == g["kickoff"][11:])
                    and (_zh_match(g["home_cn"], p["home"]) or _zh_match(g["away_cn"], p["away"]))]
        if len(both) == 1:
            g["pred"] = both[0]


# ------------------------------------------------------------------ plan

def game_probs(g: dict) -> dict:
    """Probabilities used for the ticket, plus where they came from."""
    mkt = g.get("market")
    pred = g.get("pred")
    model = [pred["model"][k] for k in ("H", "D", "A")] if pred and pred.get("model") else None
    if mkt is None and pred and all(k in pred.get("market", {}) for k in ("H", "D", "A")):
        mkt = [pred["market"][k] for k in ("H", "D", "A")]
    if model:
        p, src = model, "模型"
    elif mkt:
        s = config.ZC_UPSET_SHRINK
        p, src = [(1 - s) * a + s * b for a, b in zip(mkt, config.ZC_BASE_RATES)], "市场"
    else:
        p, src = list(config.ZC_BASE_RATES), "无数据"
    return {"p": dict(zip(PICKS, p)), "model": dict(zip(PICKS, model)) if model else None,
            "market": dict(zip(PICKS, mkt)) if mkt else None, "source": src, "fixed": _page_pick(pred)}


def _page_pick(pred: dict | None) -> str | None:
    """The 1X2 value bet the schedule recommends for this game (highest edge), so the ticket agrees with it.

    Upset and long-odds picks are left out: forcing them cut simulated hits from 7.9 to 6.6 a ticket."""
    if not pred:
        return None
    picks = [b for b in pred.get("bets", []) if b["selection"] in _KEYS.values()]
    if not picks:
        return None
    best = max(picks, key=lambda b: b["edge"])
    return {v: k for k, v in _KEYS.items()}[best["selection"]]


def _double_option(info: dict) -> tuple[str, float, bool]:
    """Second outcome to add, its log-gain score, and whether it is an upset the market underprices."""
    p = info["p"]
    first = _single(info)
    rest = [k for k in PICKS if k != first]
    upset = []
    if info["model"] and info["market"]:
        upset = [k for k in rest if info["model"][k] - info["market"][k] >= config.UPSET_MIN_GAP]
    if first != max(p, key=p.get):  # the page recommends an upset: the double covers the favourite
        second, upset = max(p, key=p.get), [first]
    else:
        second = max(upset, key=p.get) if upset else max(rest, key=p.get)
    score = math.log((p[first] + p[second]) / p[first]) + (config.ZC_UPSET_BONUS if upset else 0.0)
    return second, score, bool(upset)


def _single(info: dict) -> str:
    return info.get("fixed") or max(info["p"], key=info["p"].get)


def plan(probs: list[dict]) -> dict:
    """14-match and pick-9 tickets for one period, from per-game probability dicts."""
    singles = [_single(i) for i in probs]
    options = [_double_option(i) for i in probs]
    top = [i["p"][s] for i, s in zip(probs, singles)]

    # 14 matches: double the least certain games (top pick below ZC_DOUBLE_MAX_P), best score first
    cand = sorted((j for j in range(len(probs)) if top[j] < config.ZC_DOUBLE_MAX_P), key=lambda j: -options[j][1])
    doubles14 = set(cand[: config.ZC_MAX_DOUBLES_14])
    t14 = [_pick(probs[j], singles[j], options[j] if j in doubles14 else None) for j in range(len(probs))]

    # pick-9: the nine surest games, one double where it raises the chance of all nine the most
    best = None
    for d in [None] + list(range(len(probs))):
        if d is not None and top[d] >= config.ZC_DOUBLE_MAX_P:
            continue
        rest = sorted((j for j in range(len(probs)) if j != d), key=lambda j: -top[j])
        chosen = rest[: 9 - (d is not None)] + ([d] if d is not None else [])
        cov = 1.0
        for j in chosen:
            cov *= top[j] + (probs[j]["p"][options[j][0]] if j == d else 0)
        score = cov * (math.exp(config.ZC_UPSET_BONUS) if d is not None and options[d][2] else 1.0)
        if best is None or score > best[0]:
            best = (score, set(chosen), d)
    _, chosen9, d9 = best
    r9 = [_pick(probs[j], singles[j], options[j] if j == d9 else None) if j in chosen9 else None for j in range(len(probs))]
    return {"t14": _ticket(t14), "r9": _ticket(r9)}


def _pick(info: dict, single: str, double) -> dict:
    picks = [single] + ([double[0]] if double else [])
    picks.sort(key=PICKS.index)
    return {"picks": picks, "code": "".join(picks), "hit": sum(info["p"][k] for k in picks),
            "double": bool(double), "upset": bool(double and double[2]), "fixed": bool(info.get("fixed"))}


def _ticket(picks: list[dict | None]) -> dict:
    used = [p for p in picks if p]
    bets = int(np.prod([len(p["picks"]) for p in used])) if used else 0
    return {
        "picks": picks, "bets": bets, "cost": bets * BET_PRICE,
        "code": ",".join(p["code"] if p else "-" for p in picks),
        "p_all": float(np.prod([p["hit"] for p in used])) if used else 0.0,
        "exp_hits": float(sum(p["hit"] for p in used)),
    }


def build(periods: list[dict], predictions: list[dict]) -> list[dict]:
    out = []
    for period in periods:
        attach_predictions(period, predictions)
        probs = [game_probs(g) for g in period["games"]]
        tk = plan(probs)
        games = []
        for j, (g, info) in enumerate(zip(period["games"], probs)):
            pred = g.get("pred")
            games.append({
                **{k: g[k] for k in ("no", "comp", "kickoff", "home_cn", "away_cn")},
                "home": pred["home"] if pred else None, "away": pred["away"] if pred else None,
                **info, "t14": tk["t14"]["picks"][j], "r9": tk["r9"]["picks"][j],
            })
        out.append({**{k: period[k] for k in ("expect", "deadline")}, "simulated": period.get("simulated", False),
                    "games": games, "t14": {k: v for k, v in tk["t14"].items() if k != "picks"},
                    "r9": {k: v for k, v in tk["r9"].items() if k != "picks"}})
    return out


def simulated_period(predictions: list[dict]) -> dict:
    """Fallback when 500.com is unreachable: the next 14 games we model. Cannot be bought."""
    games = [p for p in predictions if p.get("model")][:14]
    return {"expect": "模拟", "deadline": None, "closed": False, "simulated": True, "games": [
        {"no": i + 1, "comp": p["league_name"], "kickoff": f'{p["bj_date"]} {p.get("bj_time") or ""}'.strip(),
         "home_cn": p["home"], "away_cn": p["away"], "market": None, "pred": p}
        for i, p in enumerate(games)]} if len(games) == 14 else None


# ------------------------------------------------------------- validation

def simulate(preds: list[dict], draws: int = 30, seed: int = 0) -> dict:
    """Replay the rules on random 14-game sets from backtest weeks.

    `preds` are backtest rows with blended model probabilities ("blend"),
    market probabilities ("mkt") and the result index ("res", 0 home, 1 draw,
    2 away). Compares our tickets with always taking the market favourite.
    """
    rng = np.random.default_rng(seed)
    rows = [p for p in preds if None not in p["mkt"]]
    weeks: dict = {}
    for p in rows:
        weeks.setdefault(p["date"].to_period("W"), []).append(p)
    hits14, base14, r9_ok, base9_ok, ge13, base_ge13 = [], [], [], [], [], []
    for games in weeks.values():
        if len(games) < 14:
            continue
        for _ in range(draws):
            sample = [games[i] for i in rng.choice(len(games), 14, replace=False)]
            probs = [{"p": dict(zip(PICKS, g["blend"])), "model": dict(zip(PICKS, g["blend"])),
                      "market": dict(zip(PICKS, g["mkt"])), "fixed": _page_pick({"bets": g.get("page_bets", [])})}
                     for g in sample]
            tk = plan(probs)
            res = [PICKS[g["res"]] for g in sample]
            h = sum(r in p["picks"] for r, p in zip(res, tk["t14"]["picks"]))
            fav = [PICKS[int(np.argmax(g["mkt"]))] for g in sample]
            b = sum(r == f for r, f in zip(res, fav))
            hits14.append(h)
            base14.append(b)
            ge13.append(h >= 13)
            base_ge13.append(b >= 13)
            r9_ok.append(all(r in p["picks"] for r, p in zip(res, tk["r9"]["picks"]) if p))
            sure9 = sorted(range(14), key=lambda j: -max(sample[j]["mkt"]))[:9]
            base9_ok.append(all(res[j] == fav[j] for j in sure9))
    if not hits14:
        return {"n": 0}
    return {
        "n": len(hits14),
        "hits14": float(np.mean(hits14)), "base_hits14": float(np.mean(base14)),
        "ge13": float(np.mean(ge13)), "base_ge13": float(np.mean(base_ge13)),
        "all14": float(np.mean([h == 14 for h in hits14])),
        "r9": float(np.mean(r9_ok)), "base_r9": float(np.mean(base9_ok)),
        "dist14": [int(np.sum(np.array(hits14) == k)) for k in range(15)],
    }
