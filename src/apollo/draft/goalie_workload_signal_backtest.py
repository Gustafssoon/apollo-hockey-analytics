from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import ProjectionError

GOALIE_WORKLOAD_SIGNALS = (
    "latest_start_share",
    "start_share_trend",
    "goalie_age",
)


@dataclass(frozen=True, slots=True)
class GoalieWorkloadSignalSeasonMetric:
    signal_name: str
    target_season: int
    player_seasons: int
    residual_rho: float | None
    quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class GoalieWorkloadSignalAggregateMetric:
    signal_name: str
    player_seasons: int
    weighted_residual_rho: float | None
    year_signs: str
    weighted_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class GoalieWorkloadSignalAggregate:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[GoalieWorkloadSignalAggregateMetric, ...]


def build_signal_metric(
    signal_name: str,
    target_season: int,
    pairs: tuple[tuple[float, float], ...],
) -> GoalieWorkloadSignalSeasonMetric:
    if signal_name not in GOALIE_WORKLOAD_SIGNALS:
        raise ProjectionError(f"Unknown goalie workload signal: {signal_name}")
    if len(pairs) < 4:
        return GoalieWorkloadSignalSeasonMetric(
            signal_name=signal_name,
            target_season=target_season,
            player_seasons=len(pairs),
            residual_rho=None,
            quartile_delta=None,
        )

    signals = [signal for signal, _ in pairs]
    residuals = [residual for _, residual in pairs]
    rho = spearman_rank_correlation(signals, residuals)

    ordered = sorted(pairs, key=lambda item: item[0])
    quartile_size = max(1, len(ordered) // 4)
    bottom = ordered[:quartile_size]
    top = ordered[-quartile_size:]
    bottom_mean = sum(residual for _, residual in bottom) / len(bottom)
    top_mean = sum(residual for _, residual in top) / len(top)
    return GoalieWorkloadSignalSeasonMetric(
        signal_name=signal_name,
        target_season=target_season,
        player_seasons=len(pairs),
        residual_rho=rho,
        quartile_delta=top_mean - bottom_mean,
    )


def _sign(value: float | None) -> str:
    if value is None or abs(value) < 0.02:
        return "0"
    return "+" if value > 0 else "-"


def build_signal_aggregate(
    target_seasons: tuple[int, ...],
    baseline_player_seasons: int,
    season_metrics: tuple[GoalieWorkloadSignalSeasonMetric, ...],
) -> GoalieWorkloadSignalAggregate:
    metrics: list[GoalieWorkloadSignalAggregateMetric] = []
    for signal_name in GOALIE_WORKLOAD_SIGNALS:
        rows = [row for row in season_metrics if row.signal_name == signal_name]
        total_n = sum(row.player_seasons for row in rows)
        rho_rows = [row for row in rows if row.residual_rho is not None]
        qd_rows = [row for row in rows if row.quartile_delta is not None]
        weighted_rho = (
            None
            if not rho_rows
            else sum(float(row.residual_rho) * row.player_seasons for row in rho_rows)
            / sum(row.player_seasons for row in rho_rows)
        )
        weighted_qd = (
            None
            if not qd_rows
            else sum(float(row.quartile_delta) * row.player_seasons for row in qd_rows)
            / sum(row.player_seasons for row in qd_rows)
        )
        metrics.append(
            GoalieWorkloadSignalAggregateMetric(
                signal_name=signal_name,
                player_seasons=total_n,
                weighted_residual_rho=weighted_rho,
                year_signs="".join(_sign(row.residual_rho) for row in rows),
                weighted_quartile_delta=weighted_qd,
            )
        )
    return GoalieWorkloadSignalAggregate(
        target_seasons=target_seasons,
        baseline_player_seasons=baseline_player_seasons,
        metrics=tuple(metrics),
    )
