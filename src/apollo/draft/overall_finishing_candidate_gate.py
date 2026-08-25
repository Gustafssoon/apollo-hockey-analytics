from dataclasses import dataclass

from apollo.draft.shot_type_finishing_candidate import (
    ShotTypeFinishingAggregateResult,
    ShotTypeFinishingAggregateVariant,
)
from apollo.draft.projections import ProjectionError

OVERALL_SHOOTING_SIGNAL = "overall_shooting_pct"
OVERALL_SHOOTING_STRENGTH = 0.05
OVERALL_SHOOTING_CANDIDATE_VERSION = "apollo-skater-v0.7-candidate-overall-shpct-shrink5"


@dataclass(frozen=True, slots=True)
class OverallFinishingGateCohort:
    label: str
    min_actual_games: int
    position_group: str | None
    result: ShotTypeFinishingAggregateResult
    candidate: ShotTypeFinishingAggregateVariant


@dataclass(frozen=True, slots=True)
class OverallFinishingGateResult:
    latest_target_season: int
    target_seasons: tuple[int, ...]
    cohorts: tuple[OverallFinishingGateCohort, ...]


def select_overall_sh5_candidate(
    result: ShotTypeFinishingAggregateResult,
) -> ShotTypeFinishingAggregateVariant:
    try:
        return next(
            variant
            for variant in result.variants
            if variant.signal_name == OVERALL_SHOOTING_SIGNAL
            and variant.strength == OVERALL_SHOOTING_STRENGTH
        )
    except StopIteration as exc:
        raise ProjectionError("Overall SH% 5% candidate missing from gate input") from exc


def build_overall_finishing_gate_result(
    *,
    latest_target_season: int,
    cohorts: tuple[
        tuple[str, int, str | None, ShotTypeFinishingAggregateResult], ...
    ],
) -> OverallFinishingGateResult:
    if not cohorts:
        raise ProjectionError("Overall finishing candidate gate requires at least one cohort")

    built = tuple(
        OverallFinishingGateCohort(
            label=label,
            min_actual_games=min_actual_games,
            position_group=position_group,
            result=result,
            candidate=select_overall_sh5_candidate(result),
        )
        for label, min_actual_games, position_group, result in cohorts
    )
    target_seasons = built[0].result.target_seasons
    if any(cohort.result.target_seasons != target_seasons for cohort in built[1:]):
        raise ProjectionError("Overall finishing gate cohorts must use identical target seasons")

    return OverallFinishingGateResult(
        latest_target_season=latest_target_season,
        target_seasons=target_seasons,
        cohorts=built,
    )
