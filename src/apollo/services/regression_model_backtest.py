from apollo.db import Database
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.draft.regression_model_backtest import (
    RegressionModelAggregateResult,
    build_regression_model_aggregate,
)
from apollo.services.regression_backtest import run_regression_backtest


def run_regression_model_aggregate(
    database: Database,
    end_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> RegressionModelAggregateResult:
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
    return build_regression_model_aggregate(results)
