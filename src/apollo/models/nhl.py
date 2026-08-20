from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NHLStat:
    name: str
    value: float


@dataclass(frozen=True, slots=True)
class NHLPlayerData:
    nhl_player_id: int
    first_name: str
    last_name: str
    team_abbrev: str | None
    position: str | None
    is_active: bool
    sweater_number: int | None
    birth_date: str | None
    season: int | None
    stats: tuple[NHLStat, ...]


@dataclass(frozen=True, slots=True)
class NHLGame:
    game_id: int
    season: int
    game_type: int
    game_date: str
    start_time_utc: str | None
    away_team: str | None
    home_team: str | None
    game_state: str | None


@dataclass(frozen=True, slots=True)
class NHLGameLogEntry:
    game: NHLGame
    team_abbrev: str | None
    opponent_abbrev: str | None
    home_road: str | None
    stats: tuple[NHLStat, ...]
