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
           cl_predictions: list[dict] | None = None) -> Path:
    site_dir = site_dir or config.SITE_DIR
    site_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(config.ROOT / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters.update(pct=pct, signed_pct=signed_pct, num=num)
    by_league = {code: [p for p in predictions if p["league"] == code] for code in config.LEAGUES}
    cl_predictions = cl_predictions or []
    bets, cl_bets = _bets(predictions), _bets(cl_predictions)
    longs = sorted(
        ({**b, **{k: p[k] for k in ("league", "league_name", "date", "home", "away")},
          "score": (p["model"] or {}).get("scores", {}).get(b["selection"])}
         for p in predictions + cl_predictions for b in p.get("longshots", [])),
        key=lambda b: (b["date"], -b["edge"]),
    )
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
        leagues=config.LEAGUES, by_league=by_league, bets=bets, bt=backtest, status=status,
        cfg=config, bt_rows=bt_rows, cl_games=cl_predictions, cl_bets=cl_bets, longs=longs, long_bt=long_bt,
        svg_long=bankroll_svg(long_bt.get("avg", {}).get("bankroll", [])),
        svg_avg=bankroll_svg(backtest.get("bets", {}).get("avg", {}).get("bankroll", [])),
        svg_max=bankroll_svg(backtest.get("bets", {}).get("max", {}).get("bankroll", [])),
    )
    out = site_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    (site_dir / "predictions.json").write_text(json.dumps(predictions + cl_predictions, ensure_ascii=False, indent=1), encoding="utf-8")
    (site_dir / "backtest.json").write_text(json.dumps(backtest, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _bets(predictions: list[dict]) -> list[dict]:
    return sorted(
        ({**b, **{k: p[k] for k in ("league_name", "date", "home", "away")}} for p in predictions for b in p["bets"]),
        key=lambda b: (b["date"], -b["edge"]),
    )
