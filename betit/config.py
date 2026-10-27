"""Paths, data sources and league metadata."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BETIT_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
WEB_DIR = ROOT / "web"

FOOTBALL_FILE = DATA_DIR / "football_matches.csv"
FIXTURES_FILE = DATA_DIR / "football_fixtures.csv"
TENNIS_FILE = DATA_DIR / "tennis_matches.csv"
BACKTEST_DIR = DATA_DIR / "backtests"

# Primary sources (free, public). football-data.co.uk and tennis-data.co.uk
# publish results together with closing-ish bookmaker odds, which is what a
# value-betting backtest needs.
FOOTBALL_DATA_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
FOOTBALL_FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
TENNIS_DATA_URL = "http://www.tennis-data.co.uk/{year}{suffix}/{year}.xlsx"

# Fallback mirrors on GitHub, used when the primary sites are unreachable.
FOOTBALL_MIRROR_URL = (
    "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data-2000-2025/main/data/Matches.csv"
)
TENNIS_MIRROR_URL = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master/{year}.csv"

LEAGUES = {
    "E0": "England Premier League",
    "E1": "England Championship",
    "E2": "England League One",
    "E3": "England League Two",
    "SC0": "Scotland Premiership",
    "SC1": "Scotland Championship",
    "D1": "Germany Bundesliga",
    "D2": "Germany 2. Bundesliga",
    "I1": "Italy Serie A",
    "I2": "Italy Serie B",
    "SP1": "Spain La Liga",
    "SP2": "Spain Segunda",
    "F1": "France Ligue 1",
    "F2": "France Ligue 2",
    "N1": "Netherlands Eredivisie",
    "B1": "Belgium Pro League",
    "P1": "Portugal Primeira Liga",
    "T1": "Turkey Super Lig",
    "G1": "Greece Super League",
}

# Model defaults. xi is the Dixon-Coles time-decay rate per day
# (0.0019/day is close to the value Dixon & Coles found optimal: half-life ~1 year).
DC_XI = 0.0019
DC_WINDOW_DAYS = 3 * 365
DC_MAX_GOALS = 10

# Second tiers fitted jointly with their top tier so promoted/relegated teams
# carry a rating across divisions instead of starting from scratch.
FEEDER = {"E0": "E1", "E1": "E2", "E2": "E3", "D1": "D2", "I1": "I2",
          "SP1": "SP2", "F1": "F2", "SC0": "SC1"}
