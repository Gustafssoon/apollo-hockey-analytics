from dataclasses import dataclass

from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GoalieBacktestMetric,
    GoalieBacktestPlayer,
    GoalieBacktestResult,
)
from apollo.draft.goalie_rate_candidate import (
    GOALIE_RATE_VARIANTS,
    apply_rate_regression,
)
from apollo.draft.projections import ProjectionError

GOALIE_BASELINE_V02_CANDIDATE_VERSION = "apollo-goalie-baseline-v0.2-candidate"
GOALIE_BASELINE_V02_COMPONENTS = ("sv-5", "gaa-5")


@dataclass(frozen=True, slots=True)
class GoalieBaselineV02SeasonResult:
    target_season: int
    baseline: GoalieBacktestResult
    candidate: GoalieBacktestResult
    save_pct_prior: float
    gaa_prior: float


@dataclass(frozen=True, slots=True)
class GoalieBaselineV02Aggregate:
    target_seasons: tuple[int, ...]
    player_seasons: int
    baseline_metrics: tuple[GoalieBacktestMetric, ...]
    candidate_metrics: tuple[GoalieBacktestMetric, ...]


def build_goalie_baseline_v02_player(
    baseline: GoalieBacktestPlayer,
    *,
    save_pct_prior: float,
    gaa_prior: float,
) -> GoalieBacktestPlayer:
    specs = {
        spec.name: spec
        for spec in GOALIE_RATE_VARIANTS
        if spec.name in GOALIE_BASELINE_V02_COMPONENTS
    }
    if set(specs) != set(GOALIE_BASELINE_V02_COMPONENTS):
        raise ProjectionError("Goalie baseline v0.2 requires approved sv-5 and gaa-5 specs")

    player = apply_rate_regression(baseline, specs["sv-5"], save_pct_prior)
    return apply_rate_regression(player, specs["gaa-5"], gaa_prior)


def _metric(result: GoalieBacktestResult, stat_name: str) -> GoalieBacktestMetric:
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _aggregate_metric(
    results: tuple[GoalieBaselineV02SeasonResult, ...],
    stat_name: str,
    *,
    candidate: bool,
) -> GoalieBacktestMetric:
    total_n = sum(item.baseline.evaluated_goalies for item in results)
    metrics = [
        (
            _metric(item.candidate if candidate else item.baseline, stat_name),
            item.baseline.evaluated_goalies,
        )
        for item in results
    ]
    mae = sum(metric.mae * n for metric, n in metrics) / total_n
    rho_pairs = [
        (metric.spearman_rho, n)
        for metric, n in metrics
        if metric.spearman_rho is not None
    ]
    rho = (
        None
        if not rho_pairs
        else sum(float(value) * n for value, n in rho_pairs)
        / sum(n for _, n in rho_pairs)
    )
    return GoalieBacktestMetric(stat_name, mae, rho, None, None)


def build_goalie_baseline_v02_aggregate(
    results: tuple[GoalieBaselineV02SeasonResult, ...],
) -> GoalieBaselineV02Aggregate:
    if not results:
        raise ProjectionError("Goalie baseline v0.2 aggregate requires season results")
    player_seasons = sum(item.baseline.evaluated_goalies for item in results)
    if player_seasons <= 0:
        raise ProjectionError("Goalie baseline v0.2 aggregate requires evaluated goalies")

    return GoalieBaselineV02Aggregate(
        target_seasons=tuple(item.target_season for item in results),
        player_seasons=player_seasons,
        baseline_metrics=tuple(
            _aggregate_metric(results, stat_name, candidate=False)
            for stat_name in GOALIE_BACKTEST_STATS
        ),
        candidate_metrics=tuple(
            _aggregate_metric(results, stat_name, candidate=True)
            for stat_name in GOALIE_BACKTEST_STATS
        ),
    )
