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
