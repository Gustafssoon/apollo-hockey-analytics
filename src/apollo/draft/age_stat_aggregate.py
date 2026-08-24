from dataclasses import dataclass

from apollo.draft.age_stat_backtest import AGE_STAT_NAMES, AgeStatBacktestResult
from apollo.draft.projections import ProjectionError


@dataclass(frozen=True, slots=True)
class AgeStatAggregateStrategy:
    stat_name: str
    strategy_name: str
    weighted_mae: float
    weighted_rho: float | None
    neutral_weighted_mae: float
    neutral_weighted_rho: float | None
    improved_years: int
    total_years: int
    worst_rho_delta: float | None

    @property
    def mae_gain(self) -> float:
        return self.neutral_weighted_mae - self.weighted_mae

    @property
    def rho_delta(self) -> float | None:
        if self.weighted_rho is None or self.neutral_weighted_rho is None:
            return None
        return self.weighted_rho - self.neutral_weighted_rho


@dataclass(frozen=True, slots=True)
class AgeStatAggregateSummary:
    end_season: int
    target_seasons: tuple[int, ...]
    total_player_seasons: int
    strategies: tuple[AgeStatAggregateStrategy, ...]


def _weighted_average(values: list[tuple[float, int]]) -> float:
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("Age stat aggregate requires positive player-season weights")
    return sum(value * weight for value, weight in values) / weight_sum


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(value, weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted_average([(float(value), weight) for value, weight in usable])


def build_age_stat_aggregate_summary(
    results: tuple[AgeStatBacktestResult, ...],
) -> AgeStatAggregateSummary:
    if not results:
        raise ProjectionError("Age stat aggregate requires at least one backtest result")

    target_seasons = tuple(result.target_season for result in results)
    total_player_seasons = sum(result.evaluated_players for result in results)
    if total_player_seasons <= 0:
        raise ProjectionError("Age stat aggregate requires evaluated player-seasons")

    strategy_names = tuple(
        metric.strategy_name
        for metric in results[0].metrics
        if metric.stat_name == AGE_STAT_NAMES[0]
    )
    if "neutral" not in strategy_names:
        raise ProjectionError("Age stat aggregate requires a neutral strategy")

    aggregates: list[AgeStatAggregateStrategy] = []
    for stat_name in AGE_STAT_NAMES:
        neutral_by_season = []
        for result in results:
            neutral = next(
                (
                    metric
                    for metric in result.metrics
                    if metric.stat_name == stat_name and metric.strategy_name == "neutral"
                ),
                None,
            )
            if neutral is None:
                raise ProjectionError(
                    f"Missing neutral age metric for {stat_name} in {result.target_season}"
                )
            neutral_by_season.append((neutral, result.evaluated_players))

        neutral_weighted_mae = _weighted_average(
            [(metric.mae, weight) for metric, weight in neutral_by_season]
        )
        neutral_weighted_rho = _weighted_optional(
            [(metric.spearman_rho, weight) for metric, weight in neutral_by_season]
        )

        for strategy_name in strategy_names:
            metrics_by_season = []
            rho_deltas: list[float] = []
            improved_years = 0
            for result, (neutral, weight) in zip(results, neutral_by_season, strict=True):
                metric = next(
                    (
                        candidate
                        for candidate in result.metrics
                        if candidate.stat_name == stat_name
                        and candidate.strategy_name == strategy_name
                    ),
                    None,
                )
                if metric is None:
                    raise ProjectionError(
                        f"Missing age metric for {stat_name}/{strategy_name} "
                        f"in {result.target_season}"
                    )
                metrics_by_season.append((metric, weight))
                if metric.mae < neutral.mae:
                    improved_years += 1
                if metric.spearman_rho is not None and neutral.spearman_rho is not None:
                    rho_deltas.append(metric.spearman_rho - neutral.spearman_rho)

            aggregates.append(
                AgeStatAggregateStrategy(
                    stat_name=stat_name,
                    strategy_name=strategy_name,
                    weighted_mae=_weighted_average(
                        [(metric.mae, weight) for metric, weight in metrics_by_season]
                    ),
                    weighted_rho=_weighted_optional(
                        [(metric.spearman_rho, weight) for metric, weight in metrics_by_season]
                    ),
                    neutral_weighted_mae=neutral_weighted_mae,
                    neutral_weighted_rho=neutral_weighted_rho,
                    improved_years=improved_years,
                    total_years=len(results),
                    worst_rho_delta=min(rho_deltas) if rho_deltas else None,
                )
            )

    return AgeStatAggregateSummary(
        end_season=results[0].target_season,
        target_seasons=target_seasons,
        total_player_seasons=total_player_seasons,
        strategies=tuple(aggregates),
    )
