import argparse

from apollo import cli_v31 as v31_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.shot_type_signal_backtest import run_shot_type_signal_aggregate

SIGNAL_LABELS = {
    "tip_deflect_shot_share": "Tip+Defl shot%",
    "wrist_shot_share": "Wrist shot%",
    "snap_shot_share": "Snap shot%",
    "overall_shooting_pct": "Overall SH%",
    "tip_deflect_shooting_pct": "Tip+Defl SH%",
    "wrist_shooting_pct": "Wrist SH%",
    "snap_shooting_pct": "Snap SH%",
    "tip_deflect_goal_share": "Tip+Defl goal%",
    "wrist_goal_share": "Wrist goal%",
    "snap_goal_share": "Snap goal%",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _fmt_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v31_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "shot-type-signal-summary",
        help="Screen source-only individual shot profiles against production v0.6 residuals",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_shot_type_signal_summary(args: argparse.Namespace) -> None:
    result = run_shot_type_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO SHOT-TYPE GOAL SIGNAL SCREEN")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production v0.6 player-seasons: {result.baseline_player_seasons}")
    print("Signals use source seasons only; target shot-type fields are never used as features.")
    print("Target G/A only measure actual - production v0.6 residuals.")
    print("Missing 3/3 shot-type context reduces that signal's N, never baseline coverage.")
    print("RHO = residual rank correlation. QD = top - bottom signal quartile residual.")
    print()
    print(
        f"{'SIGNAL':<16} {'N':>5} {'COV':>6} "
        f"{'G RHO':>7} {'G YRS':>5} {'G QD':>7} "
        f"{'PTS RHO':>8} {'PTS YRS':>7} {'PTS QD':>8}"
    )
    for metric in result.metrics:
        coverage = (
            metric.player_seasons / result.baseline_player_seasons
            if result.baseline_player_seasons
            else 0.0
        )
        print(
            f"{SIGNAL_LABELS[metric.signal_name]:<16} {metric.player_seasons:>5} "
            f"{coverage * 100:>5.1f}% "
            f"{_fmt(metric.weighted_goals_residual_rho):>7} "
            f"{metric.goals_year_signs:>5} "
            f"{_fmt_delta(metric.weighted_goals_quartile_delta):>7} "
            f"{_fmt(metric.weighted_points_residual_rho):>8} "
            f"{metric.points_year_signs:>7} "
            f"{_fmt_delta(metric.weighted_points_quartile_delta):>8}"
        )

    print()
    print("Year-sign order matches target seasons; 0 means |rho| < 0.02.")
    print("Diagnostic only. No shot-type signal is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "shot-type-signal-summary":
        v31_cli.main(argv)
        return

    try:
        _draft_shot_type_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Shot-type signal summary error: {error}") from error
