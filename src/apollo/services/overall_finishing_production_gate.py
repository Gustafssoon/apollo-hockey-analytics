from dataclasses import replace

from apollo.db import Database
from apollo.draft.overall_finishing_candidate_gate import build_overall_finishing_gate_cohort
from apollo.draft.overall_finishing_production_gate import (
    OverallFinishingProductionGateResult,
    OverallFinishingProductionSeasonCheck,
)
from apollo.draft.projections import DEFAULT_SEASON_WEIGHTS, ProjectionError, previous_seasons
from apollo.services.draft_backtest import run_skater_backtest
from apollo.services.overall_finishing_candidate_gate import _run_gate_slice


def run_overall_finishing_production_gate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> OverallFinishingProductionGateResult:
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
    checks: list[OverallFinishingProductionSeasonCheck] = []
    for season in target_seasons:
        baseline, approved_candidate, applied = _run_gate_slice(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
            position_group_filter=None,
        )
        production = run_skater_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        comparable_candidate = replace(
            approved_candidate,
            model_version=production.model_version,
        )
        exact = production == comparable_candidate
        checks.append(
            OverallFinishingProductionSeasonCheck(
                target_season=season,
                evaluated_players=production.evaluated_players,
                applied=applied,
                exact_candidate_equivalence=exact,
            )
        )
        approved_results.append((baseline, production, applied))

    aggregate = build_overall_finishing_gate_cohort(
        label="PROD GP20",
        min_actual_games=min_actual_games,
        position_group=None,
        season_results=tuple(approved_results),
    )
    return OverallFinishingProductionGateResult(
        target_seasons=target_seasons,
        season_checks=tuple(checks),
        aggregate=aggregate,
    )
