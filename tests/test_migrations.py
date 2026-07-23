"""Pruebas de regresion para migraciones con datos existentes."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "c8d9e0f1a2b3"


def _alembic_upgrade(database_url: str, revision: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_espn_event_id_migration_is_unique_and_non_destructive(tmp_path):
    db_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{db_path}"
    _alembic_upgrade(database_url, PREVIOUS_REVISION)

    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO matches (id, external_event_id, status)
        VALUES (10, 'DUP', 'scheduled')
        """
    )
    connection.execute(
        """
        INSERT INTO matches (
            id, external_event_id, status, home_score, away_score
        ) VALUES (20, 'DUP', 'finished', 3, 1)
        """
    )
    connection.execute(
        """
        INSERT INTO match_events (id, match_id, event_type)
        VALUES (1, 20, 'goal')
        """
    )
    connection.commit()
    connection.close()

    _alembic_upgrade(database_url, "head")

    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        """
        SELECT id, espn_event_id, status, home_score, away_score
        FROM matches ORDER BY id
        """
    ).fetchall()
    assert rows == [
        (10, None, "scheduled", None, None),
        (20, None, "finished", 3, 1),
    ]
    assert connection.execute("SELECT match_id FROM match_events WHERE id = 1").fetchone() == (20,)

    connection.execute("INSERT INTO matches (id, espn_event_id) VALUES (30, 'DUP')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO matches (id, espn_event_id) VALUES (40, 'DUP')")
    connection.close()
