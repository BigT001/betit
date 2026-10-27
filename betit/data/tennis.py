"""Tennis results (+ odds when available), normalised to one schema:

    date, tour, tournament, surface, round, best_of, winner, loser,
    w_rank, l_rank, odds_w, odds_l, max_w, max_l

tennis-data.co.uk is preferred because it includes bookmaker odds for ATP
and WTA. The TML GitHub mirror (ATP only, no odds) is the fallback.
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

COLUMNS = ["date", "tour", "tournament", "surface", "round", "best_of", "winner", "loser",
           "w_rank", "l_rank", "odds_w", "odds_l", "max_w", "max_l"]

ROUND_ORDER = {"Q1": 0, "Q2": 1, "Q3": 2, "R128": 3, "1st Round": 3, "R64": 4, "2nd Round": 4,
               "R32": 5, "3rd Round": 5, "R16": 6, "4th Round": 6, "RR": 6, "Round Robin": 6,
               "QF": 7, "Quarterfinals": 7, "SF": 8, "Semifinals": 8, "BR": 8.5, "F": 9, "The Final": 9}


def _first(df, names):
    out = pd.Series(np.nan, index=df.index, dtype="object")
    for n in names:
        if n in df.columns:
            out = out.where(out.notna(), df[n])
    return out


def normalise_tennis_data(raw: pd.DataFrame, tour: str) -> pd.DataFrame:
    """Map a tennis-data.co.uk sheet onto COLUMNS."""
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["Date"], errors="coerce"),
        "tour": tour,
        "tournament": raw.get("Tournament"),
        "surface": raw.get("Surface"),
        "round": raw.get("Round"),
        "best_of": pd.to_numeric(raw.get("Best of"), errors="coerce"),
        "winner": raw["Winner"].astype(str).str.strip(),
        "loser": raw["Loser"].astype(str).str.strip(),
        "w_rank": pd.to_numeric(raw.get("WRank"), errors="coerce"),
        "l_rank": pd.to_numeric(raw.get("LRank"), errors="coerce"),
        "odds_w": pd.to_numeric(_first(raw, ["AvgW", "B365W", "PSW"]), errors="coerce"),
        "odds_l": pd.to_numeric(_first(raw, ["AvgL", "B365L", "PSL"]), errors="coerce"),
        "max_w": pd.to_numeric(_first(raw, ["MaxW", "PSW"]), errors="coerce"),
        "max_l": pd.to_numeric(_first(raw, ["MaxL", "PSL"]), errors="coerce"),
    })
    if "Comment" in raw.columns:  # walkovers carry no information about strength
        out = out[~raw["Comment"].astype(str).str.contains("Walkover|w/o", case=False, na=False)]
    return _finish(out)


def normalise_tml(raw: pd.DataFrame) -> pd.DataFrame:
    score = raw["score"].astype(str)
    raw = raw[~score.str.contains("W/O|walkover|DEF|unfinished", case=False, na=False)]
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["tourney_date"].astype(str), format="%Y%m%d", errors="coerce"),
        "tour": "ATP",
        "tournament": raw["tourney_name"],
        "surface": raw["surface"],
        "round": raw["round"],
        "best_of": pd.to_numeric(raw["best_of"], errors="coerce"),
        "winner": raw["winner_name"].astype(str).str.strip(),
        "loser": raw["loser_name"].astype(str).str.strip(),
        "w_rank": pd.to_numeric(raw["winner_rank"], errors="coerce"),
        "l_rank": pd.to_numeric(raw["loser_rank"], errors="coerce"),
        "match_num": pd.to_numeric(raw["match_num"], errors="coerce"),
    })
    # Tournament start date for every match: keep draw order within the event.
    out = out.sort_values(["date", "tournament", "match_num"]).drop(columns="match_num")
    for c in ("odds_w", "odds_l", "max_w", "max_l"):
        out[c] = np.nan
    return _finish(out, keep_order=True)


def _finish(df: pd.DataFrame, keep_order: bool = False) -> pd.DataFrame:
    df = df.dropna(subset=["date", "winner", "loser"])
    df["surface"] = df["surface"].fillna("Hard").astype(str).str.capitalize()
    df["best_of"] = df["best_of"].fillna(3).astype(int)
    for c in ("odds_w", "odds_l", "max_w", "max_l"):
        df[c] = df[c].where(df[c] > 1.01)
    if not keep_order:
        df = df.assign(_r=df["round"].map(ROUND_ORDER).fillna(5)).sort_values(
            ["date", "_r"], kind="stable").drop(columns="_r")
    return df[COLUMNS].reset_index(drop=True)


def _get(url: str, timeout: int = 60) -> bytes:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "betit/1.0 (research)"})
    r.raise_for_status()
    return r.content


def fetch_tennis_data(first_year: int) -> pd.DataFrame:
    frames = []
    for year in range(first_year, date.today().year + 1):
        for tour, suffix in (("ATP", ""), ("WTA", "w")):
            url = config.TENNIS_DATA_URL.format(year=year, suffix=suffix)
            try:
                raw = pd.read_excel(io.BytesIO(_get(url)))
            except Exception as e:
                log.info("skip %s: %s", url, e)
                continue
            frames.append(normalise_tennis_data(raw, tour))
            log.info("tennis-data.co.uk %s %d: %d matches", tour, year, len(frames[-1]))
    if not frames:
        raise RuntimeError("tennis-data.co.uk returned no data")
    return pd.concat(frames, ignore_index=True)


def fetch_mirror(first_year: int) -> pd.DataFrame:
    frames = []
    for year in range(first_year, date.today().year + 1):
        try:
            raw = pd.read_csv(io.BytesIO(_get(config.TENNIS_MIRROR_URL.format(year=year))), low_memory=False)
        except Exception as e:
            log.info("skip TML %d: %s", year, e)
            continue
        frames.append(normalise_tml(raw))
    if not frames:
        raise RuntimeError("TML mirror returned no data")
    return pd.concat(frames, ignore_index=True)


def update(first_year: int | None = None) -> dict:
    first_year = first_year or date.today().year - 12
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        df = fetch_tennis_data(first_year)
        source = "tennis-data.co.uk (ATP + WTA, with odds)"
    except Exception as e:
        log.warning("tennis-data.co.uk unavailable (%s); using TML mirror", e)
        df = fetch_mirror(first_year)
        source = "TML-Database mirror (ATP only, no odds)"
    df.to_csv(config.TENNIS_FILE, index=False)
    return {"source": source, "matches": len(df), "latest_result": str(df.date.max().date()),
            "with_odds": int(df.odds_w.notna().sum())}


def load() -> pd.DataFrame:
    if not config.TENNIS_FILE.exists():
        raise FileNotFoundError("No tennis data yet. Run: python -m betit update")
    return pd.read_csv(config.TENNIS_FILE, parse_dates=["date"])
