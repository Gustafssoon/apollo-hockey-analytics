import argparse

from apollo import cli_v12 as v12_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.draft_backtest import run_skater_backtest

STAT_LABELS = {
    "gamesPlayed": "GP",
    "points": "PTS",
    "goals": "G",
    "assists": "A",
    "powerPlayPoints": "PPP",
    "shots": "SOG",
    "hits": "HIT",
    "blockedShots": "BLK",
}


def build_parser() -> argparse.ArgumentParser:
    return v12_cli.build_parser()


def _season_label(season: int) -> str:
    text = str(season)
    if len(text) == 8:
        return f"{text[:4]}-{text[6:]}"
    return text


def _format_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _draft_backtest(args: argparse.Namespace) -> None:
    result = run_skater_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO PROJECTION BACKTEST")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    source_seasons = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {source_seasons}")
    print(f"Model: {result.model_version}")
    print(
        f"Filters: actual GP >= {result.min_actual_games} | "
        f"history seasons >= {result.min_history_seasons}"
    )
    print()

    print("Coverage")
    print("--------")
    print(
        f"Evaluated: {result.evaluated_players}/{result.actual_eligible_players} "
        f"({result.coverage * 100:.1f}%)"
    )
    for history_seasons, player_count in result.history_counts:
        print(f"{history_seasons} history seasons: {player_count}")
    if result.skipped_incomplete_history:
        print(f"Skipped incomplete historical stat sets: {result.skipped_incomplete_history}")
    print()

    print("Mean Absolute Error")
    print("-------------------")
    for metric in result.metrics:
        print(f"{STAT_LABELS[metric.stat_name]:<5} {metric.mae:>8.2f}")
    print()

    print("Rank Quality - Spearman rho")
    print("---------------------------")
    for metric in result.metrics:
        print(f"{STAT_LABELS[metric.stat_name]:<5} {_format_rho(metric.spearman_rho):>8}")
    print()

    print("Actual-GP Oracle Diagnostic")
    print("---------------------------")
    print(f"{'STAT':<5} {'BASE MAE':>9} {'ORACLE MAE':>11} {'GAIN':>9} {'BASE RHO':>9} {'ORACLE RHO':>11}")
    for metric in result.metrics:
        if metric.oracle_gp_mae is None:
            continue
        gain = metric.mae - metric.oracle_gp_mae
        print(
            f"{STAT_LABELS[metric.stat_name]:<5} "
            f"{metric.mae:>9.2f} {metric.oracle_gp_mae:>11.2f} {gain:>+9.2f} "
            f"{_format_rho(metric.spearman_rho):>9} "
            f"{_format_rho(metric.oracle_gp_spearman_rho):>11}"
        )
    print()
    print("GAIN = baseline MAE - oracle MAE; positive means GP uncertainty caused error.")
    print()

    print("Top-K PTS Overlap")
    print("-----------------")
    for baseline, oracle in zip(
        result.top_k_points,
        result.oracle_gp_top_k_points,
        strict=True,
    ):
        print(
            f"Top {baseline.requested_k:<3} "
            f"base {baseline.overlap}/{baseline.compared_k} "
            f"({baseline.overlap_rate * 100:.1f}%) | "
            f"actual-GP {oracle.overlap}/{oracle.compared_k} "
            f"({oracle.overlap_rate * 100:.1f}%)"
        )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "backtest":
        v12_cli.main(argv)
        return

    try:
        _draft_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"Backtest error: {error}") from error
