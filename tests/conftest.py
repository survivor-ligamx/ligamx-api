"""Configuracion de pytest: BD SQLite aislada y cliente de pruebas.

Cada test corre con una base limpia y el cache vaciado, sin tocar la red.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///./_pytest.db"
os.environ["SYNC_API_KEY"] = "test-key"
os.environ["RUN_SCHEDULER"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"  # los tests no deben toparse con el limite por IP

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import engine, Base, SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app import models  # noqa: E402
from app.cache import clear_cache  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Recrea el esquema y limpia el cache antes de cada test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    clear_cache()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded(db):
    """Inserta datos minimos: 2 equipos, 1 partido finalizado, tabla y 1 jugador."""
    db.add(models.Stadium(id=1, name="Estadio Test", city="CDMX", capacity=50000))
    db.flush()
    db.add(models.Season(id=1, name="Apertura 2026", year=2026, tournament_type="Apertura"))
    db.add(models.Team(id=1, name="América", short_name="AME", city="CDMX", logo_url="http://x/a.png", founded=1916, stadium_id=1))
    db.add(models.Team(id=2, name="Chivas", short_name="GDL", city="Guadalajara"))
    db.flush()
    db.add(
        models.Match(
            id=1,
            season_id=1,
            home_team_id=1,
            away_team_id=2,
            home_score=2,
            away_score=1,
            status="finished",
            match_date=datetime(2026, 7, 20),
            week_number=1,
            external_event_id="ESP1",
            espn_event_id="ESP1",
        )
    )
    db.add(models.Standing(season_id=1, team_id=1, position=1, played=1, won=1, drawn=0, lost=0, goals_for=2, goals_against=1, goal_difference=1, points=3))
    db.add(models.Standing(season_id=1, team_id=2, position=2, played=1, won=0, drawn=0, lost=1, goals_for=1, goals_against=2, goal_difference=-1, points=0))
    db.add(models.Player(id=10, team_id=1, name="Henry Martín", position="Delantero", nationality="México"))
    # Eventos del partido (gol y tarjeta) y alineacion
    db.add(models.MatchEvent(match_id=1, event_type="goal", event_time=23, player_name="Henry Martín", team_id=1, team_name="América", description="Goal", is_home=1))
    db.add(models.MatchEvent(match_id=1, event_type="yellow_card", event_time=55, player_name="Rival X", team_id=2, team_name="Chivas", description="Yellow Card", is_home=0))
    db.add(models.MatchLineup(match_id=1, player_id=10, player_name="Henry Martín", team_id=1, team_name="América", position="FW", is_substitute=0, jersey_number=21))
    db.add(models.MatchStat(team_id=1, team_name="América", event_id="ESP1", season="Apertura 2026", possession=58.0, shots=12, shots_on_target=5, yellow_cards=1, red_cards=0))
    db.commit()
    return db
