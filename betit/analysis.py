"""Descriptive football analysis: form, head-to-head, home/away splits, shot quality."""
from __future__ import annotations

import numpy as np
import pandas as pd


def team_matches(results: pd.DataFrame, team: str) -> pd.DataFrame:
    m = results[(results.home == team) | (results.away == team)].copy()
    is_home = m.home == team
    m["venue"] = np.where(is_home, "H", "A")
    m["opponent"] = np.where(is_home, m.away, m.home)
    m["gf"] = np.where(is_home, m.hg, m.ag)
    m["ga"] = np.where(is_home, m.ag, m.hg)
    m["sf"] = np.where(is_home, m.hs, m.as_)
    m["sa"] = np.where(is_home, m.as_, m.hs)
    m["sotf"] = np.where(is_home, m.hst, m.ast)
    m["sota"] = np.where(is_home, m.ast, m.hst)
    m["result"] = np.select([m.gf > m.ga, m.gf == m.ga], ["W", "D"], "L")
    m["points"] = m.result.map({"W": 3, "D": 1, "L": 0})
    return m.sort_values("date")


def summarise(m: pd.DataFrame) -> dict:
    if m.empty:
        return {"played": 0}
    n = len(m)
    out = {
        "played": n,
        "ppg": float(m.points.mean()),
        "w": int((m.result == "W").sum()), "d": int((m.result == "D").sum()), "l": int((m.result == "L").sum()),
        "gf_pg": float(m.gf.mean()), "ga_pg": float(m.ga.mean()),
        "clean_sheets": float((m.ga == 0).mean()),
        "btts": float(((m.gf > 0) & (m.ga > 0)).mean()),
        "over25": float(((m.gf + m.ga) > 2.5).mean()),
    }
    if m.sotf.notna().any():
        out |= {
            "shots_pg": float(m.sf.mean()), "shots_against_pg": float(m.sa.mean()),
            "sot_pg": float(m.sotf.mean()), "sot_against_pg": float(m.sota.mean()),
            # Finishing: goals per shot on target. Far above ~0.30-0.35 is
            # usually luck that regresses, not a skill that persists.
            "conversion": float(m.gf.sum() / max(m.sotf.sum(), 1)),
            "keeping": float(1 - m.ga.sum() / max(m.sota.sum(), 1)),
        }
    return out


def team_report(results: pd.DataFrame, team: str, league: str, last_n: int = 10) -> dict:
    allm = team_matches(results, team)
    season = allm[allm.league == league].season.max() if (allm.league == league).any() else None
    cur = allm[(allm.season == season) & (allm.league == league)] if season else allm.iloc[0:0]
    recent = allm.tail(last_n)
    return {
        "team": team,
        "season": season,
        "season_stats": summarise(cur),
        "home_stats": summarise(cur[cur.venue == "H"]),
        "away_stats": summarise(cur[cur.venue == "A"]),
        "recent_stats": summarise(recent),
        "form": "".join(recent.result.tail(5)),
        "recent": [
            {"date": d.strftime("%Y-%m-%d"), "venue": v, "opponent": o, "score": f"{int(gf)}-{int(ga)}", "result": r}
            for d, v, o, gf, ga, r in zip(recent.date, recent.venue, recent.opponent, recent.gf, recent.ga, recent.result)
        ][::-1],
    }


def head_to_head(results: pd.DataFrame, a: str, b: str, n: int = 10) -> dict:
    m = results[((results.home == a) & (results.away == b)) | ((results.home == b) & (results.away == a))]
    m = m.sort_values("date").tail(n)
    a_w = ((m.home == a) & (m.hg > m.ag)) | ((m.away == a) & (m.ag > m.hg))
    b_w = ((m.home == b) & (m.hg > m.ag)) | ((m.away == b) & (m.ag > m.hg))
    return {
        "played": int(len(m)), "a_wins": int(a_w.sum()), "b_wins": int(b_w.sum()),
        "draws": int(len(m) - a_w.sum() - b_w.sum()),
        "avg_goals": float((m.hg + m.ag).mean()) if len(m) else None,
        "matches": [{"date": d.strftime("%Y-%m-%d"), "home": h, "away": aw, "score": f"{int(x)}-{int(y)}"}
                    for d, h, aw, x, y in zip(m.date, m.home, m.away, m.hg, m.ag)][::-1],
    }


def league_table(results: pd.DataFrame, league: str, season: str | None = None) -> list[dict]:
    lg = results[results.league == league]
    season = season or lg.season.max()
    s = lg[lg.season == season]
    rows = {}
    for r in s.itertuples():
        for team, gf, ga in ((r.home, r.hg, r.ag), (r.away, r.ag, r.hg)):
            t = rows.setdefault(team, {"team": team, "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})
            t["p"] += 1
            t["gf"] += int(gf)
            t["ga"] += int(ga)
            if gf > ga:
                t["w"] += 1
                t["pts"] += 3
            elif gf == ga:
                t["d"] += 1
                t["pts"] += 1
            else:
                t["l"] += 1
    table = sorted(rows.values(), key=lambda t: (-t["pts"], -(t["gf"] - t["ga"]), -t["gf"]))
    for i, t in enumerate(table, 1):
        t["pos"] = i
        t["gd"] = t["gf"] - t["ga"]
    return table
