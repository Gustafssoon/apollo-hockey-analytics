import argparse

from apollo import cli_v25 as v25_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.advanced_pdo_backtest import run_advanced_pdo_aggregate

SIGNAL_LABELS = {
    "shooting_pct_5v5": "SH% 5v5",
    "save_pct_5v5": "SV% 5v5",
    "pdo_5v5": "PDO 5v5",
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
    parser = v25_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "advanced-pdo-summary",
        help="Screen source-only 5v5 shooting/save/PDO against v0.4 G/A/PTS residuals",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_advanced_pdo_summary(args: argparse.Namespace) -> None:
    result = run_advanced_pdo_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO ADVANCED PDO / LUCK SCREEN")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Baseline v0.4 player-seasons: {result.baseline_player_seasons}")
    print("Signals use source seasons only; target stats only measure v0.4 residuals.")
    print("PDO = 5v5 on-ice shooting% + 5v5 on-ice save%.")
    print("RHO = correlation with actual - v0.4. QDELTA = top - bottom signal quartile residual.")
    print()
    print(
        f"{'SIGNAL':<10} {'N':>5} {'G RHO':>8} {'G YRS':>6} {'G QD':>7} "
        f"{'A RHO':>8} {'A YRS':>6} {'A QD':>7} {'PTS RHO':>8} {'PTS YRS':>7} {'PTS QD':>8}"
    )

    ordered = sorted(
        result.metrics,
        key=lambda metric: abs(metric.weighted_points_residual_rho or 0.0),
        reverse=True,
    )
    for metric in ordered:
        print(
            f"{SIGNAL_LABELS[metric.signal_name]:<10} {metric.player_seasons:>5} "
            f"{_fmt(metric.weighted_goals_residual_rho):>8} {metric.goals_year_signs:>6} "
            f"{_fmt(metric.weighted_goals_quartile_delta, 2):>7} "
            f"{_fmt(metric.weighted_assists_residual_rho):>8} {metric.assists_year_signs:>6} "
            f"{_fmt(metric.weighted_assists_quartile_delta, 2):>7} "
            f"{_fmt(metric.weighted_points_residual_rho):>8} {metric.points_year_signs:>7} "
            f"{_fmt(metric.weighted_points_quartile_delta, 2):>8}"
        )

    print()
    print("Year-sign order matches target seasons; 0 means |rho| < 0.02.")
    print("Diagnostic only. No PDO/luck correction is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "advanced-pdo-summary":
        v25_cli.main(argv)
        return

    try:
        _draft_advanced_pdo_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Advanced PDO summary error: {error}") from error
