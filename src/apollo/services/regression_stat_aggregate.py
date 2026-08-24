from apollo.db import Database
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.draft.regression_stat_aggregate import (
    RegressionStatAggregateSummary,
    build_regression_stat_aggregate_summary,
)
from apollo.services.regression_backtest import run_regression_backtest


def run_regression_stat_aggregate(
    database: Database,
    end_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> RegressionStatAggregateSummary:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")

    target_seasons = (end_season, *previous_seasons(end_season, years - 1))
    results = tuple(
        run_regression_backtest(
            database,
            target_season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        for target_season in target_seasons
    )
    return build_regression_stat_aggregate_summary(results)
