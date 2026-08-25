from dataclasses import dataclass

from apollo.draft.goalie_baseline import GoalieBacktestMetric, GoalieBacktestResult
from apollo.draft.projections import ProjectionError

GOALIE_RATE_GATE_STRENGTH = 0.05
GOALIE_RATE_GATE_CANDIDATES = ("sv-5", "gaa-5")


@dataclass(frozen=True, slots=True)
class GoalieRateGateCohortSpec:
    name: str
    min_actual_starts: int
    min_age: float | None = None
    max_age: float | None = None


GOALIE_RATE_GATE_COHORTS = (
    GoalieRateGateCohortSpec("GS10 ALL", 10),
    GoalieRateGateCohortSpec("GS20 ALL", 20),
    GoalieRateGateCohortSpec("GS30 ALL", 30),
    GoalieRateGateCohortSpec("GS20 AGE<30", 20, max_age=30.0),
    GoalieRateGateCohortSpec("GS20 AGE>=30", 20, min_age=30.0),
)


@dataclass(frozen=True, slots=True)
class GoalieRateGateSeasonCandidate:
    candidate_name: str
    stat_name: str
    result: GoalieBacktestResult


@dataclass(frozen=True, slots=True)
class GoalieRateGateSeasonResult:
    cohort: GoalieRateGateCohortSpec
    target_season: int
    baseline: GoalieBacktestResult
    candidates: tuple[GoalieRateGateSeasonCandidate, ...]


@dataclass(frozen=True, slots=True)
class GoalieRateGateCohortCandidateAggregate:
    cohort: GoalieRateGateCohortSpec
    candidate_name: str
    stat_name: str
    player_seasons: int
    baseline_metrics: tuple[GoalieBacktestMetric, ...]
    candidate_metrics: tuple[GoalieBacktestMetric, ...]
    improved_years: int
    worst_mae_gain: float


@dataclass(frozen=True, slots=True)
class GoalieRateGateAggregate:
    target_seasons: tuple[int, ...]
    rows: tuple[GoalieRateGateCohortCandidateAggregate, ...]


def _metric(result: GoalieBacktestResult, stat_name: str) -> GoalieBacktestMetric:
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _aggregate_metrics(
    results: tuple[GoalieBacktestResult, ...],
) -> tuple[GoalieBacktestMetric, ...]:
    if not results:
        raise ProjectionError("Goalie rate gate requires season results")
    total_n = sum(result.evaluated_goalies for result in results)
    if total_n <= 0:
        raise ProjectionError("Goalie rate gate requires evaluated goalies")

    stat_names = tuple(metric.stat_name for metric in results[0].metrics)
    metrics: list[GoalieBacktestMetric] = []
    for stat_name in stat_names:
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
        metrics.append(GoalieBacktestMetric(stat_name, mae, rho, None, None))
    return tuple(metrics)


def build_goalie_rate_gate_aggregate(
    season_results: tuple[GoalieRateGateSeasonResult, ...],
) -> GoalieRateGateAggregate:
    if not season_results:
        raise ProjectionError("Goalie rate gate requires season results")
    target_seasons = tuple(dict.fromkeys(item.target_season for item in season_results))
    rows: list[GoalieRateGateCohortCandidateAggregate] = []

    for cohort in GOALIE_RATE_GATE_COHORTS:
        cohort_results = tuple(item for item in season_results if item.cohort == cohort)
        if len(cohort_results) != len(target_seasons):
            raise ProjectionError(f"Goalie rate gate missing seasons for cohort {cohort.name}")
        baseline_results = tuple(item.baseline for item in cohort_results)
        baseline_metrics = _aggregate_metrics(baseline_results)

        for candidate_name in GOALIE_RATE_GATE_CANDIDATES:
            candidates = tuple(
                next(
                    candidate
                    for candidate in item.candidates
                    if candidate.candidate_name == candidate_name
                )
                for item in cohort_results
            )
            stat_name = candidates[0].stat_name
            candidate_results = tuple(candidate.result for candidate in candidates)
            candidate_metrics = _aggregate_metrics(candidate_results)
            gains = [
                _metric(item.baseline, stat_name).mae - _metric(candidate.result, stat_name).mae
                for item, candidate in zip(cohort_results, candidates, strict=True)
            ]
            rows.append(
                GoalieRateGateCohortCandidateAggregate(
                    cohort=cohort,
                    candidate_name=candidate_name,
                    stat_name=stat_name,
                    player_seasons=sum(result.evaluated_goalies for result in baseline_results),
                    baseline_metrics=baseline_metrics,
                    candidate_metrics=candidate_metrics,
                    improved_years=sum(gain > 0 for gain in gains),
                    worst_mae_gain=min(gains),
                )
            )

    return GoalieRateGateAggregate(target_seasons=target_seasons, rows=tuple(rows))
