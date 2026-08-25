from dataclasses import replace

from apollo.db import Database
from apollo.draft.pp_deployment_candidate_gate import (
    PP_DEPLOYMENT_CANDIDATE_SIGNAL,
    PP_DEPLOYMENT_CANDIDATE_STRENGTH,
    build_pp_deployment_gate_cohort,
)
from apollo.draft.pp_deployment_production_gate import (
    PPDeploymentProductionGateResult,
    PPDeploymentProductionSeasonCheck,
)
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError, previous_seasons
from apollo.services.draft_backtest import run_skater_backtest
from apollo.services.pp_deployment_candidate import run_pp_deployment_candidate_backtest


def run_pp_deployment_production_gate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> PPDeploymentProductionGateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")
    if min_history_seasons < 1 or min_history_seasons > len(DEFAULT_SEASON_WEIGHTS):
        raise ProjectionError(
            f"min_history_seasons must be between 1 and {len(DEFAULT_SEASON_WEIGHTS)}"
        )

    database.initialize()
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )

    approved_results = []
    checks: list[PPDeploymentProductionSeasonCheck] = []
    for season in target_seasons:
        candidate_season = run_pp_deployment_candidate_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        approved_variant = next(
            variant
            for variant in candidate_season.variants
            if variant.signal_name == PP_DEPLOYMENT_CANDIDATE_SIGNAL
            and variant.strength == PP_DEPLOYMENT_CANDIDATE_STRENGTH
        )
        production = run_skater_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        comparable_candidate = replace(
            approved_variant.result,
            model_version=production.model_version,
        )
        exact = production == comparable_candidate
        checks.append(
            PPDeploymentProductionSeasonCheck(
                target_season=season,
                evaluated_players=production.evaluated_players,
                applied=approved_variant.applied,
                exact_candidate_equivalence=exact,
            )
        )
        approved_results.append(
            (candidate_season.baseline, production, approved_variant.applied)
        )

    aggregate = build_pp_deployment_gate_cohort(
        label="PROD GP20",
        min_actual_games=min_actual_games,
        position_group=None,
        season_results=tuple(approved_results),
    )
    return PPDeploymentProductionGateResult(
        target_seasons=target_seasons,
        season_checks=tuple(checks),
        aggregate=aggregate,
    )
