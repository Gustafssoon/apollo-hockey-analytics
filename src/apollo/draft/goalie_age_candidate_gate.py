from dataclasses import dataclass

from apollo.draft.goalie_baseline import GoalieBacktestMetric, GoalieBacktestResult
from apollo.draft.projections import ProjectionError

GOALIE_AGE_GATE_SLOPE = 0.005


@dataclass(frozen=True, slots=True)
class GoalieAgeGateCohortSpec:
    name: str
    min_actual_starts: int
    min_age: float | None = None
    max_age: float | None = None


GOALIE_AGE_GATE_COHORTS = (
    GoalieAgeGateCohortSpec("GS10 ALL", 10),
    GoalieAgeGateCohortSpec("GS20 ALL", 20),
    GoalieAgeGateCohortSpec("GS30 ALL", 30),
    GoalieAgeGateCohortSpec("GS20 AGE<30", 20, max_age=30.0),
    GoalieAgeGateCohortSpec("GS20 AGE>=30", 20, min_age=30.0),
)


@dataclass(frozen=True, slots=True)
class GoalieAgeGateSeasonResult:
    cohort: GoalieAgeGateCohortSpec
    target_season: int
    baseline: GoalieBacktestResult
    candidate: GoalieBacktestResult
    applied: int


@dataclass(frozen=True, slots=True)
class GoalieAgeGateCohortAggregate:
    cohort: GoalieAgeGateCohortSpec
    player_seasons: int
    applied: int
    baseline_metrics: tuple[GoalieBacktestMetric, ...]
    candidate_metrics: tuple[GoalieBacktestMetric, ...]
    improved_years: int
    worst_gs_mae_gain: float


@dataclass(frozen=True, slots=True)
class GoalieAgeGateAggregate:
    target_seasons: tuple[int, ...]
    cohorts: tuple[GoalieAgeGateCohortAggregate, ...]


def _metric(result: GoalieBacktestResult, stat_name: str) -> GoalieBacktestMetric:
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _aggregate_metric(
    results: tuple[GoalieBacktestResult, ...],
    stat_name: str,
) -> GoalieBacktestMetric:
    total_n = sum(result.evaluated_goalies for result in results)
    if total_n <= 0:
        raise ProjectionError("Goalie age gate requires evaluated goalies")
    pairs = [(_metric(result, stat_name), result.evaluated_goalies) for result in results]
    mae = sum(metric.mae * n for metric, n in pairs) / total_n
    rho_pairs = [
        (metric.spearman_rho, n)
        for metric, n in pairs
        if metric.spearman_rho is not None
    ]
    rho = (
        None
        if not rho_pairs
        else sum(float(value) * n for value, n in rho_pairs)
        / sum(n for _, n in rho_pairs)
    )
    return GoalieBacktestMetric(stat_name, mae, rho, None, None)


def build_goalie_age_gate_aggregate(
    season_results: tuple[GoalieAgeGateSeasonResult, ...],
) -> GoalieAgeGateAggregate:
    if not season_results:
        raise ProjectionError("Goalie age gate requires season results")

    target_seasons = tuple(
        dict.fromkeys(result.target_season for result in season_results)
    )
    aggregates: list[GoalieAgeGateCohortAggregate] = []
    stat_names = (
        "gamesStarted",
        "wins",
        "saves",
        "goalsAgainst",
        "shutouts",
        "savePctg",
        "goalsAgainstAvg",
    )

    for cohort in GOALIE_AGE_GATE_COHORTS:
        matches = tuple(result for result in season_results if result.cohort == cohort)
        if len(matches) != len(target_seasons):
            raise ProjectionError(f"Goalie age gate missing season result for {cohort.name}")
        baseline_results = tuple(result.baseline for result in matches)
        candidate_results = tuple(result.candidate for result in matches)
        player_seasons = sum(result.evaluated_goalies for result in baseline_results)
        baseline_metrics = tuple(
            _aggregate_metric(baseline_results, stat_name) for stat_name in stat_names
        )
        candidate_metrics = tuple(
            _aggregate_metric(candidate_results, stat_name) for stat_name in stat_names
        )
        gs_gains = [
            _metric(result.baseline, "gamesStarted").mae
            - _metric(result.candidate, "gamesStarted").mae
            for result in matches
        ]
        aggregates.append(
            GoalieAgeGateCohortAggregate(
                cohort=cohort,
                player_seasons=player_seasons,
                applied=sum(result.applied for result in matches),
                baseline_metrics=baseline_metrics,
                candidate_metrics=candidate_metrics,
                improved_years=sum(gain > 0 for gain in gs_gains),
                worst_gs_mae_gain=min(gs_gains),
            )
        )

    return GoalieAgeGateAggregate(
        target_seasons=target_seasons,
        cohorts=tuple(aggregates),
    )
