"""Odds maths: margin removal, edge, Kelly staking."""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def implied(odds) -> np.ndarray:
    return 1.0 / np.asarray(odds, dtype=float)


def overround(odds) -> float:
    return float(implied(odds).sum() - 1)


def fair_probs(odds, method: str = "power") -> np.ndarray:
    """Bookmaker probabilities with the margin removed.

    'power' (p_i = q_i^k, sum = 1) handles the favourite-longshot bias better
    than plain normalisation, which over-states longshots' chances.
    """
    q = implied(odds)
    if np.any(~np.isfinite(q)):
        return np.full_like(q, np.nan)
    if method == "proportional" or abs(q.sum() - 1) < 1e-9:
        return q / q.sum()
    k = brentq(lambda k: (q ** k).sum() - 1, 0.5, 3.0)
    return q ** k


def edge(p: float, odds: float) -> float:
    """Expected profit per unit staked: p * odds - 1."""
    return p * odds - 1


def kelly_fraction(p: float, odds: float) -> float:
    """Full-Kelly fraction of bankroll; 0 when the bet has no edge."""
    b = odds - 1
    if b <= 0:
        return 0.0
    return max(0.0, (p * b - (1 - p)) / b)


def stake(p: float, odds: float, bankroll: float, kelly_mult: float = 0.25, cap: float = 0.03) -> float:
    """Fractional-Kelly stake, capped at `cap` of bankroll.

    Full Kelly is only optimal if p is exactly right. Our p is an estimate,
    so we bet a quarter-Kelly by default, which gives up a little growth for
    a large cut in variance and ruin risk.
    """
    return bankroll * min(cap, kelly_mult * kelly_fraction(p, odds))


def risk_of_drawdown(p: float, odds: float, f: float, n_bets: int = 500, drop: float = 0.5,
                     sims: int = 2000, seed: int = 0) -> float:
    """Monte Carlo probability that bankroll falls below (1 - drop) of start
    at some point over n_bets identical bets of fraction f."""
    rng = np.random.default_rng(seed)
    wins = rng.random((sims, n_bets)) < p
    growth = np.where(wins, 1 + f * (odds - 1), 1 - f)
    path = np.cumprod(growth, axis=1)
    return float((path.min(axis=1) < 1 - drop).mean())
