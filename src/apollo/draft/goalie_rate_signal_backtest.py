from dataclasses import dataclass

from apollo.draft.backtest import spearman_rank_correlation
from apollo.draft.projections import ProjectionError

GOALIE_RATE_SIGNALS = (
    "weighted_save_pct",
    "latest_save_pct",
    "weighted_gaa",
    "latest_gaa",
)


@dataclass(frozen=True, slots=True)
class GoalieRateSignalSeasonMetric:
    signal_name: str
    target_season: int
    player_seasons: int
    residual_rho: float | None
    quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class GoalieRateSignalMetric:
    signal_name: str
    player_seasons: int
    weighted_residual_rho: float | None
    year_signs: str
    weighted_quartile_delta: float | None


@dataclass(frozen=True, slots=True)
class GoalieRateSignalAggregate:
    target_seasons: tuple[int, ...]
    baseline_player_seasons: int
    metrics: tuple[GoalieRateSignalMetric, ...]


def build_signal_metric(
    signal_name: str,
    target_season: int,
    pairs: tuple[tuple[float, float], ...],
) -> GoalieRateSignalSeasonMetric:
    if not pairs:
        return GoalieRateSignalSeasonMetric(signal_name, target_season, 0, None, None)
    signals = [signal for signal, _ in pairs]
    residuals = [residual for _, residual in pairs]
    rho = spearman_rank_correlation(signals, residuals)
    ordered = sorted(pairs, key=lambda pair: pair[0])
    quartile_n = max(1, len(ordered) // 4)
    bottom = ordered[:quartile_n]
    top = ordered[-quartile_n:]
    bottom_mean = sum(residual for _, residual in bottom) / len(bottom)
    top_mean = sum(residual for _, residual in top) / len(top)
    return GoalieRateSignalSeasonMetric(
        signal_name=signal_name,
        target_season=target_season,
        player_seasons=len(pairs),
        residual_rho=rho,
        quartile_delta=top_mean - bottom_mean,
    )


def _year_sign(value: float | None) -> str:
    if value is None or abs(value) < 0.02:
        return "0"
    return "+" if value > 0 else "-"


def build_signal_aggregate(
    target_seasons: tuple[int, ...],
    baseline_player_seasons: int,
    season_metrics: tuple[GoalieRateSignalSeasonMetric, ...],
) -> GoalieRateSignalAggregate:
    if not target_seasons or baseline_player_seasons <= 0:
        raise ProjectionError("Goalie rate signal aggregate requires evaluated goalies")
    metrics: list[GoalieRateSignalMetric] = []
    for signal_name in GOALIE_RATE_SIGNALS:
        rows = [
            metric
            for metric in season_metrics
            if metric.signal_name == signal_name
            and metric.target_season in target_seasons
        ]
        if len(rows) != len(target_seasons):
            raise ProjectionError(f"Missing goalie rate signal seasons for {signal_name}")
        total_n = sum(row.player_seasons for row in rows)
        rho_rows = [row for row in rows if row.residual_rho is not None]
        weighted_rho = (
            None
            if not rho_rows
            else sum(float(row.residual_rho) * row.player_seasons for row in rho_rows)
            / sum(row.player_seasons for row in rho_rows)
        )
        delta_rows = [row for row in rows if row.quartile_delta is not None]
        weighted_delta = (
            None
            if not delta_rows
            else sum(float(row.quartile_delta) * row.player_seasons for row in delta_rows)
            / sum(row.player_seasons for row in delta_rows)
        )
        metrics.append(
            GoalieRateSignalMetric(
                signal_name=signal_name,
                player_seasons=total_n,
                weighted_residual_rho=weighted_rho,
                year_signs="".join(_year_sign(row.residual_rho) for row in rows),
                weighted_quartile_delta=weighted_delta,
            )
        )
    return GoalieRateSignalAggregate(
        target_seasons=target_seasons,
        baseline_player_seasons=baseline_player_seasons,
        metrics=tuple(metrics),
    )
