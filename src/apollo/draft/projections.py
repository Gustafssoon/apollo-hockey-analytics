from dataclasses import dataclass
from datetime import date

from apollo.draft.aging import AGE_MODEL_VERSION, adjust_rate_for_seasons
from apollo.draft.assist_rate import (
    ASSIST_RATE_MODEL_VERSION,
)
from apollo.draft.assist_rate import (
    correction_factor as assist_rate_correction_factor,
)
from apollo.draft.availability import (
    AVAILABILITY_MODEL_VERSION,
    AvailabilityError,
    project_available_games,
)
from apollo.draft.overall_finishing import (
    OVERALL_FINISHING_MODEL_VERSION,
)
from apollo.draft.overall_finishing import (
    correction_factor as overall_finishing_correction_factor,
)
from apollo.draft.pp_deployment import PP_DEPLOYMENT_MODEL_VERSION
from apollo.draft.pp_deployment import correction_factor as pp_deployment_correction_factor
from apollo.draft.regression import (
    REGRESSION_MODEL_VERSION,
    REGRESSION_PSEUDO_GAMES_BY_STAT,
    position_group,
    regress_rate,
)
from apollo.draft.shooting_context import (
    SHOOTING_CONTEXT_MODEL_VERSION,
)
from apollo.draft.shooting_context import (
    correction_factor as shooting_context_correction_factor,
)

MODEL_VERSION = "apollo-skater-baseline-v0.8"
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
    availability_model_version: str = AVAILABILITY_MODEL_VERSION
    age_model_version: str | None = None
    regression_model_version: str | None = None
    shooting_context_model_version: str | None = None
    assist_rate_model_version: str | None = None
    overall_finishing_model_version: str | None = None
    pp_deployment_model_version: str | None = None


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
    birth_date: date | None = None,
    regression_priors: dict[tuple[int, str, str], float] | None = None,
    shooting_context_ratio: float | None = None,
    assist_rate_context_ratio: float | None = None,
    overall_finishing_context_ratio: float | None = None,
    pp_deployment_context_ratio: float | None = None,
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

    try:
        projected_games = project_available_games(
            tuple((season.season, season.games_played) for season in history),
            season_weights,
        )
    except AvailabilityError as exc:
        raise ProjectionError(str(exc)) from exc

    group = position_group(position)
    regression_applied = False
    projected_stats: dict[str, float] = {}
    for stat_name in SKATER_PROJECTION_STATS:
        rate_values: list[tuple[float, float]] = []
        pseudo_games = REGRESSION_PSEUDO_GAMES_BY_STAT[stat_name]
        for season in usable:
            value = season.stats.get(stat_name)
            if value is None:
                continue
            prior_rate = None
            if regression_priors is not None:
                prior_rate = regression_priors.get((season.season, group, stat_name))
            try:
                rate, applied = regress_rate(
                    value=value,
                    games_played=season.games_played,
                    prior_rate=prior_rate,
                    pseudo_games=pseudo_games,
                )
            except ValueError as exc:
                raise ProjectionError(str(exc)) from exc
            regression_applied = regression_applied or applied
            if birth_date is not None:
                try:
                    rate = adjust_rate_for_seasons(
                        observed_rate=rate,
                        birth_date=birth_date,
                        source_season=season.season,
                        target_season=target_season,
                        position=position,
                    )
                except ValueError as exc:
                    raise ProjectionError(str(exc)) from exc
            rate_values.append((rate, weights_by_season[season.season]))
        if not rate_values:
            raise ProjectionError(
                f"Missing historical stat '{stat_name}' for {player_name}"
            )
        projected_stats[stat_name] = _weighted_average(rate_values) * projected_games

    shooting_context_applied = False
    if shooting_context_ratio is not None:
        try:
            factor = shooting_context_correction_factor(shooting_context_ratio)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc
        projected_stats["goals"] *= factor
        projected_stats["assists"] *= factor
        shooting_context_applied = True

    assist_rate_applied = False
    if assist_rate_context_ratio is not None:
        try:
            factor = assist_rate_correction_factor(assist_rate_context_ratio)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc
        projected_stats["assists"] *= factor
        assist_rate_applied = True

    overall_finishing_applied = False
    if overall_finishing_context_ratio is not None:
        try:
            factor = overall_finishing_correction_factor(overall_finishing_context_ratio)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc
        projected_stats["goals"] *= factor
        overall_finishing_applied = True

    pp_deployment_applied = False
    if pp_deployment_context_ratio is not None:
        try:
            factor = pp_deployment_correction_factor(pp_deployment_context_ratio)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc
        projected_stats["powerPlayPoints"] *= factor
        pp_deployment_applied = True

    return SkaterProjection(
        player_id=player_id,
        player_name=player_name,
        team_abbrev=team_abbrev,
        position=position,
        target_season=target_season,
        projected_games=projected_games,
        stats=projected_stats,
        source_seasons=tuple(season.season for season in usable),
        age_model_version=(AGE_MODEL_VERSION if birth_date is not None else None),
        regression_model_version=(REGRESSION_MODEL_VERSION if regression_applied else None),
        shooting_context_model_version=(
            SHOOTING_CONTEXT_MODEL_VERSION if shooting_context_applied else None
        ),
        assist_rate_model_version=(ASSIST_RATE_MODEL_VERSION if assist_rate_applied else None),
        overall_finishing_model_version=(
            OVERALL_FINISHING_MODEL_VERSION if overall_finishing_applied else None
        ),
        pp_deployment_model_version=(
            PP_DEPLOYMENT_MODEL_VERSION if pp_deployment_applied else None
        ),
    )
