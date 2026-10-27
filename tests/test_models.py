import numpy as np
import pandas as pd
import pytest

from betit import backtest, betting
from betit.models import elo
from betit.models.dixon_coles import DixonColes, markets_from_matrix, score_matrix


def synthetic_league(n_teams=12, seasons=3, seed=1):
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.3, n_teams)
    dfn = rng.normal(0, 0.3, n_teams)
    teams = [f"T{i}" for i in range(n_teams)]
    rows, day = [], pd.Timestamp("2020-08-01")
    for s in range(seasons):
        for h in range(n_teams):
            for a in range(n_teams):
                if h == a:
                    continue
                lam, mu = np.exp(0.25 + att[h] + dfn[a]), np.exp(att[a] + dfn[h])
                rows.append({"date": day, "league": "X", "season": str(s), "home": teams[h], "away": teams[a],
                             "hg": rng.poisson(lam), "ag": rng.poisson(mu), "hst": np.nan, "ast": np.nan})
                day += pd.Timedelta(hours=14)
    return pd.DataFrame(rows), att, dfn


def test_dixon_coles_recovers_strengths():
    df, att, dfn = synthetic_league()
    m = DixonColes(xi=0.0).fit(df)
    est = dict(zip(m.teams, m.attack))
    true = dict(zip([f"T{i}" for i in range(len(att))], att))
    corr = np.corrcoef([est[t] for t in true], list(true.values()))[0, 1]
    assert corr > 0.8
    assert 0.1 < m.home_adv < 0.4


def test_markets_sum_to_one():
    mat = score_matrix(1.6, 1.1, -0.08)
    mk = markets_from_matrix(mat)
    assert abs(mk["home"] + mk["draw"] + mk["away"] - 1) < 1e-9
    assert abs(mk["over_2.5"] + mk["under_2.5"] - 1) < 1e-9
    assert mk["top_scores"][0]["p"] >= mk["top_scores"][1]["p"]


def test_fair_probs_remove_margin():
    p = betting.fair_probs([1.9, 3.6, 4.5])
    assert abs(p.sum() - 1) < 1e-9
    assert p[0] > p[1] > p[2]


def test_kelly():
    assert betting.kelly_fraction(0.5, 2.0) == 0
    assert betting.kelly_fraction(0.4, 2.0) == 0
    assert betting.kelly_fraction(0.6, 2.0) == pytest.approx(0.2)
    assert betting.stake(0.6, 2.0, 1000, 0.25, 0.03) == pytest.approx(30)  # capped at 3%
    assert betting.stake(0.6, 2.0, 1000, 0.1, 0.03) == pytest.approx(20)


def test_best_of_5_favours_favourite():
    assert elo.best_of_5(0.5) == pytest.approx(0.5)
    assert elo.best_of_5(0.7) > 0.7
    assert elo.best_of_5(0.3) < 0.3


def test_elo_learns():
    rows = [{"date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i), "winner": "A", "loser": "B",
             "surface": "Hard", "best_of": 3} for i in range(30)]
    e, frame = elo.run(pd.DataFrame(rows))
    assert e.predict("A", "B", "Hard") > 0.8
    assert frame.p_winner.iloc[0] == pytest.approx(0.5)


def test_simulate_has_no_edge_on_fair_coin():
    n = 4000
    rng = np.random.default_rng(0)
    won = rng.random(n) < 0.5
    pred = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="h"), "home": "A", "away": "B",
                         "league": "X", "p_a": 0.55, "m_a": 0.5, "odds_a": 2.0, "max_a": 2.0, "won_a": won})
    res = backtest.simulate(pred, {"a": ("p_a", "odds_a", "max_a", "won_a")}, blend=0.0, edge_min=0.05)
    assert res["bets"] == n
    lo, hi = res["flat_roi_ci95"]
    assert lo < 0 < hi  # a fake "edge" must not show up as proven profit


def test_normalise_football_data_fixture_format():
    from betit.data.football import normalise_football_data
    raw = pd.DataFrame({"Div": ["E0"], "Date": ["01/11/2026"], "HomeTeam": ["Arsenal"], "AwayTeam": ["Fulham"],
                        "AvgH": [1.4], "AvgD": [4.9], "AvgA": [8.3], "MaxH": [1.45], "Avg>2.5": [1.7], "Avg<2.5": [2.1]})
    df = normalise_football_data(raw)
    assert df.date.iloc[0] == pd.Timestamp("2026-11-01") and df.season.iloc[0] == "2627"
    assert df.odds_h.iloc[0] == 1.4 and df.max_h.iloc[0] == 1.45 and df.odds_u25.iloc[0] == 2.1


def test_normalise_tennis_data():
    from betit.data.tennis import normalise_tennis_data
    raw = pd.DataFrame({"Date": ["2025-01-05", "2025-01-06"], "Tournament": ["Brisbane"] * 2, "Surface": ["Hard"] * 2,
                        "Round": ["2nd Round", "1st Round"], "Best of": [3, 3], "Winner": ["Sinner J.", "X A."],
                        "Loser": ["Y B.", "Z C."], "WRank": [1, 50], "LRank": [40, 60], "B365W": [1.1, 1.8],
                        "B365L": [7.0, 2.0], "AvgW": [1.12, 1.85], "AvgL": [6.5, 1.95], "MaxW": [1.15, 1.9],
                        "MaxL": [7.5, 2.1], "Comment": ["Completed", "Walkover"]})
    df = normalise_tennis_data(raw, "ATP")
    assert len(df) == 1  # walkover dropped
    assert df.odds_w.iloc[0] == 1.12 and df.max_l.iloc[0] == 7.5
