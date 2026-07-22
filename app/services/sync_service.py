from datetime import datetime, timedelta, timezone
import logging
import os
import re
import time
from typing import List, Dict, Any
from app.scrapers.factory import get_scraper
from app.scrapers.news_scraper import fetch_news
from app.scrapers.sync_all_stats import sync_all_stats
from app.scrapers.sofascore_scraper import get_events as get_sofascore_events
from app.scrapers import extras_scraper
from app.season import current_tournament, tournament_from_matches
from app import models

logger = logging.getLogger(__name__)

def _validate_season(detected_tournament: str, detected_year: int):
    """Red de seguridad: verifica que los datos detectados correspondan al
    torneo/ano esperado ANTES de tocar la BD. Si no coinciden, se aborta el
    sync para no sobreescribir datos buenos con los de un torneo equivocado.

    El esperado se calcula de la fecha del sistema, pero puede forzarse con
    variables de entorno (utiles para pruebas o casos borde de calendario):
      - EXPECTED_SEASON_YEAR (p. ej. "2026")
      - EXPECTED_TOURNAMENT  (p. ej. "Apertura")
    """
    exp_tournament, exp_year = current_tournament()
    if os.getenv("EXPECTED_SEASON_YEAR"):
        try:
            exp_year = int(os.getenv("EXPECTED_SEASON_YEAR"))  # type: ignore[arg-type]
        except ValueError:
            logger.warning("EXPECTED_SEASON_YEAR no es un entero valido; se ignora")
    if os.getenv("EXPECTED_TOURNAMENT"):
        exp_tournament = os.getenv("EXPECTED_TOURNAMENT")

    # El ano es la senal mas fuerte: si no coincide, abortamos.
    if detected_year != exp_year:
        raise ValueError(
            f"Sync abortado: ano detectado en los datos ({detected_tournament} "
            f"{detected_year}) != esperado ({exp_tournament} {exp_year}). "
            f"Se conservan los datos previos. Si es intencional, define "
            f"EXPECTED_SEASON_YEAR={detected_year}."
        )

    # Diferencia de torneo (mismo ano): solo advertencia (casos borde de calendario).
    if detected_tournament != exp_tournament:
        logger.warning(
            f"Torneo detectado ({detected_tournament}) distinto al esperado "
            f"({exp_tournament}) para {exp_year}; se continua pero revisa la fuente."
        )

def calculate_week_numbers(matches: List[Dict[str, Any]]):
    """Asigna numeros de jornada basandose en la fecha.
    La jornada de Liga MX va de viernes a jueves."""
    matches_with_date = [m for m in matches if m.get("match_date")]
    if not matches_with_date:
        return

    def week_start(date):
        days_since_friday = (date.weekday() - 4) % 7
        return (date - timedelta(days=days_since_friday)).date()

    matches_with_date.sort(key=lambda m: m["match_date"])

    groups: dict[str, Any] = {}
    for m in matches_with_date:
        ws = week_start(m["match_date"])
        groups.setdefault(ws, []).append(m)

    sorted_weeks = sorted(groups.keys())
    week_number_by_start = {ws: i + 1 for i, ws in enumerate(sorted_weeks)}

    for m in matches_with_date:
        ws = week_start(m["match_date"])
        m["week"] = week_number_by_start[ws]


def compute_standings_from_matches(matches):
    """Calcula la tabla general a partir de los partidos JUGADOS (3 pts victoria,
    1 empate). Util para el backfill de temporadas pasadas: en vez de depender de
    la tabla 'por ano' de ESPN (que mezcla los dos torneos), reconstruimos la
    clasificacion real del torneo desde sus propios resultados.
    Orden: puntos, luego diferencia de goles, luego goles a favor."""
    agg: dict[str, Any] = {}

    def row(name):
        return agg.setdefault(name, {
            "team_name": name, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "goals_for": 0, "goals_against": 0, "points": 0,
        })

    for m in matches:
        if m.get("status") != "finished":
            continue
        hs, as_ = m.get("home_score"), m.get("away_score")
        h, a = m.get("home_team"), m.get("away_team")
        if hs is None or as_ is None or not h or not a:
            continue
        rh, ra = row(h), row(a)
        rh["played"] += 1
        ra["played"] += 1
        rh["goals_for"] += hs
        rh["goals_against"] += as_
        ra["goals_for"] += as_
        ra["goals_against"] += hs
        if hs > as_:
            rh["won"] += 1
            ra["lost"] += 1
            rh["points"] += 3
        elif as_ > hs:
            ra["won"] += 1
            rh["lost"] += 1
            ra["points"] += 3
        else:
            rh["drawn"] += 1
            ra["drawn"] += 1
            rh["points"] += 1
            ra["points"] += 1

    rows = list(agg.values())
    for r in rows:
        r["goal_difference"] = r["goals_for"] - r["goals_against"]
    rows.sort(key=lambda r: (r["points"], r["goal_difference"], r["goals_for"]), reverse=True)
    for i, r in enumerate(rows):
        r["position"] = i + 1
    return rows

def _teams_match(name1: str, name2: str) -> bool:
    """Compara nombres de equipos de ESPN y SofaScore."""
    if not name1 or not name2:
        return False
    n1 = name1.lower().replace("fc", "").replace("cf", "").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").strip()
    n2 = name2.lower().replace("fc", "").replace("cf", "").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").strip()
    return n1 in n2 or n2 in n1 or n1.split()[0] in n2 or n2.split()[0] in n1

def _sync_sofascore_event_ids(db):
    """Busca y guarda sofascore_event_id para cada partido."""
    try:
        sofascore_events = get_sofascore_events()
        logger.info(f"SofaScore events found: {len(sofascore_events)}")
        
        matches = db.query(models.Match).all()
        updated = 0
        
        for match in matches:
            home_team = match.home_team.name if match.home_team else None
            away_team = match.away_team.name if match.away_team else None
            
            if not home_team or not away_team:
                continue
            
            for event in sofascore_events:
                event_home = event.get("homeTeam", {}).get("name", "")
                event_away = event.get("awayTeam", {}).get("name", "")
                
                if _teams_match(home_team, event_home) and _teams_match(away_team, event_away):
                    match.sofascore_event_id = event.get("id")
                    db.add(match)
                    updated += 1
                    break
        
        db.commit()
        logger.info(f"SofaScore event IDs updated: {updated}")
    except Exception as e:
        logger.warning(f"SofaScore event ID sync failed: {e}")

_S_MIN, _S_GOALS, _S_ASSIST, _S_XG, _S_XA = 30, 27, 26, 76, 78
_S_SHOTS, _S_KEYP, _S_TOUCH, _S_PASSC, _S_INT = 3, 46, 45, 19, 41


def _stat_int(v):
    if v is None:
        return None
    m = re.match(r"\s*(-?\d+)", str(v))
    return int(m.group(1)) if m else None


def _stat_float(v):
    if v is None:
        return None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def _stat_fraction(v):
    """'21/26 (81%)' -> (21, 26). '5' -> (5, None)."""
    if v is None:
        return (None, None)
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", str(v))
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (_stat_int(v), None)


def _find_365_game(games, home, away, match_date):
    """Empareja un partido de la BD con un juego de 365Scores por nombres de
    equipo y fecha (tolerancia de 2 dias)."""
    for g in games:
        if not (_teams_match(home, g.get("home_team")) and _teams_match(away, g.get("away_team"))):
            continue
        gd = g["match_date"].date() if g.get("match_date") else None
        if match_date and gd and abs((match_date - gd).days) > 2:
            continue
        return g
    return None


def _sync_365_match_details(db, season, season_id=None):
    """En UNA sola pasada por 365Scores (un request por partido jugado), persiste:
      - el arbitro de cada partido (ESPN no lo expone), y
      - las estadisticas COMPLETAS por jugador (tabla player_match_stats).
    Empareja los partidos por nombres de equipo + fecha. Best-effort y aislado:
    un fallo no invalida el resto del sync."""
    try:
        from app.scrapers.scores365_scraper import Scores365Scraper
        scraper = Scores365Scraper()
        games = [g for g in scraper.get_matches() if g.get("status") == "finished"]
        if not games:
            return
        # Reemplazo idempotente de las stats por jugador de esta temporada.
        db.query(models.PlayerMatchStat).filter(models.PlayerMatchStat.season == season).delete()
        db.commit()

        q = db.query(models.Match).filter(models.Match.status == "finished")
        if season_id is not None:
            q = q.filter(models.Match.season_id == season_id)
        db_matches = q.all()
        refs = rows = 0
        for dm in db_matches:
            home = dm.home_team.name if dm.home_team else None
            away = dm.away_team.name if dm.away_team else None
            if not home or not away:
                continue
            md = dm.match_date.date() if dm.match_date else None
            g = _find_365_game(games, home, away, md)
            if not g:
                continue
            try:
                game = scraper.get_game(g["event_id"])
            except Exception as e:
                logger.warning(f"365 detalle del juego {g.get('event_id')} fallo: {e}")
                continue

            # Arbitro
            if dm.referee is None:
                try:
                    info = scraper.get_match_info(g["event_id"], game=game)
                    if info.get("referee"):
                        dm.referee = info["referee"]
                        db.add(dm)
                        refs += 1
                except Exception:
                    pass

            # Stats por jugador (todos los jugadores con minutos, no solo goleadores)
            try:
                pdata = scraper.get_match_player_stats(g["event_id"], game=game)
            except Exception:
                pdata = {"teams": []}
            for team in pdata.get("teams", []):
                # team_id de NUESTRA BD segun local/visitante (los ids de 365 no coinciden)
                db_team_id = dm.home_team_id if team.get("home_away") == "home" else dm.away_team_id
                for p in team.get("players", []):
                    bt = p.get("stats_by_type") or {}
                    pc, pa = _stat_fraction(bt.get(_S_PASSC))
                    db.add(models.PlayerMatchStat(
                        match_id=dm.id,
                        player_id=p.get("player_id"),
                        player_name=p.get("name"),
                        team_id=db_team_id,
                        team_name=team.get("team_name"),
                        season=season,
                        starter=1 if p.get("starter") else 0,
                        minutes=_stat_int(bt.get(_S_MIN)),
                        goals=_stat_int(bt.get(_S_GOALS)) or 0,
                        assists=_stat_int(bt.get(_S_ASSIST)) or 0,
                        shots=_stat_int(bt.get(_S_SHOTS)),
                        xg=_stat_float(bt.get(_S_XG)),
                        xa=_stat_float(bt.get(_S_XA)),
                        key_passes=_stat_int(bt.get(_S_KEYP)),
                        touches=_stat_int(bt.get(_S_TOUCH)),
                        passes_completed=pc,
                        passes_attempted=pa,
                        interceptions=_stat_int(bt.get(_S_INT)),
                        rating=p.get("rating"),
                        stats=p.get("stats") or None,
                    ))
                    rows += 1
            time.sleep(0.1)
        db.commit()
        logger.info(f"365 detalle: {refs} arbitros, {rows} filas de stats por jugador")
    except Exception as e:
        db.rollback()
        logger.warning(f"Sync de detalle 365 fallo (no critico): {e}")


def _enrich_team_assets(db):
    """Enriquece equipos y estadios con datos de TheSportsDB (cruzando por
    idESPN): ano de fundacion, escudo (si falta) y capacidad del estadio.
    Fuente complementaria, no critica."""
    try:
        assets = extras_scraper.get_team_assets()
        if not assets:
            return
        by_espn = {a["espn_team_id"]: a for a in assets if a.get("espn_team_id")}
        updated_teams = 0
        updated_stadiums = 0
        for team in db.query(models.Team).all():
            a = by_espn.get(team.id)
            if not a:
                continue
            if a.get("founded") and not team.founded:
                team.founded = a["founded"]
            if a.get("badge") and not team.logo_url:
                team.logo_url = a["badge"]
            db.add(team)
            updated_teams += 1
            if team.stadium and a.get("stadium_capacity") and not team.stadium.capacity:
                team.stadium.capacity = a["stadium_capacity"]
                db.add(team.stadium)
                updated_stadiums += 1
        db.commit()
        logger.info(f"TheSportsDB enrich: {updated_teams} equipos, {updated_stadiums} estadios")
    except Exception as e:
        db.rollback()
        logger.warning(f"Enriquecimiento TheSportsDB fallo (no critico): {e}")


def _parse_minute(value):
    """Extrae el minuto (entero) de cadenas como "45'", "90'+2", "45+1'"."""
    if value is None:
        return None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


def _sync_events_and_lineups(db, scraper, match_map: Dict[str, Dict], tmap: Dict):
    """Persiste, por cada partido jugado, sus EVENTOS (goles, tarjetas amarillas
    y rojas, cambios) y sus ALINEACIONES (titulares y suplentes con posicion y
    dorsal). Las tablas match_events/match_lineups ya fueron vaciadas en WRITE.

    match_map: { event_id(str): {match_id, home, away} }.
    Tolerante a fallos por partido para no abortar todo el sync.
    """
    if not match_map:
        logger.info("Sin partidos jugados para detallar (eventos/alineaciones)")
        return
    n_events = n_lineups = 0
    for eid, info in match_map.items():
        mid, home = info["match_id"], info.get("home")
        # Eventos
        try:
            for ev in (scraper.get_match_events(eid) or []):
                tname = ev.get("team_name")
                db.add(models.MatchEvent(
                    match_id=mid,
                    event_type=ev.get("category") or "other",
                    event_time=_parse_minute(ev.get("minute")),
                    player_name=ev.get("player"),
                    team_id=tmap.get(tname),
                    team_name=tname,
                    description=ev.get("type"),
                    is_home=(1 if tname and tname == home else 0) if tname else None,
                ))
                n_events += 1
        except Exception as e:
            logger.warning(f"Eventos del partido {eid} fallaron: {e}")
        # Alineaciones
        try:
            lineups = scraper.get_match_lineups(eid) or {}
            for team in lineups.get("teams", []):
                tname = team.get("team_name")
                tid = tmap.get(tname)
                for group, is_sub in (("starters", 0), ("substitutes", 1)):
                    for pl in team.get(group, []) or []:
                        db.add(models.MatchLineup(
                            match_id=mid,
                            player_id=pl.get("player_id"),
                            player_name=pl.get("name"),
                            team_id=tid,
                            team_name=tname,
                            position=pl.get("position"),
                            is_substitute=is_sub,
                            jersey_number=pl.get("jersey"),
                        ))
                        n_lineups += 1
        except Exception as e:
            logger.warning(f"Alineaciones del partido {eid} fallaron: {e}")
        time.sleep(0.1)
    db.commit()
    logger.info(f"Persistidos {n_events} eventos y {n_lineups} jugadores en alineaciones")


def _upsert_stadiums(db, stadiums):
    """Crea o actualiza estadios por nombre (no se borran: son compartidos
    entre temporadas)."""
    smap = {}
    for s in stadiums:
        name = s.get("name")
        if not name:
            continue
        st = db.query(models.Stadium).filter(models.Stadium.name == name).first()
        if st is None:
            st = models.Stadium(name=name)
            db.add(st)
        st.city = s.get("city") or st.city
        if s.get("capacity"):
            st.capacity = s["capacity"]
        db.flush()
        smap[name] = st.id
    return smap


def _upsert_teams(db, teams, smap):
    """Crea o actualiza equipos por id. No se borran para no romper los partidos
    de temporadas anteriores. Los campos enriquecidos (founded/logo) solo se
    sobreescriben si llega un valor nuevo no vacio."""
    tmap, team_stadium = {}, {}
    for t in teams:
        tm = db.get(models.Team, t["id"])
        if tm is None:
            tm = models.Team(id=t["id"])
            db.add(tm)
        tm.name = t["name"]
        tm.short_name = t.get("short_name") or tm.short_name
        tm.city = t.get("city") or tm.city
        tm.colors = t.get("colors") or tm.colors
        if t.get("founded"):
            tm.founded = t["founded"]
        if t.get("logo_url"):
            tm.logo_url = t["logo_url"]
        sid = smap.get(t.get("stadium_name"))
        if sid:
            tm.stadium_id = sid
        db.flush()
        tmap[t["name"]] = tm.id
        tmap[t["id"]] = tm.id
        team_stadium[tm.id] = tm.stadium_id
    return tmap, team_stadium


def _upsert_players(db, players, tmap):
    """Crea o actualiza jugadores por id (compartidos entre temporadas)."""
    for p in players:
        if not p.get("id"):
            continue
        pl = db.get(models.Player, p["id"])
        if pl is None:
            pl = models.Player(id=p["id"])
            db.add(pl)
        pl.name = p["name"]
        pl.position = p.get("position") or pl.position
        if p.get("number") is not None:
            pl.number = p["number"]
        pl.nationality = p.get("nationality") or pl.nationality
        pl.birth_date = p.get("birth_date") or pl.birth_date
        pl.photo_url = p.get("photo_url") or pl.photo_url
        pl.flag_url = p.get("flag_url") or pl.flag_url
        pl.height = p.get("height") or pl.height
        pl.weight = p.get("weight") or pl.weight
        tid = tmap.get(p.get("team_name"))
        if tid:
            pl.team_id = tid


def _write_season_data(db, *, stadiums, teams, players, matches, standings, tournament, year):
    """Escribe los datos de UNA temporada de forma NO destructiva para las demas:
      - estadios/equipos/jugadores -> upsert (se actualizan, no se borran),
      - partidos/tabla/stats de ESTA temporada -> se reemplazan (delete acotado
        por temporada + insert).
    Esto permite acumular varias temporadas (historico) en vez de pisarlas.
    Devuelve (season, tmap, team_stadium, match_objs, season_label)."""
    season_label = f"{tournament} {year}"

    smap = _upsert_stadiums(db, stadiums)
    tmap, team_stadium = _upsert_teams(db, teams, smap)
    _upsert_players(db, players, tmap)

    # Temporada: reutiliza la fila si ya existe (no la duplica en cada sync)
    sn = db.query(models.Season).filter(models.Season.name == season_label).first()
    if sn is None:
        sn = models.Season(name=season_label, year=year, tournament_type=tournament)
        db.add(sn)
        db.flush()

    # Reemplazo SOLO de esta temporada. Primero los hijos de los partidos viejos
    # (tienen FK a matches), luego partidos y tabla, y por ultimo las stats por
    # etiqueta de temporada. Las otras temporadas quedan intactas.
    old_match_ids = [mid for (mid,) in db.query(models.Match.id).filter(models.Match.season_id == sn.id).all()]
    if old_match_ids:
        for child in (models.MatchEvent, models.MatchLineup, models.PlayerMatchStat):
            db.query(child).filter(child.match_id.in_(old_match_ids)).delete(synchronize_session=False)
    db.query(models.Match).filter(models.Match.season_id == sn.id).delete(synchronize_session=False)
    db.query(models.Standing).filter(models.Standing.season_id == sn.id).delete(synchronize_session=False)
    for m in (models.MatchStat, models.PlayerStat, models.TopScorer):
        db.query(m).filter(m.season == season_label).delete(synchronize_session=False)
    db.flush()

    match_objs = []  # (Match, raw_dict) para enlazar eventos/alineaciones luego
    for m in matches:
        hid = tmap.get(m.get("home_team_id")) or tmap.get(m.get("home_team"))
        aid = tmap.get(m.get("away_team_id")) or tmap.get(m.get("away_team"))
        if hid and aid:
            eid = m.get("event_id")
            mo = models.Match(
                season_id=sn.id,
                home_team_id=hid,
                away_team_id=aid,
                stadium_id=team_stadium.get(hid),
                match_date=m.get("match_date"),
                home_score=m.get("home_score"),
                away_score=m.get("away_score"),
                status=m.get("status", "scheduled"),
                week_number=m.get("week"),
                stage_name=m.get("stage_name"),
                round_name=m.get("round_name"),
                external_event_id=str(eid) if eid is not None else None,
            )
            db.add(mo)
            match_objs.append((mo, m))

    for s in standings:
        db.add(models.Standing(
            season_id=sn.id,
            team_id=tmap.get(s.get("team_name")),
            position=s["position"],
            played=s["played"],
            won=s["won"],
            drawn=s["drawn"],
            lost=s["lost"],
            goals_for=s["goals_for"],
            goals_against=s["goals_against"],
            goal_difference=s["goals_for"] - s["goals_against"],
            points=s["points"],
        ))

    return sn, tmap, team_stadium, match_objs, season_label


def run_sync(db, source: str = "espn"):
    """Ejecuta la sincronizacion completa de datos.

    Estrategia segura:
      1. FETCH: se descargan TODOS los datos externos a memoria primero.
         Si alguna fuente critica falla, se aborta sin tocar la BD,
         conservando los datos previos intactos.
      2. WRITE: borrado + insercion dentro de UNA sola transaccion.
         Si la escritura falla, se hace rollback y los datos viejos quedan.
      3. ENRICH: stats avanzados, SofaScore y noticias se ejecutan de forma
         aislada; un fallo en estas etapas NO invalida el sync principal.
    """
    logger.info(f"Iniciando sincronizacion desde {source}")
    scraper = get_scraper(source)

    # -------- FASE 1: FETCH (sin tocar la BD) --------
    # Si algo critico falla aqui, abortamos antes de borrar nada.
    try:
        raw_stadiums = scraper.get_stadiums()
        raw_teams = scraper.get_teams()
        raw_players = scraper.get_players()
        raw_matches = scraper.get_matches()
        raw_standings = scraper.get_standings()
    except Exception as e:
        logger.error(f"Sync abortado: fallo al obtener datos de {source}: {e}. "
                     f"Los datos previos se conservan intactos.")
        raise

    if not raw_teams or not raw_matches:
        logger.error("Sync abortado: el scraper no devolvio equipos/partidos. "
                     "Los datos previos se conservan intactos.")
        raise ValueError("Datos insuficientes del scraper; se aborta para no vaciar la BD")

    calculate_week_numbers(raw_matches)
    # El torneo/ano se deduce de las fechas REALES de los partidos cargados
    # (p. ej. partidos jul-dic 2026 => "Apertura 2026"), no del mes actual.
    tournament, current_year = tournament_from_matches(raw_matches)
    logger.info(f"Temporada detectada de los datos: {tournament} {current_year}")

    # Red de seguridad: aborta (sin tocar la BD) si no es el torneo/ano esperado.
    _validate_season(tournament, current_year)

    # -------- FASE 2: WRITE (una sola transaccion, NO destructiva entre temporadas) --------
    try:
        sn, tmap, team_stadium, match_objs, season_label = _write_season_data(
            db,
            stadiums=raw_stadiums,
            teams=raw_teams,
            players=raw_players,
            matches=raw_matches,
            standings=raw_standings,
            tournament=tournament,
            year=current_year,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Sync fallo durante la escritura, rollback aplicado. "
                     f"Los datos previos se conservan: {e}")
        raise

    # -------- FASE 3: ENRICH (aislado, no critico) --------
    # Escudos, fundacion y capacidad via TheSportsDB (cruce por idESPN)
    _enrich_team_assets(db)

    # Stats avanzados (clave de temporada = etiqueta completa, p. ej. "Apertura 2026")
    try:
        sync_all_stats(db, raw_matches, scraper, tmap, season_label)
    except Exception as e:
        db.rollback()
        logger.warning(f"sync_all_stats fallo (no critico): {e}")

    # SofaScore event IDs (puede fallar por bloqueo de Cloudflare/403)
    _sync_sofascore_event_ids(db)

    # Detalle 365Scores: arbitros + stats completas por jugador (un request/partido)
    _sync_365_match_details(db, season_label, sn.id)

    # Cruce de identidad ESPN<->365Scores: rellena players.external_365_id para
    # poder emparejar stats por id exacto (no por nombre). Best-effort.
    try:
        from app.services.player_identity import build_player_identity_map
        res = build_player_identity_map(db, season_label)
        logger.info(f"Identidad de jugadores: {res['mapped']}/{res['sources_365']} mapeados")
    except Exception as e:
        db.rollback()
        logger.warning(f"Cruce de identidad de jugadores fallo (no critico): {e}")

    # Detalle por partido: eventos (goles/tarjetas/cambios) y alineaciones.
    # Se enlaza por el id externo del partido (solo partidos jugados).
    try:
        match_map = {}
        for mo, raw in match_objs:
            if raw.get("status") in ("finished", "live") and raw.get("event_id") is not None:
                match_map[str(raw["event_id"])] = {
                    "match_id": mo.id,
                    "home": raw.get("home_team"),
                    "away": raw.get("away_team"),
                }
        _sync_events_and_lineups(db, scraper, match_map, tmap)
    except Exception as e:
        db.rollback()
        logger.warning(f"Sync de eventos/alineaciones fallo (no critico): {e}")

    # News sync (aislado para no invalidar el resto): combina RSS + 365Scores.
    try:
        news_items = fetch_news(limit=50)
        # 365Scores: feed propio de Liga MX (con imagen)
        try:
            from app.scrapers.scores365_scraper import Scores365Scraper

            def _parse_365_date(v):
                if not v:
                    return None
                try:
                    return datetime.fromisoformat(v).replace(tzinfo=None)
                except (ValueError, TypeError):
                    return None

            for n in Scores365Scraper().get_news(limit=30):
                if n.get("url") and n.get("title"):
                    news_items.append({
                        "title": n["title"],
                        "link": n["url"],
                        "description": n["title"],
                        "source": "365Scores",
                        "image_url": n.get("image"),
                        "published_at": _parse_365_date(n.get("published_at")),
                    })
        except Exception as e:
            logger.warning(f"Noticias 365Scores fallaron (no critico): {e}")

        # Dedup por enlace
        seen, unique = set(), []
        for n in news_items:
            link = n.get("link")
            if not link or link in seen:
                continue
            seen.add(link)
            unique.append(n)

        db.query(models.News).delete()
        for n in unique:
            db.add(models.News(
                title=n.get("title"), link=n.get("link"), description=n.get("description"),
                source=n.get("source"), image_url=n.get("image_url"), published_at=n.get("published_at"),
            ))
        db.commit()
        logger.info(f"Noticias sincronizadas: {len(unique)} (RSS + 365Scores)")
    except Exception as e:
        db.rollback()
        logger.warning(f"Sync de noticias fallo (no critico): {e}")

    logger.info("Sincronizacion completada")

    return {
        "stadiums": len(raw_stadiums),
        "teams": len(raw_teams),
        "players": len(db.query(models.Player).all()),
        "matches": db.query(models.Match).filter(models.Match.season_id == sn.id).count(),
        "season": season_label,
    }


def run_sync_with_log(db, source: str = "espn"):
    """Ejecuta run_sync y registra el resultado (exito o error) en sync_logs.
    Devuelve el resultado del sync; relanza la excepcion si falla."""
    started = datetime.now(timezone.utc)
    try:
        result = run_sync(db, source)
    except Exception as e:
        try:
            db.rollback()
            db.add(models.SyncLog(
                source=source, status="error", detail=str(e)[:500],
                started_at=started,
                duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
            ))
            db.commit()
        except Exception:
            db.rollback()
        raise
    try:
        db.add(models.SyncLog(
            source=source, status="success", detail="ok",
            season=result.get("season"),
            teams=result.get("teams"), players=result.get("players"),
            matches=result.get("matches"),
            started_at=started,
            duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
        ))
        db.commit()
    except Exception:
        db.rollback()
    return result



def run_backfill(db, year: int, tournament: str, source: str = "espn"):
    """Carga (backfill) una temporada PASADA concreta sin tocar las demas.

    A diferencia de run_sync (que detecta el torneo vigente y enriquece con datos
    en vivo), aqui el operador indica explicitamente el torneo+ano. Reusa el WRITE
    no destructivo (_write_season_data), por lo que acumula la temporada en el
    historico. La tabla se RECONSTRUYE desde los resultados (no se pide a ESPN).

    No enriquece con 365Scores/noticias (esos datos solo existen para lo reciente).
    """
    if tournament not in ("Apertura", "Clausura"):
        raise ValueError("tournament debe ser 'Apertura' o 'Clausura'")
    logger.info(f"Backfill de {tournament} {year} desde {source}")
    scraper = get_scraper(source)

    try:
        raw_stadiums = scraper.get_stadiums()
        raw_teams = scraper.get_teams()
        raw_players = scraper.get_players()
        raw_matches = scraper.get_matches(season_id=year, tournament=tournament)
    except Exception as e:
        logger.error(f"Backfill abortado: fallo al obtener datos: {e}")
        raise

    if not raw_matches:
        raise ValueError(f"Sin partidos para {tournament} {year}; no se backfillea nada")

    calculate_week_numbers(raw_matches)
    standings = compute_standings_from_matches(raw_matches)

    try:
        sn, tmap, team_stadium, match_objs, season_label = _write_season_data(
            db,
            stadiums=raw_stadiums,
            teams=raw_teams,
            players=raw_players,
            matches=raw_matches,
            standings=standings,
            tournament=tournament,
            year=year,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Backfill fallo durante la escritura, rollback aplicado: {e}")
        raise

    finished = sum(1 for m in raw_matches if m.get("status") == "finished")
    logger.info(f"Backfill {season_label} OK: {len(raw_matches)} partidos, tabla de {len(standings)} equipos")
    return {
        "season": season_label,
        "teams": len(raw_teams),
        "matches": db.query(models.Match).filter(models.Match.season_id == sn.id).count(),
        "finished_matches": finished,
        "standings_rows": len(standings),
    }


def run_backfill_with_log(db, year: int, tournament: str, source: str = "espn"):
    """Ejecuta run_backfill y registra el resultado en sync_logs."""
    started = datetime.now(timezone.utc)
    try:
        result = run_backfill(db, year, tournament, source)
    except Exception as e:
        try:
            db.rollback()
            db.add(models.SyncLog(
                source=f"backfill:{tournament} {year}", status="error", detail=str(e)[:500],
                started_at=started,
                duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
            ))
            db.commit()
        except Exception:
            db.rollback()
        raise
    try:
        db.add(models.SyncLog(
            source=f"backfill:{source}", status="success", detail="ok",
            season=result.get("season"), teams=result.get("teams"),
            matches=result.get("matches"),
            started_at=started,
            duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
        ))
        db.commit()
    except Exception:
        db.rollback()
    return result
