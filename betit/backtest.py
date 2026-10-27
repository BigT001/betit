"""Walk-forward backtests.

Stage 1 (slow) produces out-of-sample predictions: the model is refitted every
`refit_days` using only matches played *before* that date, then predicts the
next block. Nothing from the future leaks into a prediction.

Stage 2 (fast) simulates a betting strategy on those predictions against the
odds that were actually available, so strategy settings can be explored
without refitting.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import betting
from .models.dixon_coles import fit_league

log = logging.getLogger(__name__)

FOOTBALL_SELECTIONS = {
    # selection: (prob column, odds column, max-odds column, did-it-win column)
    "home": ("p_home", "odds_h", "max_h", "won_home"),
    "draw": ("p_draw", "odds_d", "max_d", "won_draw"),
    "away": ("p_away", "odds_a", "max_a", "won_away"),
    "over25": ("p_over25", "odds_o25", "max_o25", "won_over25"),
    "under25": ("p_under25", "odds_u25", "max_u25", "won_under25"),
}


def football_predictions(results: pd.DataFrame, leagues: list[str], start: str,
                         end: str | None = None, refit_days: int = 7) -> pd.DataFrame:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end else results.date.max() + pd.Timedelta(days=1)
    rows = []
    for lg in leagues:
        target = results[(results.league == lg) & (results.date >= start_ts) & (results.date < end_ts)]
        if target.empty:
            continue
        t = start_ts
        while t < end_ts:
            block = target[(target.date >= t) & (target.date < t + pd.Timedelta(days=refit_days))]
            if not block.empty:
                try:
                    model = fit_league(results, lg, as_of=t)
                except ValueError:
                    t += pd.Timedelta(days=refit_days)
                    continue
                for r in block.itertuples():
                    p = model.predict(r.home, r.away)
                    rows.append({
                        "date": r.date, "league": lg, "season": r.season, "home": r.home, "away": r.away,
                        "hg": r.hg, "ag": r.ag,
                        "p_home": p["home"], "p_draw": p["draw"], "p_away": p["away"],
                        "p_over25": p["over_2.5"], "p_under25": p["under_2.5"],
                        "xg_home": p["xg_home"], "xg_away": p["xg_away"],
                        "odds_h": r.odds_h, "odds_d": r.odds_d, "odds_a": r.odds_a,
                        "max_h": r.max_h, "max_d": r.max_d, "max_a": r.max_a,
                        "odds_o25": r.odds_o25, "odds_u25": r.odds_u25,
                        "max_o25": r.max_o25, "max_u25": r.max_u25,
                    })
            t += pd.Timedelta(days=refit_days)
        log.info("backtest predictions %s done", lg)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["won_home"] = df.hg > df.ag
    df["won_draw"] = df.hg == df.ag
    df["won_away"] = df.hg < df.ag
    df["won_over25"] = (df.hg + df.ag) > 2.5
    df["won_under25"] = ~df.won_over25
    return add_market_probs(df)


def add_market_probs(df: pd.DataFrame) -> pd.DataFrame:
    """De-vigged market probabilities for each row (NaN when odds missing)."""
    m1 = np.full((len(df), 3), np.nan)
    m2 = np.full((len(df), 2), np.nan)
    o1 = df[["odds_h", "odds_d", "odds_a"]].to_numpy(float)
    o2 = df[["odds_o25", "odds_u25"]].to_numpy(float)
    for i in range(len(df)):
        if np.isfinite(o1[i]).all():
            m1[i] = betting.fair_probs(o1[i])
        if np.isfinite(o2[i]).all():
            m2[i] = betting.fair_probs(o2[i])
    df["m_home"], df["m_draw"], df["m_away"] = m1.T
    df["m_over25"], df["m_under25"] = m2.T
    return df


# ------------------------------------------------------------------ metrics
def _logloss(p: np.ndarray) -> float:
    return float(-np.log(np.clip(p, 1e-12, 1)).mean())


def football_accuracy(pred: pd.DataFrame) -> dict:
    """How good are the probabilities, compared with the bookmakers'?"""
    ok = pred.dropna(subset=["m_home"])
    won = ok[["won_home", "won_draw", "won_away"]].to_numpy(bool)
    pm = ok[["p_home", "p_draw", "p_away"]].to_numpy()
    mm = ok[["m_home", "m_draw", "m_away"]].to_numpy()
    out = {
        "matches": int(len(ok)),
        "model_logloss": _logloss(pm[won]),
        "market_logloss": _logloss(mm[won]),
        "model_brier": float(((pm - won) ** 2).sum(1).mean()),
        "market_brier": float(((mm - won) ** 2).sum(1).mean()),
        "model_hit_rate": float((pm.argmax(1) == won.argmax(1)).mean()),
        "market_hit_rate": float((mm.argmax(1) == won.argmax(1)).mean()),
    }
    out["calibration"] = calibration(pm.ravel(), won.ravel())
    return out


def calibration(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() >= 20:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "predicted": float(p[m].mean()),
                        "observed": float(y[m].mean()), "n": int(m.sum())})
    return out


# --------------------------------------------------------------- simulation
def simulate(pred: pd.DataFrame, selections: dict, *, edge_min: float = 0.05, max_odds: float = 6.0,
             blend: float = 0.5, kelly_mult: float = 0.25, cap: float = 0.03,
             bankroll: float = 1000.0, use_best_odds: bool = False, markets: list[str] | None = None,
             confirm: bool = False) -> dict:
    """Bet when (blended) model probability beats the price by `edge_min`.

    blend = weight on the market's own de-vigged probability. 0 = pure model,
    which in practice over-bets on the model's mistakes; 0.5 is a common,
    more conservative choice. confirm=True additionally requires the model
    to rate the selection at least as likely as the market does.
    """
    markets = markets or list(selections)
    bets = []
    for sel in markets:
        pcol, ocol, mcol, wcol = selections[sel]
        mcol_p = "m_" + pcol[2:]
        odds = pred[mcol if use_best_odds else ocol]
        p = (1 - blend) * pred[pcol] + blend * pred[mcol_p].fillna(pred[pcol])
        e = p * odds - 1
        take = (e >= edge_min) & odds.notna() & (odds <= max_odds)
        if confirm:
            take &= pred[pcol] >= pred[mcol_p]
        sub = pred[take]
        for (i, r), pi, oi, ei in zip(sub.iterrows(), p[take], odds[take], e[take]):
            bets.append({"date": r.date, "match": f"{r.get('home', r.get('p1'))} v {r.get('away', r.get('p2'))}",
                         "league": r.get("league", r.get("tour")), "selection": sel, "p": float(pi),
                         "odds": float(oi), "edge": float(ei), "won": bool(r[wcol])})
    if not bets:
        return {"bets": 0, "message": "No bets met the criteria."}
    b = pd.DataFrame(bets).sort_values(["date", "edge"], ascending=[True, False]).reset_index(drop=True)

    # Kelly staking; all bets on one day are sized from that morning's bankroll.
    bank = bankroll
    stakes, pnl, banks = [], [], []
    for _, day in b.groupby("date", sort=True):
        start = bank
        for r in day.itertuples():
            s = betting.stake(r.p, r.odds, start, kelly_mult, cap)
            prof = s * (r.odds - 1) if r.won else -s
            stakes.append(s)
            pnl.append(prof)
        bank += sum(pnl[-len(day):])
        banks.extend([bank] * len(day))
    b["stake"], b["pnl"], b["bankroll"] = stakes, pnl, banks

    # Flat 1-unit stakes, with a bootstrap CI so luck isn't mistaken for skill.
    flat = np.where(b.won, b.odds - 1, -1.0)
    rng = np.random.default_rng(0)
    boot = rng.choice(flat, (2000, len(flat))).mean(1)
    peak = np.maximum.accumulate(np.r_[bankroll, b.bankroll.to_numpy()])
    dd = 1 - np.r_[bankroll, b.bankroll.to_numpy()] / peak
    daily = b.groupby("date").agg(bankroll=("bankroll", "last")).reset_index()
    monthly = b.assign(month=b.date.dt.strftime("%Y-%m")).groupby("month").agg(
        bets=("pnl", "size"), pnl=("pnl", "sum"), staked=("stake", "sum")).reset_index()
    by_sel = b.groupby("selection").apply(
        lambda g: pd.Series({"bets": len(g), "win_rate": g.won.mean(),
                             "flat_roi": np.where(g.won, g.odds - 1, -1).mean()}), include_groups=False
    ).reset_index()
    return {
        "bets": int(len(b)),
        "win_rate": float(b.won.mean()),
        "avg_odds": float(b.odds.mean()),
        "avg_edge_claimed": float(b.edge.mean()),
        "flat_roi": float(flat.mean()),
        "flat_roi_ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
        "flat_profit_units": float(flat.sum()),
        "kelly_start": bankroll,
        "kelly_end": float(bank),
        "kelly_staked": float(b.stake.sum()),
        "kelly_roi": float(b.pnl.sum() / b.stake.sum()) if b.stake.sum() else 0.0,
        "max_drawdown": float(dd.max()),
        "longest_losing_streak": int(_longest_run(~b.won.to_numpy())),
        "equity": [{"date": d.strftime("%Y-%m-%d"), "bankroll": round(v, 2)}
                   for d, v in zip(daily.date, daily.bankroll)],
        "monthly": monthly.round(2).to_dict("records"),
        "by_selection": by_selection_records(by_sel),
        "last_bets": b.tail(50).assign(date=b.date.dt.strftime("%Y-%m-%d")).round(4).to_dict("records")[::-1],
    }


def by_selection_records(df: pd.DataFrame) -> list[dict]:
    return [{k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in r.items()}
            for r in df.to_dict("records")]


def _longest_run(x: np.ndarray) -> int:
    best = cur = 0
    for v in x:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


# Fixed, pre-registered strategies. They are all reported side by side so
# the comparison isn't cherry-picked after looking at results.
STRATEGIES = {
    "model_only": {
        "label": "Model only",
        "description": "Bet when the model's probability beats the average price by 5%+.",
        "params": {"blend": 0.0, "edge_min": 0.05, "use_best_odds": False},
    },
    "blended": {
        "label": "Model + market blend",
        "description": "Average the model with the market's de-vigged probability, bet at average price with 3%+ edge.",
        "params": {"blend": 0.5, "edge_min": 0.03, "use_best_odds": False},
    },
    "line_shopping": {
        "label": "Line shopping",
        "description": "No model: bet when the best available price beats the market consensus fair price by 3%+.",
        "params": {"blend": 1.0, "edge_min": 0.03, "use_best_odds": True},
    },
    "shopping_confirmed": {
        "label": "Line shopping + model agrees",
        "description": "Line shopping, but only when the model also rates the outcome at least as likely as the market.",
        "params": {"blend": 1.0, "edge_min": 0.03, "use_best_odds": True, "confirm": True},
    },
}


def run_strategies(pred: pd.DataFrame, selections: dict, **kw) -> dict:
    out = {}
    for key, s in STRATEGIES.items():
        res = simulate(pred, selections, **(s["params"] | kw))
        out[key] = {"label": s["label"], "description": s["description"], "params": s["params"]} | res
    return out


# ------------------------------------------------------------------- tennis
TENNIS_SELECTIONS = {
    "player_a": ("p_a", "odds_a", "max_a", "won_a"),
    "player_b": ("p_b", "odds_b", "max_b", "won_b"),
}


def tennis_predictions(elo_frame: pd.DataFrame, start: str, min_experience: int = 10) -> pd.DataFrame:
    """Turn the sequential Elo output into an orientation-free table.
    Player A is whichever name sorts first, so the winner is not always A."""
    t = elo_frame[(elo_frame.date >= pd.Timestamp(start)) & (elo_frame.min_experience >= min_experience)].copy()
    a_is_w = t.winner <= t.loser
    pick = lambda w, l: np.where(a_is_w, t[w], t[l])
    df = pd.DataFrame({
        "date": t.date, "tour": t.tour, "league": t.tour, "tournament": t.tournament, "surface": t.surface,
        "home": np.where(a_is_w, t.winner, t.loser), "away": np.where(a_is_w, t.loser, t.winner),
        "p_a": np.where(a_is_w, t.p_winner, 1 - t.p_winner),
        "odds_a": pick("odds_w", "odds_l"), "odds_b": pick("odds_l", "odds_w"),
        "max_a": pick("max_w", "max_l"), "max_b": pick("max_l", "max_w"),
        "won_a": a_is_w.to_numpy(),
    })
    df["p_b"] = 1 - df.p_a
    df["won_b"] = ~df.won_a
    m = np.full((len(df), 2), np.nan)
    o = df[["odds_a", "odds_b"]].to_numpy(float)
    for i in range(len(df)):
        if np.isfinite(o[i]).all():
            m[i] = betting.fair_probs(o[i])
    df["m_a"], df["m_b"] = m.T
    return df.reset_index(drop=True)


def tennis_accuracy(pred: pd.DataFrame) -> dict:
    p = pred.p_a.to_numpy()
    y = pred.won_a.to_numpy(bool)
    pw = np.where(y, p, 1 - p)
    out = {"matches": int(len(pred)), "model_logloss": _logloss(pw),
           "model_brier": float(((p - y) ** 2).mean()), "model_hit_rate": float(((p > 0.5) == y).mean()),
           "calibration": calibration(p, y)}
    ok = pred.m_a.notna().to_numpy()
    if ok.sum() > 100:
        mp = pred.m_a.to_numpy()[ok]
        mw = np.where(y[ok], mp, 1 - mp)
        out |= {"odds_matches": int(ok.sum()), "market_logloss": _logloss(mw),
                "model_logloss_same_matches": _logloss(pw[ok]),
                "market_brier": float(((mp - y[ok]) ** 2).mean()),
                "market_hit_rate": float(((mp > 0.5) == y[ok]).mean())}
    return out
