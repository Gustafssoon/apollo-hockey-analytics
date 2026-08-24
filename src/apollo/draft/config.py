from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_ROSTER_SLOTS = ("C", "LW", "RW", "D", "G", "BN")
_REQUIRED_SKATER_STATS = ("G", "A", "PPP", "SOG", "HIT", "BLK")
_REQUIRED_GOALIE_STATS = ("W", "SV", "GA", "SO")


class DraftConfigError(ValueError):
    """Raised when a draft configuration cannot be loaded or validated."""


@dataclass(frozen=True, slots=True)
class LeagueConfig:
    name: str
    teams: int


@dataclass(frozen=True, slots=True)
class DraftSettings:
    draft_type: str
    my_slot: int
    rounds: int


@dataclass(frozen=True, slots=True)
class RosterSlot:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class ScoringCategory:
    stat: str
    points: float


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    skaters: tuple[ScoringCategory, ...]
    goalies: tuple[ScoringCategory, ...]


@dataclass(frozen=True, slots=True)
class DraftLeagueConfig:
    league: LeagueConfig
    draft: DraftSettings
    roster: tuple[RosterSlot, ...]
    scoring: ScoringConfig


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DraftConfigError(f"{context} must be a mapping")
    return value


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in data:
        raise DraftConfigError(f"Missing required section: {key}")
    return _require_mapping(data[key], key)


def _required_string(data: Mapping[str, Any], key: str, context: str) -> str:
    if key not in data:
        raise DraftConfigError(f"Missing required value: {context}.{key}")
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise DraftConfigError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _required_int(data: Mapping[str, Any], key: str, context: str) -> int:
    if key not in data:
        raise DraftConfigError(f"Missing required value: {context}.{key}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise DraftConfigError(f"{context}.{key} must be an integer")
    return value


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DraftConfigError(f"{context} must be a number")
    return float(value)


def _load_roster(data: Mapping[str, Any]) -> tuple[RosterSlot, ...]:
    missing = [slot for slot in _REQUIRED_ROSTER_SLOTS if slot not in data]
    if missing:
        raise DraftConfigError(f"Missing required roster slots: {', '.join(missing)}")

    slots: list[RosterSlot] = []
    for name, value in data.items():
        if not isinstance(name, str) or not name.strip():
            raise DraftConfigError("Roster slot names must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int):
            raise DraftConfigError(f"roster.{name} must be an integer")
        if value < 0:
            raise DraftConfigError(f"roster.{name} must be >= 0")
        slots.append(RosterSlot(name=name.strip(), count=value))
    return tuple(slots)


def _load_scoring_categories(
    data: Mapping[str, Any],
    context: str,
    required: tuple[str, ...],
) -> tuple[ScoringCategory, ...]:
    missing = [stat for stat in required if stat not in data]
    if missing:
        raise DraftConfigError(f"Missing required {context} scoring stats: {', '.join(missing)}")

    categories: list[ScoringCategory] = []
    for stat, value in data.items():
        if not isinstance(stat, str) or not stat.strip():
            raise DraftConfigError(f"{context} scoring stat names must be non-empty strings")
        categories.append(
            ScoringCategory(
                stat=stat.strip(),
                points=_number(value, f"scoring.{context}.{stat}"),
            )
        )
    return tuple(categories)


def _parse_config(raw: Any) -> DraftLeagueConfig:
    data = _require_mapping(raw, "draft config")

    league_data = _section(data, "league")
    name = _required_string(league_data, "name", "league")
    teams = _required_int(league_data, "teams", "league")
    if teams < 2:
        raise DraftConfigError("league.teams must be >= 2")

    draft_data = _section(data, "draft")
    draft_type = _required_string(draft_data, "type", "draft").lower()
    if draft_type != "snake":
        raise DraftConfigError("draft.type currently supports only 'snake'")
    my_slot = _required_int(draft_data, "my_slot", "draft")
    rounds = _required_int(draft_data, "rounds", "draft")
    if my_slot < 1 or my_slot > teams:
        raise DraftConfigError(f"draft.my_slot must be between 1 and {teams}")
    if rounds < 1:
        raise DraftConfigError("draft.rounds must be >= 1")

    roster = _load_roster(_section(data, "roster"))

    scoring_data = _section(data, "scoring")
    skaters = _load_scoring_categories(
        _section(scoring_data, "skaters"),
        "skaters",
        _REQUIRED_SKATER_STATS,
    )
    goalies = _load_scoring_categories(
        _section(scoring_data, "goalies"),
        "goalies",
        _REQUIRED_GOALIE_STATS,
    )

    return DraftLeagueConfig(
        league=LeagueConfig(name=name, teams=teams),
        draft=DraftSettings(draft_type=draft_type, my_slot=my_slot, rounds=rounds),
        roster=roster,
        scoring=ScoringConfig(skaters=skaters, goalies=goalies),
    )


def load_draft_config(path: str | Path) -> DraftLeagueConfig:
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        detail = error.strerror or str(error)
        raise DraftConfigError(f"Could not read draft config '{config_path}': {detail}") from error

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise DraftConfigError(f"Invalid YAML in draft config '{config_path}': {error}") from error

    return _parse_config(raw)
