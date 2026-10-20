"""Daily pipeline: python -m footy [--no-live-odds] [--days-ahead N]."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

import pandas as pd

from . import backtest, config, data, odds, odds_api, report
from .predict import predict_fixtures


def main() -> None:
    ap = argparse.ArgumentParser(description="Premier League / Serie A / La Liga value-bet report")
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
    bt = {} if args.skip_backtest else backtest.run(history, today)

    status = {
        "history_matches": len(history),
        "history_source": data.LAST_HISTORY_SOURCE,
        "live_sources": {"OddsPortal": len(op), "The Odds API": len(api), "football-data": len(fd)},
        "odds_api_enabled": bool(os.environ.get("THE_ODDS_API_KEY")),
        "fixtures": len(fixtures),
        "days_ahead": args.days_ahead,
    }
    out = report.render(predictions, bt, status)
    n_bets = sum(len(p["bets"]) for p in predictions)
    log.info("wrote %s: %d fixtures, %d value bets", out, len(predictions), n_bets)


if __name__ == "__main__":
    main()
