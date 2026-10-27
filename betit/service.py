"""Application layer: loads data once, caches fitted models, answers UI queries."""
from __future__ import annotations

import json
import threading
from functools import lru_cache

import numpy as np
import pandas as pd

from . import analysis, backtest, betting, config
from .data import football, tennis
from .models import elo as elo_mod
from .models.dixon_coles import fit_league

_lock = threading.Lock()

# 1X2 / totals selections offered in the match analyser:
# key -> (label, probability key from the model, market group)
FOOTBALL_MARKETS = {
    "home": ("Home win", "home", "1x2"),
    "draw": ("Draw", "draw", "1x2"),
    "away": ("Away win", "away", "1x2"),
    "over25": ("Over 2.5 goals", "over_2.5", "ou25"),
    "under25": ("Under 2.5 goals", "under_2.5", "ou25"),
    "btts_yes": ("Both teams score", "btts_yes", "btts"),
    "btts_no": ("Not both score", "btts_no", "btts"),
}
GROUP_SELECTIONS = {"1x2": ["home", "draw", "away"], "ou25": ["over25", "under25"], "btts": ["btts_yes", "btts_no"]}


class State:
    results: pd.DataFrame | None = None
    fixtures: pd.DataFrame | None = None
    tennis: pd.DataFrame | None = None
    elo: elo_mod.TennisElo | None = None
    models: dict = {}


S = State()


def reload() -> None:
    with _lock:
        S.results = football.load_results() if config.FOOTBALL_FILE.exists() else None
        S.fixtures = football.load_fixtures()
        S.tennis = tennis.load() if config.TENNIS_FILE.exists() else None
        S.elo = elo_mod.run(S.tennis)[0] if S.tennis is not None else None
        S.models = {}


def model(league: str):
    with _lock:
        if league not in S.models:
            S.models[league] = fit_league(S.results, league)
        return S.models[league]


# ----------------------------------------------------------------- football
def status() -> dict:
    out = {"football": None, "tennis": None, "fixtures": 0}
    if S.results is not None:
        out["football"] = {"matches": len(S.results), "latest": str(S.results.date.max().date()),
                           "leagues": int(S.results.league.nunique())}
    if S.fixtures is not None:
        out["fixtures"] = len(S.fixtures)
    if S.tennis is not None:
        out["tennis"] = {"matches": len(S.tennis), "latest": str(S.tennis.date.max().date()),
                         "tours": sorted(S.tennis.tour.unique().tolist()),
                         "with_odds": int(S.tennis.odds_w.notna().sum())}
    return out


def leagues() -> list[dict]:
    if S.results is None:
        return []
    out = []
    for code, name in config.LEAGUES.items():
        lg = S.results[S.results.league == code]
        if lg.empty:
            continue
        season = lg.season.max()
        cur = lg[lg.season == season]
        teams = sorted(set(cur.home) | set(cur.away))
        out.append({"code": code, "name": name, "season": season, "teams": teams,
                    "latest": str(lg.date.max().date())})
    return out


def ratings(league: str) -> list[dict]:
    m = model(league)
    lg = S.results[S.results.league == league]
    cur = lg[lg.season == lg.season.max()]
    teams = set(cur.home) | set(cur.away)
    r = m.ratings()
    r = r[r.team.isin(teams)].reset_index(drop=True)
    r.insert(0, "rank", r.index + 1)
    return r.round(3).to_dict("records")


def _value_rows(probs: dict, offered: dict, consensus: dict, bankroll: float, kelly_mult: float,
                cap: float, edge_min: float) -> list[dict]:
    """Price every selection. `offered` = odds the user can actually get;
    `consensus` = market average odds used to estimate the fair price."""
    # Market view: consensus (average) odds when we have them, otherwise the
    # user's own bookmaker's full set of prices with the margin removed.
    fair_market, source = {}, {}
    for group, sels in GROUP_SELECTIONS.items():
        for src_name, src in (("consensus", consensus), ("bookmaker", offered)):
            odds = [src.get(s) for s in sels]
            if all(o and o > 1 for o in odds):
                for s, fp in zip(sels, betting.fair_probs(odds)):
                    fair_market[s] = float(fp)
                    source[s] = src_name
                break
    rows = []
    for key, (label, pkey, group) in FOOTBALL_MARKETS.items():
        p_model = probs[pkey]
        p_mkt = fair_market.get(key)
        price = offered.get(key) or consensus.get(key)
        row = {"key": key, "label": label, "group": group, "p_model": p_model, "fair_odds": 1 / p_model,
               "p_market": p_mkt, "market_source": source.get(key), "odds": price, "value": False, "stake": 0.0}
        if price and price > 1:
            row["model_edge"] = betting.edge(p_model, price)
            if p_mkt is not None:
                p_blend = 0.5 * p_model + 0.5 * p_mkt
                row |= {"price_edge": betting.edge(p_mkt, price), "p_blend": p_blend,
                        "blend_edge": betting.edge(p_blend, price)}
            if source.get(key) == "consensus":
                # Price beats the market consensus and the model doesn't object:
                # the only rule that didn't lose money in the backtest.
                row["value"] = bool(row["price_edge"] >= edge_min and row["blend_edge"] >= 0)
                row["basis"] = "price"
                p_stake = p_mkt
            elif p_mkt is not None:
                # Only one bookmaker's prices: its own fair price can't beat
                # itself, so fall back to the model/market blend (unproven).
                row["value"] = bool(row["blend_edge"] >= edge_min)
                row["basis"] = "blend"
                p_stake = p_blend
            else:
                # Single price, no market view at all: model only, which lost
                # money in the backtest, so demand twice the edge.
                row["value"] = bool(row["model_edge"] >= 2 * edge_min)
                row["basis"] = "model"
                p_stake = p_model
            row["stake"] = betting.stake(p_stake, price, bankroll, kelly_mult, cap) if row["value"] else 0.0
        rows.append(row)
    return rows


def football_match(league: str, home: str, away: str, offered: dict | None = None,
                   consensus: dict | None = None, bankroll: float = 1000, kelly_mult: float = 0.25,
                   cap: float = 0.03, edge_min: float = 0.03, neutral: bool = False) -> dict:
    m = model(league)
    p = m.predict(home, away, neutral)
    offered = {k: v for k, v in (offered or {}).items() if v}
    consensus = consensus or {}
    if not consensus:
        fx = S.fixtures
        if fx is not None and len(fx):
            hit = fx[(fx.home == home) & (fx.away == away)]
            if len(hit):
                consensus = _fixture_consensus(hit.iloc[0])
    r = m.ratings().set_index("team")
    return {
        "league": league, "home": home, "away": away,
        "known": {"home": m.knows(home), "away": m.knows(away)},
        "model": {"fitted_on": int(m.n_matches), "as_of": str(m.fitted_at.date()),
                  "home_advantage": float(np.exp(m.home_adv)), "rho": m.rho},
        "probs": {k: v for k, v in p.items() if k not in ("matrix", "top_scores")},
        "matrix": p["matrix"], "top_scores": p["top_scores"],
        "ratings": {t: (r.loc[t].round(3).to_dict() if t in r.index else None) for t in (home, away)},
        "home_report": analysis.team_report(S.results, home, league),
        "away_report": analysis.team_report(S.results, away, league),
        "h2h": analysis.head_to_head(S.results, home, away),
        "consensus": consensus,
        "value": _value_rows(p, offered, consensus, bankroll, kelly_mult, cap, edge_min),
    }


def _fixture_consensus(r) -> dict:
    c = {"home": r.odds_h, "draw": r.odds_d, "away": r.odds_a, "over25": r.odds_o25, "under25": r.odds_u25}
    best = {"home": r.max_h, "draw": r.max_d, "away": r.max_a, "over25": r.max_o25, "under25": r.max_u25}
    out = {k: float(v) for k, v in c.items() if pd.notna(v)}
    out["best"] = {k: float(v) for k, v in best.items() if pd.notna(v)}
    return out


def value_board(bankroll: float = 1000, kelly_mult: float = 0.25, cap: float = 0.03, edge_min: float = 0.03) -> dict:
    fx = S.fixtures
    if fx is None or fx.empty:
        return {"fixtures": [], "message": "No upcoming fixtures loaded. Run an update on a machine that can reach "
                                           "football-data.co.uk, or use the match analyser with your own odds."}
    out = []
    for r in fx.itertuples():
        if S.results is None or r.league not in set(S.results.league):
            continue
        try:
            m = model(r.league)
        except ValueError:
            continue
        p = m.predict(r.home, r.away)
        cons = _fixture_consensus(r)
        rows = _value_rows(p, cons.get("best", {}), cons, bankroll, kelly_mult, cap, edge_min)
        out.append({"date": r.date.strftime("%Y-%m-%d"), "league": r.league,
                    "league_name": config.LEAGUES.get(r.league, r.league),
                    "home": r.home, "away": r.away,
                    "p_home": p["home"], "p_draw": p["draw"], "p_away": p["away"],
                    "p_over25": p["over_2.5"], "xg_home": p["xg_home"], "xg_away": p["xg_away"],
                    "known": m.knows(r.home) and m.knows(r.away),
                    "bets": [x for x in rows if x.get("value")]})
    return {"fixtures": out}


def league_table(league: str) -> list[dict]:
    return analysis.league_table(S.results, league)


# ------------------------------------------------------------------- tennis
def tennis_players(q: str = "", limit: int = 30) -> list[dict]:
    if S.elo is None:
        return []
    since = S.tennis.date.max() - pd.Timedelta(days=365)
    t = S.elo.table(active_since=since, min_matches=10)
    if q:
        t = t[t.player.str.contains(q, case=False, regex=False)]
    return t.head(limit).round(1).to_dict("records")


def tennis_match(p1: str, p2: str, surface: str = "Hard", best_of: int = 3, odds1: float | None = None,
                 odds2: float | None = None, bankroll: float = 1000, kelly_mult: float = 0.25,
                 cap: float = 0.03, edge_min: float = 0.03) -> dict:
    e = S.elo
    p = e.predict(p1, p2, surface, best_of)
    out = {
        "p1": p1, "p2": p2, "surface": surface, "best_of": best_of, "p1_win": p, "p2_win": 1 - p,
        "fair_odds": [1 / p, 1 / (1 - p)],
        "players": [_tennis_player(e, x, surface) for x in (p1, p2)],
        "h2h": _tennis_h2h(p1, p2),
    }
    if odds1 and odds2:
        fair = betting.fair_probs([odds1, odds2])
        rows = []
        for name, pm, pmk, o in ((p1, p, fair[0], odds1), (p2, 1 - p, fair[1], odds2)):
            pb = 0.5 * pm + 0.5 * pmk
            e_b = betting.edge(pb, o)
            rows.append({"player": name, "odds": o, "p_model": pm, "p_market": float(pmk), "p_blend": pb,
                         "model_edge": betting.edge(pm, o), "blend_edge": e_b,
                         "stake": betting.stake(pb, o, bankroll, kelly_mult, cap) if e_b >= edge_min else 0.0,
                         "value": bool(e_b >= edge_min)})
        out["value"] = rows
        out["overround"] = betting.overround([odds1, odds2])
    return out


def _tennis_player(e, name: str, surface: str) -> dict:
    t = S.tennis
    m = t[(t.winner == name) | (t.loser == name)].tail(10)
    return {
        "name": name, "elo": e.overall[name], "surface_elo": e.surface[(name, surface)],
        "blended": e.rating(name, surface), "matches": e.n_overall[name],
        "surface_matches": e.n_surface[(name, surface)],
        "recent": [{"date": d.strftime("%Y-%m-%d"), "tournament": tn, "surface": s,
                    "opponent": (l if w == name else w), "result": "W" if w == name else "L"}
                   for d, tn, s, w, l in zip(m.date, m.tournament, m.surface, m.winner, m.loser)][::-1],
        "history": e.history.get(name, [])[-60:],
    }


def _tennis_h2h(a: str, b: str) -> dict:
    t = S.tennis
    m = t[((t.winner == a) & (t.loser == b)) | ((t.winner == b) & (t.loser == a))]
    return {"a_wins": int((m.winner == a).sum()), "b_wins": int((m.winner == b).sum()),
            "matches": [{"date": d.strftime("%Y-%m-%d"), "tournament": tn, "surface": s, "winner": w}
                        for d, tn, s, w in zip(m.date, m.tournament, m.surface, m.winner)][::-1][:10]}


# ---------------------------------------------------------------- backtests
def backtests() -> dict:
    out = {}
    for f in sorted(config.BACKTEST_DIR.glob("*.json")) if config.BACKTEST_DIR.exists() else []:
        out[f.stem] = json.loads(f.read_text())
    return out
