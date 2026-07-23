from pydantic import BaseModel, ConfigDict, computed_field, field_serializer
from typing import Optional
from datetime import datetime, timezone
from urllib.parse import quote_plus


def utc_isoformat(value: Optional[datetime]) -> Optional[str]:
    """Serializa datetimes almacenados como UTC naive con una zona explicita."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class StadiumBase(BaseModel):
    name: str
    city: Optional[str] = None
    capacity: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class StadiumResponse(StadiumBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def maps_url(self) -> Optional[str]:
        """Link a Google Maps del estadio (por coordenadas si las hay, o por
        nombre+ciudad). Comodo para apps que muestren la sede."""
        if self.latitude is not None and self.longitude is not None:
            return f"https://www.google.com/maps/search/?api=1&query={self.latitude},{self.longitude}"
        if not self.name:
            return None
        query = self.name + (f" {self.city}" if self.city else "")
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)


class TeamBase(BaseModel):
    name: str
    short_name: Optional[str] = None
    city: Optional[str] = None
    colors: Optional[str] = None
    founded: Optional[int] = None
    logo_url: Optional[str] = None


class TeamResponse(TeamBase):
    id: int
    stadium: Optional[StadiumResponse] = None
    model_config = ConfigDict(from_attributes=True)


class PlayerBase(BaseModel):
    name: str
    position: Optional[str] = None
    number: Optional[int] = None
    nationality: Optional[str] = None
    birth_date: Optional[str] = None
    photo_url: Optional[str] = None
    flag_url: Optional[str] = None
    height: Optional[str] = None
    weight: Optional[str] = None


class PlayerResponse(PlayerBase):
    id: int
    team_id: int
    model_config = ConfigDict(from_attributes=True)


class MatchBase(BaseModel):
    home_team_id: int
    away_team_id: int
    match_date: Optional[datetime] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: str = "scheduled"
    week_number: Optional[int] = None
    referee: Optional[str] = None
    sofascore_event_id: Optional[int] = None


class MatchResponse(MatchBase):
    id: int
    espn_event_id: Optional[str] = None
    home_team: Optional[TeamResponse] = None
    away_team: Optional[TeamResponse] = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("match_date", when_used="json")
    def serialize_match_date(self, value: Optional[datetime]) -> Optional[str]:
        return utc_isoformat(value)


class StandingResponse(BaseModel):
    position: int
    team: TeamResponse
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    model_config = ConfigDict(from_attributes=True)


class TopScorerResponse(BaseModel):
    id: int
    player: str
    team: Optional[str] = None
    goals: int
    assists: Optional[int] = None
    matches: Optional[int] = None
    penalties: Optional[int] = None
    season: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class NewsResponse(BaseModel):
    id: int
    title: str
    link: str
    description: Optional[str] = None
    source: Optional[str] = None
    image_url: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class MatchStatResponse(BaseModel):
    id: int
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    event_id: Optional[str] = None
    season: Optional[str] = None
    possession: Optional[float] = None
    shots: Optional[int] = None
    shots_on_target: Optional[int] = None
    corners: Optional[int] = None
    fouls: Optional[int] = None
    yellow_cards: Optional[int] = None
    red_cards: Optional[int] = None
    offsides: Optional[int] = None
    saves: Optional[int] = None
    passes: Optional[int] = None
    total_passes: Optional[int] = None
    tackles: Optional[int] = None
    interceptions: Optional[int] = None
    blocked_shots: Optional[int] = None
    crosses: Optional[int] = None
    long_balls: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class PlayerStatResponse(BaseModel):
    id: int
    player_id: Optional[int] = None
    player_name: str
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    season: Optional[str] = None
    goals: int
    assists: int
    yellow_cards: int
    red_cards: int
    matches_played: int
    model_config = ConfigDict(from_attributes=True)


class MatchEventResponse(BaseModel):
    id: int
    event_type: str
    event_time: Optional[int] = None
    player_name: Optional[str] = None
    player_id: Optional[int] = None
    team_name: Optional[str] = None
    team_id: Optional[int] = None
    description: Optional[str] = None
    is_home: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class MatchLineupResponse(BaseModel):
    id: int
    player_name: Optional[str] = None
    player_id: Optional[int] = None
    team_name: Optional[str] = None
    team_id: Optional[int] = None
    position: Optional[str] = None
    is_substitute: int = 0
    jersey_number: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class PlayerMatchStatResponse(BaseModel):
    id: int
    match_id: Optional[int] = None
    player_id: Optional[int] = None
    player_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    season: Optional[str] = None
    starter: int = 0
    minutes: Optional[int] = None
    goals: int = 0
    assists: int = 0
    shots: Optional[int] = None
    xg: Optional[float] = None
    xa: Optional[float] = None
    key_passes: Optional[int] = None
    touches: Optional[int] = None
    passes_completed: Optional[int] = None
    passes_attempted: Optional[int] = None
    interceptions: Optional[int] = None
    rating: Optional[float] = None
    stats: Optional[dict] = None
    model_config = ConfigDict(from_attributes=True)


class MatchOddsCreate(BaseModel):
    """Snapshot de momios de un partido para archivar (POST /odds)."""

    home_team: str
    away_team: str
    season: Optional[str] = None
    match_date: Optional[datetime] = None
    source: Optional[str] = None
    odds_local: Optional[float] = None
    odds_empate: Optional[float] = None
    odds_visita: Optional[float] = None
    ou_linea: Optional[float] = None
    odds_over: Optional[float] = None
    odds_under: Optional[float] = None
    extra: Optional[dict] = None


class MatchOddsResponse(MatchOddsCreate):
    id: int
    captured_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
