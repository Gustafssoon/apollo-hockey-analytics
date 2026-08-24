import argparse

from apollo import cli_v24 as v24_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.advanced_signal_backtest import run_advanced_signal_aggregate

SIGNAL_LABELS = {
    "cf60_5v5": "CF60 5v5",
    "ff60_5v5": "FF60 5v5",
    "sat_pct_5v5": "SAT% 5v5",
    "usat_pct_5v5": "USAT% 5v5",
    "sat_relative_5v5": "SAT rel 5v5",
    "usat_relative_5v5": "USAT rel 5v5",
    "zone_start_pct_5v5": "OZ start%",
    "shooting_pct_5v5": "SH% 5v5",
    "toi_per_game_5v5": "TOI/GP 5v5",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v24_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "advanced-signal-summary",
        help="Screen historical 5v5 advanced signals against v0.4 G/SOG residuals",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_advanced_signal_summary(args: argparse.Namespace) -> None:
    result = run_advanced_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO ADVANCED SIGNAL SCREEN")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Baseline v0.4 player-seasons: {result.baseline_player_seasons}")
    print("Signals use source seasons only; target stats are used only to measure v0.4 residuals.")
    print("RHO = correlation with actual - v0.4 projection. QDELTA = top - bottom signal quartile residual.")
    print()
    print(
        f"{'SIGNAL':<14} {'N':>5} {'G RHO':>8} {'G YRS':>6} {'G QDELTA':>9} "
        f"{'SOG RHO':>8} {'SOG YRS':>7} {'SOG QDELTA':>11}"
    )

    ordered = sorted(
        result.metrics,
        key=lambda metric: max(
            abs(metric.weighted_goals_residual_rho or 0.0),
            abs(metric.weighted_shots_residual_rho or 0.0),
        ),
        reverse=True,
    )
    for metric in ordered:
        print(
            f"{SIGNAL_LABELS[metric.signal_name]:<14} {metric.player_seasons:>5} "
            f"{_fmt(metric.weighted_goals_residual_rho):>8} {metric.goals_year_signs:>6} "
            f"{_fmt(metric.weighted_goals_quartile_delta, 2):>9} "
            f"{_fmt(metric.weighted_shots_residual_rho):>8} {metric.shots_year_signs:>7} "
            f"{_fmt(metric.weighted_shots_quartile_delta, 2):>11}"
        )

    print()
    print("Year-sign order matches the target seasons shown above; 0 means |rho| < 0.02.")
    print("This is a diagnostic screen only. No advanced signal is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "advanced-signal-summary":
        v24_cli.main(argv)
        return

    try:
        _draft_advanced_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Advanced signal summary error: {error}") from error
