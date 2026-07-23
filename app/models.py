from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, JSON, func
from sqlalchemy.orm import relationship
from app.database import Base


class Stadium(Base):
    __tablename__ = "stadiums"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    city = Column(String)
    capacity = Column(Integer, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    teams = relationship("Team", back_populates="stadium")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    short_name = Column(String)
    city = Column(String)
    founded = Column(Integer, nullable=True)
    colors = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    stadium_id = Column(Integer, ForeignKey("stadiums.id"), nullable=True)

    stadium = relationship("Stadium", back_populates="teams")
    players = relationship("Player", back_populates="team")
    home_matches = relationship("Match", foreign_keys="Match.home_team_id", back_populates="home_team")
    away_matches = relationship("Match", foreign_keys="Match.away_team_id", back_populates="away_team")


class Season(Base):
    __tablename__ = "seasons"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    year = Column(Integer)
    tournament_type = Column(String)

    matches = relationship("Match", back_populates="season")
    standings = relationship("Standing", back_populates="season")


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    season_id = Column(Integer, ForeignKey("seasons.id"))
    week_number = Column(Integer, nullable=True)
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    stadium_id = Column(Integer, ForeignKey("stadiums.id"), nullable=True)
    match_date = Column(DateTime, nullable=True)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String, default="scheduled")
    sofascore_event_id = Column(Integer, nullable=True, index=True)
    # Identificador legado de la fuente primaria. Se conserva por compatibilidad,
    # pero el contrato estable y el upsert usan exclusivamente espn_event_id.
    external_event_id = Column(String, nullable=True, index=True)
    espn_event_id = Column(String, nullable=True, unique=True, index=True)
    referee = Column(String, nullable=True)  # arbitro principal (via 365Scores; ESPN no lo expone)
    stage_name = Column(String, nullable=True)  # fase: regular o nombre de la fase final (Liguilla)
    round_name = Column(String, nullable=True)  # ronda: "Fecha N", "Cuartos de Final", "Semifinal", "Final"...

    season = relationship("Season", back_populates="matches")
    stadium = relationship("Stadium")
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    match_events = relationship("MatchEvent", back_populates="match", cascade="all, delete-orphan")
    match_lineups = relationship("MatchLineup", back_populates="match", cascade="all, delete-orphan")


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    name = Column(String, index=True)
    position = Column(String, nullable=True)
    number = Column(Integer, nullable=True)
    nationality = Column(String, nullable=True)
    birth_date = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    flag_url = Column(String, nullable=True)  # bandera del pais (citizenship) via ESPN
    height = Column(String, nullable=True)  # ej "1.85 m"
    weight = Column(String, nullable=True)  # ej "78 kg"
    # id del jugador en 365Scores (fuente de player_match_stats). Permite cruzar
    # stats por id EXACTO en vez de por nombre. Se rellena en el sync (best-effort).
    external_365_id = Column(Integer, nullable=True, index=True)

    team = relationship("Team", back_populates="players")


class Standing(Base):
    __tablename__ = "standings"

    id = Column(Integer, primary_key=True, index=True)
    season_id = Column(Integer, ForeignKey("seasons.id"))
    team_id = Column(Integer, ForeignKey("teams.id"))
    position = Column(Integer)
    played = Column(Integer, default=0)
    won = Column(Integer, default=0)
    drawn = Column(Integer, default=0)
    lost = Column(Integer, default=0)
    goals_for = Column(Integer, default=0)
    goals_against = Column(Integer, default=0)
    goal_difference = Column(Integer, default=0)
    points = Column(Integer, default=0)

    season = relationship("Season", back_populates="standings")
    team = relationship("Team")


class TopScorer(Base):
    __tablename__ = "top_scorers"
    id = Column(Integer, primary_key=True, index=True)
    player = Column(String, index=True)
    team = Column(String, nullable=True)
    goals = Column(Integer, default=0)
    assists = Column(Integer, nullable=True)
    matches = Column(Integer, nullable=True)
    penalties = Column(Integer, nullable=True)
    season = Column(String, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    link = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    source = Column(String)
    image_url = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())


class SyncLog(Base):
    __tablename__ = "sync_logs"
    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, nullable=True)
    status = Column(String, index=True)  # "success" | "error"
    detail = Column(String, nullable=True)
    season = Column(String, nullable=True)
    teams = Column(Integer, nullable=True)
    players = Column(Integer, nullable=True)
    matches = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, default=func.now(), index=True)


class MatchStat(Base):
    __tablename__ = "match_stats"
    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team_name = Column(String, nullable=True)
    event_id = Column(String, nullable=True, index=True)
    season = Column(String, nullable=True)
    possession = Column(Float, nullable=True)
    shots = Column(Integer, nullable=True)
    shots_on_target = Column(Integer, nullable=True)
    corners = Column(Integer, nullable=True)
    fouls = Column(Integer, nullable=True)
    yellow_cards = Column(Integer, nullable=True)
    red_cards = Column(Integer, nullable=True)
    offsides = Column(Integer, nullable=True)
    saves = Column(Integer, nullable=True)
    passes = Column(Integer, nullable=True)
    total_passes = Column(Integer, nullable=True)
    tackles = Column(Integer, nullable=True)
    interceptions = Column(Integer, nullable=True)
    blocked_shots = Column(Integer, nullable=True)
    crosses = Column(Integer, nullable=True)
    long_balls = Column(Integer, nullable=True)
    team = relationship("Team")


class PlayerStat(Base):
    __tablename__ = "player_stats"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    player_name = Column(String, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team_name = Column(String, nullable=True)
    season = Column(String, nullable=True)
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    yellow_cards = Column(Integer, default=0)
    red_cards = Column(Integer, default=0)
    matches_played = Column(Integer, default=0)
    player = relationship("Player", backref="stats")
    team = relationship("Team")


class PlayerMatchStat(Base):
    """Estadisticas COMPLETAS de un jugador en un partido concreto (via 365Scores).

    Es la version granular y persistida de /365scores/matches/{id}/player-stats:
    una fila por jugador por partido. Permite histórico y agregados de temporada
    por SQL. Los campos numericos clave estan promovidos a columnas (para ordenar
    y sumar) y el resto de metricas se guardan completas en `stats` (JSON).
    """

    __tablename__ = "player_match_stats"
    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True, index=True)
    player_id = Column(Integer, nullable=True, index=True)  # id de la fuente (365Scores), sin FK: no coincide con players.id (ESPN)
    player_name = Column(String, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team_name = Column(String, nullable=True)
    season = Column(String, nullable=True, index=True)
    starter = Column(Integer, default=0)
    minutes = Column(Integer, nullable=True)
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    shots = Column(Integer, nullable=True)
    xg = Column(Float, nullable=True)
    xa = Column(Float, nullable=True)
    key_passes = Column(Integer, nullable=True)
    touches = Column(Integer, nullable=True)
    passes_completed = Column(Integer, nullable=True)
    passes_attempted = Column(Integer, nullable=True)
    interceptions = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    stats = Column(JSON, nullable=True)  # dict completo {nombre_metrica: valor}

    match = relationship("Match")
    team = relationship("Team")


class MatchEvent(Base):
    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    sofascore_event_id = Column(Integer, nullable=True)
    event_type = Column(String)
    event_time = Column(Integer, nullable=True)
    player_name = Column(String, nullable=True)
    player_id = Column(Integer, nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team_name = Column(String, nullable=True)
    description = Column(String, nullable=True)
    is_home = Column(Integer, nullable=True)

    match = relationship("Match", back_populates="match_events")


class MatchLineup(Base):
    __tablename__ = "match_lineups"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    sofascore_event_id = Column(Integer, nullable=True)
    player_id = Column(Integer, nullable=True)
    player_name = Column(String, nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team_name = Column(String, nullable=True)
    position = Column(String, nullable=True)
    is_substitute = Column(Integer, default=0)
    jersey_number = Column(Integer, nullable=True)

    match = relationship("Match", back_populates="match_lineups")


class MatchOdds(Base):
    """
    Snapshot de MOMIOS de un partido en un momento dado (histórico de mercado).

    Se guarda de forma denormalizada (por nombres de equipo + fecha) para que el
    cliente pueda archivar sin resolver ids internos. Cada POST es una fila nueva
    con su `captured_at`: es una SERIE TEMPORAL (los momios se mueven), pensada
    para poder backtestear luego la mezcla modelo+mercado con datos reales.
    """

    __tablename__ = "match_odds"

    id = Column(Integer, primary_key=True, index=True)
    season = Column(String, nullable=True, index=True)
    home_team = Column(String, index=True)
    away_team = Column(String, index=True)
    match_date = Column(DateTime, nullable=True)
    source = Column(String, nullable=True)  # p. ej. "odds-api.io", casa
    # 1X2 (cuota decimal)
    odds_local = Column(Float, nullable=True)
    odds_empate = Column(Float, nullable=True)
    odds_visita = Column(Float, nullable=True)
    # Over/Under
    ou_linea = Column(Float, nullable=True)
    odds_over = Column(Float, nullable=True)
    odds_under = Column(Float, nullable=True)
    extra = Column(JSON, nullable=True)  # payload completo (por si acaso)
    captured_at = Column(DateTime, default=func.now(), index=True)
