import argparse

from apollo import cli_v28 as v28_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.scoring_rate_signal_backtest import run_scoring_rate_signal_aggregate

SIGNAL_LABELS = {
    "g60_5v5": "G/60 5v5",
    "a60_5v5": "A/60 5v5",
    "pts60_5v5": "PTS/60 5v5",
    "primary_a60_5v5": "Primary A/60",
    "secondary_a60_5v5": "Secondary A/60",
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
    parser = v28_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "scoring-rate-signal-summary",
        help="Screen source-only individual 5v5 scoring rates against production v0.5 residuals",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_scoring_rate_signal_summary(args: argparse.Namespace) -> None:
    result = run_scoring_rate_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO INDIVIDUAL 5v5 SCORING-RATE SIGNAL SCREEN")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production v0.5 player-seasons: {result.baseline_player_seasons}")
    print("Signals use source seasons only; target stats only measure v0.5 residuals.")
    print("RHO = correlation with actual - v0.5. QD = top - bottom signal quartile residual.")
    print()
    print(
        f"{'SIGNAL':<16} {'N':>5} "
        f"{'G RHO':>7} {'G YRS':>5} {'G QD':>7} "
        f"{'A RHO':>7} {'A YRS':>5} {'A QD':>7} "
        f"{'PTS RHO':>8} {'PTS YRS':>7} {'PTS QD':>8}"
    )
    for metric in result.metrics:
        print(
            f"{SIGNAL_LABELS[metric.signal_name]:<16} {metric.player_seasons:>5} "
            f"{_fmt(metric.weighted_goals_residual_rho):>7} "
            f"{metric.goals_year_signs:>5} "
            f"{_fmt_delta(metric.weighted_goals_quartile_delta):>7} "
            f"{_fmt(metric.weighted_assists_residual_rho):>7} "
            f"{metric.assists_year_signs:>5} "
            f"{_fmt_delta(metric.weighted_assists_quartile_delta):>7} "
            f"{_fmt(metric.weighted_points_residual_rho):>8} "
            f"{metric.points_year_signs:>7} "
            f"{_fmt_delta(metric.weighted_points_quartile_delta):>8}"
        )

    print()
    print("Year-sign order matches target seasons; 0 means |rho| < 0.02.")
    print("Diagnostic only. No scoring-rate signal is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "scoring-rate-signal-summary":
        v28_cli.main(argv)
        return

    try:
        _draft_scoring_rate_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Scoring-rate signal summary error: {error}") from error
