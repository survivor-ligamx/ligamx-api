"""Busqueda global: un solo endpoint para encontrar equipos, jugadores y
estadios por nombre, ignorando acentos y mayusculas."""

import unicodedata
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app import models

router = APIRouter()


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _rank(name: str, nq: str) -> int:
    """0 = empieza por la consulta (mejor), 1 = la contiene (resto)."""
    n = _norm(name)
    return 0 if n.startswith(nq) else 1


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, description="Texto a buscar (ignora acentos y mayusculas)"),
    limit: int = Query(10, ge=1, le=50, description="Maximo de resultados por categoria"),
    db: Session = Depends(get_db),
):
    """Busca equipos, jugadores y estadios cuyo nombre contenga la consulta.
    Los resultados que EMPIEZAN por la consulta aparecen primero."""
    nq = _norm(q)

    teams = [
        t
        for t in db.query(models.Team).all()
        if nq in _norm(t.name) or nq in _norm(t.short_name or "")  # type: ignore[arg-type]
    ]
    teams.sort(key=lambda t: (_rank(t.name, nq), _norm(t.name)))  # type: ignore[arg-type]

    players = [
        p
        for p in db.query(models.Player).options(joinedload(models.Player.team)).all()
        if nq in _norm(p.name)  # type: ignore[arg-type]
    ]
    players.sort(key=lambda p: (_rank(p.name, nq), _norm(p.name)))  # type: ignore[arg-type]

    stadiums = [s for s in db.query(models.Stadium).all() if nq in _norm(s.name)]  # type: ignore[arg-type]
    stadiums.sort(key=lambda s: (_rank(s.name, nq), _norm(s.name)))  # type: ignore[arg-type]

    team_out = [
        {
            "id": t.id,
            "name": t.name,
            "short_name": t.short_name,
            "city": t.city,
            "logo_url": t.logo_url,
        }
        for t in teams[:limit]
    ]

    player_out = [
        {
            "id": p.id,
            "name": p.name,
            "team_id": p.team_id,
            "team_name": p.team.name if p.team else None,
            "position": p.position,
            "number": p.number,
            "photo_url": p.photo_url,
        }
        for p in players[:limit]
    ]

    stadium_out = [{"id": s.id, "name": s.name, "city": s.city} for s in stadiums[:limit]]

    return {
        "query": q,
        "counts": {"teams": len(teams), "players": len(players), "stadiums": len(stadiums)},
        "teams": team_out,
        "players": player_out,
        "stadiums": stadium_out,
    }
