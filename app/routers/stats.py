from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import resolve_season_label
from app import models, schemas

router = APIRouter()

@router.get("/player-stats", response_model=list[schemas.PlayerStatResponse])
def get_player_stats(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=500), season: str = Query(None)):
    label = resolve_season_label(db, season)
    return db.query(models.PlayerStat).filter(models.PlayerStat.season == label).order_by(models.PlayerStat.goals.desc()).limit(limit).all()
