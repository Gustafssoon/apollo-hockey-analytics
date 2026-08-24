from dataclasses import dataclass

from apollo.draft.projections import ProjectionError
from apollo.draft.regression_backtest import (
    REGRESSION_STATS,
    RegressionBacktestResult,
)


@dataclass(frozen=True, slots=True)
class RegressionStatAggregateStrategy:
    stat_name: str
    strategy_name: str
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
class RegressionStatAggregateSummary:
    end_season: int
    target_seasons: tuple[int, ...]
    total_player_seasons: int
    strategies: tuple[RegressionStatAggregateStrategy, ...]


def _weighted_average(values: list[tuple[float, int]]) -> float:
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("Regression stat aggregate requires positive player-season weights")
    return sum(value * weight for value, weight in values) / weight_sum


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(value, weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted_average([(float(value), weight) for value, weight in usable])


def _metric(result: RegressionBacktestResult, strategy_name: str, stat_name: str):
    strategy = next(
        (candidate for candidate in result.strategies if candidate.strategy_name == strategy_name),
        None,
    )
    if strategy is None:
        raise ProjectionError(
            f"Missing regression strategy {strategy_name} in {result.target_season}"
        )
    metric = next((item for item in strategy.metrics if item.stat_name == stat_name), None)
    if metric is None:
        raise ProjectionError(
            f"Missing regression metric {strategy_name}/{stat_name} in {result.target_season}"
        )
    return metric


def build_regression_stat_aggregate_summary(
    results: tuple[RegressionBacktestResult, ...],
) -> RegressionStatAggregateSummary:
    if not results:
        raise ProjectionError("Regression stat aggregate requires at least one backtest result")

    target_seasons = tuple(result.target_season for result in results)
    total_player_seasons = sum(result.evaluated_players for result in results)
    if total_player_seasons <= 0:
        raise ProjectionError("Regression stat aggregate requires evaluated player-seasons")

    strategy_names = tuple(strategy.strategy_name for strategy in results[0].strategies)
    if "baseline_v03" not in strategy_names:
        raise ProjectionError("Regression stat aggregate requires baseline_v03")

    aggregates: list[RegressionStatAggregateStrategy] = []
    for stat_name in REGRESSION_STATS:
        baseline_by_season = [
            (_metric(result, "baseline_v03", stat_name), result.evaluated_players)
            for result in results
        ]
        baseline_weighted_mae = _weighted_average(
            [(metric.mae, weight) for metric, weight in baseline_by_season]
        )
        baseline_weighted_rho = _weighted_optional(
            [(metric.spearman_rho, weight) for metric, weight in baseline_by_season]
        )

        for strategy_name in strategy_names:
            metrics_by_season = []
            rho_deltas: list[float] = []
            improved_years = 0
            for result, (baseline, weight) in zip(results, baseline_by_season, strict=True):
                metric = _metric(result, strategy_name, stat_name)
                metrics_by_season.append((metric, weight))
                if metric.mae < baseline.mae:
                    improved_years += 1
                if metric.spearman_rho is not None and baseline.spearman_rho is not None:
                    rho_deltas.append(metric.spearman_rho - baseline.spearman_rho)

            aggregates.append(
                RegressionStatAggregateStrategy(
                    stat_name=stat_name,
                    strategy_name=strategy_name,
                    weighted_mae=_weighted_average(
                        [(metric.mae, weight) for metric, weight in metrics_by_season]
                    ),
                    weighted_rho=_weighted_optional(
                        [(metric.spearman_rho, weight) for metric, weight in metrics_by_season]
                    ),
                    baseline_weighted_mae=baseline_weighted_mae,
                    baseline_weighted_rho=baseline_weighted_rho,
                    improved_years=improved_years,
                    total_years=len(results),
                    worst_rho_delta=min(rho_deltas) if rho_deltas else None,
                )
            )

    return RegressionStatAggregateSummary(
        end_season=results[0].target_season,
        target_seasons=target_seasons,
        total_player_seasons=total_player_seasons,
        strategies=tuple(aggregates),
    )
