"""Vista de resumen ('dashboard'): todo lo clave de la temporada en UNA llamada.
Ideal para la pantalla principal de una app o un bot."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.dependencies import resolve_season_id, resolve_season_label
from app import models

router = APIRouter()


def _team_brief(t):
    if not t:
        return None
    return {"id": t.id, "name": t.name, "logo_url": t.logo_url}


def _match_brief(m):
    return {
        "id": m.id,
        "date": m.match_date,
        "week": m.week_number,
        "status": m.status,
        "home": _team_brief(m.home_team),
        "away": _team_brief(m.away_team),
        "score": {"home": m.home_score, "away": m.away_score},
    }


@router.get("/dashboard")
def dashboard(season: str = Query(None), db: Session = Depends(get_db)):
    """Resumen de la temporada: líder de la tabla, goleador, próximos partidos,
    últimos resultados y noticias recientes."""
    season_id = resolve_season_id(db, season)
    label = resolve_season_label(db, season)
    now = datetime.now(timezone.utc)

    leader = None
    if season_id is not None:
        st = db.query(models.Standing).options(joinedload(models.Standing.team)).filter(models.Standing.season_id == season_id).order_by(models.Standing.position).first()
        if st:
            leader = {"position": st.position, "team": _team_brief(st.team), "points": st.points, "played": st.played}

    scorer = db.query(models.TopScorer).filter(models.TopScorer.season == label).order_by(models.TopScorer.goals.desc()).first()
    top_scorer = {"player": scorer.player, "team": scorer.team, "goals": scorer.goals} if scorer else None

    mq = db.query(models.Match).options(joinedload(models.Match.home_team), joinedload(models.Match.away_team))
    if season_id is not None:
        mq = mq.filter(models.Match.season_id == season_id)

    upcoming = mq.filter(models.Match.match_date >= now).order_by(models.Match.match_date).limit(5).all()
    recent = mq.filter(models.Match.status == "finished").order_by(models.Match.match_date.desc()).limit(5).all()
    news = db.query(models.News).order_by(models.News.published_at.desc().nullslast()).limit(5).all()

    return {
        "season": label,
        "standings_leader": leader,
        "top_scorer": top_scorer,
        "upcoming_matches": [_match_brief(m) for m in upcoming],
        "recent_results": [_match_brief(m) for m in recent],
        "latest_news": [{"title": n.title, "url": n.link, "image": n.image_url, "source": n.source, "published_at": n.published_at} for n in news],
    }
