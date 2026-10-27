"""Surface-aware tennis Elo (the FiveThirtyEight / Kovalchik approach).

Each player has an overall rating and one rating per surface. The K-factor
shrinks as a player accumulates matches, K = 250 / (n + 5)^0.4, so newcomers
move fast and established players move slowly. A match prediction uses the
average of the overall and surface ratings, which Kovalchik (2016) found
among the best-calibrated public tennis models.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

START = 1500.0
SURFACES = ("Hard", "Clay", "Grass", "Carpet")


def k_factor(n: int) -> float:
    return 250.0 / (n + 5) ** 0.4


def win_prob(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def best_of_5(p3: float) -> float:
    """Convert a best-of-3 match probability to best-of-5.

    Solve for the per-set win probability s with P(win Bo3) = s^2 (3 - 2s),
    then P(win Bo5) = s^3 (10 - 15 s + 6 s^2). Favourites are stronger over
    five sets, which this captures.
    """
    lo, hi = 0.0, 1.0
    for _ in range(60):
        s = (lo + hi) / 2
        if s * s * (3 - 2 * s) < p3:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return s ** 3 * (10 - 15 * s + 6 * s * s)


@dataclass
class TennisElo:
    surface_weight: float = 0.5
    overall: dict = field(default_factory=lambda: defaultdict(lambda: START))
    surface: dict = field(default_factory=lambda: defaultdict(lambda: START))
    n_overall: dict = field(default_factory=lambda: defaultdict(int))
    n_surface: dict = field(default_factory=lambda: defaultdict(int))
    last_played: dict = field(default_factory=dict)
    history: dict = field(default_factory=lambda: defaultdict(list))

    def rating(self, player: str, surface: str) -> float:
        w = self.surface_weight
        return (1 - w) * self.overall[player] + w * self.surface[(player, surface)]

    def predict(self, p1: str, p2: str, surface: str, best_of: int = 3) -> float:
        p = win_prob(self.rating(p1, surface), self.rating(p2, surface))
        return best_of_5(p) if best_of == 5 else p

    def update(self, winner: str, loser: str, surface: str, when=None) -> float:
        """Record a result; returns the pre-match win probability of the winner (Bo3 scale)."""
        p = win_prob(self.rating(winner, surface), self.rating(loser, surface))
        po = win_prob(self.overall[winner], self.overall[loser])
        ws, ls = (winner, surface), (loser, surface)
        psf = win_prob(self.surface[ws], self.surface[ls])
        kw, kl = k_factor(self.n_overall[winner]), k_factor(self.n_overall[loser])
        self.overall[winner] += kw * (1 - po)
        self.overall[loser] -= kl * (1 - po)
        kws, kls = k_factor(self.n_surface[ws]), k_factor(self.n_surface[ls])
        self.surface[ws] += kws * (1 - psf)
        self.surface[ls] -= kls * (1 - psf)
        self.n_overall[winner] += 1
        self.n_overall[loser] += 1
        self.n_surface[ws] += 1
        self.n_surface[ls] += 1
        if when is not None:
            self.last_played[winner] = self.last_played[loser] = when
            self.history[winner].append((when, round(self.overall[winner], 1)))
            self.history[loser].append((when, round(self.overall[loser], 1)))
        return p

    def table(self, surface: str | None = None, active_since=None, min_matches: int = 20) -> pd.DataFrame:
        rows = []
        for pl, r in self.overall.items():
            if self.n_overall[pl] < min_matches:
                continue
            if active_since is not None and self.last_played.get(pl, active_since) < active_since:
                continue
            rows.append({
                "player": pl, "elo": r, "matches": self.n_overall[pl],
                **{f"elo_{s.lower()}": self.surface[(pl, s)] for s in SURFACES[:3]},
                "blended": self.rating(pl, surface) if surface else r,
                "last_played": str(pd.Timestamp(self.last_played[pl]).date()) if pl in self.last_played else None,
            })
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values("blended", ascending=False).reset_index(drop=True)


def run(matches: pd.DataFrame, surface_weight: float = 0.5) -> tuple[TennisElo, pd.DataFrame]:
    """Process every match in date order. Returns the final ratings and a
    frame of out-of-sample pre-match probabilities (one row per match)."""
    elo = TennisElo(surface_weight=surface_weight)
    pw = np.empty(len(matches))
    exp_w = np.empty(len(matches))
    for i, r in enumerate(matches[["winner", "loser", "surface", "best_of", "date"]].itertuples(index=False)):
        exp_w[i] = min(elo.n_overall[r.winner], elo.n_overall[r.loser])
        p = elo.update(r.winner, r.loser, r.surface, r.date)
        pw[i] = best_of_5(p) if r.best_of == 5 else p
    out = matches.copy()
    out["p_winner"] = pw
    out["min_experience"] = exp_w
    return elo, out
