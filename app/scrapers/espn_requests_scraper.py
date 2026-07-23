import time, requests, logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from app.scrapers.base import BaseScraper


def _to_naive_utc(raw_date: str):
    """Convierte una fecha ISO (con o sin zona) a datetime *naive* en UTC.
    Unificar todo a UTC naive evita comparar datetimes aware vs naive
    (que en Python lanza TypeError) y guarda fechas consistentes en la BD."""
    if not raw_date:
        return None
    try:
        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# Estadios por team_id de ESPN. Nombres OFICIALES del Apertura 2026 (calendario
# LIGA BBVA MX). Renombres 2026: Azteca -> Banorte (America, rumbo al Mundial
# 2026); Alfonso Lastras -> Libertad Financiera (Atletico de San Luis).
STADIUMS = {
    227: {"name": "Estadio Banorte", "city": "Ciudad de México"},
    226: {"name": "Estadio Ciudad de los Deportes", "city": "Ciudad de México"},
    216: {"name": "Estadio Jalisco", "city": "Guadalajara"},
    15720: {"name": "Estadio Libertad Financiera", "city": "San Luis Potosí"},
    218: {"name": "Estadio Ciudad de los Deportes", "city": "Ciudad de México"},
    17851: {"name": "Estadio Olímpico Benito Juárez", "city": "Ciudad Juárez"},
    219: {"name": "Estadio Akron", "city": "Zapopan"},
    228: {"name": "Estadio León", "city": "León"},
    220: {"name": "Estadio BBVA", "city": "Guadalupe"},
    229: {"name": "Estadio Victoria", "city": "Aguascalientes"},
    234: {"name": "Estadio Hidalgo", "city": "Pachuca"},
    231: {"name": "Estadio Cuauhtémoc", "city": "Puebla"},
    233: {"name": "Estadio Olímpico Universitario", "city": "Ciudad de México"},
    222: {"name": "Estadio Corregidora", "city": "Querétaro"},
    225: {"name": "Estadio Corona", "city": "Torreón"},
    232: {"name": "Estadio Universitario", "city": "San Nicolás de los Garza"},
    10125: {"name": "Estadio Caliente", "city": "Tijuana"},
    223: {"name": "Estadio Nemesio Díez", "city": "Toluca"},
}

logger = logging.getLogger(__name__)


class ESPNRequestsScraper(BaseScraper):
    def __init__(self):
        self._teams = []
        self._headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    @property
    def source_name(self):
        return "espn_requests"

    def _get_json(self, url, params=None, retries=3):
        for attempt in range(retries):
            try:
                r = requests.get(url, headers=self._headers, params=params, timeout=20)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                logger.warning(f"Request failed (attempt {attempt + 1}/{retries}): {url} - {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(2**attempt)
        return {}

    def get_teams(self) -> List[Dict]:
        data = self._get_json("https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/teams", {"region": "mx", "lang": "es"})
        teams = []
        for sport in data.get("sports", []):
            for league in sport.get("leagues", []):
                for t in league.get("teams", []):
                    team = t.get("team", {})
                    venue = team.get("venue", {})
                    logos = team.get("logos") or []
                    logo_url = logos[0].get("href") if logos else None
                    teams.append(
                        {
                            "id": int(team.get("id")),
                            "name": team.get("displayName", team.get("name", "")),
                            "short_name": team.get("abbreviation", ""),
                            "city": team.get("location", ""),
                            "colors": team.get("color", ""),
                            "logo_url": logo_url,
                            "stadium_name": STADIUMS.get(int(team.get("id")), {}).get("name"),
                            "venue": venue,
                        }
                    )
        self._teams = teams
        return teams

    def get_standings(self) -> List[Dict]:
        data = self._get_json("https://site.api.espn.com/apis/v2/sports/soccer/mex.1/standings", {"region": "mx", "lang": "es"})

        def g(stats, names):
            for n in names:
                if n in stats:
                    return stats[n]
            return 0

        standings = []
        for child in data.get("children", []):
            for i, entry in enumerate(child.get("standings", {}).get("entries", [])):
                team = entry.get("team", {})
                stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
                standings.append(
                    {
                        "position": int(g(stats, ["rank", "order"])) or i + 1,
                        "team_name": team.get("displayName", team.get("name", "")),
                        "played": int(g(stats, ["gamesPlayed", "games"])),
                        "won": int(g(stats, ["wins"])),
                        "drawn": int(g(stats, ["ties", "draws"])),
                        "lost": int(g(stats, ["losses"])),
                        "goals_for": int(g(stats, ["pointsFor", "goalsFor"])),
                        "goals_against": int(g(stats, ["pointsAgainst", "goalsAgainst"])),
                        "goal_difference": int(g(stats, ["pointDifferential", "goalDifference"])),
                        "points": int(g(stats, ["points"])),
                    }
                )
        return standings

    def get_stadiums(self) -> List[Dict]:
        teams = self._teams or self.get_teams()
        used = set()
        result = []
        for tid in {t["id"] for t in teams}:
            s = STADIUMS.get(tid)
            if s and s["name"] not in used:
                used.add(s["name"])
                result.append(s)
        return result

    def get_players(self) -> List[Dict]:
        teams = self._teams or self.get_teams()
        if not teams:
            return []
        players = []
        for team in teams:
            tid = team["id"]
            try:
                data = self._get_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/teams/{tid}/roster", {"region": "mx", "lang": "es"})
            except Exception as e:
                print(f"⚠️ roster {tid}: {e}")
                continue
            for ath in data.get("athletes", []):
                country = ath.get("country") or {}
                nationality = ath.get("citizenship") or (country.get("name") if isinstance(country, dict) else None)
                players.append(
                    {
                        "id": int(ath.get("id")) if ath.get("id") else None,
                        "name": ath.get("displayName", ""),
                        "team_name": team["name"],
                        "position": (ath.get("position") or {}).get("abbreviation") or (ath.get("position") or {}).get("name"),
                        "number": int(ath.get("jersey")) if ath.get("jersey") not in (None, "") else None,
                        "nationality": nationality,
                        "birth_date": ath.get("dateOfBirth"),
                        "photo_url": (ath.get("headshot") or {}).get("href") if ath.get("headshot") else None,
                        "flag_url": (ath.get("flag") or {}).get("href") if ath.get("flag") else None,
                        "height": ath.get("displayHeight"),
                        "weight": ath.get("displayWeight"),
                    }
                )
            time.sleep(0.15)
        return players

    def get_matches(self, season_id: Optional[int] = None, tournament: Optional[str] = None) -> List[Dict]:
        teams = self._teams or self.get_teams()
        if not teams:
            return []
        tnames = {t["name"] for t in teams}
        matches = {}

        def ps(v):
            if v is None or v == "":
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        year = season_id or datetime.now().year
        # Liga MX juega DOS torneos por ano: Clausura (ene-jun) y Apertura (jul-dic).
        # Antes solo se bajaban los meses jul-dic, por lo que el Clausura nunca se
        # cargaba. Ahora elegimos la ventana de meses segun el torneo (el vigente
        # por defecto, o el indicado para backfill de temporadas pasadas).
        from app.season import current_tournament

        tournament = tournament or current_tournament()[0]
        months = [1, 2, 3, 4, 5, 6] if tournament == "Clausura" else [7, 8, 9, 10, 11, 12]
        ranges = []
        for mm in months:
            start = f"{year}{mm:02d}01"
            end = f"{year}1231" if mm == 12 else f"{year}{mm + 1:02d}01"
            ranges.append((start, end))
        for start, end in ranges:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/scoreboard?region=mx&lang=es&dates={start}-{end}"
            try:
                data = self._get_json(url)
            except Exception as e:
                print(f"⚠️ {start}-{end}: {e}")
                continue
            for ev in data.get("events", []):
                eid = ev.get("id")
                if not eid or eid in matches:
                    continue
                comp = ev.get("competitions", [{}])[0]
                home: dict[str, Any] = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "home"), {})
                away: dict[str, Any] = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "away"), {})
                hn = home.get("team", {}).get("displayName", "")
                an = away.get("team", {}).get("displayName", "")
                if hn not in tnames or an not in tnames:
                    continue
                st = ev.get("status", {}).get("type", {})
                status = "finished" if st.get("completed") else "live" if st.get("state") == "in" else "scheduled"
                md = _to_naive_utc(ev.get("date"))
                matches[eid] = {
                    "event_id": eid,
                    "home_team_id": int(home.get("team", {}).get("id")) if home.get("team", {}).get("id") else None,
                    "away_team_id": int(away.get("team", {}).get("id")) if away.get("team", {}).get("id") else None,
                    "home_team": hn,
                    "away_team": an,
                    "home_score": ps(home.get("score")),
                    "away_score": ps(away.get("score")),
                    "match_date": md,
                    "status": status,
                    "week": ev.get("week", {}).get("number"),
                }
            time.sleep(0.15)
        return list(matches.values())

    def get_live_matches(self) -> List[Dict]:
        today = datetime.now().strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/scoreboard?region=mx&lang=es&dates={today}"
        try:
            data = self._get_json(url)
        except Exception as e:
            print(f"⚠️ live matches: {e}")
            return []
        live = []
        for ev in data.get("events", []):
            st = ev.get("status", {}).get("type", {})
            if st.get("state") != "in":
                continue
            comp = ev.get("competitions", [{}])[0]
            home: dict[str, Any] = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "home"), {})
            away: dict[str, Any] = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "away"), {})
            if not home or not away:
                continue
            live.append(
                {
                    "event_id": ev.get("id"),
                    "home_team": home.get("team", {}).get("displayName"),
                    "away_team": away.get("team", {}).get("displayName"),
                    "home_score": int(home.get("score")) if home.get("score") not in (None, "") else 0,  # type: ignore[arg-type]
                    "away_score": int(away.get("score")) if away.get("score") not in (None, "") else 0,  # type: ignore[arg-type]
                    "status": "live",
                    "match_date": ev.get("date"),
                    "clock": ev.get("status", {}).get("displayClock"),
                    "period": ev.get("status", {}).get("period"),
                }
            )
        return live

    def get_matches_by_date(self, date_str: str) -> List[Dict]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/scoreboard?region=mx&lang=es&dates={date_str}"
        try:
            data = self._get_json(url)
        except Exception as e:
            print(f"⚠️ matches by date {date_str}: {e}")
            return []
        matches = []
        for ev in data.get("events", []):
            comp = ev.get("competitions", [{}])[0]
            home = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "home"), {})  # type: ignore[var-annotated]
            away = next((c for c in comp.get("competitors", []) if c.get("homeAway") == "away"), {})  # type: ignore[var-annotated]
            if not home or not away:
                continue
            st = ev.get("status", {}).get("type", {})
            status = "finished" if st.get("completed") else "live" if st.get("state") == "in" else "scheduled"
            matches.append(
                {
                    "event_id": ev.get("id"),
                    "home_team": home.get("team", {}).get("displayName"),
                    "away_team": away.get("team", {}).get("displayName"),
                    "home_score": int(home.get("score")) if home.get("score") not in (None, "") else None,  # type: ignore[arg-type]
                    "away_score": int(away.get("score")) if away.get("score") not in (None, "") else None,  # type: ignore[arg-type]
                    "status": status,
                    "match_date": ev.get("date"),
                    "clock": ev.get("status", {}).get("displayClock"),
                    "period": ev.get("status", {}).get("period"),
                }
            )
        return matches

    def get_match_stats(self, event_id: str) -> List[Dict]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/summary?event={event_id}"
        try:
            data = self._get_json(url)
        except Exception as e:
            print(f"⚠️ match stats {event_id}: {e}")
            return []
        stats = []
        for team in data.get("boxscore", {}).get("teams", []):
            team_name = team.get("team", {}).get("displayName")
            team_stats = {s.get("name"): s.get("displayValue") for s in team.get("statistics", [])}

            def _num(key, default=None):
                v = team_stats.get(key)
                if v is None:
                    return default
                s = str(v).replace("%", "").replace(",", "").strip()
                try:
                    return int(s)
                except ValueError:
                    try:
                        return float(s)
                    except ValueError:
                        return default

            stats.append(
                {
                    "team_name": team_name,
                    "possession": _num("possessionPct"),
                    "shots": _num("totalShots"),
                    "shots_on_target": _num("shotsOnTarget"),
                    "corners": _num("wonCorners"),
                    "fouls": _num("foulsCommitted"),
                    "yellow_cards": _num("yellowCards"),
                    "red_cards": _num("redCards"),
                    "offsides": _num("offsides"),
                    "saves": _num("saves"),
                    "passes": _num("accuratePasses"),
                    "total_passes": _num("totalPasses"),
                    "tackles": _num("effectiveTackles"),
                    "interceptions": _num("interceptions"),
                    "blocked_shots": _num("blockedShots"),
                    "crosses": _num("accurateCrosses"),
                    "long_balls": _num("accurateLongBalls"),
                }
            )
        return stats

    def get_match_lineups(self, event_id: str, strict: bool = False) -> Dict:
        """Alineaciones del partido (titulares, suplentes, formacion y posiciones)
        a partir del endpoint summary de ESPN."""
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/summary?event={event_id}"
        try:
            data = self._get_json(url)
        except Exception as e:
            logger.warning(f"match lineups {event_id}: {e}")
            if strict:
                raise
            return {"event_id": event_id, "teams": []}
        teams = []
        for r in data.get("rosters", []):
            starters, bench = [], []  # type: ignore[var-annotated]
            for pl in r.get("roster", []):
                ath = pl.get("athlete", {}) or {}
                pos = pl.get("position", {}) or {}
                jersey = pl.get("jersey")
                player = {
                    "player_id": int(ath["id"]) if ath.get("id") else None,
                    "name": ath.get("displayName"),
                    "jersey": int(jersey) if jersey not in (None, "") else None,
                    "position": pos.get("abbreviation") or pos.get("name"),
                    "formation_place": pl.get("formationPlace"),
                    "subbed_in": bool(pl.get("subbedIn")),
                    "subbed_out": bool(pl.get("subbedOut")),
                }
                (starters if pl.get("starter") else bench).append(player)
            teams.append(
                {
                    "team_name": r.get("team", {}).get("displayName"),
                    "home_away": r.get("homeAway"),
                    "formation": r.get("formation"),
                    "starters": starters,
                    "substitutes": bench,
                }
            )
        return {"event_id": event_id, "teams": teams}

    def get_match_events(self, event_id: str, strict: bool = False) -> List[Dict]:
        """Eventos clave del partido: goles, tarjetas (amarillas/rojas) y cambios."""
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/summary?event={event_id}"
        try:
            data = self._get_json(url)
        except Exception as e:
            logger.warning(f"match events {event_id}: {e}")
            if strict:
                raise
            return []
        events = []
        for k in data.get("keyEvents", []):
            type_text = (k.get("type", {}) or {}).get("text", "")
            participants = k.get("participants") or []
            player = participants[0].get("athlete", {}).get("displayName") if participants else None
            lower = type_text.lower()
            if "card" in lower:
                if "yellow" in lower:
                    category = "yellow_card"
                elif "red" in lower:
                    category = "red_card"
                else:
                    category = "card"
            elif k.get("scoringPlay") or "goal" in lower:
                category = "goal"
            elif "substitution" in lower:
                category = "substitution"
            else:
                category = "other"
            events.append(
                {
                    "type": type_text,
                    "category": category,
                    "minute": (k.get("clock", {}) or {}).get("displayValue"),
                    "period": (k.get("period", {}) or {}).get("number") if isinstance(k.get("period"), dict) else k.get("period"),
                    "team_name": (k.get("team", {}) or {}).get("displayName"),
                    "player": player,
                    "scoring_play": bool(k.get("scoringPlay")),
                }
            )
        return events

    def get_match_cards(self, event_id: str) -> List[Dict]:
        """Solo tarjetas (amarillas y rojas) del partido."""
        return [e for e in self.get_match_events(event_id) if e["category"] in ("yellow_card", "red_card")]

    def get_match_live(self, event_id: str) -> Dict:
        """Marcador en vivo de un partido: goles, estado, reloj y periodo,
        a partir del header del summary de ESPN."""
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/summary?event={event_id}"
        try:
            data = self._get_json(url)
        except Exception as e:
            logger.warning(f"match live {event_id}: {e}")
            return {"event_id": event_id, "status": "unknown"}
        header = data.get("header", {}) or {}
        comp = (header.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})  # type: ignore[var-annotated]
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})  # type: ignore[var-annotated]
        status = comp.get("status", {}) or {}
        st = status.get("type", {}) or {}
        state = "finished" if st.get("completed") else "live" if st.get("state") == "in" else "scheduled"

        def _score(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        return {
            "event_id": event_id,
            "home_team": home.get("team", {}).get("displayName"),
            "away_team": away.get("team", {}).get("displayName"),
            "home_score": _score(home.get("score")),
            "away_score": _score(away.get("score")),
            "status": state,
            "status_detail": st.get("description"),
            "clock": status.get("displayClock"),
            "period": status.get("period"),
        }

    def get_team_season_stats(self, team_id, year: Optional[int] = None) -> Dict:
        """Estadisticas de EQUIPO agregadas de la temporada (via el core API de
        ESPN): ~100+ metricas en 4 categorias (defensive, general, goalKeeping,
        offensive) -> porterias a cero, goles recibidos, pases completados,
        tackles, intercepciones, etc. `year` es el ano-temporada de ESPN."""
        year = year or datetime.now().year
        url = f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/mex.1/seasons/{year}/types/1/teams/{team_id}/statistics"
        try:
            data = self._get_json(url)
        except Exception as e:
            logger.warning(f"team season stats {team_id} {year}: {e}")
            return {"team_id": int(team_id), "season_year": year, "categories": {}}
        categories = {}
        for cat in data.get("splits", {}).get("categories", []):
            name = cat.get("name")
            if not name:
                continue
            categories[name] = {s.get("name"): s.get("value") for s in cat.get("stats", []) if s.get("name")}
        return {"team_id": int(team_id), "season_year": year, "categories": categories}
