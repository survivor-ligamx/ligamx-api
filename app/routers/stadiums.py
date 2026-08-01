from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_or_404
from app import models, schemas

router = APIRouter()

@router.get("/stadiums", response_model=list[schemas.StadiumResponse])
def get_stadiums(db: Session = Depends(get_db)):
    return db.query(models.Stadium).all()

@router.get("/stadiums/{stadium_id}", response_model=schemas.StadiumResponse)
def get_stadium(stadium_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, models.Stadium, stadium_id)
