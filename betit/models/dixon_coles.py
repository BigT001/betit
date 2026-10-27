"""Dixon-Coles (1997) bivariate Poisson model with exponential time decay.

    home goals ~ Poisson(lam),  lam = exp(home_adv + att[h] + def[a])
    away goals ~ Poisson(mu),   mu  = exp(att[a] + def[h])

with the low-score correction tau(x, y) that fixes the independent Poisson
model's under-estimation of 0-0 / 1-1 draws. Matches are weighted by
exp(-xi * days_ago) so recent form counts more. A small ridge penalty keeps
ratings identifiable and shrinks teams with little data toward a prior
(average for established teams, below average for newcomers, which in
practice are usually promoted sides).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from .. import config

NEWCOMER_PRIOR = (-0.20, 0.20)  # (attack, defence) log-rate offsets


@dataclass
class DixonColes:
    xi: float = config.DC_XI
    ridge: float = 1.0
    shots_weight: float = 0.3
    max_goals: int = config.DC_MAX_GOALS
    teams: list[str] = field(default_factory=list)
    attack: np.ndarray | None = None
    defence: np.ndarray | None = None
    home_adv: float = 0.25
    rho: float = -0.05
    fitted_at: pd.Timestamp | None = None
    n_matches: int = 0

    # ------------------------------------------------------------------ fit
    def fit(self, matches: pd.DataFrame, as_of: pd.Timestamp | None = None) -> "DixonColes":
        m = matches.dropna(subset=["hg", "ag"])
        as_of = pd.Timestamp(as_of) if as_of is not None else m.date.max() + pd.Timedelta(days=1)
        m = m[(m.date < as_of) & (m.date >= as_of - pd.Timedelta(days=config.DC_WINDOW_DAYS))]
        if len(m) < 50:
            raise ValueError(f"not enough matches to fit ({len(m)})")
        self.teams = sorted(set(m.home) | set(m.away))
        idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        h = m.home.map(idx).to_numpy()
        a = m.away.map(idx).to_numpy()
        x = m.hg.to_numpy(float)
        y = m.ag.to_numpy(float)
        low_x, low_y = x.copy(), y.copy()  # actual scores drive the low-score correction
        if self.shots_weight > 0:
            # Shots on target are a less noisy signal of chance quality than
            # goals. Blend in "shot-based goals" (SOT x league conversion rate).
            sot = m[["hst", "ast"]].to_numpy(float)
            ok = np.isfinite(sot).all(1)
            conv = (x[ok].sum() + y[ok].sum()) / max(sot[ok].sum(), 1)
            s = self.shots_weight
            x = np.where(ok, (1 - s) * x + s * conv * sot[:, 0], x)
            y = np.where(ok, (1 - s) * y + s * conv * sot[:, 1], y)
        days = (as_of - m.date).dt.days.to_numpy(float)
        w = np.exp(-self.xi * days)

        # Prior centre: teams with little history in the window start below average.
        games = np.bincount(h, minlength=n) + np.bincount(a, minlength=n)
        newcomer = games < 0.5 * np.median(games)
        prior_att = np.where(newcomer, NEWCOMER_PRIOR[0], 0.0)
        prior_def = np.where(newcomer, NEWCOMER_PRIOR[1], 0.0)

        def unpack(p):
            return p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1]

        def nll(p):
            att, dfn, ha, rho = unpack(p)
            eta_h = ha + att[h] + dfn[a]
            eta_a = att[a] + dfn[h]
            lam, mu = np.exp(eta_h), np.exp(eta_a)
            tau = np.ones_like(lam)
            d_lam = np.zeros_like(lam)  # d log tau / d lam
            d_mu = np.zeros_like(lam)
            d_rho = np.zeros_like(lam)
            s00 = (low_x == 0) & (low_y == 0)
            s01 = (low_x == 0) & (low_y == 1)
            s10 = (low_x == 1) & (low_y == 0)
            s11 = (low_x == 1) & (low_y == 1)
            tau[s00] = 1 - lam[s00] * mu[s00] * rho
            tau[s01] = 1 + lam[s01] * rho
            tau[s10] = 1 + mu[s10] * rho
            tau[s11] = 1 - rho
            tau = np.maximum(tau, 1e-10)
            d_lam[s00] = -mu[s00] * rho / tau[s00]
            d_mu[s00] = -lam[s00] * rho / tau[s00]
            d_rho[s00] = -lam[s00] * mu[s00] / tau[s00]
            d_lam[s01] = rho / tau[s01]
            d_rho[s01] = lam[s01] / tau[s01]
            d_mu[s10] = rho / tau[s10]
            d_rho[s10] = mu[s10] / tau[s10]
            d_rho[s11] = -1 / tau[s11]

            ll = w * (np.log(tau) + x * eta_h - lam + y * eta_a - mu)
            ra, rd = att - prior_att, dfn - prior_def
            f = -ll.sum() + self.ridge * (ra @ ra + rd @ rd)

            g_h = w * (x - lam + lam * d_lam)
            g_a = w * (y - mu + mu * d_mu)
            g_att = np.bincount(h, g_h, n) + np.bincount(a, g_a, n)
            g_def = np.bincount(a, g_h, n) + np.bincount(h, g_a, n)
            grad = np.concatenate([
                -g_att + 2 * self.ridge * ra,
                -g_def + 2 * self.ridge * rd,
                [-g_h.sum(), -(w * d_rho).sum()],
            ])
            return f, grad

        p0 = np.concatenate([prior_att, prior_def, [0.25, -0.05]])
        bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.25, 0.25)]
        res = minimize(nll, p0, jac=True, method="L-BFGS-B", bounds=bounds)
        self.attack, self.defence, self.home_adv, self.rho = unpack(res.x)
        self.home_adv, self.rho = float(self.home_adv), float(self.rho)
        self.fitted_at = as_of
        self.n_matches = len(m)
        return self

    # -------------------------------------------------------------- predict
    def knows(self, team: str) -> bool:
        return team in self.teams

    def rates(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        idx = {t: i for i, t in enumerate(self.teams)}

        def ad(t):
            if t in idx:
                return self.attack[idx[t]], self.defence[idx[t]]
            return NEWCOMER_PRIOR

        ah, dh = ad(home)
        aa, da = ad(away)
        ha = 0.0 if neutral else self.home_adv
        return float(np.exp(ha + ah + da)), float(np.exp(aa + dh))

    def score_matrix(self, home: str, away: str, neutral: bool = False) -> np.ndarray:
        lam, mu = self.rates(home, away, neutral)
        return score_matrix(lam, mu, self.rho, self.max_goals)

    def predict(self, home: str, away: str, neutral: bool = False) -> dict:
        lam, mu = self.rates(home, away, neutral)
        mat = score_matrix(lam, mu, self.rho, self.max_goals)
        return markets_from_matrix(mat) | {"xg_home": lam, "xg_away": mu}

    def ratings(self) -> pd.DataFrame:
        df = pd.DataFrame({"team": self.teams, "attack": self.attack, "defence": self.defence})
        # Expected goals for/against vs an average team on neutral ground.
        df["exp_gf"] = np.exp(df.attack)
        df["exp_ga"] = np.exp(df.defence)
        # Overall strength: expected goal difference per match vs an average side.
        df["strength"] = df.exp_gf - df.exp_ga
        return df.sort_values("strength", ascending=False).reset_index(drop=True)


def score_matrix(lam: float, mu: float, rho: float, max_goals: int = 10) -> np.ndarray:
    g = np.arange(max_goals + 1)
    mat = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    mat[0, 0] *= 1 - lam * mu * rho
    mat[0, 1] *= 1 + lam * rho
    mat[1, 0] *= 1 + mu * rho
    mat[1, 1] *= 1 - rho
    mat = np.clip(mat, 0, None)
    return mat / mat.sum()


def markets_from_matrix(mat: np.ndarray) -> dict:
    n = mat.shape[0]
    hg, ag = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    tot = hg + ag
    p_home = float(np.tril(mat, -1).sum())
    p_draw = float(np.trace(mat))
    p_away = float(np.triu(mat, 1).sum())
    out = {
        "home": p_home, "draw": p_draw, "away": p_away,
        "btts_yes": float(mat[1:, 1:].sum()),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        out[f"over_{line}"] = float(mat[tot > line].sum())
        out[f"under_{line}"] = 1 - out[f"over_{line}"]
    out["btts_no"] = 1 - out["btts_yes"]
    out["home_or_draw"] = p_home + p_draw
    out["away_or_draw"] = p_away + p_draw
    out["home_or_away"] = p_home + p_away
    flat = [(float(mat[i, j]), i, j) for i in range(min(n, 7)) for j in range(min(n, 7))]
    flat.sort(reverse=True)
    out["top_scores"] = [{"score": f"{i}-{j}", "p": p} for p, i, j in flat[:8]]
    out["matrix"] = mat[:7, :7].round(5).tolist()
    return out


def league_frame(results: pd.DataFrame, league: str) -> pd.DataFrame:
    """Matches used to rate a league: the league itself plus its feeder division."""
    leagues = {league, config.FEEDER.get(league, league)}
    return results[results.league.isin(leagues)]


def fit_league(results: pd.DataFrame, league: str, as_of=None, **kw) -> DixonColes:
    return DixonColes(**kw).fit(league_frame(results, league), as_of=as_of)
