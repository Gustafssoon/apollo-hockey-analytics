import argparse

from apollo import cli_v19 as v19_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.pp_usage_backtest import run_pp_usage_backtest


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
    parser = v19_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    pp_parser = draft_subparsers.add_parser(
        "pp-usage-backtest",
        help="Compare PP TOI-driven PPP projections against skater projection v0.3",
    )
    pp_parser.add_argument("--season", type=int, required=True)
    pp_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    pp_parser.add_argument("--min-actual-games", type=int, default=20)
    pp_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_pp_usage_backtest(args: argparse.Namespace) -> None:
    result = run_pp_usage_backtest(
        Database(args.db),
        args.season,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    baseline = next(
        strategy for strategy in result.strategies if strategy.strategy_name == "baseline_v03"
    )

    print("APOLLO PP USAGE SHOOTOUT")
    print()
    print(f"Target season: {_season_label(result.target_season)}")
    sources = ", ".join(_season_label(season) for season in result.source_seasons)
    print(f"Source seasons: {sources}")
    print(
        f"PP history coverage: {result.evaluated_players}/{result.base_eligible_players} "
        f"({result.pp_history_coverage * 100:.1f}%)"
    )
    print("PP TOI MAE is reported in seconds per game.")
    print("Control: apollo-skater-baseline-v0.3")
    print()
    print(
        f"{'STRATEGY':<20} {'PP MAE(s)':>9} {'PP RHO':>8} {'PPP MAE':>8} "
        f"{'GAIN':>8} {'PPP RHO':>8} {'TOP25':>7}"
    )

    ordered = sorted(result.strategies, key=lambda strategy: strategy.ppp_mae)
    for strategy in ordered:
        pp_mae = "n/a" if strategy.pp_toi_mae is None else f"{strategy.pp_toi_mae:.2f}"
        top25 = next(item for item in strategy.top_k_ppp if item.requested_k == 25)
        print(
            f"{strategy.strategy_name:<20} {pp_mae:>9} "
            f"{_format_rho(strategy.pp_toi_spearman_rho):>8} {strategy.ppp_mae:>8.2f} "
            f"{baseline.ppp_mae - strategy.ppp_mae:>+8.2f} "
            f"{_format_rho(strategy.ppp_spearman_rho):>8} "
            f"{top25.overlap_rate * 100:>6.1f}%"
        )

    oracle = next(
        strategy
        for strategy in result.strategies
        if strategy.strategy_name == "actual_pp_toi_oracle"
    )
    print()
    print(
        "Actual-PP-TOI oracle: "
        f"PPP MAE {oracle.ppp_mae:.2f} | gain {baseline.ppp_mae - oracle.ppp_mae:+.2f} | "
        f"rho {_format_rho(oracle.ppp_spearman_rho)}"
    )
    print("Actual target PP TOI is diagnostic only and is never used for real projections.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "pp-usage-backtest":
        v19_cli.main(argv)
        return

    try:
        _draft_pp_usage_backtest(args)
    except ProjectionError as error:
        raise SystemExit(f"PP usage backtest error: {error}") from error
