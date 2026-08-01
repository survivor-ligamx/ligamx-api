from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import verify_api_key
from app.rate_limit import limiter, SYNC_LIMIT
from app import models, schemas

router = APIRouter(tags=["odds"])


@router.post("/odds", summary="Archivar snapshots de momios (histórico)")
@limiter.limit(SYNC_LIMIT)
def guardar_odds(
    request: Request,
    payload: list[schemas.MatchOddsCreate],
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """
    Guarda una lista de snapshots de momios (uno por partido) en el histórico.
    Protegido con X-API-Key. Cada llamada agrega filas nuevas (serie temporal):
    la idea es acumular momios jornada a jornada para poder backtestear luego la
    mezcla modelo+mercado con datos reales.
    """
    filas = []
    for item in payload:
        filas.append(
            models.MatchOdds(
                season=item.season,
                home_team=item.home_team,
                away_team=item.away_team,
                match_date=item.match_date,
                source=item.source,
                odds_local=item.odds_local,
                odds_empate=item.odds_empate,
                odds_visita=item.odds_visita,
                ou_linea=item.ou_linea,
                odds_over=item.odds_over,
                odds_under=item.odds_under,
                extra=item.extra,
            )
        )
    db.add_all(filas)
    db.commit()
    return {"message": "Momios archivados", "guardados": len(filas)}


@router.get("/odds", response_model=list[schemas.MatchOddsResponse], summary="Consultar histórico de momios")
def listar_odds(
    db: Session = Depends(get_db),
    season: Optional[str] = Query(None, description="Filtra por temporada (ej. 'Apertura 2026')"),
    home_team: Optional[str] = Query(None, description="Filtra por equipo local"),
    away_team: Optional[str] = Query(None, description="Filtra por equipo visitante"),
    desde: Optional[datetime] = Query(None, description="Solo snapshots capturados desde esta fecha (ISO)"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Momios archivados (más recientes primero), con filtros opcionales."""
    q = db.query(models.MatchOdds)
    if season:
        q = q.filter(models.MatchOdds.season == season)
    if home_team:
        q = q.filter(models.MatchOdds.home_team == home_team)
    if away_team:
        q = q.filter(models.MatchOdds.away_team == away_team)
    if desde:
        q = q.filter(models.MatchOdds.captured_at >= desde)
    return q.order_by(models.MatchOdds.captured_at.desc()).offset(offset).limit(limit).all()
