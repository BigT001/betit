"""Download and normalise football results + bookmaker odds.

Every source is mapped onto one schema so the models never care where data
came from:

    date, league, season, home, away, hg, ag, hs, as_, hst, ast, hc, ac,
    odds_h, odds_d, odds_a, max_h, max_d, max_a,
    odds_o25, odds_u25, max_o25, max_u25
"""
from __future__ import annotations

import io
import logging
from datetime import date

import numpy as np
import pandas as pd
import requests

from .. import config

log = logging.getLogger(__name__)

COLUMNS = [
    "date", "league", "season", "home", "away", "hg", "ag",
    "hs", "as_", "hst", "ast", "hc", "ac",
    "odds_h", "odds_d", "odds_a", "max_h", "max_d", "max_a",
    "odds_o25", "odds_u25", "max_o25", "max_u25",
]

# For each normalised field, the football-data.co.uk columns to try in order.
# Market average odds are preferred over a single bookmaker because they are
# a fairer estimate of the price a normal bettor can get.
FD_CANDIDATES = {
    "home": ["HomeTeam", "Home", "HT"],
    "away": ["AwayTeam", "Away", "AT"],
    "hg": ["FTHG", "HG"],
    "ag": ["FTAG", "AG"],
    "hs": ["HS"], "as_": ["AS"], "hst": ["HST"], "ast": ["AST"],
    "hc": ["HC"], "ac": ["AC"],
    "odds_h": ["AvgH", "BbAvH", "B365H", "PSH", "WHH"],
    "odds_d": ["AvgD", "BbAvD", "B365D", "PSD", "WHD"],
    "odds_a": ["AvgA", "BbAvA", "B365A", "PSA", "WHA"],
    "max_h": ["MaxH", "BbMxH", "PSH"],
    "max_d": ["MaxD", "BbMxD", "PSD"],
    "max_a": ["MaxA", "BbMxA", "PSA"],
    "odds_o25": ["Avg>2.5", "BbAv>2.5", "B365>2.5", "P>2.5"],
    "odds_u25": ["Avg<2.5", "BbAv<2.5", "B365<2.5", "P<2.5"],
    "max_o25": ["Max>2.5", "BbMx>2.5", "P>2.5"],
    "max_u25": ["Max<2.5", "BbMx<2.5", "P<2.5"],
}

MIRROR_MAP = {
    "Division": "league", "MatchDate": "date", "HomeTeam": "home", "AwayTeam": "away",
    "FTHome": "hg", "FTAway": "ag", "HomeShots": "hs", "AwayShots": "as_",
    "HomeTarget": "hst", "AwayTarget": "ast", "HomeCorners": "hc", "AwayCorners": "ac",
    "OddHome": "odds_h", "OddDraw": "odds_d", "OddAway": "odds_a",
    "MaxHome": "max_h", "MaxDraw": "max_d", "MaxAway": "max_a",
    "Over25": "odds_o25", "Under25": "odds_u25", "MaxOver25": "max_o25", "MaxUnder25": "max_u25",
}


def season_of(d: pd.Timestamp) -> str:
    """European season label, e.g. 2025-08-16 -> '2526'."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def season_codes(n: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return [f"{(y % 100):02d}{((y + 1) % 100):02d}" for y in range(start - n + 1, start + 1)]


def _first(df: pd.DataFrame, names: list[str]) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype="object")
    for n in names:
        if n in df.columns:
            out = out.where(out.notna(), df[n])
    return out


def normalise_football_data(raw: pd.DataFrame, league: str | None = None) -> pd.DataFrame:
    """Map a football-data.co.uk CSV (results or fixtures) onto COLUMNS."""
    raw = raw.dropna(how="all")
    if raw.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.DataFrame(index=raw.index)
    out["date"] = pd.to_datetime(raw["Date"], dayfirst=True, errors="coerce", format="mixed")
    out["league"] = raw["Div"] if "Div" in raw.columns else league
    for field, names in FD_CANDIDATES.items():
        out[field] = _first(raw, names)
    out = out.dropna(subset=["date", "home", "away"])
    out["season"] = out["date"].map(season_of)
    return _coerce(out)


def normalise_mirror(raw: pd.DataFrame) -> pd.DataFrame:
    out = raw.rename(columns=MIRROR_MAP)[[c for c in MIRROR_MAP.values()]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date", "home", "away", "hg", "ag"])
    out["season"] = out["date"].map(season_of)
    return _coerce(out)


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    num = [c for c in COLUMNS if c not in ("date", "league", "season", "home", "away")]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    df["home"] = df["home"].astype(str).str.strip()
    df["away"] = df["away"].astype(str).str.strip()
    # Odds below 1.01 are data errors.
    odds_cols = [c for c in num if c.startswith(("odds_", "max_"))]
    df[odds_cols] = df[odds_cols].where(df[odds_cols] > 1.01)
    return df[COLUMNS].sort_values("date").reset_index(drop=True)


def _get(url: str, timeout: int = 30) -> bytes:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "betit/1.0 (research)"})
    r.raise_for_status()
    return r.content


def fetch_football_data(leagues: list[str], n_seasons: int = 6) -> pd.DataFrame:
    """Results from football-data.co.uk. Raises if the site is unreachable."""
    frames = []
    for season in season_codes(n_seasons):
        for div in leagues:
            url = config.FOOTBALL_DATA_URL.format(season=season, div=div)
            try:
                raw = pd.read_csv(io.BytesIO(_get(url)), encoding="latin-1", on_bad_lines="skip")
            except requests.HTTPError as e:
                log.info("skip %s: %s", url, e)
                continue
            df = normalise_football_data(raw, league=div)
            frames.append(df.dropna(subset=["hg", "ag"]))
            log.info("football-data.co.uk %s %s: %d matches", div, season, len(df))
    if not frames:
        raise RuntimeError("football-data.co.uk returned no data")
    return pd.concat(frames, ignore_index=True)


def fetch_mirror(leagues: list[str], since_year: int) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(_get(config.FOOTBALL_MIRROR_URL, timeout=180)), low_memory=False)
    df = normalise_mirror(raw)
    return df[df.league.isin(leagues) & (df.date.dt.year >= since_year)].reset_index(drop=True)


def fetch_fixtures() -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(_get(config.FOOTBALL_FIXTURES_URL)), encoding="latin-1", on_bad_lines="skip")
    df = normalise_football_data(raw)
    return df[df.league.isin(config.LEAGUES)].reset_index(drop=True)


def update(leagues: list[str] | None = None, n_seasons: int = 6) -> dict:
    """Refresh local results + fixtures. Returns a short status report."""
    leagues = leagues or list(config.LEAGUES)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    status = {}
    try:
        df = fetch_football_data(leagues, n_seasons)
        status["results_source"] = "football-data.co.uk"
    except Exception as e:  # network blocked, site down, ...
        log.warning("football-data.co.uk unavailable (%s); using GitHub mirror", e)
        df = fetch_mirror(leagues, since_year=date.today().year - n_seasons)
        status["results_source"] = "github mirror (xgabora/Club-Football-Match-Data)"
    df = df.drop_duplicates(subset=["date", "home", "away"], keep="last").sort_values("date")
    df.to_csv(config.FOOTBALL_FILE, index=False)
    status["results"] = len(df)
    status["latest_result"] = str(df.date.max().date())
    try:
        fx = fetch_fixtures()
        fx.to_csv(config.FIXTURES_FILE, index=False)
        status["fixtures"] = len(fx)
    except Exception as e:
        log.warning("fixtures unavailable: %s", e)
        status["fixtures"] = 0
        status["fixtures_error"] = str(e)[:200]
    return status


def load_results() -> pd.DataFrame:
    if not config.FOOTBALL_FILE.exists():
        raise FileNotFoundError("No football data yet. Run: python -m betit update")
    return pd.read_csv(config.FOOTBALL_FILE, parse_dates=["date"], dtype={"season": str})


def load_fixtures() -> pd.DataFrame:
    if not config.FIXTURES_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    fx = pd.read_csv(config.FIXTURES_FILE, parse_dates=["date"], dtype={"season": str})
    today = pd.Timestamp(date.today())
    return fx[fx.date >= today].reset_index(drop=True)
