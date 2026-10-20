"""Render the static site published to GitHub Pages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config

MARKET_LABEL = {"1X2": "胜平负", "O/U 2.5": "大小球 2.5"}


def pct(v, digits=1):
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def signed_pct(v, digits=1):
    return "–" if v is None else f"{v * 100:+.{digits}f}%"


def num(v, digits=2):
    return "–" if v is None else f"{v:.{digits}f}"


def bankroll_svg(curve: list[list], width: int = 720, height: int = 220) -> str:
    """Inline SVG line of the Kelly bankroll, start = 100."""
    if not curve:
        return ""
    vals = [v for _, v in curve]
    lo, hi = min(min(vals), 100.0), max(max(vals), 100.0)
    pad_l, pad_r, pad_t, pad_b = 44, 12, 12, 26
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    span = hi - lo or 1.0

    def x(i):
        return pad_l + (w * i / max(1, len(vals) - 1))

    def y(v):
        return pad_t + h * (1 - (v - lo) / span)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    area = f"{pad_l},{y(lo):.1f} {pts} {x(len(vals) - 1):.1f},{y(lo):.1f}"
    ticks = sorted({round(lo), 100, round(hi)})
    grid = "".join(
        f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
        f'<text x="{pad_l - 6}" y="{y(t) + 4:.1f}" text-anchor="end" class="axis">{t}</text>'
        for t in ticks
    )
    first, last = curve[0][0], curve[-1][0]
    end = vals[-1]
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="资金曲线，从 100 到 {end:.1f}">'
        f"{grid}"
        f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(100):.1f}" y2="{y(100):.1f}" class="base"/>'
        f'<polygon points="{area}" class="area"/><polyline points="{pts}" class="line"/>'
        f'<circle cx="{x(len(vals) - 1):.1f}" cy="{y(end):.1f}" r="3.5" class="dot"/>'
        f'<text x="{pad_l}" y="{height - 6}" class="axis">{first}</text>'
        f'<text x="{width - pad_r}" y="{height - 6}" text-anchor="end" class="axis">{last}</text>'
        "</svg>"
    )


def render(predictions: list[dict], backtest: dict, status: dict, site_dir: Path | None = None,
           cl_predictions: list[dict] | None = None, lottery: list[dict] | None = None) -> Path:
    site_dir = site_dir or config.SITE_DIR
    site_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(config.ROOT / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters.update(pct=pct, signed_pct=signed_pct, num=num)
    cl_predictions = cl_predictions or []
    games = sorted(predictions + cl_predictions, key=_game_key)
    for g in games:
        g["picks"] = _picks(g)
    picks = [{**pk, "game": g} for g in games for pk in g["picks"]]
    days = [{"date": d, "label": _day_label(d), "games": [g for g in games if g["bj_date"] == d]}
            for d in sorted({g["bj_date"] for g in games})]
    comps = [(code, v["name"]) for code, v in config.LEAGUES.items()] + [(config.CL["code"], config.CL["name"])]
    long_bt = backtest.get("long", {})
    bt_rows = {}
    for price, block in backtest.get("bets", {}).items():
        rows = [("全部", block["all"])]
        rows += [(f"{k} 赛季", v) for k, v in block.get("by_season", {}).items()]
        rows += [(config.LEAGUES[k]["name"], v) for k, v in block.get("by_league", {}).items()]
        rows += [(MARKET_LABEL.get(k, k), v) for k, v in block.get("by_market", {}).items()]
        bt_rows[price] = rows
    html = env.get_template("report.html.j2").render(
        updated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        days=days, picks=picks, comps=comps, n_games=len(games), bt=backtest, status=status,
        cfg=config, bt_rows=bt_rows, long_bt=long_bt, lottery=lottery or [], zc_sim=backtest.get("zucai", {}),
        svg_long=bankroll_svg(long_bt.get("avg", {}).get("bankroll", [])),
        svg_avg=bankroll_svg(backtest.get("bets", {}).get("avg", {}).get("bankroll", [])),
        svg_max=bankroll_svg(backtest.get("bets", {}).get("max", {}).get("bankroll", [])),
    )
    out = site_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    (site_dir / "predictions.json").write_text(json.dumps(predictions + cl_predictions, ensure_ascii=False, indent=1), encoding="utf-8")
    (site_dir / "lottery.json").write_text(json.dumps(lottery or [], ensure_ascii=False, indent=1), encoding="utf-8")
    (site_dir / "backtest.json").write_text(json.dumps(backtest, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


WEEKDAYS = "一二三四五六日"
_ORDER = {code: i for i, code in enumerate(list(config.LEAGUES) + [config.CL["code"]])}


def _game_key(g: dict) -> tuple:
    return g["bj_date"], g["bj_time"] or "99:99", _ORDER.get(g["league"], 9), g["home"]


def _day_label(d: str) -> str:
    t = datetime.strptime(d, "%Y-%m-%d")
    return f"{t.month}月{t.day}日 周{WEEKDAYS[t.weekday()]}"


def _picks(g: dict) -> list[dict]:
    """Value bets and long-odds picks of one game, merged per selection."""
    scores = (g.get("model") or {}).get("scores", {})
    out: dict[str, dict] = {}
    for b in g.get("bets", []):
        out[b["selection"]] = {**b, "tags": ["价值"], "score": scores.get(b["selection"])}
    for b in g.get("longshots", []):
        if b["selection"] in out:
            out[b["selection"]]["tags"] += b["tags"]
        else:
            out[b["selection"]] = {**b, "tags": list(b["tags"]), "score": scores.get(b["selection"])}
    return sorted(out.values(), key=lambda b: -b["edge"])
