from dataclasses import dataclass

from apollo.draft.projections import SKATER_PROJECTION_STATS, ProjectionError
from apollo.draft.regression_backtest import RegressionBacktestResult

REGRESSION_MODEL_CANDIDATES: dict[str, dict[str, str]] = {
    "baseline_v03": {stat: "baseline_v03" for stat in SKATER_PROJECTION_STATS},
    "all5": {stat: "regress_pos_5" for stat in SKATER_PROJECTION_STATS},
    "all10": {stat: "regress_pos_10" for stat in SKATER_PROJECTION_STATS},
    "category_robust": {
        "goals": "regress_pos_10",
        "assists": "regress_pos_10",
        "powerPlayPoints": "baseline_v03",
        "shots": "regress_pos_5",
        "hits": "baseline_v03",
        "blockedShots": "regress_pos_10",
    },
    "category_bestmae": {
        "goals": "regress_pos_10",
        "assists": "regress_pos_10",
        "powerPlayPoints": "baseline_v03",
        "shots": "regress_pos_5",
        "hits": "regress_pos_5",
        "blockedShots": "regress_pos_10",
    },
    "points_robust": {
        "goals": "regress_pos_5",
        "assists": "regress_pos_5",
        "powerPlayPoints": "baseline_v03",
        "shots": "regress_pos_5",
        "hits": "baseline_v03",
        "blockedShots": "regress_pos_10",
    },
    "points_bestmae": {
        "goals": "regress_pos_5",
        "assists": "regress_pos_5",
        "powerPlayPoints": "baseline_v03",
        "shots": "regress_pos_5",
        "hits": "regress_pos_5",
        "blockedShots": "regress_pos_10",
    },
}


@dataclass(frozen=True, slots=True)
class RegressionModelMetric:
    stat_name: str
    weighted_mae: float
    weighted_rho: float | None
    baseline_weighted_mae: float
    baseline_weighted_rho: float | None
    improved_years: int
    total_years: int
    worst_rho_delta: float | None

    @property
    def mae_gain(self) -> float:
        return self.baseline_weighted_mae - self.weighted_mae

    @property
    def rho_delta(self) -> float | None:
        if self.weighted_rho is None or self.baseline_weighted_rho is None:
            return None
        return self.weighted_rho - self.baseline_weighted_rho


@dataclass(frozen=True, slots=True)
class RegressionModelCandidate:
    candidate_name: str
    stat_strategy_map: dict[str, str]
    metrics: tuple[RegressionModelMetric, ...]
    top25_overlap_rate: float
    raw_stats_improved: int
    average_raw_improvement_pct: float
    worst_raw_rho_delta: float | None


@dataclass(frozen=True, slots=True)
class RegressionModelAggregateResult:
    target_seasons: tuple[int, ...]
    total_player_seasons: int
    candidates: tuple[RegressionModelCandidate, ...]


def _metric(result: RegressionBacktestResult, strategy_name: str, stat_name: str):
    strategy = next(
        (item for item in result.strategies if item.strategy_name == strategy_name),
        None,
    )
    if strategy is None:
        raise ProjectionError(
            f"Regression model candidate missing strategy {strategy_name} in {result.target_season}"
        )
    metric = next((item for item in strategy.metrics if item.stat_name == stat_name), None)
    if metric is None:
        raise ProjectionError(
            f"Regression model candidate missing metric {stat_name}/{strategy_name}"
        )
    return metric


def _weighted_average(values: list[tuple[float, int]]) -> float:
    denominator = sum(weight for _, weight in values)
    if denominator <= 0:
        raise ProjectionError("Regression model aggregate requires positive player-season weights")
    return sum(value * weight for value, weight in values) / denominator


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(value, weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted_average([(float(value), weight) for value, weight in usable])


def _aggregate_metric(
    results: tuple[RegressionBacktestResult, ...],
    stat_name: str,
    strategy_name: str,
) -> RegressionModelMetric:
    candidate_metrics = [
        (_metric(result, strategy_name, stat_name), result.evaluated_players)
        for result in results
    ]
    baseline_metrics = [
        (_metric(result, "baseline_v03", stat_name), result.evaluated_players)
        for result in results
    ]
    rho_deltas = [
        candidate.spearman_rho - baseline.spearman_rho
        for (candidate, _), (baseline, _) in zip(
            candidate_metrics,
            baseline_metrics,
            strict=True,
        )
        if candidate.spearman_rho is not None and baseline.spearman_rho is not None
    ]
    return RegressionModelMetric(
        stat_name=stat_name,
        weighted_mae=_weighted_average(
            [(metric.mae, weight) for metric, weight in candidate_metrics]
        ),
        weighted_rho=_weighted_optional(
            [(metric.spearman_rho, weight) for metric, weight in candidate_metrics]
        ),
        baseline_weighted_mae=_weighted_average(
            [(metric.mae, weight) for metric, weight in baseline_metrics]
        ),
        baseline_weighted_rho=_weighted_optional(
            [(metric.spearman_rho, weight) for metric, weight in baseline_metrics]
        ),
        improved_years=sum(
            candidate.mae < baseline.mae
            for (candidate, _), (baseline, _) in zip(
                candidate_metrics,
                baseline_metrics,
                strict=True,
            )
        ),
        total_years=len(results),
        worst_rho_delta=min(rho_deltas) if rho_deltas else None,
    )


def _top25_overlap_rate(
    results: tuple[RegressionBacktestResult, ...],
    strategy_name: str,
) -> float:
    overlap = 0
    compared = 0
    for result in results:
        strategy = next(
            item for item in result.strategies if item.strategy_name == strategy_name
        )
        top25 = next(item for item in strategy.top_k_points if item.requested_k == 25)
        overlap += top25.overlap
        compared += top25.compared_k
    if compared <= 0:
        raise ProjectionError("Regression model aggregate has no Top-25 comparisons")
    return overlap / compared


def build_regression_model_aggregate(
    results: tuple[RegressionBacktestResult, ...],
) -> RegressionModelAggregateResult:
    if not results:
        raise ProjectionError("Regression model aggregate requires at least one backtest result")

    target_seasons = tuple(result.target_season for result in results)
    total_player_seasons = sum(result.evaluated_players for result in results)
    if total_player_seasons <= 0:
        raise ProjectionError("Regression model aggregate requires evaluated player-seasons")

    candidates: list[RegressionModelCandidate] = []
    for candidate_name, mapping in REGRESSION_MODEL_CANDIDATES.items():
        if set(mapping) != set(SKATER_PROJECTION_STATS):
            raise ProjectionError(f"Incomplete regression model mapping: {candidate_name}")
        points_strategy = mapping["goals"]
        if mapping["assists"] != points_strategy:
            raise ProjectionError(
                f"Regression candidate {candidate_name} must use the same G/A strategy "
                "because PTS is derived from G + A"
            )

        metrics = [
            _aggregate_metric(results, "points", points_strategy),
            *(
                _aggregate_metric(results, stat_name, mapping[stat_name])
                for stat_name in SKATER_PROJECTION_STATS
            ),
        ]
        raw_metrics = metrics[1:]
        raw_stats_improved = sum(metric.mae_gain > 0 for metric in raw_metrics)
        relative_gains = [
            metric.mae_gain / metric.baseline_weighted_mae * 100
            if metric.baseline_weighted_mae > 0
            else 0.0
            for metric in raw_metrics
        ]
        raw_rho_deltas = [
            metric.worst_rho_delta
            for metric in raw_metrics
            if metric.worst_rho_delta is not None
        ]
        candidates.append(
            RegressionModelCandidate(
                candidate_name=candidate_name,
                stat_strategy_map=dict(mapping),
                metrics=tuple(metrics),
                top25_overlap_rate=_top25_overlap_rate(results, points_strategy),
                raw_stats_improved=raw_stats_improved,
                average_raw_improvement_pct=sum(relative_gains) / len(relative_gains),
                worst_raw_rho_delta=min(raw_rho_deltas) if raw_rho_deltas else None,
            )
        )

    return RegressionModelAggregateResult(
        target_seasons=target_seasons,
        total_player_seasons=total_player_seasons,
        candidates=tuple(candidates),
    )
