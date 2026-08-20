from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    external_id: str
    first_name: str
    last_name: str
    primary_position: str
    nhl_team: str | None = None


@dataclass(frozen=True, slots=True)
class TeamSnapshot:
    external_id: str
    name: str
    is_user_team: bool
    players: tuple[PlayerSnapshot, ...]


@dataclass(frozen=True, slots=True)
class StatCategorySnapshot:
    abbr: str
    display_name: str


@dataclass(frozen=True, slots=True)
class LeagueSnapshot:
    source: str
    external_id: str
    name: str
    teams: tuple[TeamSnapshot, ...]
    stat_categories: tuple[StatCategorySnapshot, ...]
