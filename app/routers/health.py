from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, cache
from app.season import current_tournament, current_season_name, to_naive_utc
from app.metrics import metrics
from app.cache import cache_stats

router = APIRouter()


@router.get("/")
def read_root():
    return {"message": "API Liga MX", "version": "1.0", "status": "running"}


@router.get("/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now()}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)):
    """Readiness: verifica conexión a BD y a Redis. Devuelve 503 si la BD no
    responde (Redis es opcional, no marca 'no listo')."""
    checks = {"database": "ok", "redis": "disabled"}
    healthy = True
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        checks["database"] = f"error: {str(e)[:80]}"
        healthy = False
    if cache._redis is not None:
        try:
            cache._redis.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {str(e)[:80]}"
    return JSONResponse(status_code=200 if healthy else 503, content={"ready": healthy, "checks": checks})


@router.get("/metrics", tags=["meta"])
def metrics_endpoint():
    """Metricas de observabilidad en proceso: uptime, total de requests, desglose
    por codigo (2xx/4xx/5xx), latencias, rutas mas usadas y estado del cache."""
    snap = metrics.snapshot()
    snap["cache"] = cache_stats()
    return snap


@router.get("/season")
def season_info(db: Session = Depends(get_db)):
    """Informacion del torneo vigente y de los datos cargados, para saber con
    claridad QUE torneo esta sirviendo la API (Apertura vs Clausura)."""
    tournament, year = current_tournament()
    season = db.query(models.Season).order_by(models.Season.id.desc()).first()
    total_matches = db.query(models.Match).count()
    finished = db.query(models.Match).filter(models.Match.status == "finished").count()
    first = db.query(models.Match).filter(models.Match.match_date.isnot(None)).order_by(models.Match.match_date).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    first_date = to_naive_utc(first.match_date if first else None)
    started = bool(first_date and first_date <= now)
    return {
        "tournament_now": current_season_name(),
        "tournament_type": tournament,
        "year": year,
        "loaded_season": season.name if season else None,
        "loaded_season_type": season.tournament_type if season else None,
        "has_started": started,
        "first_match_date": first_date,
        "total_matches": total_matches,
        "finished_matches": finished,
    }
