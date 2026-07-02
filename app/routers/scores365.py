"""Endpoints en vivo basados en 365Scores (datos frescos de Liga MX)."""
from fastapi import APIRouter, Query, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.scrapers.scores365_scraper import Scores365Scraper, LIGAMX_TEAM_NAME_MAP
from app.cache import cached
from app.database import get_db
from app.dependencies import resolve_season_label
from app import models

router = APIRouter(prefix="/365scores", tags=["365scores"])


@cached(60)
def _cached_lineups(game_id: int):
    """Alineaciones del partido cacheadas (aisla la llamada HTTP a 365Scores del
    calculo con BD, que no puede ir dentro de @cached por la sesion)."""
    return Scores365Scraper().get_match_lineups(game_id)


@router.get("/matches")
@cached(60)
def matches(week: int = Query(None, description="Filtra por jornada (roundNum)"),
            status: str = Query(None, description="scheduled | live | finished")):
    data = Scores365Scraper().get_matches()
    if week is not None:
        data = [m for m in data if m.get("week") == week]
    if status:
        data = [m for m in data if m.get("status") == status]
    return sorted(data, key=lambda m: (m.get("match_date") is None, m.get("match_date")))


@router.get("/standings")
@cached(120)
def standings():
    return Scores365Scraper().get_standings()


@router.get("/teams")
@cached(3600)
def teams():
    return Scores365Scraper().get_teams()


@router.get("/matches/{game_id}/info")
@cached(60)
def info(game_id: int):
    """Ficha del partido: sede, arbitro/cuerpo arbitral, marcador y estado."""
    return Scores365Scraper().get_match_info(game_id)


@router.get("/matches/{game_id}/lineups")
@cached(60)
def lineups(game_id: int):
    return Scores365Scraper().get_match_lineups(game_id)


@router.get("/matches/{game_id}/probable-lineup")
@cached(120)
def probable_lineup(game_id: int):
    """XI PROBABLE (esperado) que publica 365Scores ANTES del confirmado, marcado
    claramente como probable. Solo devuelve datos si 365Scores marca la alineacion
    como NO confirmada; si ya esta confirmada (usa /lineups) o aun no hay XI,
    responde `disponible: false` con motivo. No se fabrica ninguna alineacion."""
    return Scores365Scraper().get_probable_lineup(game_id)


@router.get("/matches/{game_id}/events")
@cached(60)
def events(game_id: int):
    return Scores365Scraper().get_match_events(game_id)


@router.get("/matches/{game_id}/cards")
@cached(60)
def cards(game_id: int):
    return Scores365Scraper().get_match_cards(game_id)


@router.get("/matches/{game_id}/player-stats")
@cached(120)
def match_player_stats(game_id: int):
    """Estadisticas COMPLETAS por jugador del partido (minutos, goles, xG, xA,
    remates, pases, regates, duelos, intercepciones, rating...) para todos los
    jugadores de la alineacion. Joyita que ESPN no expone."""
    return Scores365Scraper().get_match_player_stats(game_id)


@router.get("/leaders")
@cached(600)
def player_leaders(category_id: int = Query(None, description="1=Goles, 3=Asistencias, 5=Goles+Asist, 12=Amarillas, 15=Salvadas...")):
    """Lideres de temporada por jugador en 16 categorias (goles, xG, asistencias,
    tarjetas, salvadas, valla invicta...). Filtra con category_id."""
    return Scores365Scraper().get_player_season_leaders(category_id)


@router.get("/team-leaders")
@cached(600)
def team_leaders(category_id: int = Query(None)):
    """Lideres de temporada por equipo."""
    return Scores365Scraper().get_team_season_leaders(category_id)


@router.get("/news")
@cached(300)
def news(limit: int = Query(30, ge=1, le=100)):
    """Noticias de Liga MX desde el feed propio de 365Scores (titulo, imagen,
    url y fecha)."""
    return Scores365Scraper().get_news(limit)


@router.get("/goalkeepers")
@cached(600)
def goalkeepers():
    """Tabla de porteros de la temporada: vallas invictas, goles recibidos,
    salvadas y penales atajados (ordenada por vallas invictas)."""
    return Scores365Scraper().get_goalkeepers()


@router.get("/matches/{game_id}/heatmaps")
@cached(300)
def match_heatmaps(game_id: int):
    """Mapas de calor por jugador del partido (URL de imagen lista para mostrar)."""
    return Scores365Scraper().get_match_heatmaps(game_id)


@router.get("/matches/{game_id}/shots")
@cached(120)
def match_shots(game_id: int):
    """Mapa de tiros con xG del partido: cada disparo con xG, xGoT, parte del
    cuerpo, resultado y coordenadas, mas los totales de xG por equipo."""
    return Scores365Scraper().get_match_shots(game_id)


@router.get("/matches/{game_id}/top-performers")
@cached(120)
def match_top_performers(game_id: int):
    """Mejores jugadores del partido por posicion (local y visitante)."""
    return Scores365Scraper().get_match_top_performers(game_id)


@router.get("/transfers")
@cached(600)
def transfers(status: str = Query(None, description="Filtra por estado: confirmado | rumor"),
              year: int = Query(None, description="Anio del mercado (por defecto el actual, ej. 2026)")):
    """Mercado de fichajes de Liga MX AGRUPADO POR EQUIPO. Para cada equipo
    devuelve sus `altas` (jugadores que entran) y `bajas` (los que salen), con el
    club de origen/destino y el tipo de operacion ("transfer" o "loan"). Los
    nombres de equipo se normalizan a los de ESPN para que empaten con el resto
    de la API. Si 365Scores no expone datos, devuelve `equipos: {}` y
    `disponible: false` (no se fabrican datos). Por defecto muestra el mercado
    del anio en curso."""
    return Scores365Scraper().get_transfers(status=status, year=year)


def _team_impact(db: Session, team: dict, label: str) -> dict:
    """Calcula la fuerza del XI confirmado de un equipo y sus jugadores clave.

    Importancia de un jugador = (goles + asistencias) de la temporada como % de
    la produccion total (goles + asistencias) de su equipo. Es una senal simple
    y transparente de cuanto pesa cada jugador en la ofensiva del equipo. Un
    jugador sin stats (recien llegado, pretemporada) pesa 0%.
    """
    M = models.PlayerMatchStat
    players = team.get("players") or []
    lineup_ids = {p["player_id"] for p in players if p.get("player_id")}
    starter_ids = {p["player_id"] for p in players if p.get("player_id") and p.get("starter")}

    # Detecta el team_id (BD/ESPN) a partir de los jugadores del XI: el team_id
    # mas frecuente entre sus stats de la temporada. Robusto ante nombres.
    team_id = None
    if lineup_ids:
        rows = (db.query(M.team_id, func.count(M.id))
                .filter(M.season == label, M.player_id.in_(lineup_ids), M.team_id.isnot(None))
                .group_by(M.team_id).order_by(func.count(M.id).desc()).first())
        team_id = rows[0] if rows else None

    # Nombre del equipo: preferimos el de la BD (mismo texto que ESPN); si no,
    # normalizamos el de 365Scores con el mapa compartido.
    raw_name = team.get("team_name")
    team_name = LIGAMX_TEAM_NAME_MAP.get(raw_name, raw_name)
    if team_id is not None:
        t = db.get(models.Team, team_id)
        if t:
            team_name = t.name

    # Produccion (goles + asistencias) de toda la plantilla del equipo en la temporada.
    prod_by_id, name_by_id = {}, {}
    if team_id is not None:
        agg = (db.query(M.player_id, M.player_name,
                        func.sum(M.goals + M.assists).label("prod"))
               .filter(M.season == label, M.team_id == team_id)
               .group_by(M.player_id, M.player_name).all())
        for pid, pname, prod in agg:
            if pid is None:
                continue
            prod_by_id[pid] = float(prod or 0)
            name_by_id[pid] = pname
    team_total = sum(prod_by_id.values())

    def _pct(pid):
        if team_total <= 0:
            return 0.0
        return round(prod_by_id.get(pid, 0.0) / team_total * 100, 1)

    fuerza = round(sum(_pct(pid) for pid in starter_ids), 1) if team_total > 0 else 0.0

    # Top-5 jugadores del equipo por importancia (produccion en la temporada).
    top5 = sorted(prod_by_id.items(), key=lambda kv: kv[1], reverse=True)[:5]
    ausentes, titulares = [], []
    for pid, _prod in top5:
        entry = {"jugador": name_by_id.get(pid), "importancia_pct": _pct(pid)}
        if entry["importancia_pct"] <= 0:
            continue
        (titulares if pid in starter_ids else ausentes).append(entry)

    return {
        "team_id": team_id,
        "fuerza_xi_pct": min(fuerza, 100.0),
        "titulares_confirmados": len(starter_ids),
        "ausentes_clave": ausentes,
        "titulares_clave": titulares,
    }, team_name


@router.get("/matches/{game_id}/lineup-impact")
def lineup_impact(game_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Impacto del XI confirmado: para un partido, calcula que tan fuerte es la
    alineacion titular de cada equipo (fuerza_xi_pct, 0-100) y que jugadores
    clave estan ausentes, usando SOLO datos reales de la temporada persistidos en
    la BD (player_match_stats).

    - `fuerza_xi_pct`: suma de la importancia de los titulares confirmados. 100%
      = esta toda la produccion ofensiva del equipo; menos = falta gente clave.
    - `ausentes_clave`: jugadores del top-5 de importancia que NO son titulares.
    - `titulares_clave`: jugadores del top-5 de importancia que SI arrancan.

    Si aun no hay XI publicado, devuelve `disponible: false` (no se fabrica nada).
    Importancia = (goles + asistencias) del jugador como % del total del equipo.
    """
    label = resolve_season_label(db, season)
    lineups = _cached_lineups(game_id)
    teams = lineups.get("teams") or []

    # Sin XI publicado (365Scores aun no expone titulares) -> no disponible.
    has_starters = any(any(p.get("starter") for p in (t.get("players") or [])) for t in teams)
    if not has_starters:
        return {"disponible": False, "season": label, "game_id": game_id, "equipos": {}}

    equipos = {}
    for t in teams:
        impact, team_name = _team_impact(db, t, label)
        if team_name:
            equipos[team_name] = impact

    return {
        "disponible": True,
        "season": label,
        "game_id": game_id,
        "metodo": "importancia = (goles + asistencias) del jugador como % del total del equipo en la temporada; jugadores sin stats pesan 0%.",
        "equipos": equipos,
    }
