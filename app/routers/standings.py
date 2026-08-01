from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
import math
from typing import Any
from app.database import get_db
from app.dependencies import resolve_season_id, resolve_season_label, latest_season, _apertura_first, find_season
from app import models, schemas

router = APIRouter()

# Mismos factores que el predictor de /predict (modelo de Poisson)
_HOME_ADVANTAGE = 1.20
_AWAY_FACTOR = 0.85
_MAX_GOALS = 8


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam**k) / math.factorial(k)


@router.get("/seasons")
def list_seasons(db: Session = Depends(get_db)):
    """Temporadas (torneos) disponibles en la BD, con su numero de partidos.
    Util para el historico multi-temporada: cada torneo es 'Apertura/Clausura AAAA'."""
    seasons = db.query(models.Season).order_by(models.Season.year.desc(), _apertura_first().desc()).all()
    current = latest_season(db)
    out = []
    for s in seasons:
        matches = db.query(func.count(models.Match.id)).filter(models.Match.season_id == s.id).scalar()
        out.append(
            {
                "id": s.id,
                "name": s.name,
                "year": s.year,
                "tournament": s.tournament_type,
                "matches": matches,
                "is_current": bool(current and current.id == s.id),
            }
        )
    return out


def _season_summary(db, s):
    """Resumen agregado de una temporada para comparar."""
    standings = db.query(models.Standing).options(joinedload(models.Standing.team)).filter(models.Standing.season_id == s.id).order_by(models.Standing.position).all()
    leader = standings[0] if standings else None
    matches = db.query(models.Match).filter(models.Match.season_id == s.id).all()
    finished = [m for m in matches if m.status == "finished" and m.home_score is not None]
    total_goals = sum((m.home_score or 0) + (m.away_score or 0) for m in finished)
    played = len(finished)
    top = db.query(models.TopScorer).filter(models.TopScorer.season == s.name).order_by(models.TopScorer.goals.desc()).first()
    return {
        "season": s.name,
        "year": s.year,
        "tournament": s.tournament_type,
        "teams": len(standings),
        "matches_total": len(matches),
        "matches_played": played,
        "goals_total": total_goals,
        "goals_per_match": round(total_goals / played, 2) if played else 0.0,
        "standings_leader": (
            {
                "team": leader.team.name if leader.team else None,
                "points": leader.points,
                "played": leader.played,
            }
            if leader
            else None
        ),
        "top_scorer": (
            {
                "player": top.player,
                "team": top.team,
                "goals": top.goals,
            }
            if top
            else None
        ),
    }


@router.get("/seasons/compare")
def compare_seasons(
    a: str = Query(..., description="Temporada A ('Apertura 2025' o ano)"),
    b: str = Query(..., description="Temporada B ('Clausura 2026' o ano)"),
    db: Session = Depends(get_db),
):
    """Compara dos temporadas lado a lado: líder de la fase regular, goleador,
    goles totales y promedio por partido, equipos y partidos. Útil para el
    histórico multi-temporada."""
    sa, sb = find_season(db, a), find_season(db, b)
    if sa is None or sb is None:
        missing = a if sa is None else b
        raise HTTPException(status_code=404, detail=f"Temporada no encontrada: {missing}")
    return {"a": _season_summary(db, sa), "b": _season_summary(db, sb)}


@router.get("/standings", response_model=list[schemas.StandingResponse])
def get_standings(season: str = Query(None, description="Etiqueta ('Apertura 2026') o ano; por defecto la vigente"), db: Session = Depends(get_db)):
    season_id = resolve_season_id(db, season)
    q = db.query(models.Standing).options(joinedload(models.Standing.team))
    if season_id is not None:
        q = q.filter(models.Standing.season_id == season_id)
    return q.order_by(models.Standing.position).all()


@router.get("/standings/projection")
def standings_projection(season: str = Query(None), db: Session = Depends(get_db)):
    """Proyeccion de la tabla FINAL: a los puntos actuales de cada equipo se le
    suman los puntos esperados de sus partidos restantes (programados), estimados
    con un modelo de Poisson (fuerzas de ataque/defensa vs media de liga + ventaja
    de local). Util para anticipar quien terminara en zona de Liguilla."""
    season_id = resolve_season_id(db, season)
    label = resolve_season_label(db, season)
    if season_id is None:
        raise HTTPException(status_code=404, detail="No hay temporadas cargadas")

    standings = db.query(models.Standing).options(joinedload(models.Standing.team)).filter(models.Standing.season_id == season_id).all()
    played = [s for s in standings if s.played and s.played > 0]
    if len(played) < 2:
        raise HTTPException(status_code=400, detail="No hay suficientes partidos jugados para proyectar")

    avg_gf = sum(s.goals_for / s.played for s in played) / len(played)
    if avg_gf <= 0:
        raise HTTPException(status_code=400, detail="Datos insuficientes (promedio de goles nulo)")

    by_team = {s.team_id: s for s in standings}

    def atk(s):
        return (s.goals_for / s.played) / avg_gf if s.played else 1.0

    def dfn(s):
        return (s.goals_against / s.played) / avg_gf if s.played else 1.0

    proj = {s.team_id: {"team": s.team, "current_points": s.points, "current_position": s.position, "projected_points": float(s.points), "remaining_matches": 0} for s in standings}

    remaining = db.query(models.Match).filter(models.Match.season_id == season_id, models.Match.status != "finished").all()
    for m in remaining:
        sh, sa = by_team.get(m.home_team_id), by_team.get(m.away_team_id)
        if not sh or not sa or not sh.played or not sa.played:
            continue
        exp_h = max(0.05, atk(sh) * dfn(sa) * avg_gf * _HOME_ADVANTAGE)
        exp_a = max(0.05, atk(sa) * dfn(sh) * avg_gf * _AWAY_FACTOR)
        ph = [_poisson_pmf(i, exp_h) for i in range(_MAX_GOALS + 1)]
        pa = [_poisson_pmf(j, exp_a) for j in range(_MAX_GOALS + 1)]
        hw = dw = aw = 0.0
        for i in range(_MAX_GOALS + 1):
            for j in range(_MAX_GOALS + 1):
                p = ph[i] * pa[j]
                if i > j:
                    hw += p
                elif i == j:
                    dw += p
                else:
                    aw += p
        proj[m.home_team_id]["projected_points"] += 3 * hw + dw
        proj[m.away_team_id]["projected_points"] += 3 * aw + dw
        proj[m.home_team_id]["remaining_matches"] += 1
        proj[m.away_team_id]["remaining_matches"] += 1

    out = []
    for v in proj.values():
        out.append(
            {
                "team_id": v["team"].id if v["team"] else None,
                "team": v["team"].name if v["team"] else None,
                "logo_url": v["team"].logo_url if v["team"] else None,
                "current_points": v["current_points"],
                "current_position": v["current_position"],
                "remaining_matches": v["remaining_matches"],
                "projected_points": round(v["projected_points"], 1),
            }
        )
    out.sort(key=lambda r: r["projected_points"], reverse=True)
    for i, r in enumerate(out):
        r["projected_position"] = i + 1
    return {
        "season": label,
        "model": "Poisson sobre partidos restantes (ataque/defensa vs media de liga + ventaja de local)",
        "projected_standings": out,
    }


@router.get("/top-scorers", response_model=list[schemas.TopScorerResponse])
def get_top_scorers(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), season: str = Query(None)):
    label = resolve_season_label(db, season)
    return db.query(models.TopScorer).filter(models.TopScorer.season == label).order_by(models.TopScorer.goals.desc()).offset(offset).limit(limit).all()


@router.get("/liguilla")
def get_liguilla(season: str = Query(None), db: Session = Depends(get_db)):
    """Foto de la clasificacion segun el formato de Liga MX:
    - Liguilla directa: posiciones 1-6
    - Play-In: posiciones 7-10
    - Eliminados: 11 en adelante
    """
    season_id = resolve_season_id(db, season)
    q = db.query(models.Standing).options(joinedload(models.Standing.team))
    if season_id is not None:
        q = q.filter(models.Standing.season_id == season_id)
    rows = q.order_by(models.Standing.position).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No hay tabla de posiciones todavia")

    def entry(s):
        return {
            "position": s.position,
            "team_id": s.team_id,
            "team": s.team.name if s.team else None,
            "logo_url": s.team.logo_url if s.team else None,
            "played": s.played,
            "points": s.points,
            "goal_difference": s.goal_difference,
        }

    direct, play_in, eliminated = [], [], []
    for s in rows:
        if s.position <= 6:
            direct.append(entry(s))
        elif s.position <= 10:
            play_in.append(entry(s))
        else:
            eliminated.append(entry(s))

    return {
        "format": "Liga MX: 1-6 Liguilla directa, 7-10 Play-In, 11+ eliminados",
        "liguilla_directa": direct,
        "play_in": play_in,
        "eliminados": eliminated,
    }


def _classify_phase(round_name, stage_name):
    """Clasifica un partido en una fase de Liguilla a partir de su ronda/etapa.
    Devuelve (clave, etiqueta, orden) o (None, None, None) si es temporada regular."""
    text = f"{round_name or ''} {stage_name or ''}".lower()
    if not text.strip():
        return None, None, None
    if "semi" in text:
        return "semifinals", "Semifinales", 3
    if "cuartos" in text or "quarter" in text:
        return "quarterfinals", "Cuartos de final", 2
    if "play" in text or "reclasif" in text or "repechaje" in text:
        return "play_in", "Play-In", 1
    if "final" in text:
        return "final", "Final", 4
    return None, None, None


@router.get("/liguilla/results")
def liguilla_results(season: str = Query(None), db: Session = Depends(get_db)):
    """Resultados REALES de la Liguilla por serie (no la siembra teorica): agrupa
    los partidos de fase final ya jugados en series (ida y vuelta), calcula el
    marcador GLOBAL y el ganador de cada llave. Se llena conforme se juega la
    Liguilla; si la temporada aun no llego a fase final, devuelve listas vacias."""
    season_id = resolve_season_id(db, season)
    label = resolve_season_label(db, season)
    if season_id is None:
        raise HTTPException(status_code=404, detail="No hay temporadas cargadas")

    matches = (
        db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team)).filter(models.Match.season_id == season_id, models.Match.status == "finished").all()
    )

    series: dict[Any, Any] = {}
    for m in matches:
        phase, phase_label, order = _classify_phase(m.round_name, m.stage_name)
        if not phase or m.home_team_id is None or m.away_team_id is None:
            continue
        key = (order, frozenset([m.home_team_id, m.away_team_id]))
        s = series.setdefault(
            key,
            {
                "phase": phase,
                "phase_label": phase_label,
                "order": order,
                "aggregate": {},
                "names": {},
                "legs": [],
            },
        )
        s["aggregate"][m.home_team_id] = s["aggregate"].get(m.home_team_id, 0) + (m.home_score or 0)
        s["aggregate"][m.away_team_id] = s["aggregate"].get(m.away_team_id, 0) + (m.away_score or 0)
        s["names"][m.home_team_id] = m.home_team.name if m.home_team else None
        s["names"][m.away_team_id] = m.away_team.name if m.away_team else None
        s["legs"].append(
            {
                "match_id": m.id,
                "date": m.match_date,
                "round_name": m.round_name,
                "home_team_id": m.home_team_id,
                "home_team": m.home_team.name if m.home_team else None,
                "away_team_id": m.away_team_id,
                "away_team": m.away_team.name if m.away_team else None,
                "home_score": m.home_score,
                "away_score": m.away_score,
            }
        )

    out = []
    for (order, _teams), s in series.items():
        tids = list(s["aggregate"].keys())
        winner_id = None
        decided = False
        if len(tids) == 2:
            a, b = tids
            ga, gb = s["aggregate"][a], s["aggregate"][b]
            if ga != gb:
                winner_id = a if ga > gb else b
                decided = True
        out.append(
            {
                "phase": s["phase"],
                "phase_label": s["phase_label"],
                "order": s["order"],
                "teams": [{"team_id": t, "team": s["names"].get(t), "aggregate": s["aggregate"][t]} for t in tids],
                "legs": sorted(s["legs"], key=lambda x: (x["date"] is None, x["date"])),
                "winner_team_id": winner_id,
                "winner": s["names"].get(winner_id) if winner_id else None,
                "decided": decided,
                "note": None if decided else "Serie sin definir o empate global (se resuelve por posicion/penales)",
            }
        )
    out.sort(key=lambda r: r["order"])

    by_phase: dict[str, Any] = {}
    for r in out:
        by_phase.setdefault(r["phase"], []).append(r)

    return {
        "season": label,
        "has_playoff_data": len(out) > 0,
        "series_count": len(out),
        "phases": by_phase,
        "note": ("Datos reales de la Liguilla." if out else "Aun no hay partidos de fase final cargados para esta temporada (la Liguilla se juega al cierre del torneo)."),
    }


@router.get("/liguilla/bracket")
def get_liguilla_bracket(season: str = Query(None), db: Session = Depends(get_db)):
    """Cuadro (bracket) oficial de la Liguilla, sembrado por la posicion final
    de la tabla:
      - Play-In (7º-10º): tres juegos que definen los puestos 7 y 8.
      - Cuartos de final: 1º vs 8º, 2º vs 7º, 3º vs 6º, 4º vs 5º (ida y vuelta).
    Semifinales y final se resiembran por posicion entre los ganadores.
    """
    season_id = resolve_season_id(db, season)
    label = resolve_season_label(db, season)
    q = db.query(models.Standing).options(joinedload(models.Standing.team))
    if season_id is not None:
        q = q.filter(models.Standing.season_id == season_id)
    rows = q.order_by(models.Standing.position).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No hay tabla de posiciones todavia")

    def seed(s):
        return {
            "position": s.position,
            "team_id": s.team_id,
            "team": s.team.name if s.team else None,
            "logo_url": s.team.logo_url if s.team else None,
            "points": s.points,
            "goal_difference": s.goal_difference,
        }

    by_pos = {s.position: seed(s) for s in rows}

    def at(p):
        return by_pos.get(p)

    play_in = {
        "game_1": {"label": "7º vs 8º", "home": at(7), "away": at(8), "reward": "El ganador clasifica como 7º de la Liguilla"},
        "game_2": {"label": "9º vs 10º", "home": at(9), "away": at(10), "reward": "El perdedor queda eliminado"},
        "game_3": {"label": "Perdedor del Juego 1 vs Ganador del Juego 2", "reward": "El ganador clasifica como 8º de la Liguilla"},
    }
    quarterfinals = [
        {"series": "C1", "matchup": "1º vs 8º", "high_seed": at(1), "low_seed": "8º (vía Play-In)"},
        {"series": "C2", "matchup": "2º vs 7º", "high_seed": at(2), "low_seed": "7º (vía Play-In)"},
        {"series": "C3", "matchup": "3º vs 6º", "high_seed": at(3), "low_seed": at(6)},
        {"series": "C4", "matchup": "4º vs 5º", "high_seed": at(4), "low_seed": at(5)},
    ]
    return {
        "season": label,
        "format": "Liga MX: Play-In (7º-10º) + Liguilla a ida y vuelta (cuartos, semifinales y final)",
        "legs": "ida y vuelta",
        "qualified_direct": [at(p) for p in range(1, 7) if at(p)],
        "play_in_teams": [at(p) for p in range(7, 11) if at(p)],
        "play_in": play_in,
        "quarterfinals": quarterfinals,
        "semifinals": {"note": "Se resiembra por posición de tabla entre los 4 ganadores de cuartos (mejor vs peor). Ida y vuelta."},
        "final": {"note": "Ida y vuelta; el mejor sembrado cierra la serie como local."},
        "tiebreaker": "Si la serie termina empatada en el global, avanza el equipo mejor ubicado en la tabla (salvo la final, que se define en cancha).",
    }
