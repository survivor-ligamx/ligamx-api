from fastapi import APIRouter, Query
from app.scrapers.sofascore_scraper import (
    get_events,
    get_player_stats,
    get_match_details,
    get_match_incidents,
    get_lineups,
)

router = APIRouter()

@router.get("/sofascore/matches")
def get_sofascore_matches():
    return get_events()

@router.get("/sofascore/player-stats")
def get_sofascore_player_stats(limit: int = Query(20, ge=1, le=500)):
    return get_player_stats(limit=limit)

@router.get("/sofascore/matches/{event_id}")
def get_sofascore_match(event_id: int):
    return get_match_details(event_id)

@router.get("/sofascore/matches/{event_id}/incidents")
def get_sofascore_incidents(event_id: int):
    return get_match_incidents(event_id)

@router.get("/sofascore/matches/{event_id}/lineups")
def get_sofascore_lineups(event_id: int):
    return get_lineups(event_id)
