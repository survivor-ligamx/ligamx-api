import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db, engine
from app.db_identity import db_fingerprint
from app.dependencies import verify_api_key
from app.rate_limit import limiter, SYNC_LIMIT
from app.season import to_naive_utc
from app.services.sync_service import run_sync_with_log, run_backfill_with_log
from app import models

router = APIRouter()


@router.post("/sync")
@limiter.limit(SYNC_LIMIT)
def sync_data(request: Request, source: str = "espn", db: Session = Depends(get_db), api_key: str = Depends(verify_api_key)):
    result = run_sync_with_log(db, source)
    return {"message": "Datos sincronizados", "source": source, "result": result}


@router.post("/sync/backfill")
@limiter.limit(SYNC_LIMIT)
def backfill_season(
    request: Request,
    year: int = Query(..., ge=2000, le=2100, description="Ano del torneo, p. ej. 2025"),
    tournament: str = Query(..., description="'Apertura' o 'Clausura'"),
    source: str = Query("espn"),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """Carga una temporada PASADA al historico sin borrar las demas. La tabla se
    reconstruye desde los resultados. Ej: POST /sync/backfill?year=2025&tournament=Apertura"""
    if tournament not in ("Apertura", "Clausura"):
        raise HTTPException(status_code=422, detail="tournament debe ser 'Apertura' o 'Clausura'")
    try:
        result = run_backfill_with_log(db, year, tournament, source)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"message": "Temporada cargada al historico", "result": result}


@router.post("/sync/player-identity")
@limiter.limit(SYNC_LIMIT)
def sync_player_identity(
    request: Request,
    season: str = Query(None, description="Etiqueta de temporada; por defecto todas"),
    force: bool = Query(False, description="Si True, re-mapea incluso enlaces existentes"),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """Reconstruye el mapa de identidad ESPN<->365Scores (rellena
    players.external_365_id) emparejando por nombre+equipo. Respeta los enlaces
    manuales existentes salvo que force=True. Idempotente."""
    from app.services.player_identity import build_player_identity_map

    result = build_player_identity_map(db, season, force=force)
    return {"message": "Mapa de identidad reconstruido", "result": result}


@router.post("/players/{player_id}/link-365")
@limiter.limit(SYNC_LIMIT)
def link_player_365(
    request: Request,
    player_id: int,
    external_365_id: int = Query(..., description="id del jugador en 365Scores"),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """Enlaza MANUALMENTE un jugador (ESPN) con su id de 365Scores. Util para los
    pocos casos que el cruce automatico no resuelve (apodos sin tokens en comun).
    El enlace manual se respeta en futuras reconstrucciones del mapa."""
    player = db.query(models.Player).filter(models.Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    player.external_365_id = external_365_id  # type: ignore[assignment]
    db.add(player)
    db.commit()
    return {
        "message": "Jugador enlazado con 365Scores",
        "player_id": player.id,
        "player": player.name,
        "external_365_id": external_365_id,
    }


@router.get("/sync/status")
def sync_status(db: Session = Depends(get_db)):
    """Estado y frescura de los datos: ultimo sync, si fue exitoso, hace cuanto
    corrio y un resumen (`freshness`) que marca si los datos estan viejos segun
    un umbral (DATA_STALE_AFTER_HOURS, 6h por defecto). Incluye `data_counts`
    (cuan poblada esta la BD; en pretemporada 0 es correcto, no un error) y la
    fecha del partido mas reciente. Util para monitoreo y para confiar en la API."""
    last = db.query(models.SyncLog).order_by(models.SyncLog.finished_at.desc()).first()
    last_success = db.query(models.SyncLog).filter(models.SyncLog.status == "success").order_by(models.SyncLog.finished_at.desc()).first()

    def serialize(log):
        if not log:
            return None
        return {
            "status": log.status,
            "source": log.source,
            "season": log.season,
            "teams": log.teams,
            "players": log.players,
            "matches": log.matches,
            "detail": log.detail,
            "duration_seconds": round(log.duration_seconds, 1) if log.duration_seconds else None,
            "finished_at": log.finished_at,
        }

    age_seconds = None
    if last_success and last_success.finished_at:
        age_seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - to_naive_utc(last_success.finished_at)).total_seconds()

    # Umbral de obsolescencia (horas). El sync corre cada 2h; damos margen para
    # una corrida perdida antes de marcar los datos como "viejos".
    try:
        stale_after_hours = float(os.getenv("DATA_STALE_AFTER_HOURS", "6"))
    except ValueError:
        stale_after_hours = 6.0

    age_hours = round(age_seconds / 3600, 1) if age_seconds is not None else None
    is_stale = age_seconds is None or age_seconds > stale_after_hours * 3600

    if age_seconds is None:
        message = "Sin sincronizaciones exitosas todavia."
    elif is_stale:
        message = f"Datos posiblemente viejos: ultima sync exitosa hace {age_hours}h (umbral {stale_after_hours}h)."
    else:
        message = f"Datos frescos: ultima sync exitosa hace {age_hours}h."

    # Conteos para saber de un vistazo QUE tan poblada esta la BD (util en
    # pretemporada: vacio/0 es correcto, no un error).
    newest_match = db.query(func.max(models.Match.match_date)).scalar()
    data_counts = {
        "teams": db.query(models.Team).count(),
        "players": db.query(models.Player).count(),
        "matches": db.query(models.Match).count(),
        "finished_matches": db.query(models.Match).filter(models.Match.status == "finished").count(),
        "standings": db.query(models.Standing).count(),
        "player_match_stats": db.query(models.PlayerMatchStat).count(),
        "news": db.query(models.News).count(),
    }

    return {
        "last_sync": serialize(last),
        "last_successful_sync": serialize(last_success),
        "data_age_seconds": int(age_seconds) if age_seconds is not None else None,
        "data_age_hours": age_hours,
        "has_data": data_counts["teams"] > 0,
        "database": {
            "dialect": engine.dialect.name,
            "fingerprint": db_fingerprint(),
        },
        "freshness": {
            "is_stale": is_stale,
            "stale_after_hours": stale_after_hours,
            "data_age_hours": age_hours,
            "last_successful_sync_at": last_success.finished_at if last_success else None,
            "message": message,
        },
        "data_counts": data_counts,
        "newest_match_date": newest_match,
    }
