from dataclasses import dataclass


MODEL_VERSION = "apollo-skater-baseline-v0.1"
DEFAULT_SEASON_WEIGHTS = (0.6, 0.3, 0.1)
SKATER_PROJECTION_STATS = (
    "goals",
    "assists",
    "powerPlayPoints",
    "shots",
    "hits",
    "blockedShots",
)


class ProjectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectionSeason:
    season: int
    games_played: float
    stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class SkaterProjection:
    player_id: int
    player_name: str
    team_abbrev: str | None
    position: str
    target_season: int
    projected_games: float
    stats: dict[str, float]
    source_seasons: tuple[int, ...]
    model_version: str = MODEL_VERSION


def _season_years(season: int) -> tuple[int, int]:
    text = str(season)
    if len(text) != 8:
        raise ProjectionError(f"Invalid NHL season id: {season}")
    start_year = int(text[:4])
    end_year = int(text[4:])
    if end_year != start_year + 1:
        raise ProjectionError(f"Invalid NHL season id: {season}")
    return start_year, end_year


def previous_seasons(target_season: int, count: int = 3) -> tuple[int, ...]:
    start_year, _ = _season_years(target_season)
    return tuple(
        int(f"{year}{year + 1}")
        for year in range(start_year - 1, start_year - count - 1, -1)
    )


def _weighted_average(values: list[tuple[float, float]]) -> float:
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("Projection requires at least one positive season weight")
    return sum(value * weight for value, weight in values) / weight_sum


def build_skater_projection(
    *,
    player_id: int,
    player_name: str,
    team_abbrev: str | None,
    position: str,
    target_season: int,
    history: tuple[ProjectionSeason, ...],
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> SkaterProjection:
    if not history:
        raise ProjectionError(f"No historical NHL season data available for {player_name}")
    if len(history) > len(season_weights):
        raise ProjectionError("More history seasons supplied than configured season weights")

    usable = tuple(season for season in history if season.games_played > 0)
    if not usable:
        raise ProjectionError(f"No seasons with games played available for {player_name}")

    weights_by_season = {
        season.season: season_weights[index]
        for index, season in enumerate(history)
        if index < len(season_weights)
    }

    projected_games = min(
        82.0,
        _weighted_average(
            [
                (season.games_played, weights_by_season[season.season])
                for season in usable
            ]
        ),
    )

    projected_stats: dict[str, float] = {}
    for stat_name in SKATER_PROJECTION_STATS:
        rate_values: list[tuple[float, float]] = []
        for season in usable:
            value = season.stats.get(stat_name)
            if value is None:
                continue
            rate_values.append(
                (
                    value / season.games_played,
                    weights_by_season[season.season],
                )
            )
        if not rate_values:
            raise ProjectionError(
                f"Missing historical stat '{stat_name}' for {player_name}"
            )
        projected_stats[stat_name] = _weighted_average(rate_values) * projected_games

    return SkaterProjection(
        player_id=player_id,
        player_name=player_name,
        team_abbrev=team_abbrev,
        position=position,
        target_season=target_season,
        projected_games=projected_games,
        stats=projected_stats,
        source_seasons=tuple(season.season for season in usable),
    )
