from dataclasses import dataclass

from apollo.draft.backtest import ProjectionBacktestResult
from apollo.draft.projections import ProjectionError

PP_DEPLOYMENT_CANDIDATE_SIGNAL = "pp_toi_ratio"
PP_DEPLOYMENT_CANDIDATE_STRENGTH = 0.05
PP_DEPLOYMENT_CANDIDATE_VERSION = "apollo-ppp-v0.1-candidate-pp-toi-shrink5"
PP_DEPLOYMENT_ROBUSTNESS_COHORTS = (
    ("GP20 ALL", 20, None),
    ("GP30 ALL", 30, None),
    ("GP40 ALL", 40, None),
    ("GP20 F", 20, "F"),
    ("GP20 D", 20, "D"),
)


@dataclass(frozen=True, slots=True)
class PPDeploymentGateMetric:
    stat_name: str
    baseline_mae: float
    candidate_mae: float
    mae_gain: float
    baseline_rho: float | None
    candidate_rho: float | None


@dataclass(frozen=True, slots=True)
class PPDeploymentGateCohort:
    label: str
    min_actual_games: int
    position_group: str | None
    player_seasons: int
    applied: int
    metrics: tuple[PPDeploymentGateMetric, ...]
    ppp_improved_years: int
    worst_ppp_gain: float


@dataclass(frozen=True, slots=True)
class PPDeploymentGateResult:
    latest_target_season: int
    target_seasons: tuple[int, ...]
    cohorts: tuple[PPDeploymentGateCohort, ...]


def _metric(result: ProjectionBacktestResult, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _weighted(values: list[tuple[float, int]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0:
        raise ProjectionError("PP deployment candidate gate requires evaluated skaters")
    return sum(value * weight for value, weight in values) / total


def _weighted_optional(values: list[tuple[float | None, int]]) -> float | None:
    usable = [(float(value), weight) for value, weight in values if value is not None]
    if not usable:
        return None
    return _weighted(usable)


def build_pp_deployment_gate_cohort(
    *,
    label: str,
    min_actual_games: int,
    position_group: str | None,
    season_results: tuple[tuple[ProjectionBacktestResult, ProjectionBacktestResult, int], ...],
) -> PPDeploymentGateCohort:
    if not season_results:
        raise ProjectionError("PP deployment candidate gate cohort requires season results")

    stat_names = (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "powerPlayPoints",
        "shots",
        "hits",
        "blockedShots",
    )
    metrics: list[PPDeploymentGateMetric] = []
    for stat_name in stat_names:
        baseline_mae = _weighted(
            [(_metric(base, stat_name).mae, base.evaluated_players) for base, _, _ in season_results]
        )
        candidate_mae = _weighted(
            [
                (_metric(candidate, stat_name).mae, candidate.evaluated_players)
                for _, candidate, _ in season_results
            ]
        )
        baseline_rho = _weighted_optional(
            [
                (_metric(base, stat_name).spearman_rho, base.evaluated_players)
                for base, _, _ in season_results
            ]
        )
        candidate_rho = _weighted_optional(
            [
                (_metric(candidate, stat_name).spearman_rho, candidate.evaluated_players)
                for _, candidate, _ in season_results
            ]
        )
        metrics.append(
            PPDeploymentGateMetric(
                stat_name=stat_name,
                baseline_mae=baseline_mae,
                candidate_mae=candidate_mae,
                mae_gain=baseline_mae - candidate_mae,
                baseline_rho=baseline_rho,
                candidate_rho=candidate_rho,
            )
        )

    ppp_gains = [
        _metric(base, "powerPlayPoints").mae - _metric(candidate, "powerPlayPoints").mae
        for base, candidate, _ in season_results
    ]
    return PPDeploymentGateCohort(
        label=label,
        min_actual_games=min_actual_games,
        position_group=position_group,
        player_seasons=sum(base.evaluated_players for base, _, _ in season_results),
        applied=sum(applied for _, _, applied in season_results),
        metrics=tuple(metrics),
        ppp_improved_years=sum(gain > 0 for gain in ppp_gains),
        worst_ppp_gain=min(ppp_gains),
    )


def build_pp_deployment_gate_result(
    *,
    latest_target_season: int,
    target_seasons: tuple[int, ...],
    cohorts: tuple[PPDeploymentGateCohort, ...],
) -> PPDeploymentGateResult:
    if not cohorts:
        raise ProjectionError("PP deployment candidate gate requires cohorts")
    return PPDeploymentGateResult(
        latest_target_season=latest_target_season,
        target_seasons=target_seasons,
        cohorts=cohorts,
    )
