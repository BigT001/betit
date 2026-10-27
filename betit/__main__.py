"""Command line: python -m betit {update,backtest,serve}"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime

import pandas as pd

from . import backtest, config
from .data import football, tennis
from .models import elo as elo_mod


def cmd_update(args):
    leagues = args.leagues.split(",") if args.leagues else None
    print("football:", json.dumps(football.update(leagues, args.seasons), indent=1))
    print("tennis:", json.dumps(tennis.update(), indent=1))


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, float) and o != o:
        return None
    return o


def cmd_backtest(args):
    config.BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="minutes")
    if args.sport in ("football", "all"):
        results = football.load_results()
        leagues = args.leagues.split(",") if args.leagues else [
            l for l in config.LEAGUES if (results.league == l).sum() > 500]
        pred = backtest.football_predictions(results, leagues, args.start, refit_days=args.refit_days)
        acc = backtest.football_accuracy(pred)
        per_league = []
        for lg, g in pred.groupby("league"):
            a = backtest.football_accuracy(g)
            a.pop("calibration")
            per_league.append({"league": lg, "name": config.LEAGUES.get(lg, lg)} | a)
        out = {"sport": "football", "created": stamp, "start": args.start,
               "end": str(pred.date.max().date()), "leagues": leagues, "accuracy": acc,
               "per_league": per_league,
               "strategies": backtest.run_strategies(pred, backtest.FOOTBALL_SELECTIONS)}
        (config.BACKTEST_DIR / "football.json").write_text(json.dumps(_jsonable(out)))
        print(f"football: {acc['matches']} matches, model logloss {acc['model_logloss']:.4f} "
              f"vs market {acc['market_logloss']:.4f}")
        for k, s in out["strategies"].items():
            if s["bets"]:
                print(f"  {s['label']:<30} bets {s['bets']:>6}  flat ROI {s['flat_roi']:+.2%}  "
                      f"95% CI [{s['flat_roi_ci95'][0]:+.2%}, {s['flat_roi_ci95'][1]:+.2%}]")
    if args.sport in ("tennis", "all"):
        matches = tennis.load()
        _, frame = elo_mod.run(matches)
        pred = backtest.tennis_predictions(frame, args.start)
        acc = backtest.tennis_accuracy(pred)
        per_year = []
        for (tour, yr), g in pred.groupby([pred.tour, pred.date.dt.year]):
            a = backtest.tennis_accuracy(g)
            a.pop("calibration")
            per_year.append({"tour": tour, "year": int(yr)} | a)
        out = {"sport": "tennis", "created": stamp, "start": args.start, "end": str(pred.date.max().date()),
               "accuracy": acc, "per_year": per_year, "strategies": None}
        if pred.odds_a.notna().sum() > 500:
            out["strategies"] = backtest.run_strategies(pred.dropna(subset=["odds_a", "odds_b"]),
                                                        backtest.TENNIS_SELECTIONS, max_odds=10.0)
        (config.BACKTEST_DIR / "tennis.json").write_text(json.dumps(_jsonable(out)))
        print(f"tennis: {acc['matches']} matches, hit rate {acc['model_hit_rate']:.1%}, "
              f"logloss {acc['model_logloss']:.4f}, odds available: {out['strategies'] is not None}")


def cmd_serve(args):
    import uvicorn
    uvicorn.run("betit.api:app", host=args.host, port=args.port)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="betit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="download latest results, odds and fixtures")
    u.add_argument("--leagues", help="comma-separated league codes (default: all)")
    u.add_argument("--seasons", type=int, default=6)
    u.set_defaults(func=cmd_update)
    b = sub.add_parser("backtest", help="walk-forward backtest")
    b.add_argument("--sport", choices=["football", "tennis", "all"], default="all")
    b.add_argument("--start", default=str(pd.Timestamp.today().year - 3) + "-07-01")
    b.add_argument("--leagues")
    b.add_argument("--refit-days", type=int, default=7)
    b.set_defaults(func=cmd_backtest)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    s.set_defaults(func=cmd_serve)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
