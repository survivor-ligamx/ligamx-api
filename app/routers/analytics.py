"""Analitica: comparador de jugadores/equipos y predictor de partidos.

El predictor usa un modelo de Poisson clasico a partir de la tabla de posiciones
(fuerzas de ataque/defensa relativas a la media de la liga + ventaja de local),
sin dependencias externas ni ML.
"""

import math
import unicodedata

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app import models
from app.database import get_db
from app.dependencies import get_or_404, resolve_season_id, resolve_season_label

router = APIRouter()

HOME_ADVANTAGE = 1.20  # los locales anotan ~20% mas
AWAY_FACTOR = 0.85  # los visitantes anotan ~15% menos
MAX_GOALS = 8  # rejilla de Poisson
DEFAULT_PRIOR_STRENGTH = 5.0
MIN_SPLIT_SAMPLE = 3
DEFAULT_DC_RHO = -0.08


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
        "player_id": player.id,
        "name": player.name,
        "team_id": player.team_id,
        "appearances": len(rows),
        "minutes": s("minutes"),
        "goals": s("goals"),
        "assists": s("assists"),
        "shots": s("shots"),
        "xg": round(s("xg"), 2),
        "xa": round(s("xa"), 2),
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
        st = db.query(models.Standing).filter(models.Standing.season_id == season_id, models.Standing.team_id == team.id).first()
    M = models.PlayerMatchStat
    xg = db.query(func.sum(M.xg)).filter(M.team_id == team.id, M.season == label).scalar()
    goals = db.query(func.sum(M.goals)).filter(M.team_id == team.id, M.season == label).scalar()
    standing = None
    if st:
        standing = {
            "position": st.position,
            "points": st.points,
            "played": st.played,
            "won": st.won,
            "drawn": st.drawn,
            "lost": st.lost,
            "goals_for": st.goals_for,
            "goals_against": st.goals_against,
            "goal_difference": st.goal_difference,
        }
    return {
        "team_id": team.id,
        "name": team.name,
        "logo_url": team.logo_url,
        "standing": standing,
        "xg": round(float(xg or 0), 2),
        "goals": int(goals or 0),
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
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _regularize_rate(obs_rate: float, sample: int, prior_mean: float, prior_strength: float) -> float:
    den = float(sample) + float(prior_strength)
    if den <= 0:
        return prior_mean
    return ((obs_rate * sample) + (prior_mean * prior_strength)) / den


def _dixon_coles_tau(i: int, j: int, lam_home: float, lam_away: float, rho: float) -> float:
    if i == 0 and j == 0:
        return max(0.01, 1 - (lam_home * lam_away * rho))
    if i == 0 and j == 1:
        return max(0.01, 1 + (lam_home * rho))
    if i == 1 and j == 0:
        return max(0.01, 1 + (lam_away * rho))
    if i == 1 and j == 1:
        return max(0.01, 1 - rho)
    return 1.0


def _normalize_matrix(matrix):
    total = sum(sum(row) for row in matrix)
    if total <= 0:
        size = len(matrix) * len(matrix[0])
        return [[1.0 / size for _ in row] for row in matrix]
    return [[v / total for v in row] for row in matrix]


def _outcome_cap(confidence: float) -> float:
    """Techo que se le permite a un solo resultado, segun la confianza."""
    return 0.75 + (0.2 * confidence)


def _temper_outcome_extremes(probabilities: dict[str, float], confidence: float) -> dict[str, float]:
    """Recorta los extremos SIN que el valor recortado vuelva a subir.

    La version anterior recortaba al techo y despues renormalizaba dividiendo
    entre la suma. Como esa suma quedaba por debajo de 1, la division volvia a
    inflar el propio valor que se acababa de recortar: con cap 0.75, la entrada
    0.82 / 0.12 / 0.06 sumaba 0.93 tras el recorte y terminaba devolviendo
    0.806, por encima del techo que la funcion decia respetar.

    Ahora el excedente se reparte entre las opciones que todavia tienen holgura,
    proporcional a su peso, de modo que el total sigue sumando 1, ninguna clave
    supera el cap y se conserva la proporcion relativa entre las no recortadas.
    """
    total = sum(probabilities.values())
    if total <= 0:
        size = len(probabilities) or 1
        return {k: 1.0 / size for k in probabilities}

    result = {k: float(v) / total for k, v in probabilities.items()}
    cap = _outcome_cap(confidence)
    if cap >= 1.0:
        return result

    # Un solo pase basta para 3 resultados; el bucle es red de seguridad.
    for _ in range(len(result)):
        excess = 0.0
        for k, v in result.items():
            if v > cap:
                excess += v - cap
                result[k] = cap
        if excess <= 1e-12:
            break
        pool = sum(v for v in result.values() if v < cap)
        if pool <= 1e-12:
            # Todas pegadas al techo: reparto parejo y salimos.
            share = excess / len(result)
            for k in result:
                result[k] += share
            break
        for k, v in list(result.items()):
            if v < cap:
                result[k] = v + excess * (v / pool)
    return result


def _match_sample_stats(db: Session, season_id: int | None):
    query = db.query(models.Match)
    # Nunca mezclar temporadas: evita que una predicción histórica vea partidos
    # posteriores. El endpoint no conoce una fecha de fixture, por lo que usa
    # únicamente los partidos ya terminados del torneo solicitado.
    if season_id is not None:
        query = query.filter(models.Match.season_id == season_id)
    rows = query.filter(
        and_(
            models.Match.status == "finished",
            models.Match.home_score.isnot(None),
            models.Match.away_score.isnot(None),
            models.Match.home_team_id.isnot(None),
            models.Match.away_team_id.isnot(None),
        )
    ).all()
    team_stats = {}
    home_goals_total = 0.0
    away_goals_total = 0.0
    matches_total = 0

    def acc(team_id: int):
        if team_id not in team_stats:
            team_stats[team_id] = {
                "overall": {"matches": 0, "gf": 0.0, "ga": 0.0},
                "home": {"matches": 0, "gf": 0.0, "ga": 0.0},
                "away": {"matches": 0, "gf": 0.0, "ga": 0.0},
            }
        return team_stats[team_id]

    for m in rows:
        if m.home_team_id is None or m.away_team_id is None:
            continue
        hg = float(m.home_score or 0)
        ag = float(m.away_score or 0)
        home_goals_total += hg
        away_goals_total += ag
        matches_total += 1

        h = acc(int(m.home_team_id))
        a = acc(int(m.away_team_id))

        h["overall"]["matches"] += 1
        h["overall"]["gf"] += hg
        h["overall"]["ga"] += ag
        h["home"]["matches"] += 1
        h["home"]["gf"] += hg
        h["home"]["ga"] += ag

        a["overall"]["matches"] += 1
        a["overall"]["gf"] += ag
        a["overall"]["ga"] += hg
        a["away"]["matches"] += 1
        a["away"]["gf"] += ag
        a["away"]["ga"] += hg

    if matches_total <= 0:
        return team_stats, {"home_avg": 0.0, "away_avg": 0.0, "overall_avg": 0.0, "matches": 0}
    home_avg = _safe_div(home_goals_total, matches_total)
    away_avg = _safe_div(away_goals_total, matches_total)
    return team_stats, {"home_avg": home_avg, "away_avg": away_avg, "overall_avg": (home_avg + away_avg) / 2.0, "matches": matches_total}


def _standing_fallback_stats(standings):
    by_team = {}
    league_gf = 0.0
    league_played = 0
    for s in standings:
        played = int(s.played or 0)
        if played <= 0:
            continue
        gf = float(s.goals_for or 0)
        ga = float(s.goals_against or 0)
        league_gf += gf
        league_played += played
        by_team[s.team_id] = {
            "overall": {"matches": played, "gf": gf, "ga": ga},
            "home": {"matches": 0, "gf": 0.0, "ga": 0.0},
            "away": {"matches": 0, "gf": 0.0, "ga": 0.0},
        }
    avg = _safe_div(league_gf, league_played)
    return by_team, {"home_avg": avg * HOME_ADVANTAGE, "away_avg": avg * AWAY_FACTOR, "overall_avg": avg, "matches": league_played}


def _select_context(team_bundle, is_home: bool):
    side_key = "home" if is_home else "away"
    side = team_bundle.get(side_key, {})
    side_matches = int(side.get("matches") or 0)
    if side_matches >= MIN_SPLIT_SAMPLE:
        return side, side_matches, side_key
    overall = team_bundle.get("overall", {})
    return overall, int(overall.get("matches") or 0), "overall"


def _rates(team_bundle, league, is_home: bool, prior_strength: float):
    context, sample, context_name = _select_context(team_bundle, is_home=is_home)
    gf_rate = _safe_div(context.get("gf", 0.0), sample)
    ga_rate = _safe_div(context.get("ga", 0.0), sample)
    side_mean = league["home_avg"] if is_home else league["away_avg"]
    # Aun con contexto overall, el prior conserva la sede; de otro modo la
    # ventaja local desaparecía precisamente cuando la muestra era pequeña.
    prior_mean = side_mean
    reg_gf = _regularize_rate(gf_rate, sample, prior_mean, prior_strength)
    reg_ga = _regularize_rate(ga_rate, sample, prior_mean, prior_strength)
    return {
        "sample": sample,
        "context": context_name,
        "raw_gf_rate": gf_rate if sample else prior_mean,
        "raw_ga_rate": ga_rate if sample else prior_mean,
        "reg_gf_rate": reg_gf,
        "reg_ga_rate": reg_ga,
    }


@router.get("/predict")
def predict_match(
    home: int = Query(..., description="team_id local"),
    away: int = Query(..., description="team_id visitante"),
    prior_strength: float = Query(DEFAULT_PRIOR_STRENGTH, ge=1.0, le=20.0, description="Fuerza del prior en partidos efectivos"),
    season: str = Query(None),
    db: Session = Depends(get_db),
):
    """Predice un partido entre dos equipos con un modelo de Poisson a partir de
    la tabla: fuerza de ataque/defensa relativa a la media de la liga + ventaja de
    local. Devuelve goles esperados, probabilidades (1/X/2) y marcador mas probable."""
    th = get_or_404(db, models.Team, home)
    ta = get_or_404(db, models.Team, away)
    season_id = resolve_season_id(db, season)
    standings = db.query(models.Standing).filter(models.Standing.season_id == season_id).all() if season_id is not None else []
    team_stats, league = _match_sample_stats(db, season_id)
    if league["matches"] <= 0:
        team_stats, league = _standing_fallback_stats([s for s in standings if (s.played or 0) > 0])

    if league["overall_avg"] <= 0:
        league = {"home_avg": 1.35, "away_avg": 1.05, "overall_avg": 1.20, "matches": 0}

    neutral = {"overall": {"matches": 0, "gf": 0.0, "ga": 0.0}, "home": {"matches": 0, "gf": 0.0, "ga": 0.0}, "away": {"matches": 0, "gf": 0.0, "ga": 0.0}}
    home_bundle = team_stats.get(home, neutral)
    away_bundle = team_stats.get(away, neutral)

    home_rates = _rates(home_bundle, league, is_home=True, prior_strength=prior_strength)
    away_rates = _rates(away_bundle, league, is_home=False, prior_strength=prior_strength)

    base_avg = max(league["overall_avg"], 0.05)

    raw_h = max(0.05, home_rates["raw_gf_rate"] * (away_rates["raw_ga_rate"] / base_avg))
    raw_a = max(0.05, away_rates["raw_gf_rate"] * (home_rates["raw_ga_rate"] / base_avg))

    exp_h = max(
        0.05,
        league["home_avg"]
        * (home_rates["reg_gf_rate"] / base_avg)
        * (away_rates["reg_ga_rate"] / base_avg),
    )
    exp_a = max(
        0.05,
        league["away_avg"]
        * (away_rates["reg_gf_rate"] / base_avg)
        * (home_rates["reg_ga_rate"] / base_avg),
    )

    confidence = min(1.0, ((home_rates["sample"] + away_rates["sample"]) / 2.0) / (((home_rates["sample"] + away_rates["sample"]) / 2.0) + prior_strength))
    max_lam = 3.1 + confidence * 0.9
    exp_h = min(max_lam, exp_h)
    exp_a = min(max_lam, exp_a)

    ph_raw = [_poisson(i, raw_h) for i in range(MAX_GOALS + 1)]
    pa_raw = [_poisson(j, raw_a) for j in range(MAX_GOALS + 1)]
    ph = [_poisson(i, exp_h) for i in range(MAX_GOALS + 1)]
    pa = [_poisson(j, exp_a) for j in range(MAX_GOALS + 1)]

    home_raw = draw_raw = away_raw = 0.0
    matrix = []
    for i in range(MAX_GOALS + 1):
        row = []
        for j in range(MAX_GOALS + 1):
            p_raw = ph_raw[i] * pa_raw[j]
            if i > j:
                home_raw += p_raw
            elif i == j:
                draw_raw += p_raw
            else:
                away_raw += p_raw
            row.append((ph[i] * pa[j]) * _dixon_coles_tau(i, j, exp_h, exp_a, DEFAULT_DC_RHO))
        matrix.append(row)

    matrix = _normalize_matrix(matrix)
    home_win = draw = away_win = 0.0
    scored = []
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                home_win += p
            elif i == j:
                draw += p
            else:
                away_win += p
            scored.append((i, j, p))

    tempered = _temper_outcome_extremes({"home_win": home_win, "draw": draw, "away_win": away_win}, confidence)
    raw_total = home_raw + draw_raw + away_raw or 1.0
    raw_probs = {"home_win": home_raw / raw_total, "draw": draw_raw / raw_total, "away_win": away_raw / raw_total}
    top_scorelines = sorted(scored, key=lambda x: x[2], reverse=True)[:3]
    best_score = top_scorelines[0]
    return {
        "season": resolve_season_label(db, season),
        "home_team": {"id": home, "name": th.name},
        "away_team": {"id": away, "name": ta.name},
        "expected_goals": {"home": round(exp_h, 2), "away": round(exp_a, 2)},
        "draw_probability": round(tempered["draw"], 3),
        "probabilities": {
            "home_win": round(tempered["home_win"], 3),
            "draw": round(tempered["draw"], 3),
            "away_win": round(tempered["away_win"], 3),
        },
        "probabilities_raw": {
            "home_win": round(raw_probs["home_win"], 3),
            "draw": round(raw_probs["draw"], 3),
            "away_win": round(raw_probs["away_win"], 3),
        },
        "probabilities_regularized": {
            "home_win": round(tempered["home_win"], 3),
            "draw": round(tempered["draw"], 3),
            "away_win": round(tempered["away_win"], 3),
        },
        "most_likely_score": {"home": best_score[0], "away": best_score[1], "probability": round(best_score[2], 3)},
        "top_scorelines": [{"home": i, "away": j, "probability": round(p, 3)} for i, j, p in top_scorelines],
        "sample_size": {
            "home_team": {
                "overall": home_bundle["overall"]["matches"],
                "home": home_bundle["home"]["matches"],
                "away": home_bundle["away"]["matches"],
                "used": home_rates["sample"],
                "context": home_rates["context"],
            },
            "away_team": {
                "overall": away_bundle["overall"]["matches"],
                "home": away_bundle["home"]["matches"],
                "away": away_bundle["away"]["matches"],
                "used": away_rates["sample"],
                "context": away_rates["context"],
            },
            "league_matches": league["matches"],
        },
        "prior_strength": round(prior_strength, 2),
        "tempering": {"applied": True, "method": "confidence_aware_cap", "cap": round(_outcome_cap(confidence), 3), "empirically_calibrated": False},
        "confidence": round(confidence, 3),
        "uncertainty": round(1.0 - confidence, 3),
        "factors": [
            "Regularizacion bayesiana hacia media de liga",
            "Separacion local/visitante cuando la muestra por sede es suficiente",
            "Correccion Dixon-Coles aplicada a marcadores bajos (0-0, 1-0, 0-1, 1-1)",
            "Fallback a medias de liga cuando hay pocos o nulos datos historicos",
            "Tempering heuristico de extremos; no es calibracion empirica",
        ],
        "model": "Poisson regularizado + Dixon-Coles (sin usar partidos futuros)",
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
        standings = db.query(models.Standing).options(joinedload(models.Standing.team)).filter(models.Standing.season_id == season_id).all()

    M = models.PlayerMatchStat
    xg_rows = db.query(M.team_id, func.sum(M.xg)).filter(M.season == label).group_by(M.team_id).all()
    xg_by_team = {tid: float(x or 0) for tid, x in xg_rows}

    out = []
    for s in standings:
        played = s.played or 0
        ppg = s.points / played if played else 0.0
        gdpg = s.goal_difference / played if played else 0.0
        rating = round(min(100, max(0, (ppg / 3) * 70 + ((gdpg + 3) / 6) * 30)), 1)  # type: ignore[type-var]
        out.append(
            {
                "team": {"id": s.team_id, "name": s.team.name if s.team else None, "logo_url": s.team.logo_url if s.team else None},
                "rating": rating,
                "played": played,
                "ppg": round(ppg, 2),
                "gd_per_game": round(gdpg, 2),
                "xg": round(xg_by_team.get(s.team_id, 0.0), 2),
                "table_position": s.position,
            }
        )
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
        db.query(M.player_name, func.count(M.id), func.sum(M.goals), func.sum(M.assists), func.sum(M.xg), func.sum(M.xa), func.avg(M.rating))
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

        players.append(
            {
                "player": name,
                "watch_score": round(score, 1),
                "reason": reason,
                "appearances": apps,
                "goals": g,
                "assists": a,
                "xg": round(xg, 2),
                "xa": round(xa, 2),
                "avg_rating": round(r, 2) if r else None,
            }
        )
    players.sort(key=lambda p: p["watch_score"], reverse=True)
    return players[:limit]


@router.get("/matches/{match_id}/players-to-watch")
def players_to_watch(match_id: int, limit: int = Query(3, ge=1, le=6), season: str = Query(None), db: Session = Depends(get_db)):
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
