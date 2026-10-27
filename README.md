# betit

A local web app that analyses football matches and estimates football and tennis outcome probabilities, compares
them with bookmaker prices, sizes stakes with fractional Kelly, and shows an honest walk-forward backtest of
how each betting rule would have done.

> **It is not a money machine.** In our backtest (19 leagues, 20,530 matches, Jul 2023 to Sep 2026) the
> bookmakers' prices were more accurate than the model, and bets picked by the model alone lost about 11% of
> stakes. Only "line shopping" (taking the best price when it beats the market's average fair price) came out
> ahead, at +6.6% flat ROI on 962 bets, with a 95% range of −2% to +16%. In other words, that profit is not proven,
> and historical best prices are optimistic. Please read the *Track record* tab before staking anything.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m betit update      # download results, odds and upcoming fixtures (a few minutes)
python -m betit backtest    # walk-forward backtest, about 2 minutes
python -m betit serve       # open http://127.0.0.1:8000
```

Run `update` daily (for example from cron) to refresh fixtures and ratings, then click *Refresh* in the app or
call `POST /api/reload`.

## What's inside

| Part | Method |
| --- | --- |
| Football model | Dixon-Coles (1997) bivariate Poisson with low-score correction, exponential time decay (half-life about 1 year), ridge shrinkage, a 30% blend of shots-on-target-based goals, and the second division fitted jointly so promoted teams keep a rating. Gives 1X2, over/under, BTTS, double chance and the full score matrix. |
| Football analysis | Season, home/away and last-10 form; shots and shots on target; finishing and goalkeeping rates (flagged when likely to regress); head to head; league table; team attack/defence ratings. |
| Tennis model | Surface-aware Elo (overall and surface ratings averaged), K = 250/(n+5)^0.4, with a set-level conversion for best-of-5. |
| Market view | Bookmaker margin removed with the power method, which handles the favourite-longshot bias. |
| Flags | A price is flagged when the best price beats the market's average fair price by your minimum edge and the model doesn't disagree. With only one bookmaker's prices, flags use a 50/50 model and market blend and are labelled *unproven*. |
| Staking | Fractional Kelly (25% by default) with a maximum stake per bet (3% by default). |
| Backtest | Weekly refit on only past data, settled at the odds actually offered; four strategies fixed in advance; flat ROI with a bootstrap 95% range; Kelly bankroll curve, max drawdown, longest losing run, calibration. |

## Data sources

* **Football:** [football-data.co.uk](https://www.football-data.co.uk) results, match stats, average/max odds and
  upcoming fixtures. If it can't be reached, results come from the
  [xgabora/Club-Football-Match-Data](https://github.com/xgabora/Club-Football-Match-Data-2000-2025) mirror (no fixtures).
* **Tennis:** [tennis-data.co.uk](http://www.tennis-data.co.uk) ATP and WTA results with odds. The fallback is the
  [TML-Database](https://github.com/Tennismylife/TML-Database) (ATP only, no odds, **non-commercial use only**).

Both primary sites ask for non-commercial, personal use. Check their terms before running this as a public or paid
service.

## API

`GET /api/status`, `GET /api/football/leagues`, `GET /api/football/ratings/{league}`, `GET /api/football/table/{league}`,
`POST /api/football/match`, `GET /api/football/board`, `GET /api/tennis/players?q=`, `POST /api/tennis/match`,
`GET /api/backtests`, `POST /api/reload`. Interactive docs are at `/docs`.

## Tests

```bash
pip install pytest && python -m pytest -q
```

## Responsible gambling

Only bet what you can afford to lose, set deposit and loss limits, and never chase losses. Losing runs of 10-24
bets happened in every strategy in the backtest. Free, confidential help: BeGambleAware.org, GAMSTOP (UK),
1-800-GAMBLER (US). 18+ only, and only where betting is legal.
