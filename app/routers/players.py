from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import datetime
import unicodedata
from app.database import get_db
from app.dependencies import get_or_404, resolve_season_label, resolve_season_id, discipline_summary, SUSPENSION_YELLOWS
from app import models, schemas
from typing import Any, Optional

router = APIRouter()


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


def _age_from_birthdate(birth_date):
    """Edad en anios a partir de un ISO date string (ej '1997-03-02T08:00Z')."""
    if not birth_date:
        return None
    from datetime import date, datetime as _dt
    try:
        d = _dt.fromisoformat(str(birth_date).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        try:
            d = _dt.strptime(str(birth_date)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


# Vocabulario de `status` observado en las fuentes (ESPN / 365Scores).
_STATUS_JUGADO = {"finished", "final", "ft", "fulltime", "full-time", "completed", "post", "ended"}
_STATUS_NO_JUGADO = {"scheduled", "pre", "upcoming", "postponed", "canceled", "cancelled", "suspended", "tbd"}


def _is_played(status: Any, match_date: Any, now: Any) -> bool:
    """Decide si un partido ya se disputo.

    NO se puede usar `home_score IS NOT NULL`: los partidos aun no jugados se
    guardan con marcador 0-0, no nulo. Se usa `status` y, si el valor no se
    reconoce, se cae a comparar la fecha contra el momento actual.
    """
    s = str(status or "").strip().lower()
    if s in _STATUS_JUGADO:
        return True
    if s in _STATUS_NO_JUGADO:
        return False
    if match_date is None:
        return False
    md = match_date
    if getattr(md, "tzinfo", None) is not None:
        md = md.replace(tzinfo=None)
    return bool(md < now)


def _last_played_match_by_team(db, season_id) -> dict[Any, Any]:
    """Mapa team_id -> id del ultimo partido YA JUGADO de la temporada.

    Sirve para saber si una expulsion ocurrio en la jornada mas reciente y, por
    tanto, si el castigo sigue vigente para el proximo partido.
    """
    last_by_team: dict[Any, Any] = {}
    if season_id is None:
        return last_by_team
    M = models.Match
    now = datetime.utcnow()
    rows = (
        db.query(M.id, M.home_team_id, M.away_team_id, M.status, M.match_date)
        .filter(M.season_id == season_id)
        .order_by(M.match_date.asc())
        .all()
    )
    for match_id, home_id, away_id, status, match_date in rows:
        if not _is_played(status, match_date, now):
            continue
        for team_id in (home_id, away_id):
            if team_id is not None:
                last_by_team[team_id] = match_id
    return last_by_team


def _suspension_state(yellow, red_in_last_match, threshold=SUSPENSION_YELLOWS) -> tuple[bool, Optional[str]]:
    """Determina si el jugador esta inhabilitado para el proximo partido.

    Dos causas independientes:
    - Roja en el ultimo partido disputado por su equipo.
    - Ciclo de amarillas completado (5, 10, 15...): al llegar al multiplo el
      castigo se cumple en la jornada siguiente.
    """
    y = int(yellow or 0)
    ciclo_completo = y > 0 and y % threshold == 0
    suspended = bool(red_in_last_match) or ciclo_completo
    reason: Optional[str]
    if red_in_last_match:
        reason = "roja"
    elif ciclo_completo:
        reason = "acumulacion de amarillas"
    else:
        reason = None
    return suspended, reason


# Metricas agregables para la tabla de lideres de temporada (desde player_match_stats)
_LEADER_AGG = {
    "goals": (func.sum(models.PlayerMatchStat.goals), "desc"),
    "assists": (func.sum(models.PlayerMatchStat.assists), "desc"),
    "minutes": (func.sum(models.PlayerMatchStat.minutes), "desc"),
    "shots": (func.sum(models.PlayerMatchStat.shots), "desc"),
    "xg": (func.sum(models.PlayerMatchStat.xg), "desc"),
    "xa": (func.sum(models.PlayerMatchStat.xa), "desc"),
    "key_passes": (func.sum(models.PlayerMatchStat.key_passes), "desc"),
    "interceptions": (func.sum(models.PlayerMatchStat.interceptions), "desc"),
    "touches": (func.sum(models.PlayerMatchStat.touches), "desc"),
    "rating": (func.avg(models.PlayerMatchStat.rating), "desc"),
}


@router.get("/players/top", response_model=list[schemas.PlayerStatResponse])
def get_top_players(limit: int = Query(10, ge=1, le=100), season: str = Query(None), db: Session = Depends(get_db)):
    label = resolve_season_label(db, season)
    return db.query(models.PlayerStat).filter(models.PlayerStat.season == label).order_by(models.PlayerStat.goals.desc()).limit(limit).all()


@router.get("/players/season-leaders")
def season_leaders(
    stat: str = Query("goals", description="goals|assists|minutes|shots|xg|xa|key_passes|interceptions|touches|rating"),
    season: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
    min_appearances: int = Query(1, ge=0, description="filtra jugadores con pocas apariciones (util para 'rating')"),
    db: Session = Depends(get_db),
):
    """Tabla de lideres de temporada calculada desde las stats por partido
    persistidas (player_match_stats): goleadores, asistentes, minutos, xG, xA,
    rating promedio, etc. A diferencia de /365scores/leaders, sale de la BD."""
    label = resolve_season_label(db, season)
    expr, _ = _LEADER_AGG.get(stat, _LEADER_AGG["goals"])
    M = models.PlayerMatchStat
    rows = (
        db.query(M.player_name, M.team_name, func.count(M.id).label("apps"), expr.label("value"))  # type: ignore[attr-defined]
        .filter(M.season == label)
        .group_by(M.player_name, M.team_name)
        .having(func.count(M.id) >= min_appearances)
        .order_by(expr.desc())  # type: ignore[attr-defined]
        .limit(limit)
        .all()
    )
    out = []
    for i, (name, team, apps, value) in enumerate(rows):
        if value is None:
            continue
        out.append({
            "rank": i + 1, "player": name, "team": team, "appearances": apps,
            "stat": stat, "value": round(float(value), 2) if stat in ("xg", "xa", "rating") else int(value),
        })
    return out


@router.get("/players/xg-performance")
def xg_performance(
    season: str = Query(None),
    order: str = Query("over", description="'over' = más goles que su xG primero; 'under' = al revés"),
    limit: int = Query(20, ge=1, le=100),
    min_appearances: int = Query(1, ge=0),
    db: Session = Depends(get_db),
):
    """Rendimiento goles vs xG de la temporada (desde player_match_stats): quién
    finaliza por encima de lo esperado (clínicos) y quién por debajo. diff = goles − xG."""
    label = resolve_season_label(db, season)
    M = models.PlayerMatchStat
    rows = (
        db.query(M.player_name, M.team_name, func.count(M.id).label("apps"),
                 func.sum(M.goals).label("goals"), func.sum(M.xg).label("xg"))
        .filter(M.season == label)
        .group_by(M.player_name, M.team_name)
        .having(func.count(M.id) >= min_appearances)
        .all()
    )
    out = []
    for name, team, apps, goals, xg in rows:
        g = int(goals or 0)
        x = round(float(xg or 0), 2)
        out.append({
            "player": name, "team": team, "appearances": apps,
            "goals": g, "xg": x, "diff": round(g - x, 2),
        })
    out.sort(key=lambda r: r["diff"], reverse=(order != "under"))
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out[:limit]


@router.get("/players/discipline")
def players_discipline(
    season: str = Query(None),
    order: str = Query("discipline_points", description="discipline_points|yellow_cards|red_cards"),
    limit: int = Query(20, ge=1, le=100),
    at_risk: bool = Query(False, description="solo jugadores a una amarilla de suspension"),
    unavailable: bool = Query(False, description="solo jugadores suspendidos para el proximo partido"),
    db: Session = Depends(get_db),
):
    """Tabla de disciplina por jugador de la temporada: tarjetas amarillas y rojas
    acumuladas (desde los eventos de cada partido).

    Distingue dos estados que antes se confundian:
    - `suspension_risk`: aviso preventivo, esta a UNA amarilla de suspenderse.
    - `suspended_next_match`: NO puede jugar la proxima jornada, ya sea por roja
      en el ultimo partido de su equipo o por haber completado el ciclo de
      amarillas (regla Liga MX: 5 amarillas = 1 partido).
    """
    label = resolve_season_label(db, season)
    season_id = resolve_season_id(db, season)
    if season_id is None:
        return {"season": label, "count": 0, "players": []}
    E, M = models.MatchEvent, models.Match
    rows = (
        db.query(
            E.player_name, E.team_id, E.team_name,
            func.sum(case((E.event_type == "yellow_card", 1), else_=0)).label("yellow"),
            func.sum(case((E.event_type == "red_card", 1), else_=0)).label("red"),
            func.max(case((E.event_type == "red_card", M.id), else_=None)).label("last_red_match_id"),
        )
        .join(M, E.match_id == M.id)
        .filter(M.season_id == season_id)
        .filter(E.event_type.in_(["yellow_card", "red_card"]))
        .filter(E.player_name.isnot(None))
        .group_by(E.player_name, E.team_id, E.team_name)
        .all()
    )
    last_by_team = _last_played_match_by_team(db, season_id)
    out = []
    for name, team_id, team_name, yellow, red, last_red_match_id in rows:
        d: dict[str, Any] = dict(discipline_summary(yellow, red))
        red_in_last_match = (
            last_red_match_id is not None
            and last_red_match_id == last_by_team.get(team_id)
        )
        suspended, reason = _suspension_state(yellow, red_in_last_match)
        d["suspended_next_match"] = suspended
        d["suspension_reason"] = reason
        d.update({"player": name, "team_id": team_id, "team": team_name})
        out.append(d)
    if at_risk:
        out = [r for r in out if r["suspension_risk"]]
    if unavailable:
        out = [r for r in out if r["suspended_next_match"]]
    key = order if order in ("yellow_cards", "red_cards", "discipline_points") else "discipline_points"
    out.sort(key=lambda r: r[key], reverse=True)
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return {"season": label, "count": len(out), "players": out[:limit]}


_CARD_METRICS = {"yellow_cards", "red_cards"}


@router.get("/players/leaderboard")
def players_leaderboard(
    metric: str = Query("goals", description=(
        "Rendimiento: goals|assists|minutes|shots|xg|xa|key_passes|interceptions|touches|rating. "
        "Disciplina: yellow_cards|red_cards")),
    season: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
    order: str = Query("desc", description="desc|asc"),
    min_appearances: int = Query(1, ge=0, description="apariciones minimas (solo metricas de rendimiento)"),
    db: Session = Depends(get_db),
):
    """Tabla de lideres UNIFICADA: una sola entrada para cualquier metrica, ya sea
    de rendimiento (goles, asistencias, minutos, xG, xA, rating...) o de disciplina
    (tarjetas). Consolida /players/season-leaders y /players/discipline en un unico
    endpoint configurable."""
    label = resolve_season_label(db, season)
    reverse = order != "asc"
    out = []
    if metric in _CARD_METRICS:
        season_id = resolve_season_id(db, season)
        if season_id is not None:
            E, M = models.MatchEvent, models.Match
            etype = "yellow_card" if metric == "yellow_cards" else "red_card"
            rows = (
                db.query(E.player_name, E.team_name, func.count(E.id).label("value"))
                .join(M, E.match_id == M.id)
                .filter(M.season_id == season_id, E.event_type == etype)
                .filter(E.player_name.isnot(None))
                .group_by(E.player_name, E.team_name)
                .all()
            )
            out = [{"player": n, "team": t, "value": int(v)} for n, t, v in rows]
    else:
        if metric not in _LEADER_AGG:
            metric = "goals"
        expr, _ = _LEADER_AGG[metric]
        M = models.PlayerMatchStat
        rows = (
            db.query(M.player_name, M.team_name, func.count(M.id).label("apps"), expr.label("value"))  # type: ignore[assignment, attr-defined]
            .filter(M.season == label)
            .group_by(M.player_name, M.team_name)
            .having(func.count(M.id) >= min_appearances)
            .all()
        )
        for name, team, apps, value in rows:
            if value is None:
                continue
            v = round(float(value), 2) if metric in ("xg", "xa", "rating") else int(value)
            out.append({"player": name, "team": team, "appearances": apps, "value": v})
    out.sort(key=lambda r: r["value"], reverse=reverse)
    out = out[:limit]
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return {"season": label, "metric": metric, "order": order, "count": len(out), "players": out}


@router.get("/players/identity-map")
def players_identity_map(db: Session = Depends(get_db)):
    """Diagnostico del cruce de identidad ESPN<->365Scores: cuantos jugadores
    tienen mapeado su id de 365Scores (external_365_id) y cuantas filas de stats
    quedan sin asociar a una ficha. Util para verificar la calidad del cruce."""
    total = db.query(models.Player).count()
    mapped = db.query(models.Player).filter(models.Player.external_365_id.isnot(None)).count()
    # ids de 365 que aparecen en stats pero no estan mapeados a ningun jugador
    mapped_ids = {pid for (pid,) in db.query(models.Player.external_365_id)
                  .filter(models.Player.external_365_id.isnot(None)).all()}
    src_ids = {pid for (pid,) in db.query(models.PlayerMatchStat.player_id).distinct().all()
               if pid is not None}
    unmapped_sources = sorted(src_ids - mapped_ids)
    sample = (db.query(models.Player)
              .filter(models.Player.external_365_id.isnot(None))
              .limit(20).all())
    return {
        "players_total": total,
        "players_mapped": mapped,
        "players_unmapped": total - mapped,
        "coverage_pct": round(mapped / total * 100, 1) if total else 0.0,
        "stats_source_ids": len(src_ids),
        "stats_source_ids_unmapped": len(unmapped_sources),
        "sample": [
            {"player_id": p.id, "name": p.name, "team_id": p.team_id,
             "external_365_id": p.external_365_id} for p in sample
        ],
    }


@router.get("/players/search", response_model=list[schemas.PlayerResponse])
def search_players(
    q: str = Query(None, description="Texto a buscar en el nombre (ignora acentos)"),
    position: str = Query(None),
    nationality: str = Query(None),
    team_id: int = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Busqueda y filtrado de jugadores por nombre, posicion, nacionalidad y equipo."""
    query = db.query(models.Player)
    if team_id:
        query = query.filter(models.Player.team_id == team_id)
    if position:
        query = query.filter(models.Player.position == position)
    candidates = query.all()
    nq = _norm(q) if q else None
    nnat = _norm(nationality) if nationality else None
    out = []
    for p in candidates:
        if nq and nq not in _norm(p.name):  # type: ignore[arg-type]
            continue
        if nnat and nnat not in _norm(p.nationality or ""):  # type: ignore[arg-type]
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


@router.get("/players", response_model=list[schemas.PlayerResponse])
def get_players(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return db.query(models.Player).offset(offset).limit(limit).all()

@router.get("/players/{player_id}", response_model=schemas.PlayerResponse)
def get_player(player_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, models.Player, player_id)

@router.get("/players/{player_id}/stats", response_model=schemas.PlayerStatResponse)
def get_player_stat(player_id: int, db: Session = Depends(get_db), season: str = Query(None)):
    label = resolve_season_label(db, season)
    stat = db.query(models.PlayerStat).filter(models.PlayerStat.player_id == player_id, models.PlayerStat.season == label).first()
    if not stat:
        raise HTTPException(status_code=404, detail="Estadisticas no encontradas")
    return stat


def _player_match_rows(db, player, season: Optional[str] = None):
    """Filas de player_match_stats de un jugador en una temporada.

    Estrategia robusta: si el jugador tiene `external_365_id` (mapa de identidad),
    se emparejan por id EXACTO. Si no hay mapeo o no devuelve filas, se cae al
    emparejado por nombre normalizado (retrocompatibilidad). Acepta tambien un
    string (nombre) para usos que solo tengan el nombre."""
    q = db.query(models.PlayerMatchStat)
    if season:
        q = q.filter(models.PlayerMatchStat.season == season)

    # Permite pasar el objeto Player (preferido) o solo el nombre.
    ext_id = getattr(player, "external_365_id", None)
    name = getattr(player, "name", player)

    if ext_id is not None:
        rows = q.filter(models.PlayerMatchStat.player_id == ext_id).all()
        if rows:
            return rows
    nq = _norm(name)
    return [r for r in q.all() if _norm(r.player_name or "") == nq]


@router.get("/players/{player_id}/match-stats", response_model=list[schemas.PlayerMatchStatResponse])
def get_player_match_stats(player_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Historial partido a partido del jugador con sus estadisticas completas."""
    player = get_or_404(db, models.Player, player_id)
    rows = _player_match_rows(db, player, season)
    return sorted(rows, key=lambda r: (r.match_id is None, r.match_id))


@router.get("/players/{player_id}/season-stats")
def get_player_season_stats(player_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Resumen agregado de la temporada del jugador (suma de partidos jugados):
    minutos, goles, asistencias, remates, xG, xA y rating promedio."""
    player = get_or_404(db, models.Player, player_id)
    label = resolve_season_label(db, season)
    rows = _player_match_rows(db, player, label)
    if not rows:
        raise HTTPException(status_code=404, detail="Sin estadisticas por partido para esta temporada")

    def _sum(attr):
        return sum(getattr(r, attr) or 0 for r in rows)

    ratings = [r.rating for r in rows if r.rating is not None]
    return {
        "player_id": player_id,
        "player_name": player.name,
        "team_id": player.team_id,
        "season": label,
        "appearances": len(rows),
        "starts": sum(1 for r in rows if r.starter),
        "minutes": _sum("minutes"),
        "goals": _sum("goals"),
        "assists": _sum("assists"),
        "shots": _sum("shots"),
        "key_passes": _sum("key_passes"),
        "interceptions": _sum("interceptions"),
        "touches": _sum("touches"),
        "xg": round(_sum("xg"), 2),
        "xa": round(_sum("xa"), 2),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
    }



@router.get("/players/{player_id}/discipline")
def get_player_discipline(player_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Tarjetas acumuladas de un jugador en la temporada y su estado de suspension.

    Incluye `suspended_next_match` y `suspension_reason`: una roja inhabilita el
    partido siguiente y completar el ciclo de amarillas (regla Liga MX: 5
    amarillas = 1 partido) tambien."""
    player = get_or_404(db, models.Player, player_id)
    label = resolve_season_label(db, season)
    season_id = resolve_season_id(db, season)
    nq = _norm(player.name)
    yellow = red = 0
    last_red_match_id: Any = None
    if season_id is not None:
        E, M = models.MatchEvent, models.Match
        events = (
            db.query(E.event_type, E.player_name, E.match_id)
            .join(M, E.match_id == M.id)
            .filter(M.season_id == season_id)
            .filter(E.event_type.in_(["yellow_card", "red_card"]))
            .filter(E.player_name.isnot(None))
            .all()
        )
        for etype, pname, match_id in events:
            if _norm(pname or "") != nq:
                continue
            if etype == "yellow_card":
                yellow += 1
            elif etype == "red_card":
                red += 1
                if match_id is not None and (last_red_match_id is None or match_id > last_red_match_id):
                    last_red_match_id = match_id
    d: dict[str, Any] = dict(discipline_summary(yellow, red))
    last_by_team = _last_played_match_by_team(db, season_id)
    red_in_last_match = (
        last_red_match_id is not None
        and last_red_match_id == last_by_team.get(player.team_id)
    )
    suspended, reason = _suspension_state(yellow, red_in_last_match)
    d["suspended_next_match"] = suspended
    d["suspension_reason"] = reason
    d.update({"player_id": player_id, "player": player.name,
              "team_id": player.team_id, "season": label})
    return d


@router.get("/players/{player_id}/form")
def get_player_form(player_id: int, last: int = Query(5, ge=1, le=20),
                    season: str = Query(None), db: Session = Depends(get_db)):
    """Forma reciente del jugador: ultimos N partidos con rating/goles/asistencias,
    rating promedio reciente, totales y racha de goleo (partidos seguidos marcando)."""
    player = get_or_404(db, models.Player, player_id)
    label = resolve_season_label(db, season)
    rows = _player_match_rows(db, player, label)
    recent = sorted(rows, key=lambda r: (r.match_id is None, r.match_id), reverse=True)[:last]
    ratings = [r.rating for r in recent if r.rating is not None]
    scoring_streak = 0
    for r in recent:
        if (r.goals or 0) > 0:
            scoring_streak += 1
        else:
            break
    return {
        "player_id": player_id,
        "player": player.name,
        "season": label,
        "matches_considered": len(recent),
        "goals": sum(r.goals or 0 for r in recent),
        "assists": sum(r.assists or 0 for r in recent),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "scoring_streak": scoring_streak,
        "matches": [
            {"match_id": r.match_id, "minutes": r.minutes, "goals": r.goals,
             "assists": r.assists, "rating": r.rating} for r in recent
        ],
    }


@router.get("/players/{player_id}/profile")
def get_player_profile(player_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Perfil completo del jugador en una llamada: ficha, agregado de la temporada
    y sus ultimos 5 partidos."""
    player = get_or_404(db, models.Player, player_id)
    label = resolve_season_label(db, season)
    rows = _player_match_rows(db, player, label)

    def _sum(attr):
        return sum(getattr(r, attr) or 0 for r in rows)

    ratings = [r.rating for r in rows if r.rating is not None]
    team = db.query(models.Team).filter(models.Team.id == player.team_id).first()
    recent = sorted(rows, key=lambda r: (r.match_id is None, r.match_id), reverse=True)[:5]
    return {
        "player": {
            "id": player.id, "name": player.name, "position": player.position,
            "number": player.number, "nationality": player.nationality,
            "flag_url": player.flag_url, "photo_url": player.photo_url,
            "birth_date": player.birth_date, "age": _age_from_birthdate(player.birth_date),
            "height": player.height, "weight": player.weight,
            "team": {"id": team.id, "name": team.name, "logo_url": team.logo_url} if team else None,
        },
        "season": label,
        "season_stats": {
            "appearances": len(rows), "minutes": _sum("minutes"), "goals": _sum("goals"),
            "assists": _sum("assists"), "shots": _sum("shots"),
            "xg": round(_sum("xg"), 2), "xa": round(_sum("xa"), 2),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        },
        "recent_matches": [
            {"match_id": r.match_id, "minutes": r.minutes, "goals": r.goals,
             "assists": r.assists, "rating": r.rating} for r in recent
        ],
    }
