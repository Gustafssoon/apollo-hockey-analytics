from apollo.db import Database
from apollo.draft.pp_deployment_candidate_gate import (
    PP_DEPLOYMENT_CANDIDATE_SIGNAL,
    PP_DEPLOYMENT_CANDIDATE_STRENGTH,
    PP_DEPLOYMENT_ROBUSTNESS_COHORTS,
    PPDeploymentGateResult,
    build_pp_deployment_gate_cohort,
    build_pp_deployment_gate_result,
)
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.services.pp_deployment_candidate import run_pp_deployment_candidate_backtest


def _selected_variant(result):
    return next(
        variant
        for variant in result.variants
        if variant.signal_name == PP_DEPLOYMENT_CANDIDATE_SIGNAL
        and variant.strength == PP_DEPLOYMENT_CANDIDATE_STRENGTH
    )


def run_pp_deployment_candidate_gate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_history_seasons: int = 3,
) -> PPDeploymentGateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")

    database.initialize()
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    cohorts = []
    for label, min_actual_games, group_filter in PP_DEPLOYMENT_ROBUSTNESS_COHORTS:
        season_results = []
        for season in target_seasons:
            result = run_pp_deployment_candidate_backtest(
                database,
                season,
                min_actual_games=min_actual_games,
                min_history_seasons=min_history_seasons,
                position_group_filter=group_filter,
            )
            variant = _selected_variant(result)
            season_results.append((result.baseline, variant.result, variant.applied))
        cohorts.append(
            build_pp_deployment_gate_cohort(
                label=label,
                min_actual_games=min_actual_games,
                position_group=group_filter,
                season_results=tuple(season_results),
            )
        )

    return build_pp_deployment_gate_result(
        latest_target_season=latest_target_season,
        target_seasons=target_seasons,
        cohorts=tuple(cohorts),
    )
