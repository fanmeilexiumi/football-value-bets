"""Daily pipeline: python -m footy [--no-live-odds] [--days-ahead N]."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

import pandas as pd

from . import backtest, config, data, odds, odds_api, report, ucl, zucai
from .predict import predict_cl, predict_fixtures


def main() -> None:
    ap = argparse.ArgumentParser(description="Premier League / Serie A / La Liga / Champions League value-bet report")
    ap.add_argument("--no-live-odds", action="store_true", help="skip OddsPortal and The Odds API")
    ap.add_argument("--days-ahead", type=int, default=10)
    ap.add_argument("--skip-backtest", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("footy")

    today = date.today()
    history = data.load_history(today)
    log.info("history: %d matches, %s to %s", len(history), history["date"].min().date(), history["date"].max().date())
    known = set(history["home"]) | set(history["away"])

    fd = data.load_fixtures(today, args.days_ahead)
    api = pd.DataFrame(columns=data.COLUMNS) if args.no_live_odds else odds_api.fetch(known)
    op = pd.DataFrame(columns=data.COLUMNS) if args.no_live_odds else odds.fetch_oddsportal(known)
    lo, hi = pd.Timestamp(today), pd.Timestamp(today) + pd.Timedelta(days=args.days_ahead)
    api = api[(api["date"] >= lo) & (api["date"] <= hi)]
    op = op[(op["date"] >= lo) & (op["date"] <= hi)]
    fixtures = odds.merge_fixtures(("OddsPortal", op), ("The Odds API", api), ("football-data", fd))
    log.info("fixtures: %d (OddsPortal %d, The Odds API %d, football-data %d)", len(fixtures), len(op), len(api), len(fd))

    predictions = predict_fixtures(history, fixtures, pd.Timestamp(today))

    cl_results = ucl.load_results(today, history)
    log.info("Champions League results: %d games, seasons %s", len(cl_results), ucl.LAST_CL_SEASONS)
    cl_model = ucl.CLModel(history, cl_results, pd.Timestamp(today))
    cl_known = set(cl_model.elo.ratings)
    cl_fx = pd.DataFrame(columns=data.COLUMNS) if args.no_live_odds else odds_api.fetch(cl_known, sports=odds_api.CL_SPORT_KEYS)
    cl_fx = cl_fx[(cl_fx["date"] >= lo) & (cl_fx["date"] <= hi)]
    cl_fx = odds.merge_fixtures(("The Odds API", cl_fx))
    cl_predictions = predict_cl(cl_model, cl_fx)
    unknown = sorted({t for t in pd.concat([cl_fx["home"], cl_fx["away"]]) if t not in cl_known})
    if unknown:
        log.warning("Champions League clubs without a rating: %s", unknown)
    bt = {} if args.skip_backtest else backtest.run(history, today)

    all_preds = sorted(predictions + cl_predictions, key=lambda p: (p["bj_date"], p.get("bj_time") or "99:99"))
    periods = [] if args.no_live_odds else zucai.fetch_periods(today)
    if not periods:
        sim = zucai.simulated_period(all_preds)
        periods = [sim] if sim else []
    lottery = zucai.build(periods, all_preds)
    for lp in lottery:
        log.info("lottery %s: 14-match %s (%d yuan), pick-9 %s (%d yuan), %d/14 games priced by our model",
                 lp["expect"], lp["t14"]["code"], lp["t14"]["cost"], lp["r9"]["code"], lp["r9"]["cost"],
                 sum(g["model"] is not None for g in lp["games"]))

    status = {
        "history_matches": len(history),
        "history_source": data.LAST_HISTORY_SOURCE,
        "live_sources": {"OddsPortal": len(op), "The Odds API": len(api), "football-data": len(fd)},
        "odds_api_enabled": bool(os.environ.get("THE_ODDS_API_KEY")),
        "fixtures": len(fixtures),
        "days_ahead": args.days_ahead,
        "cl_results": len(cl_results),
        "cl_seasons": ucl.LAST_CL_SEASONS,
        "cl_last_result": cl_results["date"].max().strftime("%Y-%m-%d") if len(cl_results) else None,
        "cl_current_missing": ucl.cl_seasons(today)[-1] not in ucl.LAST_CL_SEASONS,
        "cl_fixtures": len(cl_fx),
        "cl_offsets": {c: round(100 * v) for c, v in sorted(cl_model.offset.items(), key=lambda kv: -kv[1])},
    }
    out = report.render(predictions, bt, status, cl_predictions=cl_predictions, lottery=lottery)
    n_bets = sum(len(p["bets"]) for p in predictions)
    n_cl = sum(len(p["bets"]) for p in cl_predictions)
    log.info("wrote %s: %d fixtures, %d value bets; Champions League %d fixtures, %d value bets",
             out, len(predictions), n_bets, len(cl_predictions), n_cl)


if __name__ == "__main__":
    main()
