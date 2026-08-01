from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas

router = APIRouter()

@router.get("/news", response_model=list[schemas.NewsResponse])
def get_news(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return db.query(models.News).order_by(models.News.published_at.desc()).offset(offset).limit(limit).all()
