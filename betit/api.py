"""HTTP API + static web UI."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, service

app = FastAPI(title="betit", description="Football & tennis probability models with honest backtests")


@app.on_event("startup")
def _startup():
    service.reload()


class Staking(BaseModel):
    bankroll: float = 1000
    kelly_mult: float = 0.25
    cap: float = 0.03
    edge_min: float = 0.03


class FootballQuery(Staking):
    league: str
    home: str
    away: str
    neutral: bool = False
    odds: dict[str, float | None] = {}


class TennisQuery(Staking):
    p1: str
    p2: str
    surface: str = "Hard"
    best_of: int = 3
    odds1: float | None = None
    odds2: float | None = None


def _need(attr: str):
    if getattr(service.S, attr) is None:
        raise HTTPException(503, "No data loaded yet. Run: python -m betit update")


@app.get("/api/status")
def status():
    return service.status()


@app.get("/api/football/leagues")
def leagues():
    return service.leagues()


@app.get("/api/football/ratings/{league}")
def ratings(league: str):
    _need("results")
    return service.ratings(league)


@app.get("/api/football/table/{league}")
def table(league: str):
    _need("results")
    return service.league_table(league)


@app.post("/api/football/match")
def football_match(q: FootballQuery):
    _need("results")
    if q.home == q.away:
        raise HTTPException(400, "Pick two different teams")
    try:
        return service.football_match(q.league, q.home, q.away, q.odds, None, q.bankroll, q.kelly_mult,
                                      q.cap, q.edge_min, q.neutral)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/football/board")
def board(bankroll: float = 1000, kelly_mult: float = 0.25, cap: float = 0.03, edge_min: float = 0.03):
    _need("results")
    return service.value_board(bankroll, kelly_mult, cap, edge_min)


@app.get("/api/tennis/players")
def players(q: str = Query("", max_length=60), limit: int = 30):
    _need("tennis")
    return service.tennis_players(q, limit)


@app.post("/api/tennis/match")
def tennis_match(q: TennisQuery):
    _need("tennis")
    if q.p1 == q.p2:
        raise HTTPException(400, "Pick two different players")
    return service.tennis_match(q.p1, q.p2, q.surface, q.best_of, q.odds1, q.odds2, q.bankroll,
                                q.kelly_mult, q.cap, q.edge_min)


@app.get("/api/backtests")
def backtests():
    return service.backtests()


@app.post("/api/reload")
def reload():
    service.reload()
    return service.status()


app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")
