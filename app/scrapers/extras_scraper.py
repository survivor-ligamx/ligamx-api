"""Fuentes complementarias ("joyitas") para enriquecer la API de Liga MX.

- TheSportsDB: escudos, jerseys, estadios, apodos y descripciones en espanol,
  ademas de highlights en VIDEO y miniaturas de los partidos (resultados y
  proximos). Trae el campo idESPN, que coincide con los IDs de equipo de ESPN.
- Scorebat: highlights en video (fallback; su API v3 quedo deprecada).
"""

import logging
from typing import Optional, Dict, List
import requests

logger = logging.getLogger(__name__)

SCOREBAT_URL = "https://www.scorebat.com/video-api/v3/"
THESPORTSDB_KEY = "3"  # key publica gratuita
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}"
THESPORTSDB_TEAMS = f"{THESPORTSDB_BASE}/search_all_teams.php"
LIGA_MX_LEAGUE_ID = 4350  # "Mexican Primera League" en TheSportsDB

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}


def _get_json(url: str, params: Optional[Dict] = None) -> Dict:
    r = requests.get(url, headers=_HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()  # type: ignore[no-any-return]


def _event_to_dict(e: Dict) -> Dict:
    def _score(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return {
        "event_id": e.get("idEvent"),
        "title": e.get("strEvent"),
        "round": e.get("intRound"),
        "season": e.get("strSeason"),
        "home_team": e.get("strHomeTeam"),
        "away_team": e.get("strAwayTeam"),
        "home_score": _score(e.get("intHomeScore")),
        "away_score": _score(e.get("intAwayScore")),
        "date": e.get("dateEvent"),
        "time": e.get("strTime"),
        "timestamp": e.get("strTimestamp"),
        "venue": e.get("strVenue"),
        "status": e.get("strStatus"),
        "thumbnail": e.get("strThumb"),
        "video": e.get("strVideo") or None,
        "poster": e.get("strPoster") or None,
    }


def _scorebat_highlights() -> List[Dict]:
    """Fallback: highlights de Liga MX desde Scorebat (API deprecada)."""
    try:
        data = _get_json(SCOREBAT_URL)
    except Exception as e:
        logger.warning(f"Scorebat fallo: {e}")
        return []
    out = []
    for item in data.get("response", []):
        comp = (item.get("competition") or "").lower()
        if "mexico" not in comp and "liga mx" not in comp:
            continue
        out.append(
            {
                "title": item.get("title"),
                "competition": item.get("competition"),
                "date": item.get("date"),
                "thumbnail": item.get("thumbnail"),
                "video": item.get("matchviewUrl"),
                "source": "scorebat",
            }
        )
    return out


def get_highlights() -> List[Dict]:
    """Highlights en video + miniaturas de los ultimos partidos de Liga MX.

    Fuente principal: TheSportsDB (resultados recientes con strVideo/strThumb).
    Si no hay videos disponibles, cae a Scorebat como respaldo.
    """
    results = []
    try:
        data = _get_json(f"{THESPORTSDB_BASE}/eventspastleague.php", {"id": LIGA_MX_LEAGUE_ID})
        for e in data.get("events") or []:
            d = _event_to_dict(e)
            if d.get("video") or d.get("thumbnail"):
                d["source"] = "thesportsdb"
                results.append(d)
    except Exception as e:
        logger.warning(f"TheSportsDB highlights fallo: {e}")

    if not results:
        return _scorebat_highlights()
    return results


def get_upcoming_events() -> List[Dict]:
    """Calendario de los proximos partidos de Liga MX con miniatura, sede y
    horario (TheSportsDB). Joyita visual para apps de aficionados."""
    try:
        data = _get_json(f"{THESPORTSDB_BASE}/eventsnextleague.php", {"id": LIGA_MX_LEAGUE_ID})
    except Exception as e:
        logger.warning(f"TheSportsDB proximos fallo: {e}")
        return []
    return [_event_to_dict(e) for e in (data.get("events") or [])]


def get_team_assets() -> List[Dict]:
    """Escudos, jerseys, estadios, apodos y descripciones (ES) por equipo,
    indexados por idESPN para unir con los equipos de ESPN."""
    try:
        data = _get_json(THESPORTSDB_TEAMS, {"l": "Mexican Primera League"})
    except Exception as e:
        logger.warning(f"TheSportsDB fallo: {e}")
        return []
    out = []
    for t in data.get("teams", []) or []:
        espn_id = t.get("idESPN")
        out.append(
            {
                "espn_team_id": int(espn_id) if espn_id and str(espn_id).isdigit() else None,
                "name": t.get("strTeam"),
                "nickname": t.get("strKeywords"),
                "founded": int(t["intFormedYear"]) if t.get("intFormedYear") and str(t["intFormedYear"]).isdigit() else None,
                "stadium": t.get("strStadium"),
                "stadium_capacity": int(t["intStadiumCapacity"]) if t.get("intStadiumCapacity") and str(t["intStadiumCapacity"]).isdigit() else None,
                "badge": t.get("strBadge"),
                "jersey": t.get("strEquipment"),
                "stadium_image": t.get("strStadiumThumb"),
                "description_es": t.get("strDescriptionES"),
                "website": t.get("strWebsite"),
            }
        )
    return out


def get_team_assets_by_espn(espn_team_id: int) -> Dict:
    for team in get_team_assets():
        if team.get("espn_team_id") == espn_team_id:
            return team
    return {}
