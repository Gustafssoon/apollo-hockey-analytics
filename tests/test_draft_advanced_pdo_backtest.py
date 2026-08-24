import pytest

from apollo.draft.advanced_pdo_backtest import (
    AdvancedPDOPlayer,
    build_advanced_pdo_aggregate_result,
    build_advanced_pdo_backtest_result,
    build_weighted_pdo_signals,
    pdo_signal_value,
)


def test_pdo_signal_value_sums_shooting_and_save_percentages():
    stats = {"shootingPct5v5": 0.09, "skaterSavePct5v5": 0.91}

    assert pdo_signal_value("shooting_pct_5v5", stats) == pytest.approx(0.09)
    assert pdo_signal_value("save_pct_5v5", stats) == pytest.approx(0.91)
    assert pdo_signal_value("pdo_5v5", stats) == pytest.approx(1.00)


def test_weighted_pdo_signals_use_calendar_weights_and_require_three_seasons():
    complete = build_weighted_pdo_signals(
        (
            {"shootingPct5v5": 0.12, "skaterSavePct5v5": 0.90},
            {"shootingPct5v5": 0.10, "skaterSavePct5v5": 0.91},
            {"shootingPct5v5": 0.08, "skaterSavePct5v5": 0.92},
        )
    )
    incomplete = build_weighted_pdo_signals(
        (
            {"shootingPct5v5": 0.12, "skaterSavePct5v5": 0.90},
            {"shootingPct5v5": 0.10},
            {"shootingPct5v5": 0.08, "skaterSavePct5v5": 0.92},
        )
    )

    assert complete["shooting_pct_5v5"] == pytest.approx(0.11)
    assert complete["save_pct_5v5"] == pytest.approx(0.907)
    assert complete["pdo_5v5"] == pytest.approx(1.017)
    assert "shooting_pct_5v5" in incomplete
    assert "save_pct_5v5" not in incomplete
    assert "pdo_5v5" not in incomplete


def test_pdo_backtest_detects_negative_points_residual_signal():
    players = tuple(
        AdvancedPDOPlayer(
            player_id=index,
            player_name=f"Player {index}",
            baseline_goals=20.0,
            baseline_assists=30.0,
            actual_goals=20.0 + residual,
            actual_assists=30.0 + residual,
            weighted_signals={"pdo_5v5": signal},
        )
        for index, (signal, residual) in enumerate(
            ((0.97, 3.0), (0.99, 1.0), (1.01, -1.0), (1.03, -3.0)),
            start=1,
        )
    )

    result = build_advanced_pdo_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )
    metric = next(metric for metric in result.metrics if metric.signal_name == "pdo_5v5")

    assert metric.points_residual_rho == pytest.approx(-1.0)
    assert metric.points_quartile_delta == pytest.approx(-12.0)


def test_pdo_aggregate_preserves_target_season_sign_order():
    results = []
    for season, residuals in (
        (20252026, (2.0, 1.0, -1.0, -2.0)),
        (20242025, (3.0, 1.0, -1.0, -3.0)),
        (20232024, (1.0, 0.5, -0.5, -1.0)),
    ):
        players = tuple(
            AdvancedPDOPlayer(
                player_id=index,
                player_name=f"Player {index}",
                baseline_goals=20.0,
                baseline_assists=30.0,
                actual_goals=20.0 + residual,
                actual_assists=30.0 + residual,
                weighted_signals={"pdo_5v5": signal},
            )
            for index, (signal, residual) in enumerate(
                zip((0.97, 0.99, 1.01, 1.03), residuals, strict=True),
                start=1,
            )
        )
        results.append(
            build_advanced_pdo_backtest_result(
                target_season=season,
                source_seasons=(season - 10001, season - 20002, season - 30003),
                baseline_eligible_players=4,
                players=players,
            )
        )

    aggregate = build_advanced_pdo_aggregate_result(tuple(results))
    metric = next(metric for metric in aggregate.metrics if metric.signal_name == "pdo_5v5")

    assert aggregate.target_seasons == (20252026, 20242025, 20232024)
    assert metric.points_year_signs == "---"
    assert metric.weighted_points_residual_rho == pytest.approx(-1.0)
