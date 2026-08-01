import os
import secrets
from fastapi import Header, HTTPException
from sqlalchemy import case
from app import models


def verify_api_key(api_key: str = Header(..., alias="X-API-Key")):
    """Valida la API key para operaciones sensibles (sync/backfill).
    - 503 si el servidor no tiene SYNC_API_KEY configurada (error de operacion).
    - 403 si la key no coincide. La comparacion es de tiempo constante para no
      filtrar informacion por timing.
    """
    configured = os.environ.get("SYNC_API_KEY")
    if not configured:
        raise HTTPException(status_code=503, detail="SYNC_API_KEY no configurada en el servidor")
    if not secrets.compare_digest(api_key, configured):
        raise HTTPException(status_code=403, detail="API Key invalida")
    return api_key

def get_or_404(db, model, id_value):
    item = db.query(model).filter(model.id == id_value).first()
    if not item:
        raise HTTPException(status_code=404, detail="No encontrado")
    return item


# Liga MX: al acumular 5 tarjetas amarillas, el jugador cumple 1 partido de
# suspension (y el contador se reinicia). Una roja directa tambien suspende.
SUSPENSION_YELLOWS = 5


def discipline_summary(yellow, red, threshold=SUSPENSION_YELLOWS):
    """Resumen de disciplina a partir de amarillas/rojas acumuladas.

    Devuelve el conteo, cuantas suspensiones por acumulacion ha provocado, a
    cuantas amarillas esta de la siguiente suspension, si esta en riesgo (a una
    amarilla) y un indice simple de indisciplina (amarilla=1, roja=2)."""
    yellow = int(yellow or 0)
    red = int(red or 0)
    cycle = yellow % threshold
    return {
        "yellow_cards": yellow,
        "red_cards": red,
        "yellow_suspensions": yellow // threshold,
        "yellows_to_suspension": (threshold - cycle) if cycle else threshold,
        "suspension_risk": cycle == threshold - 1,
        "discipline_points": yellow + red * 2,
    }


def _apertura_first():
    # Dentro de un mismo ano, el Apertura es posterior al Clausura.
    return case((models.Season.tournament_type == "Apertura", 1), else_=0)


def latest_season(db):
    """Temporada mas reciente cargada (ano desc, y dentro del ano Apertura > Clausura)."""
    return db.query(models.Season).order_by(models.Season.year.desc(), _apertura_first().desc()).first()


def find_season(db, season=None):
    """Resuelve una temporada por etiqueta exacta ('Apertura 2026'), por ano
    ('2026' -> torneo mas reciente de ese ano) o, si no se indica, la vigente."""
    if not season:
        return latest_season(db)
    s = db.query(models.Season).filter(models.Season.name == season).first()
    if s is None and str(season).isdigit():
        s = (db.query(models.Season)
             .filter(models.Season.year == int(season))
             .order_by(_apertura_first().desc())
             .first())
    return s


def resolve_season_id(db, season=None):
    """id de temporada para filtrar matches/standings. -1 si se pidio una
    temporada inexistente (-> resultados vacios); None si no hay temporadas."""
    s = find_season(db, season)
    if s:
        return s.id
    return -1 if season else None


def resolve_season_label(db, season=None):
    """Etiqueta de temporada (clave de las tablas de stats: 'Apertura 2026')."""
    from app.season import current_season_name
    s = find_season(db, season)
    if s:
        return s.name
    return season or current_season_name()
