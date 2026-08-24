from apollo.db import Database
from apollo.draft.age_stat_aggregate import (
    AgeStatAggregateSummary,
    build_age_stat_aggregate_summary,
)
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.services.age_stat_backtest import run_age_stat_backtest


def run_age_stat_aggregate(
    database: Database,
    end_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
) -> AgeStatAggregateSummary:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")

    target_seasons = (end_season, *previous_seasons(end_season, years - 1))
    results = tuple(
        run_age_stat_backtest(
            database,
            target_season,
            min_actual_games=min_actual_games,
        )
        for target_season in target_seasons
    )
    return build_age_stat_aggregate_summary(results)
