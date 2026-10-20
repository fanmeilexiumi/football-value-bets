"""Daily pipeline: python -m footy [--no-live-odds] [--days-ahead N]."""

from __future__ import annotations

import argparse
import logging
from datetime import date

import pandas as pd

from . import backtest, config, data, odds, report
from .predict import predict_fixtures


def main() -> None:
    ap = argparse.ArgumentParser(description="Premier League / Serie A / La Liga value-bet report")
    ap.add_argument("--no-live-odds", action="store_true", help="skip the OddsPortal scrape")
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
    op = pd.DataFrame(columns=data.COLUMNS) if args.no_live_odds else odds.fetch_oddsportal(known)
    if not op.empty:
        hi = pd.Timestamp(today) + pd.Timedelta(days=args.days_ahead)
        op = op[(op["date"] >= pd.Timestamp(today)) & (op["date"] <= hi)]
    fixtures = odds.merge_fixtures(fd, op)
    log.info("fixtures: %d (football-data %d, OddsPortal %d)", len(fixtures), len(fd), len(op))

    predictions = predict_fixtures(history, fixtures, pd.Timestamp(today))
    bt = {} if args.skip_backtest else backtest.run(history, today)

    status = {
        "history_matches": len(history),
        "history_source": data.LAST_HISTORY_SOURCE,
        "live_odds_ok": not op.empty,
        "live_odds_matches": len(op),
        "fixtures": len(fixtures),
        "days_ahead": args.days_ahead,
    }
    out = report.render(predictions, bt, status)
    n_bets = sum(len(p["bets"]) for p in predictions)
    log.info("wrote %s: %d fixtures, %d value bets", out, len(predictions), n_bets)


if __name__ == "__main__":
    main()
