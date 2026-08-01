from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, case
from datetime import datetime, timezone
import re
import unicodedata
from app.database import get_db
from app.dependencies import get_or_404, resolve_season_label, resolve_season_id, discipline_summary
from app.scrapers.espn_requests_scraper import ESPNRequestsScraper
from app.cache import cached
from app import models, schemas

router = APIRouter()


def _year_from_season(season: str) -> int:
    """Extrae el ano de una etiqueta ('Apertura 2026') o de '2026'; si no hay,
    usa el ano actual (para el core API de ESPN, que indexa por ano-temporada)."""
    m = re.search(r"(20\d{2})", season or "")
    return int(m.group(1)) if m else datetime.now().year


@router.get("/teams", response_model=list[schemas.TeamResponse])
def get_teams(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return db.query(models.Team).options(joinedload(models.Team.stadium)).offset(offset).limit(limit).all()


@router.get("/teams/search", response_model=list[schemas.TeamResponse])
def search_teams(q: str, db: Session = Depends(get_db)):
    def norm(s):
        return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()

    query = norm(q)
    return [t for t in db.query(models.Team).all() if query in norm(t.name)]


@router.get("/teams/xg-performance")
def teams_xg_performance(
    season: str = Query(None),
    order: str = Query("over", description="'over' = más goles que su xG primero; 'under' = al revés"),
    db: Session = Depends(get_db),
):
    """Rendimiento goles vs xG por EQUIPO en la temporada (suma de los xG de los
    tiros de sus jugadores, desde player_match_stats). Muestra qué equipos son
    más efectivos (goles > xG) o desperdician (goles < xG)."""
    label = resolve_season_label(db, season)
    M = models.PlayerMatchStat
    rows = db.query(M.team_id, M.team_name, func.sum(M.goals).label("goals"), func.sum(M.xg).label("xg")).filter(M.season == label).group_by(M.team_id, M.team_name).all()
    out = []
    for team_id, team_name, goals, xg in rows:
        g = int(goals or 0)
        x = round(float(xg or 0), 2)
        out.append({"team_id": team_id, "team": team_name, "goals": g, "xg": x, "diff": round(g - x, 2)})
    out.sort(key=lambda r: r["diff"], reverse=(order != "under"))
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out


@router.get("/teams/{team_id}", response_model=schemas.TeamResponse)
def get_team(team_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, models.Team, team_id)


@router.get("/teams/{team_id}/players", response_model=list[schemas.PlayerResponse])
def get_team_players(team_id: int, db: Session = Depends(get_db)):
    get_or_404(db, models.Team, team_id)
    return db.query(models.Player).filter(models.Player.team_id == team_id).all()


@router.get("/teams/{team_id}/last-matches", response_model=list[schemas.MatchResponse])
def get_team_last_matches(team_id: int, limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
    get_or_404(db, models.Team, team_id)
    return (
        db.query(models.Match)
        .options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
        .filter((models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id))
        .order_by(models.Match.match_date.desc())
        .limit(limit)
        .all()
    )


@router.get("/teams/{team_id}/stats")
def get_team_stats(team_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    get_or_404(db, models.Team, team_id)
    label = resolve_season_label(db, season)
    stats = db.query(models.MatchStat).filter(models.MatchStat.team_id == team_id, models.MatchStat.season == label).all()
    if not stats:
        return {"team_id": team_id, "season": label, "matches": 0, "message": "No stats found"}
    totals = {"shots": 0, "shots_on_target": 0, "corners": 0, "fouls": 0, "yellow_cards": 0, "red_cards": 0}
    possession_sum = 0
    count = 0
    for s in stats:
        for k in totals:
            if getattr(s, k):
                totals[k] += getattr(s, k)
        if s.possession:
            possession_sum += s.possession  # type: ignore[assignment]
            count += 1
    return {"team_id": team_id, "season": label, "matches": len(stats), "possession_avg": round(possession_sum / count, 1) if count else None, "totals": totals}


@router.get("/teams/{team_id}/season-stats")
@cached(600)
def get_team_season_stats(team_id: int, season: str = Query(None, description="Etiqueta o ano; por defecto el ano vigente")):
    """Estadisticas de EQUIPO agregadas de la temporada via ESPN (~100 metricas:
    porterias a cero, goles recibidos, pases completados, tackles, intercepciones,
    duelos, etc.) en categorias defensive/general/goalKeeping/offensive."""
    return ESPNRequestsScraper().get_team_season_stats(team_id, _year_from_season(season))


@router.get("/teams/{team_id}/form")
def get_team_form(team_id: int, limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
    """Forma reciente del equipo: ultimos N partidos jugados con resultado
    (W/D/L) desde la perspectiva del equipo, mas el racha en texto."""
    get_or_404(db, models.Team, team_id)
    matches = (
        db.query(models.Match)
        .options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
        .filter((models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id))
        .filter(models.Match.status == "finished")
        .filter(models.Match.home_score.isnot(None), models.Match.away_score.isnot(None))
        .order_by(models.Match.match_date.desc())
        .limit(limit)
        .all()
    )

    results = []
    summary = {"W": 0, "D": 0, "L": 0}
    for m in matches:
        is_home = m.home_team_id == team_id
        gf = m.home_score if is_home else m.away_score
        ga = m.away_score if is_home else m.home_score
        if gf > ga:
            outcome = "W"
        elif gf < ga:
            outcome = "L"
        else:
            outcome = "D"
        summary[outcome] += 1
        opponent = m.away_team if is_home else m.home_team
        results.append(
            {
                "match_id": m.id,
                "date": m.match_date,
                "home": is_home,
                "opponent": opponent.name if opponent else None,
                "score": f"{gf}-{ga}",
                "result": outcome,
            }
        )

    return {
        "team_id": team_id,
        "played": len(results),
        "summary": summary,
        "form": "".join(r["result"] for r in results),  # type: ignore[misc]
        "matches": results,
    }


@router.get("/teams/{team_id}/discipline")
def get_team_discipline(team_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Disciplina del equipo en la temporada: totales de tarjetas y desglose por
    jugador, destacando a los que estan en riesgo de suspension por acumulacion."""
    get_or_404(db, models.Team, team_id)
    label = resolve_season_label(db, season)
    season_id = resolve_season_id(db, season)
    players = []
    total_yellow = total_red = 0
    if season_id is not None:
        E, M = models.MatchEvent, models.Match
        rows = (
            db.query(
                E.player_name,
                func.sum(case((E.event_type == "yellow_card", 1), else_=0)).label("yellow"),
                func.sum(case((E.event_type == "red_card", 1), else_=0)).label("red"),
            )
            .join(M, E.match_id == M.id)
            .filter(M.season_id == season_id, E.team_id == team_id)
            .filter(E.event_type.in_(["yellow_card", "red_card"]))
            .filter(E.player_name.isnot(None))
            .group_by(E.player_name)
            .all()
        )
        for name, yellow, red in rows:
            d = discipline_summary(yellow, red)
            d["player"] = name
            total_yellow += d["yellow_cards"]
            total_red += d["red_cards"]
            players.append(d)
        players.sort(key=lambda p: p["discipline_points"], reverse=True)
    return {
        "team_id": team_id,
        "season": label,
        "totals": {
            "yellow_cards": total_yellow,
            "red_cards": total_red,
            "discipline_points": total_yellow + total_red * 2,
        },
        "at_risk": [p for p in players if p["suspension_risk"]],
        "players": players,
    }


@router.get("/teams/{team_id}/streak")
def get_team_streak(team_id: int, db: Session = Depends(get_db)):
    """Rachas actuales del equipo (desde los partidos finalizados, del mas reciente
    hacia atras): invicto, victorias seguidas, sin ganar, anotando y porteria a cero."""
    get_or_404(db, models.Team, team_id)
    matches = (
        db.query(models.Match)
        .filter((models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id))
        .filter(models.Match.status == "finished")
        .filter(models.Match.home_score.isnot(None), models.Match.away_score.isnot(None))
        .order_by(models.Match.match_date.desc())
        .all()
    )
    results = []
    for m in matches:
        is_home = m.home_team_id == team_id
        gf = m.home_score if is_home else m.away_score
        ga = m.away_score if is_home else m.home_score
        outcome = "W" if gf > ga else ("L" if gf < ga else "D")
        results.append({"result": outcome, "gf": gf, "ga": ga})

    def run_while(pred):
        n = 0
        for r in results:
            if pred(r):
                n += 1
            else:
                break
        return n

    return {
        "team_id": team_id,
        "matches_played": len(results),
        "recent_form": "".join(r["result"] for r in results[:5]),  # type: ignore[misc]
        "streaks": {
            "unbeaten": run_while(lambda r: r["result"] in ("W", "D")),
            "wins": run_while(lambda r: r["result"] == "W"),
            "winless": run_while(lambda r: r["result"] in ("D", "L")),
            "losses": run_while(lambda r: r["result"] == "L"),
            "scoring": run_while(lambda r: r["gf"] > 0),
            "clean_sheets": run_while(lambda r: r["ga"] == 0),
        },
    }


@router.get("/teams/{team_id}/profile")
def get_team_profile(team_id: int, season: str = Query(None), db: Session = Depends(get_db)):
    """Perfil completo del equipo en una llamada: ficha + sede, posicion en la
    tabla, forma reciente, xG, tamano de plantilla, proximo partido y ultimo
    resultado."""
    team = get_or_404(db, models.Team, team_id)
    label = resolve_season_label(db, season)
    season_id = resolve_season_id(db, season)

    standing = None
    if season_id is not None:
        st = db.query(models.Standing).filter(models.Standing.season_id == season_id, models.Standing.team_id == team_id).first()
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

    finished = (
        db.query(models.Match)
        .filter(
            (models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id), models.Match.status == "finished", models.Match.home_score.isnot(None), models.Match.away_score.isnot(None)
        )
        .order_by(models.Match.match_date.desc())
        .limit(5)
        .all()
    )
    form = ""
    for m in finished:
        gf = m.home_score if m.home_team_id == team_id else m.away_score
        ga = m.away_score if m.home_team_id == team_id else m.home_score
        form += "W" if gf > ga else ("L" if gf < ga else "D")

    M = models.PlayerMatchStat
    xg = db.query(func.sum(M.xg)).filter(M.team_id == team_id, M.season == label).scalar()
    goals = db.query(func.sum(M.goals)).filter(M.team_id == team_id, M.season == label).scalar()
    squad = db.query(func.count(models.Player.id)).filter(models.Player.team_id == team_id).scalar()

    nxt = (
        db.query(models.Match)
        .options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
        .filter((models.Match.home_team_id == team_id) | (models.Match.away_team_id == team_id), models.Match.match_date >= datetime.now(timezone.utc))
        .order_by(models.Match.match_date)
        .first()
    )

    def _brief(m):
        if not m:
            return None
        return {
            "id": m.id,
            "date": m.match_date,
            "status": m.status,
            "home": m.home_team.name if m.home_team else None,
            "away": m.away_team.name if m.away_team else None,
            "score": {"home": m.home_score, "away": m.away_score},
        }

    last = finished[0] if finished else None
    return {
        "team": {
            "id": team.id,
            "name": team.name,
            "logo_url": team.logo_url,
            "city": team.city,
            "founded": team.founded,
            "stadium": {"name": team.stadium.name, "capacity": team.stadium.capacity} if team.stadium else None,
        },
        "season": label,
        "standing": standing,
        "form": form,
        "xg": round(float(xg or 0), 2),
        "goals": int(goals or 0),
        "squad_size": squad,
        "next_match": _brief(nxt),
        "last_result": _brief(last),
    }
