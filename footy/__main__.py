"""Daily pipeline: python -m footy [--no-live-odds] [--days-ahead N] [--session am|pm].

Runs twice a day: 11:00 Beijing ("am": review, then picks from the model alone) and
19:00 ("pm": the same picks re-priced with the off-pitch factors, see adjust.py).
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, timedelta

import pandas as pd

from . import adjust, backtest, config, context, data, journal, learn, odds, odds_api, report, ucl, zucai
from .predict import predict_cl, predict_fixtures


def main() -> None:
    ap = argparse.ArgumentParser(description="Premier League / Serie A / La Liga / Champions League value-bet report")
    ap.add_argument("--no-live-odds", action="store_true", help="skip OddsPortal and The Odds API")
    ap.add_argument("--days-ahead", type=int, default=10)
    ap.add_argument("--skip-backtest", action="store_true")
    ap.add_argument("--session", choices=("am", "pm"), help="default: pm from EVENING_FROM_HOUR Beijing time")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("footy")

    now = journal.now_bj()
    today = now.date()
    session = args.session or ("pm" if now.hour >= config.EVENING_FROM_HOUR else "am")
    log.info("session %s (%s Beijing)", session, now.strftime("%H:%M"))
    learned = learn.load_and_apply()
    history = data.load_history(today)
    log.info("history: %d matches, %s to %s", len(history), history["date"].min().date(), history["date"].max().date())

    # review first: settle yesterday's games, then let the weekly tuning see them
    records = journal.load("predictions.json", [])
    periods_j = journal.load("lottery.json", [])
    journal.settle(records, history, now, api_key="" if args.no_live_odds else None)
    if not args.no_live_odds:
        journal.settle_lottery(periods_j, records, now)
    bt = {} if args.skip_backtest else backtest.run(history, today)
    bt_preds = bt.pop("preds", [])
    if bt_preds and learn.due(learned, today):
        learned = learn.tune(bt_preds, records, today, learned)
        journal.save("learned.json", learned)
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

    all_preds = sorted(predictions + cl_predictions, key=lambda p: (p["bj_date"], p.get("bj_time") or "99:99"))
    by_id = {r["id"]: r for r in records}
    ctx = {} if args.no_live_odds else context.collect(all_preds, by_id, now - timedelta(hours=8))
    for p in all_preds:
        p["context"] = ctx.get(zucai.pred_id(p))
    if session == "pm":
        n_changed = adjust.evening(all_preds, ctx, by_id)
        log.info("evening update: factors weighted %s, %d games changed their picks since 11:00", config.CTX_WEIGHT, n_changed)
    else:
        for p in all_preds:
            p["ctx_adj"] = adjust.deltas(p["context"])  # stored for the weekly check, not applied
    periods = [] if args.no_live_odds else zucai.fetch_periods(today)
    if not periods:
        sim = zucai.simulated_period(all_preds)
        periods = [sim] if sim else []
    lottery = zucai.build(periods, all_preds)
    for lp in lottery:
        log.info("lottery %s: 14-match %s (%d yuan), pick-9 %s (%d yuan), %d/14 games priced by our model",
                 lp["expect"], lp["t14"]["code"], lp["t14"]["cost"], lp["r9"]["code"], lp["r9"]["cost"],
                 sum(g["model"] is not None for g in lp["games"]))

    journal.record(records, all_preds, now, session)
    journal.record_lottery(periods_j, lottery)
    journal.save("predictions.json", records)
    journal.save("lottery.json", periods_j)
    review = journal.build_review(records, periods_j, now)
    learned_view = {"state": learned, "params": [
        {"name": k, "label": lbl, "default": learn.DEFAULTS[k], "current": getattr(config, k)}
        for k, (_, _, lbl) in learn.PARAMS.items()]}

    status = {
        "history_matches": len(history),
        "history_source": data.LAST_HISTORY_SOURCE,
        "live_sources": {"OddsPortal": len(op), "The Odds API": len(api), "football-data": len(fd)},
        "odds_api_enabled": bool(os.environ.get("THE_ODDS_API_KEY")),
        "fixtures": len(fixtures),
        "session": session,
        "updated_bj": now.strftime("%Y-%m-%d %H:%M"),
        "days_ahead": args.days_ahead,
        "cl_results": len(cl_results),
        "cl_seasons": ucl.LAST_CL_SEASONS,
        "cl_last_result": cl_results["date"].max().strftime("%Y-%m-%d") if len(cl_results) else None,
        "cl_current_missing": ucl.cl_seasons(today)[-1] not in ucl.LAST_CL_SEASONS,
        "cl_fixtures": len(cl_fx),
        "cl_offsets": {c: round(100 * v) for c, v in sorted(cl_model.offset.items(), key=lambda kv: -kv[1])},
    }
    out = report.render(predictions, bt, status, cl_predictions=cl_predictions, lottery=lottery,
                        review=review, learned=learned_view)
    n_bets = sum(len(p["bets"]) for p in predictions)
    n_cl = sum(len(p["bets"]) for p in cl_predictions)
    log.info("wrote %s: %d fixtures, %d value bets; Champions League %d fixtures, %d value bets",
             out, len(predictions), n_bets, len(cl_predictions), n_cl)


if __name__ == "__main__":
    main()
