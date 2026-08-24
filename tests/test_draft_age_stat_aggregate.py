import pytest

from apollo import cli_v17
from apollo.db import Database
from apollo.draft.age_backtest import AGE_CURVE_STRATEGIES
from apollo.draft.age_stat_aggregate import build_age_stat_aggregate_summary
from apollo.draft.age_stat_backtest import (
    AGE_STAT_NAMES,
    AgeStatBacktestResult,
    AgeStatStrategyResult,
)
from apollo.services import age_stat_aggregate as aggregate_service


def _result(
    season: int,
    players: int,
    *,
    neutral_mae: float,
    medium_mae: float,
    asymmetric_mae: float,
    neutral_rho: float = 0.800,
    medium_rho: float = 0.805,
    asymmetric_rho: float = 0.804,
) -> AgeStatBacktestResult:
    metrics: list[AgeStatStrategyResult] = []
    for stat_name in AGE_STAT_NAMES:
        for strategy in AGE_CURVE_STRATEGIES:
            if strategy.name == "neutral":
                mae = neutral_mae
                rho = neutral_rho
            elif strategy.name == "medium":
                mae = medium_mae
                rho = medium_rho
            elif strategy.name == "asymmetric":
                mae = asymmetric_mae
                rho = asymmetric_rho
            else:
                mae = neutral_mae + 1.0
                rho = neutral_rho - 0.01
            metrics.append(
                AgeStatStrategyResult(
                    stat_name=stat_name,
                    strategy_name=strategy.name,
                    mae=mae,
                    spearman_rho=rho,
                )
            )
    return AgeStatBacktestResult(
        target_season=season,
        source_seasons=(),
        base_eligible_players=players,
        evaluated_players=players,
        birth_date_coverage=1.0,
        metrics=tuple(metrics),
    )


def test_age_stat_aggregate_uses_player_season_weighted_mae():
    summary = build_age_stat_aggregate_summary(
        (
            _result(
                20252026,
                100,
                neutral_mae=10.0,
                medium_mae=9.0,
                asymmetric_mae=9.5,
            ),
            _result(
                20242025,
                300,
                neutral_mae=14.0,
                medium_mae=12.0,
                asymmetric_mae=11.5,
            ),
        )
    )

    points = [strategy for strategy in summary.strategies if strategy.stat_name == "points"]
    best = min(points, key=lambda strategy: strategy.weighted_mae)

    assert summary.total_player_seasons == 400
    assert best.strategy_name == "asymmetric"
    assert best.weighted_mae == pytest.approx(11.0)
    assert best.neutral_weighted_mae == pytest.approx(13.0)
    assert best.mae_gain == pytest.approx(2.0)
    assert best.improved_years == 2
    assert best.total_years == 2


def test_age_stat_aggregate_can_leave_neutral_as_best_strategy():
    summary = build_age_stat_aggregate_summary(
        (
            _result(
                20252026,
                100,
                neutral_mae=10.0,
                medium_mae=10.5,
                asymmetric_mae=10.2,
            ),
        )
    )

    points = [strategy for strategy in summary.strategies if strategy.stat_name == "points"]
    best = min(points, key=lambda strategy: strategy.weighted_mae)

    assert best.strategy_name == "neutral"
    assert best.mae_gain == pytest.approx(0.0)
    assert best.improved_years == 0


def test_age_stat_aggregate_service_uses_consecutive_target_seasons(monkeypatch, tmp_path):
    calls: list[int] = []

    def fake_backtest(database, target_season, *, min_actual_games=20):
        assert isinstance(database, Database)
        assert min_actual_games == 20
        calls.append(target_season)
        return _result(
            target_season,
            10,
            neutral_mae=10.0,
            medium_mae=9.0,
            asymmetric_mae=9.5,
        )

    monkeypatch.setattr(aggregate_service, "run_age_stat_backtest", fake_backtest)

    summary = aggregate_service.run_age_stat_aggregate(
        Database(tmp_path / "apollo.db"),
        20252026,
        years=3,
    )

    assert calls == [20252026, 20242025, 20232024]
    assert summary.target_seasons == (20252026, 20242025, 20232024)
    assert summary.total_player_seasons == 30


def test_age_stat_aggregate_cli(monkeypatch, capsys):
    summary = build_age_stat_aggregate_summary(
        (
            _result(
                20252026,
                100,
                neutral_mae=10.0,
                medium_mae=9.0,
                asymmetric_mae=9.5,
            ),
            _result(
                20242025,
                100,
                neutral_mae=10.0,
                medium_mae=9.0,
                asymmetric_mae=9.5,
            ),
            _result(
                20232024,
                100,
                neutral_mae=10.0,
                medium_mae=9.0,
                asymmetric_mae=9.5,
            ),
        )
    )
    monkeypatch.setattr(cli_v17, "run_age_stat_aggregate", lambda *args, **kwargs: summary)

    cli_v17.main(["draft", "age-stats-summary", "--season", "20252026", "--years", "3"])

    output = capsys.readouterr().out
    assert "APOLLO AGE STAT AGGREGATE" in output
    assert "Target seasons: 2025-26, 2024-25, 2023-24" in output
    assert "Player-seasons: 300" in output
    assert "YEARS+" in output
    assert "RUNNER" in output
    assert "medium" in output
