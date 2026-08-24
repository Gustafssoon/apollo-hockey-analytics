import argparse

from apollo import cli_v14 as v14_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.age_backtest import run_age_baseline_backtest


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
    parser = v14_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    age_parser = draft_subparsers.add_parser(
        "age-backtest",
        help="Compare conservative age adjustments for skater scoring projections",
    )
    age_parser.add_argument("--season", type=int, required=True, help="Actual target season id")
    age_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    age_parser.add_argument(
        "--min-actual-games",
        type=int,
        default=20,
        help="Minimum actual games played in the target season",
    )
    return parser


def _draft_age_backtest(args: argparse.Namespace) -> None:
    result = run_age_baseline_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
    )

    print("APOLLO AGE BASELINE SHOOTOUT")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    sources = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {sources}")
    print(
        f"Birth dates: {result.evaluated_players}/{result.base_eligible_players} "
        f"({result.birth_date_coverage * 100:.1f}%)"
    )
    print("Age reference date: October 1 of each NHL season")
    print()
    print(
        f"{'RK':>2} {'STRATEGY':<14} {'PTS MAE':>9} {'PTS RMSE':>10} "
        f"{'PTS RHO':>8} {'TOP25':>8}"
    )
    ordered = sorted(result.strategies, key=lambda strategy: strategy.points_mae)
    for rank, strategy in enumerate(ordered, start=1):
        top25 = next(overlap for overlap in strategy.top_k_points if overlap.requested_k == 25)
        print(
            f"{rank:>2} {strategy.name:<14} {strategy.points_mae:>9.2f} "
            f"{strategy.points_rmse:>10.2f} {_format_rho(strategy.points_spearman_rho):>8} "
            f"{top25.overlap_rate * 100:>7.0f}%"
        )
    print()
    print("Neutral is the v0.2 control; no age strategy is promoted by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "age-backtest":
        v14_cli.main(argv)
        return

    try:
        _draft_age_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"Age backtest error: {error}") from error
