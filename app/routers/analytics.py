"""Analitica: comparador de jugadores/equipos y predictor de partidos.

El predictor usa un modelo de Poisson clasico a partir de la tabla de posiciones
(fuerzas de ataque/defensa relativas a la media de la liga + ventaja de local),
sin dependencias externas ni ML.
"""
import math
import unicodedata
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.database import get_db
from app.dependencies import get_or_404, resolve_season_label, resolve_season_id
from app import models

router = APIRouter()

HOME_ADVANTAGE = 1.20   # los locales anotan ~20% mas
AWAY_FACTOR = 0.85      # los visitantes anotan ~15% menos
MAX_GOALS = 8           # rejilla de Poisson


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


# ---------- Comparador de jugadores ----------
def _player_season_agg(db: Session, player, label: str) -> dict:
    M = models.PlayerMatchStat
    rows = []
    # Preferimos cruce por id exacto (mapa de identidad); si no, por nombre.
    if getattr(player, "external_365_id", None) is not None:
        rows = db.query(M).filter(M.season == label, M.player_id == player.external_365_id).all()
    if not rows:
        nq = _norm(player.name)
        rows = [r for r in db.query(M).filter(M.season == label).all() if _norm(r.player_name or "") == nq]  # type: ignore[arg-type]
    ratings = [r.rating for r in rows if r.rating is not None]

    def s(attr):
        return sum(getattr(r, attr) or 0 for r in rows)

    return {
        "player_id": player.id, "name": player.name, "team_id": player.team_id,
        "appearances": len(rows), "minutes": s("minutes"), "goals": s("goals"),
        "assists": s("assists"), "shots": s("shots"),
        "xg": round(s("xg"), 2), "xa": round(s("xa"), 2),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
    }


@router.get("/compare/players")
def compare_players(a: int = Query(...), b: int = Query(...), season: str = Query(None), db: Session = Depends(get_db)):
    """Compara dos jugadores lado a lado por sus stats agregadas de la temporada."""
    pa = get_or_404(db, models.Player, a)
    pb = get_or_404(db, models.Player, b)
    label = resolve_season_label(db, season)
    return {"season": label, "a": _player_season_agg(db, pa, label), "b": _player_season_agg(db, pb, label)}


# ---------- Comparador de equipos ----------
def _team_card(db: Session, team, season_id, label: str) -> dict:
    st = None
    if season_id is not None:
        st = db.query(models.Standing).filter(models.Standing.season_id == season_id,
                                               models.Standing.team_id == team.id).first()
    M = models.PlayerMatchStat
    xg = db.query(func.sum(M.xg)).filter(M.team_id == team.id, M.season == label).scalar()
    goals = db.query(func.sum(M.goals)).filter(M.team_id == team.id, M.season == label).scalar()
    standing = None
    if st:
        standing = {
            "position": st.position, "points": st.points, "played": st.played,
            "won": st.won, "drawn": st.drawn, "lost": st.lost,
            "goals_for": st.goals_for, "goals_against": st.goals_against,
            "goal_difference": st.goal_difference,
        }
    return {
        "team_id": team.id, "name": team.name, "logo_url": team.logo_url,
        "standing": standing, "xg": round(float(xg or 0), 2), "goals": int(goals or 0),
    }


@router.get("/compare/teams")
def compare_teams(a: int = Query(...), b: int = Query(...), season: str = Query(None), db: Session = Depends(get_db)):
    """Compara dos equipos lado a lado: posicion, puntos, registro, goles y xG."""
    ta = get_or_404(db, models.Team, a)
    tb = get_or_404(db, models.Team, b)
    season_id = resolve_season_id(db, season)
    label = resolve_season_label(db, season)
    return {"season": label, "a": _team_card(db, ta, season_id, label), "b": _team_card(db, tb, season_id, label)}


# ---------- Predictor de partidos (Poisson) ----------
def _poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


@router.get("/predict")
def predict_match(home: int = Query(..., description="team_id local"),
                  away: int = Query(..., description="team_id visitante"),
                  season: str = Query(None), db: Session = Depends(get_db)):
    """Predice un partido entre dos equipos con un modelo de Poisson a partir de
    la tabla: fuerza de ataque/defensa relativa a la media de la liga + ventaja de
    local. Devuelve goles esperados, probabilidades (1/X/2) y marcador mas probable."""
    th = get_or_404(db, models.Team, home)
    ta = get_or_404(db, models.Team, away)
    season_id = resolve_season_id(db, season)
    standings = db.query(models.Standing).filter(models.Standing.season_id == season_id).all() if season_id is not None else []
    played = [s for s in standings if s.played and s.played > 0]
    if len(played) < 2:
        raise HTTPException(status_code=400, detail="No hay suficientes partidos jugados en la temporada para predecir")

    by_team = {s.team_id: s for s in played}
    sh, sa = by_team.get(home), by_team.get(away)  # type: ignore[call-overload]
    if not sh or not sa:
        raise HTTPException(status_code=400, detail="Alguno de los equipos no tiene partidos jugados en esta temporada")

    avg_gf = sum(s.goals_for / s.played for s in played) / len(played)
    if avg_gf <= 0:
        raise HTTPException(status_code=400, detail="Datos insuficientes (promedio de goles nulo)")

    atk_h = (sh.goals_for / sh.played) / avg_gf
    def_h = (sh.goals_against / sh.played) / avg_gf
    atk_a = (sa.goals_for / sa.played) / avg_gf
    def_a = (sa.goals_against / sa.played) / avg_gf

    exp_h = max(0.05, atk_h * def_a * avg_gf * HOME_ADVANTAGE)
    exp_a = max(0.05, atk_a * def_h * avg_gf * AWAY_FACTOR)

    ph = [_poisson(i, exp_h) for i in range(MAX_GOALS + 1)]
    pa = [_poisson(j, exp_a) for j in range(MAX_GOALS + 1)]

    home_win = draw = away_win = 0.0
    best_score, best_p = (0, 0), 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = ph[i] * pa[j]
            if i > j:
                home_win += p
            elif i == j:
                draw += p
            else:
                away_win += p
            if p > best_p:
                best_p, best_score = p, (i, j)

    total = home_win + draw + away_win or 1.0
    return {
        "season": resolve_season_label(db, season),
        "home_team": {"id": home, "name": th.name},
        "away_team": {"id": away, "name": ta.name},
        "expected_goals": {"home": round(exp_h, 2), "away": round(exp_a, 2)},
        "probabilities": {
            "home_win": round(home_win / total, 3),
            "draw": round(draw / total, 3),
            "away_win": round(away_win / total, 3),
        },
        "most_likely_score": {"home": best_score[0], "away": best_score[1],
                              "probability": round(best_p, 3)},
        "model": "Poisson (fuerzas de ataque/defensa vs media de liga + ventaja de local)",
    }



# ---------- Power ranking ----------
@router.get("/power-ranking")
def power_ranking(season: str = Query(None), db: Session = Depends(get_db)):
    """Ranking de poder de los equipos: combina puntos por partido (70%) y
    diferencia de goles por partido (30%) en un rating 0-100. El xG se incluye
    como dato informativo del rendimiento subyacente."""
    season_id = resolve_season_id(db, season)
    label = resolve_season_label(db, season)
    standings = []
    if season_id is not None:
        standings = (db.query(models.Standing).options(joinedload(models.Standing.team))
                     .filter(models.Standing.season_id == season_id).all())

    M = models.PlayerMatchStat
    xg_rows = (db.query(M.team_id, func.sum(M.xg)).filter(M.season == label)
               .group_by(M.team_id).all())
    xg_by_team = {tid: float(x or 0) for tid, x in xg_rows}

    out = []
    for s in standings:
        played = s.played or 0
        ppg = s.points / played if played else 0.0
        gdpg = s.goal_difference / played if played else 0.0
        rating = round(min(100, max(0, (ppg / 3) * 70 + ((gdpg + 3) / 6) * 30)), 1)  # type: ignore[type-var]
        out.append({
            "team": {"id": s.team_id, "name": s.team.name if s.team else None,
                     "logo_url": s.team.logo_url if s.team else None},
            "rating": rating,
            "played": played,
            "ppg": round(ppg, 2),
            "gd_per_game": round(gdpg, 2),
            "xg": round(xg_by_team.get(s.team_id, 0.0), 2),
            "table_position": s.position,
        })
    out.sort(key=lambda r: r["rating"], reverse=True)  # type: ignore[arg-type, return-value]
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return {
        "season": label,
        "formula": "70% puntos/partido + 30% diferencia de goles/partido (escala 0-100); xG informativo",
        "ranking": out,
    }



# ---------- Jugadores a seguir en un partido ----------
def _team_standouts(db: Session, team_id: int, label: str, limit: int):
    """Jugadores destacados de un equipo en la temporada, con un 'watch score'
    y un motivo explicable. Sale de player_match_stats."""
    M = models.PlayerMatchStat
    rows = (
        db.query(M.player_name, func.count(M.id), func.sum(M.goals), func.sum(M.assists),
                 func.sum(M.xg), func.sum(M.xa), func.avg(M.rating))
        .filter(M.team_id == team_id, M.season == label)
        .group_by(M.player_name)
        .all()
    )
    players = []
    for name, apps, g, a, xg, xa, avg_r in rows:
        g = int(g or 0)
        a = int(a or 0)
        xg = float(xg or 0)
        xa = float(xa or 0)
        r = float(avg_r or 0)
        score = g * 4 + a * 2.5 + xg * 1.5 + xa + r * 3

        if g >= 2 and g >= a:
            reason = f"{g} goles en la temporada"
        elif a >= 2:
            reason = f"{a} asistencias en la temporada"
        elif r >= 7.2:
            reason = "en gran forma (rating alto)"
        elif xg >= 1.5:
            reason = "genera mucho peligro (xG alto)"
        elif g >= 1:
            reason = f"{g} gol(es) anotado(s)"
        else:
            reason = "jugador habitual del equipo"

        players.append({
            "player": name, "watch_score": round(score, 1), "reason": reason,
            "appearances": apps, "goals": g, "assists": a,
            "xg": round(xg, 2), "xa": round(xa, 2),
            "avg_rating": round(r, 2) if r else None,
        })
    players.sort(key=lambda p: p["watch_score"], reverse=True)
    return players[:limit]


@router.get("/matches/{match_id}/players-to-watch")
def players_to_watch(match_id: int, limit: int = Query(3, ge=1, le=6),
                     season: str = Query(None), db: Session = Depends(get_db)):
    """Jugadores a seguir en un partido: los más destacados de cada equipo según
    su forma de la temporada (goles, asistencias, xG, xA y rating)."""
    match = get_or_404(db, models.Match, match_id)
    if season:
        label = resolve_season_label(db, season)
    else:
        mseason = db.get(models.Season, match.season_id) if match.season_id else None
        label = mseason.name if mseason else resolve_season_label(db, None)

    home = _team_standouts(db, match.home_team_id, label, limit)
    away = _team_standouts(db, match.away_team_id, label, limit)
    result = {
        "match_id": match_id,
        "season": label,
        "home_team": {
            "id": match.home_team_id,
            "name": match.home_team.name if match.home_team else None,
            "players": home,
        },
        "away_team": {
            "id": match.away_team_id,
            "name": match.away_team.name if match.away_team else None,
            "players": away,
        },
    }
    if not home and not away:
        result["note"] = "Sin datos suficientes todavía (la temporada no tiene partidos jugados); se llena conforme se juegue."
    return result
