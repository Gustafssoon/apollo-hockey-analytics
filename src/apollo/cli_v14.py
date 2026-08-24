import argparse

from apollo import cli_v13 as v13_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.gp_backtest import run_gp_baseline_backtest


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    if len(text) == 8:
        return f"{text[:4]}-{text[6:]}"
    return text


def _format_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v13_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    gp_parser = draft_subparsers.add_parser(
        "gp-backtest",
        help="Compare simple games-played projection strategies",
    )
    gp_parser.add_argument("--season", type=int, required=True, help="Actual target season id")
    gp_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    gp_parser.add_argument(
        "--min-actual-games",
        type=int,
        default=20,
        help="Minimum actual games played in the target season",
    )
    return parser


def _draft_gp_backtest(args: argparse.Namespace) -> None:
    result = run_gp_baseline_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
    )

    print("APOLLO GP BASELINE SHOOTOUT")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    sources = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {sources}")
    print(f"Evaluated skaters: {result.evaluated_players}")
    print()
    print(
        f"{'RK':>2} {'STRATEGY':<20} {'GP MAE':>8} {'GP RHO':>8} "
        f"{'PTS MAE':>9} {'PTS RHO':>8} {'TOP25':>8}"
    )
    ordered = sorted(result.strategies, key=lambda strategy: strategy.points_mae)
    for rank, strategy in enumerate(ordered, start=1):
        top25 = next(overlap for overlap in strategy.top_k_points if overlap.requested_k == 25)
        top25_text = f"{top25.overlap_rate * 100:.0f}%"
        print(
            f"{rank:>2} {strategy.name:<20} {strategy.gp_mae:>8.2f} "
            f"{_format_rho(strategy.gp_spearman_rho):>8} {strategy.points_mae:>9.2f} "
            f"{_format_rho(strategy.points_spearman_rho):>8} {top25_text:>8}"
        )
    print()
    print("Ranked by PTS MAE; no strategy is promoted to production by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "gp-backtest":
        v13_cli.main(argv)
        return

    try:
        _draft_gp_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"GP backtest error: {error}") from error
