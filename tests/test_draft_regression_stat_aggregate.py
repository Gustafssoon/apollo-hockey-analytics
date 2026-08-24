from apollo import cli_v22
from apollo.draft.backtest import TopKOverlap
from apollo.draft.regression_backtest import (
    REGRESSION_STATS,
    RegressionBacktestResult,
    RegressionMetric,
    RegressionStrategyResult,
)
from apollo.draft.regression_stat_aggregate import (
    RegressionStatAggregateStrategy,
    RegressionStatAggregateSummary,
    build_regression_stat_aggregate_summary,
)


def _strategy(name: str, mae: float, rho: float) -> RegressionStrategyResult:
    return RegressionStrategyResult(
        strategy_name=name,
        pseudo_games=None if name == "baseline_v03" else 5.0,
        metrics=tuple(
            RegressionMetric(stat_name=stat, mae=mae, spearman_rho=rho)
            for stat in REGRESSION_STATS
        ),
        top_k_points=(TopKOverlap(25, 25, 10, 0.4),),
    )


def _result(
    season: int,
    players: int,
    *,
    baseline_mae: float,
    regress_mae: float,
    baseline_rho: float = 0.80,
    regress_rho: float = 0.81,
) -> RegressionBacktestResult:
    return RegressionBacktestResult(
        target_season=season,
        source_seasons=(),
        evaluated_players=players,
        priors={},
        strategies=(
            _strategy("baseline_v03", baseline_mae, baseline_rho),
            _strategy("regress_pos_5", regress_mae, regress_rho),
        ),
    )


def test_regression_stat_aggregate_weights_mae_by_player_seasons():
    summary = build_regression_stat_aggregate_summary(
        (
            _result(20252026, 100, baseline_mae=10.0, regress_mae=9.0),
            _result(20242025, 10, baseline_mae=20.0, regress_mae=19.0),
        )
    )
    pts = next(
        strategy
        for strategy in summary.strategies
        if strategy.stat_name == "points" and strategy.strategy_name == "regress_pos_5"
    )

    assert summary.total_player_seasons == 110
    assert pts.weighted_mae == (9.0 * 100 + 19.0 * 10) / 110
    assert pts.baseline_weighted_mae == (10.0 * 100 + 20.0 * 10) / 110
    assert pts.mae_gain == 1.0


def test_regression_stat_aggregate_tracks_years_and_worst_rho_delta():
    summary = build_regression_stat_aggregate_summary(
        (
            _result(
                20252026,
                100,
                baseline_mae=10.0,
                regress_mae=9.5,
                baseline_rho=0.80,
                regress_rho=0.82,
            ),
            _result(
                20242025,
                100,
                baseline_mae=10.0,
                regress_mae=10.2,
                baseline_rho=0.80,
                regress_rho=0.79,
            ),
        )
    )
    pts = next(
        strategy
        for strategy in summary.strategies
        if strategy.stat_name == "points" and strategy.strategy_name == "regress_pos_5"
    )

    assert pts.improved_years == 1
    assert pts.total_years == 2
    assert round(pts.worst_rho_delta or 0.0, 3) == -0.01


def test_regression_stat_aggregate_uses_fixed_strategy_across_seasons():
    first = _result(20252026, 100, baseline_mae=10.0, regress_mae=9.0)
    second = _result(20242025, 100, baseline_mae=10.0, regress_mae=11.0)
    summary = build_regression_stat_aggregate_summary((first, second))
    pts = next(
        strategy
        for strategy in summary.strategies
        if strategy.stat_name == "points" and strategy.strategy_name == "regress_pos_5"
    )

    assert pts.weighted_mae == 10.0
    assert pts.improved_years == 1
    assert pts.mae_gain == 0.0


def test_regression_summary_cli_reports_fixed_winners(monkeypatch, capsys):
    strategies = []
    for stat in REGRESSION_STATS:
        strategies.extend(
            (
                RegressionStatAggregateStrategy(
                    stat_name=stat,
                    strategy_name="baseline_v03",
                    weighted_mae=10.0,
                    weighted_rho=0.80,
                    baseline_weighted_mae=10.0,
                    baseline_weighted_rho=0.80,
                    improved_years=0,
                    total_years=3,
                    worst_rho_delta=0.0,
                ),
                RegressionStatAggregateStrategy(
                    stat_name=stat,
                    strategy_name="regress_pos_5",
                    weighted_mae=9.5,
                    weighted_rho=0.81,
                    baseline_weighted_mae=10.0,
                    baseline_weighted_rho=0.80,
                    improved_years=3,
                    total_years=3,
                    worst_rho_delta=-0.001,
                ),
            )
        )
    summary = RegressionStatAggregateSummary(
        end_season=20252026,
        target_seasons=(20252026, 20242025, 20232024),
        total_player_seasons=1141,
        strategies=tuple(strategies),
    )
    monkeypatch.setattr(cli_v22, "run_regression_stat_aggregate", lambda *args, **kwargs: summary)

    cli_v22.main(["draft", "regression-summary", "--season", "20252026"])
    output = capsys.readouterr().out

    assert "APOLLO REGRESSION STAT AGGREGATE" in output
    assert "Player-seasons: 1141" in output
    assert "regress_pos_5" in output
    assert "Fixed strategies only" in output
