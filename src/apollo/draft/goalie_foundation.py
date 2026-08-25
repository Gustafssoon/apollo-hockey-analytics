from dataclasses import dataclass

from apollo.draft.projections import ProjectionError, previous_seasons

GOALIE_FOUNDATION_FIELDS = (
    "gamesPlayed",
    "gamesStarted",
    "wins",
    "losses",
    "otLosses",
    "shotsAgainst",
    "saves",
    "goalsAgainst",
    "savePctg",
    "goalsAgainstAvg",
    "shutouts",
    "timeOnIce",
)
GOALIE_CORE_SOURCE_FIELDS = (
    "gamesStarted",
    "wins",
    "shotsAgainst",
    "saves",
    "goalsAgainst",
    "shutouts",
)
GOALIE_HISTORY_START_THRESHOLDS = (5, 10, 20)


@dataclass(frozen=True, slots=True)
class GoalieSeasonCoverage:
    season: int
    goalies_with_games: int
    goalies_with_starts: int
    field_counts: tuple[tuple[str, int], ...]
    complete_core: int
    shot_identity_checked: int
    shot_identity_exact: int


@dataclass(frozen=True, slots=True)
class GoalieHistoryCoverage:
    target_season: int
    min_actual_starts: int
    actual_eligible: int
    at_least_one_source: int
    at_least_two_sources: int
    three_sources: int


@dataclass(frozen=True, slots=True)
class GoalieFoundationAudit:
    target_seasons: tuple[int, ...]
    data_seasons: tuple[int, ...]
    season_coverage: tuple[GoalieSeasonCoverage, ...]
    history_coverage: tuple[GoalieHistoryCoverage, ...]


def _complete_source_season(stats: dict[str, float]) -> bool:
    if stats.get("gamesStarted", 0.0) <= 0:
        return False
    return all(field in stats for field in GOALIE_CORE_SOURCE_FIELDS)


def _shot_identity(stats: dict[str, float]) -> bool | None:
    values = tuple(stats.get(field) for field in ("shotsAgainst", "saves", "goalsAgainst"))
    if any(value is None for value in values):
        return None
    shots_against, saves, goals_against = (float(value) for value in values)
    return abs(shots_against - (saves + goals_against)) <= 0.5


def _season_coverage(
    season: int,
    stats_by_player: dict[int, dict[int, dict[str, float]]],
) -> GoalieSeasonCoverage:
    active = [
        seasons[season]
        for seasons in stats_by_player.values()
        if season in seasons and seasons[season].get("gamesPlayed", 0.0) > 0
    ]
    field_counts = tuple(
        (field, sum(field in stats for stats in active)) for field in GOALIE_FOUNDATION_FIELDS
    )
    identities = [_shot_identity(stats) for stats in active]
    checked = [value for value in identities if value is not None]
    return GoalieSeasonCoverage(
        season=season,
        goalies_with_games=len(active),
        goalies_with_starts=sum(stats.get("gamesStarted", 0.0) > 0 for stats in active),
        field_counts=field_counts,
        complete_core=sum(_complete_source_season(stats) for stats in active),
        shot_identity_checked=len(checked),
        shot_identity_exact=sum(value is True for value in checked),
    )


def _history_coverage(
    target_season: int,
    stats_by_player: dict[int, dict[int, dict[str, float]]],
    min_actual_starts: int,
) -> GoalieHistoryCoverage:
    source_seasons = previous_seasons(target_season, 3)
    actual_eligible = 0
    source_counts: list[int] = []
    for seasons in stats_by_player.values():
        actual = seasons.get(target_season, {})
        if not _complete_source_season(actual):
            continue
        if actual.get("gamesStarted", 0.0) < min_actual_starts:
            continue
        actual_eligible += 1
        source_counts.append(
            sum(_complete_source_season(seasons.get(season, {})) for season in source_seasons)
        )
    return GoalieHistoryCoverage(
        target_season=target_season,
        min_actual_starts=min_actual_starts,
        actual_eligible=actual_eligible,
        at_least_one_source=sum(count >= 1 for count in source_counts),
        at_least_two_sources=sum(count >= 2 for count in source_counts),
        three_sources=sum(count >= 3 for count in source_counts),
    )


def build_goalie_foundation_audit(
    stats_by_player: dict[int, dict[int, dict[str, float]]],
    latest_target_season: int,
    *,
    years: int = 3,
) -> GoalieFoundationAudit:
    if years < 1:
        raise ProjectionError("Goalie foundation audit years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    oldest_target = target_seasons[-1]
    data_seasons = (
        *target_seasons,
        *tuple(
            season
            for season in previous_seasons(oldest_target, 3)
            if season not in target_seasons
        ),
    )
    return GoalieFoundationAudit(
        target_seasons=target_seasons,
        data_seasons=data_seasons,
        season_coverage=tuple(
            _season_coverage(season, stats_by_player) for season in data_seasons
        ),
        history_coverage=tuple(
            _history_coverage(target_season, stats_by_player, threshold)
            for target_season in target_seasons
            for threshold in GOALIE_HISTORY_START_THRESHOLDS
        ),
    )
