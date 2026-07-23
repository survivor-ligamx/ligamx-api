from datetime import datetime, timedelta, timezone
import unicodedata
from typing import Any, cast
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.dependencies import find_season, get_or_404, resolve_season_id
from app import models, schemas
from app.scrapers.espn_requests_scraper import ESPNRequestsScraper
from app.scrapers.sofascore_scraper import get_match_details
from app.cache import cached

router = APIRouter()


def _norm(s: str) -> str:
    """Nombre canonico: sin acentos, minusculas, sin espacios extra."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _canonical_team_ids(db: Session, team: "models.Team") -> set:
    """Todos los team_id que comparten el nombre canonico del equipo. Agrega
    posibles filas duplicadas del mismo club a lo largo de las temporadas o
    fuentes (p. ej. si un sync viejo lo cargo con otro id). Incluye siempre el
    id propio, asi el H2H nunca devuelve menos de lo que ya devolvia."""
    nq = _norm(team.name)  # type: ignore[arg-type]
    ids = {t.id for t in db.query(models.Team.id, models.Team.name).all() if _norm(t.name) == nq}
    ids.add(team.id)
    return ids


def _with_season(query, db: Session, season: str | None, *, default_latest: bool = False):
    """Filtra por temporada solo si se solicita; algunos endpoints usan la vigente."""
    if season is None and not default_latest:
        return query
    season_id = resolve_season_id(db, season)
    if season_id is not None:
        query = query.filter(models.Match.season_id == season_id)
    return query


@router.get("/matches", response_model=list[schemas.MatchResponse])
def get_matches(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    team_id: int | None = Query(None),
    week: int | None = Query(None),
    status: str | None = Query(None),
    season: str | None = Query(None, description="Etiqueta o ano; por defecto todas las temporadas"),
    db: Session = Depends(get_db),
):
    q = db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
    q = _with_season(q, db, season)
    if team_id is not None:
        q = q.filter((models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id))
    if week is not None:
        q = q.filter(models.Match.week_number == week)
    if status:
        q = q.filter(models.Match.status == status)
    return q.order_by(models.Match.match_date).offset(offset).limit(limit).all()


@router.get("/calendar")
def get_calendar(
    season: str | None = Query(None, description="Etiqueta o ano; por defecto la temporada vigente"),
    db: Session = Depends(get_db),
):
    """Calendario completo de la temporada agrupado por jornada, con rival, fecha,
    sede (nombre oficial), marcador y estado. Combina el fixture real (con los dos
    equipos) y las sedes oficiales 2026."""
    selected_season = find_season(db, season)
    q = db.query(models.Match).options(
        joinedload(models.Match.home_team),
        joinedload(models.Match.away_team),
        joinedload(models.Match.stadium),
    )
    q = _with_season(q, db, season, default_latest=True)
    matches = q.order_by(models.Match.week_number, models.Match.match_date).all()

    jornadas: dict[Any, Any] = {}
    for m in matches:
        jn = m.week_number or 0
        j = jornadas.setdefault(jn, {"jornada": m.week_number, "matches": []})
        j["matches"].append(
            {
                "id": m.id,
                "espn_event_id": m.espn_event_id,
                "date": schemas.utc_isoformat(cast(datetime | None, m.match_date)),
                "status": m.status,
                "home_team": {"id": m.home_team_id, "name": m.home_team.name if m.home_team else None, "logo_url": m.home_team.logo_url if m.home_team else None},
                "away_team": {"id": m.away_team_id, "name": m.away_team.name if m.away_team else None, "logo_url": m.away_team.logo_url if m.away_team else None},
                "venue": m.stadium.name if m.stadium else None,
                "score": {"home": m.home_score, "away": m.away_score},
            }
        )
    return {
        "season": selected_season.name if selected_season else (season or "vigente"),
        "total_matches": len(matches),
        "jornadas": [jornadas[k] for k in sorted(jornadas)],
    }


@router.get("/matches/upcoming", response_model=list[schemas.MatchResponse])
def get_upcoming_matches(
    limit: int = Query(10, ge=1, le=50),
    season: str | None = Query(None, description="Etiqueta o ano; por defecto todas las temporadas"),
    db: Session = Depends(get_db),
):
    q = db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
    q = _with_season(q, db, season)
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    return q.filter(models.Match.match_date >= now_utc_naive).order_by(models.Match.match_date).limit(limit).all()


@router.get("/matches/team/{team_id}", response_model=list[schemas.MatchResponse])
def get_team_matches(
    team_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    season: str | None = Query(None, description="Etiqueta o ano; por defecto todas las temporadas"),
    db: Session = Depends(get_db),
):
    get_or_404(db, models.Team, team_id)
    q = db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
    q = _with_season(q, db, season)
    return q.filter((models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id)).order_by(models.Match.match_date).offset(offset).limit(limit).all()


@router.get("/matches/week/{week_number}", response_model=list[schemas.MatchResponse])
def get_matches_by_week(
    week_number: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    season: str | None = Query(None, description="Etiqueta o ano; por defecto todas las temporadas"),
    db: Session = Depends(get_db),
):
    q = db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
    q = _with_season(q, db, season)
    return q.filter(models.Match.week_number == week_number).order_by(models.Match.match_date).offset(offset).limit(limit).all()


@router.get("/h2h/{team1_id}/{team2_id}", response_model=list[schemas.MatchResponse])
def get_h2h(team1_id: int, team2_id: int, db: Session = Depends(get_db)):
    """Historial completo de enfrentamientos directos entre dos equipos, a lo
    largo de TODAS las temporadas cargadas (no solo la vigente). Agrega por
    equipo canonico (nombre normalizado), por si un club tuviera filas con ids
    distintos entre temporadas."""
    t1 = get_or_404(db, models.Team, team1_id)
    t2 = get_or_404(db, models.Team, team2_id)
    ids1 = _canonical_team_ids(db, t1)
    ids2 = _canonical_team_ids(db, t2)
    return (
        db.query(models.Match)
        .filter((models.Match.home_team_id.in_(ids1) & models.Match.away_team_id.in_(ids2)) | (models.Match.home_team_id.in_(ids2) & models.Match.away_team_id.in_(ids1)))
        .order_by(models.Match.match_date)
        .all()
    )


@router.get("/h2h/{team1_id}/{team2_id}/summary")
def get_h2h_summary(team1_id: int, team2_id: int, db: Session = Depends(get_db)):
    """Resumen del historial entre dos equipos a lo largo de TODAS las temporadas
    cargadas: partidos jugados, victorias de cada uno, empates y goles totales.
    Agrega por equipo canonico (nombre normalizado) para no perder partidos si un
    club aparece con ids distintos en distintas temporadas."""
    t1 = get_or_404(db, models.Team, team1_id)
    t2 = get_or_404(db, models.Team, team2_id)
    ids1 = _canonical_team_ids(db, t1)
    ids2 = _canonical_team_ids(db, t2)
    matches = (
        db.query(models.Match)
        .filter(
            ((models.Match.home_team_id.in_(ids1) & models.Match.away_team_id.in_(ids2)) | (models.Match.home_team_id.in_(ids2) & models.Match.away_team_id.in_(ids1))),
            models.Match.status == "finished",
            models.Match.home_score.isnot(None),
            models.Match.away_score.isnot(None),
        )
        .all()
    )

    t1_wins = t2_wins = draws = t1_goals = t2_goals = 0
    for m in matches:
        if m.home_team_id in ids1:
            g1, g2 = m.home_score, m.away_score
        else:
            g1, g2 = m.away_score, m.home_score
        t1_goals += g1  # type: ignore[assignment]
        t2_goals += g2  # type: ignore[assignment]
        if g1 > g2:
            t1_wins += 1
        elif g2 > g1:
            t2_wins += 1
        else:
            draws += 1

    return {
        "team1": {"id": team1_id, "name": t1.name, "wins": t1_wins, "goals": t1_goals},
        "team2": {"id": team2_id, "name": t2.name, "wins": t2_wins, "goals": t2_goals},
        "played": len(matches),
        "draws": draws,
        "seasons_covered": db.query(models.Season).count(),
    }


@router.get("/matches/live")
@cached(30)
def get_live_matches():
    scraper = ESPNRequestsScraper()
    return scraper.get_live_matches()


@router.get("/matches/today")
@cached(60)
def get_matches_today(date: str = Query(None)):
    scraper = ESPNRequestsScraper()
    date_str = date.replace("-", "") if date else datetime.now().strftime("%Y%m%d")
    return scraper.get_matches_by_date(date_str)


@router.get("/matches/{match_id}", response_model=schemas.MatchResponse)
def get_match(match_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, models.Match, match_id)


@router.get("/matches/{match_id}/sofascore")
def get_match_sofascore(match_id: int, db: Session = Depends(get_db)):
    match = get_or_404(db, models.Match, match_id)
    if not match.sofascore_event_id:
        raise HTTPException(status_code=404, detail="No hay datos de SofaScore para este partido")
    return get_match_details(match.sofascore_event_id)


# Funciones cacheadas que golpean la fuente externa (clave de cache = id externo).
@cached(120)
def _fetch_stats(event_id: str):
    return ESPNRequestsScraper().get_match_stats(event_id)


@cached(120)
def _fetch_lineups(event_id: str):
    return ESPNRequestsScraper().get_match_lineups(event_id)


@cached(120)
def _fetch_events(event_id: str):
    return ESPNRequestsScraper().get_match_events(event_id)


@cached(120)
def _fetch_cards(event_id: str):
    return ESPNRequestsScraper().get_match_cards(event_id)


def _espn_event_id(match_id: int, db: Session) -> str:
    """Resuelve el ID estable de ESPN a partir del ID interno del partido."""
    match = get_or_404(db, models.Match, match_id)
    if not match.espn_event_id:
        raise HTTPException(status_code=404, detail="Este partido no tiene id de ESPN")
    return match.espn_event_id  # type: ignore[no-any-return]


@router.get("/matches/{match_id}/stats")
def get_match_stats(match_id: int, db: Session = Depends(get_db)):
    """Estadisticas del partido (posesion, tiros, tarjetas, pases, etc.) desde la fuente."""
    return _fetch_stats(_espn_event_id(match_id, db))


@router.get("/matches/{match_id}/lineups")
def get_match_lineups(match_id: int, db: Session = Depends(get_db)):
    """Alineaciones del partido: titulares, suplentes, formacion y posiciones."""
    return _fetch_lineups(_espn_event_id(match_id, db))


@router.get("/matches/{match_id}/events")
def get_match_events(match_id: int, db: Session = Depends(get_db)):
    """Eventos clave del partido: goles, tarjetas y cambios."""
    return _fetch_events(_espn_event_id(match_id, db))


@router.get("/matches/{match_id}/cards")
def get_match_cards(match_id: int, db: Session = Depends(get_db)):
    """Solo tarjetas (amarillas y rojas) del partido."""
    return _fetch_cards(_espn_event_id(match_id, db))


@router.get("/weeks")
def get_weeks(
    season: str | None = Query(None, description="Etiqueta o ano; por defecto todas las temporadas"),
    db: Session = Depends(get_db),
):
    q = db.query(models.Match.week_number)
    q = _with_season(q, db, season)
    weeks = q.filter(models.Match.week_number.isnot(None)).distinct().order_by(models.Match.week_number).all()
    return [week[0] for week in weeks]


@router.get("/weeks/current")
def get_current_week(
    season: str | None = Query(None, description="Etiqueta o ano; por defecto todas las temporadas"),
    db: Session = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()
    q = db.query(models.Match)
    q = _with_season(q, db, season)
    matches = q.filter(models.Match.match_date.isnot(None)).order_by(models.Match.match_date).all()
    if not matches:
        raise HTTPException(status_code=404, detail="No hay partidos")

    def week_start(date):
        days_since_friday = (date.weekday() - 4) % 7
        return date - timedelta(days=days_since_friday)

    today_week_start = week_start(today)
    for m in matches:
        mdate = m.match_date.date() if hasattr(m.match_date, "date") else m.match_date
        if week_start(mdate) == today_week_start:
            return {"week_number": m.week_number, "start_date": str(today_week_start)}
    first_match = matches[0]
    first_date = first_match.match_date.date() if hasattr(first_match.match_date, "date") else first_match.match_date
    if today < first_date:
        return {"week_number": first_match.week_number, "start_date": str(week_start(first_date)), "note": "Temporada aun no inicia"}
    last_match = matches[-1]
    return {"week_number": last_match.week_number, "note": "Temporada finalizada"}


# ---------- Detalle persistido por partido (desde la BD) ----------


@cached(30)
def _fetch_live(event_id: str):
    return ESPNRequestsScraper().get_match_live(event_id)


@router.get("/matches/{match_id}/timeline", response_model=list[schemas.MatchEventResponse])
def get_match_timeline(match_id: int, db: Session = Depends(get_db)):
    """Linea de tiempo del partido (guardada en BD): goles, tarjetas
    amarillas/rojas y cambios, ordenados por minuto."""
    get_or_404(db, models.Match, match_id)
    return db.query(models.MatchEvent).filter(models.MatchEvent.match_id == match_id).order_by(models.MatchEvent.event_time.is_(None), models.MatchEvent.event_time).all()


@router.get("/matches/{match_id}/squad")
def get_match_squad(match_id: int, db: Session = Depends(get_db)):
    """Alineaciones guardadas del partido: titulares y suplentes por equipo,
    con posicion y dorsal."""
    get_or_404(db, models.Match, match_id)
    rows = db.query(models.MatchLineup).filter(models.MatchLineup.match_id == match_id).all()
    teams: dict[Any, Any] = {}
    for r in rows:
        t = teams.setdefault(
            r.team_id,
            {
                "team_id": r.team_id,
                "team_name": r.team_name,
                "starters": [],
                "substitutes": [],
            },
        )
        entry = {
            "player_id": r.player_id,
            "player_name": r.player_name,
            "position": r.position,
            "jersey_number": r.jersey_number,
        }
        (t["substitutes"] if r.is_substitute else t["starters"]).append(entry)
    return {"match_id": match_id, "teams": list(teams.values())}


@router.get("/matches/{match_id}/player-stats")
def get_match_player_stats_db(match_id: int, db: Session = Depends(get_db)):
    """Estadisticas COMPLETAS por jugador del partido, persistidas en BD (via
    365Scores): minutos, goles, asistencias, xG, xA, remates, pases, regates,
    intercepciones, rating... para TODOS los jugadores, agrupadas por equipo."""
    get_or_404(db, models.Match, match_id)
    rows = db.query(models.PlayerMatchStat).filter(models.PlayerMatchStat.match_id == match_id).all()
    teams: dict[Any, Any] = {}
    for r in rows:
        t = teams.setdefault(r.team_id, {"team_id": r.team_id, "team_name": r.team_name, "players": []})
        t["players"].append(
            {
                "player_id": r.player_id,
                "player_name": r.player_name,
                "starter": bool(r.starter),
                "minutes": r.minutes,
                "goals": r.goals,
                "assists": r.assists,
                "shots": r.shots,
                "xg": r.xg,
                "xa": r.xa,
                "key_passes": r.key_passes,
                "touches": r.touches,
                "passes_completed": r.passes_completed,
                "passes_attempted": r.passes_attempted,
                "interceptions": r.interceptions,
                "rating": r.rating,
                "stats": r.stats,
            }
        )
    for t in teams.values():
        t["players"].sort(key=lambda p: (p["rating"] is None, -(p["rating"] or 0)))
    return {"match_id": match_id, "teams": list(teams.values())}


@router.get("/matches/{match_id}/full")
def get_match_full(match_id: int, db: Session = Depends(get_db)):
    """TODO el detalle de un partido en una sola respuesta: equipos, marcador,
    estado, linea de tiempo (eventos), alineaciones y estadisticas."""
    match = db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team), joinedload(models.Match.stadium)).filter(models.Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Partido no encontrado")

    events = db.query(models.MatchEvent).filter(models.MatchEvent.match_id == match_id).order_by(models.MatchEvent.event_time.is_(None), models.MatchEvent.event_time).all()
    lineup_rows = db.query(models.MatchLineup).filter(models.MatchLineup.match_id == match_id).all()
    lineups: dict[Any, Any] = {}
    for r in lineup_rows:
        t = lineups.setdefault(
            r.team_id,
            {
                "team_id": r.team_id,
                "team_name": r.team_name,
                "starters": [],
                "substitutes": [],
            },
        )
        entry = {"player_id": r.player_id, "player_name": r.player_name, "position": r.position, "jersey_number": r.jersey_number}
        (t["substitutes"] if r.is_substitute else t["starters"]).append(entry)

    stats = []
    if match.espn_event_id:
        stats = db.query(models.MatchStat).filter(models.MatchStat.event_id == match.espn_event_id).all()

    def ev(e):
        return {"minute": e.event_time, "type": e.event_type, "description": e.description, "player": e.player_name, "team_id": e.team_id, "team_name": e.team_name, "is_home": e.is_home}

    def stat(s):
        return {
            "team_id": s.team_id,
            "team_name": s.team_name,
            "possession": s.possession,
            "shots": s.shots,
            "shots_on_target": s.shots_on_target,
            "corners": s.corners,
            "fouls": s.fouls,
            "yellow_cards": s.yellow_cards,
            "red_cards": s.red_cards,
            "offsides": s.offsides,
            "saves": s.saves,
            "passes": s.passes,
            "total_passes": s.total_passes,
            "tackles": s.tackles,
            "interceptions": s.interceptions,
            "blocked_shots": s.blocked_shots,
            "crosses": s.crosses,
            "long_balls": s.long_balls,
        }

    return {
        "id": match.id,
        "status": match.status,
        "match_date": schemas.utc_isoformat(cast(datetime | None, match.match_date)),
        "week_number": match.week_number,
        "referee": match.referee,
        "venue": {
            "id": match.stadium.id,
            "name": match.stadium.name,
            "city": match.stadium.city,
            "capacity": match.stadium.capacity,
        }
        if match.stadium
        else None,
        "home_team": {"id": match.home_team_id, "name": match.home_team.name if match.home_team else None},
        "away_team": {"id": match.away_team_id, "name": match.away_team.name if match.away_team else None},
        "score": {"home": match.home_score, "away": match.away_score},
        "timeline": [ev(e) for e in events],
        "lineups": list(lineups.values()),
        "stats": [stat(s) for s in stats],
        "espn_event_id": match.espn_event_id,
        "external_event_id": match.external_event_id,
    }


@router.get("/matches/{match_id}/live")
def get_match_live(match_id: int, db: Session = Depends(get_db)):
    """Marcador EN VIVO del partido (goles, reloj, periodo y estado), consultado
    en el momento a la fuente. Cacheado 30s para no saturar."""
    match = get_or_404(db, models.Match, match_id)
    if not match.espn_event_id:
        raise HTTPException(status_code=404, detail="Este partido no tiene id de ESPN para datos en vivo")
    live = _fetch_live(match.espn_event_id)
    live["match_id"] = match_id
    return live
