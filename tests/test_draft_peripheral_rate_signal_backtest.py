import pytest

from apollo import cli_v40
from apollo.draft.peripheral_rate_signal_backtest import (
    PeripheralRateSignalPlayer,
    build_peripheral_rate_signal_aggregate_result,
    build_peripheral_rate_signal_backtest_result,
    build_weighted_peripheral_rate_signals,
    peripheral_rate_value,
)
from apollo.draft.projections import ProjectionError
from apollo.services.peripheral_rate_signal_backtest import _peripheral_prior_history


def _metric(result, signal_name: str):
    return next(metric for metric in result.metrics if metric.signal_name == signal_name)


def test_peripheral_rate_values_map_category_per_game_rates():
    stats = {
        "gamesPlayed": 80.0,
        "shots": 240.0,
        "hits": 160.0,
        "blockedShots": 80.0,
    }

    assert peripheral_rate_value("sog_pg_ratio", stats) == pytest.approx(3.0)
    assert peripheral_rate_value("hit_pg_ratio", stats) == pytest.approx(2.0)
    assert peripheral_rate_value("blk_pg_ratio", stats) == pytest.approx(1.0)
    with pytest.raises(ProjectionError, match="Unknown peripheral rate signal"):
        peripheral_rate_value("not_a_signal", stats)


def test_weighted_peripheral_rates_use_source_priors_and_require_three_seasons():
    history = (
        {"gamesPlayed": 80.0, "shots": 240.0, "hits": 160.0, "blockedShots": 80.0},
        {"gamesPlayed": 80.0, "shots": 200.0, "hits": 120.0, "blockedShots": 64.0},
        {"gamesPlayed": 80.0, "shots": 160.0, "hits": 80.0},
    )
    priors = (
        {"sog_pg_ratio": 2.0, "hit_pg_ratio": 1.0, "blk_pg_ratio": 0.8},
        {"sog_pg_ratio": 2.0, "hit_pg_ratio": 1.0, "blk_pg_ratio": 0.8},
        {"sog_pg_ratio": 2.0, "hit_pg_ratio": 1.0, "blk_pg_ratio": 0.8},
    )

    weighted = build_weighted_peripheral_rate_signals(history, priors)

    assert weighted["sog_pg_ratio"] == pytest.approx(1.375)
    assert weighted["hit_pg_ratio"] == pytest.approx(1.75)
    assert "blk_pg_ratio" not in weighted


def test_peripheral_prior_history_uses_only_requested_source_seasons_and_position_group():
    priors = {
        (20242025, "F", "shots"): 2.5,
        (20242025, "F", "hits"): 1.5,
        (20242025, "F", "blockedShots"): 0.7,
        (20232024, "F", "shots"): 2.4,
        (20232024, "F", "hits"): 1.4,
        (20232024, "F", "blockedShots"): 0.6,
        (20222023, "F", "shots"): 2.3,
        (20222023, "F", "hits"): 1.3,
        (20222023, "F", "blockedShots"): 0.5,
        (20252026, "F", "shots"): 99.0,
        (20242025, "D", "shots"): 99.0,
    }

    history = _peripheral_prior_history(priors, (20242025, 20232024, 20222023), "F")

    assert history[0]["sog_pg_ratio"] == pytest.approx(2.5)
    assert history[1]["hit_pg_ratio"] == pytest.approx(1.4)
    assert history[2]["blk_pg_ratio"] == pytest.approx(0.5)
    assert all(value != 99.0 for season in history for value in season.values())


def test_peripheral_screen_detects_known_negative_shot_residual_signal():
    players = tuple(
        PeripheralRateSignalPlayer(
            player_id=index,
            player_name=f"Player {index}",
            projected_stats={"shots": 100.0, "hits": 50.0, "blockedShots": 25.0},
            actual_stats={
                "shots": 100.0 - index,
                "hits": 50.0,
                "blockedShots": 25.0,
            },
            weighted_signals={"sog_pg_ratio": float(index)},
        )
        for index in range(1, 5)
    )

    result = build_peripheral_rate_signal_backtest_result(
        target_season=20252026,
        source_seasons=(20242025, 20232024, 20222023),
        baseline_eligible_players=4,
        players=players,
    )
    metric = _metric(result, "sog_pg_ratio")

    assert metric.evaluated_players == 4
    assert metric.residual_rho == pytest.approx(-1.0)
    assert metric.quartile_delta == pytest.approx(-3.0)


def test_peripheral_aggregate_tracks_year_signs_and_cli_contract():
    def season_result(season: int):
        players = tuple(
            PeripheralRateSignalPlayer(
                player_id=index,
                player_name=f"Player {index}",
                projected_stats={"shots": 100.0, "hits": 50.0, "blockedShots": 25.0},
                actual_stats={
                    "shots": 100.0,
                    "hits": 50.0 - index,
                    "blockedShots": 25.0,
                },
                weighted_signals={"hit_pg_ratio": float(index)},
            )
            for index in range(1, 5)
        )
        return build_peripheral_rate_signal_backtest_result(
            target_season=season,
            source_seasons=(20242025, 20232024, 20222023),
            baseline_eligible_players=4,
            players=players,
        )

    result = build_peripheral_rate_signal_aggregate_result(
        (
            season_result(20252026),
            season_result(20242025),
            season_result(20232024),
        )
    )
    metric = _metric(result, "hit_pg_ratio")

    assert result.baseline_player_seasons == 12
    assert metric.year_signs == "---"
    assert metric.weighted_residual_rho == pytest.approx(-1.0)

    args = cli_v40.build_parser().parse_args(
        ["draft", "peripheral-rate-signal-summary", "--season", "20252026"]
    )
    assert args.command == "draft"
    assert args.draft_command == "peripheral-rate-signal-summary"
    assert args.years == 3
    assert args.min_history_seasons == 3
