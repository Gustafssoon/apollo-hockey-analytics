import argparse

from apollo import cli_v42 as v42_cli
from apollo.db import Database
from apollo.draft.goalie_baseline import GOALIE_BASELINE_VERSION, build_goalie_baseline_aggregate
from apollo.draft.projections import ProjectionError, previous_seasons
from apollo.services.goalie_baseline import run_goalie_baseline_backtest

STAT_LABELS = {
    "gamesStarted": "GS",
    "wins": "W",
    "saves": "SV",
    "goalsAgainst": "GA",
    "shutouts": "SO",
    "savePctg": "SV%",
    "goalsAgainstAvg": "GAA",
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
    return "n/a" if value is None else f"{value:.{digits}f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v42_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "goalie-baseline-summary",
        help="Backtest the first strict 3/3 source-only goalie projection benchmark",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_baseline_summary(args: argparse.Namespace) -> None:
    if args.years < 1:
        raise ProjectionError("years must be >= 1")
    database = Database(args.db)
    target_seasons = (
        args.season,
        *previous_seasons(args.season, args.years - 1),
    )
    results = tuple(
        run_goalie_baseline_backtest(
            database,
            season,
            min_actual_starts=args.min_actual_starts,
        )
        for season in target_seasons
    )
    aggregate = build_goalie_baseline_aggregate(results)

    print("APOLLO GOALIE BASELINE v0.1 BENCHMARK")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in aggregate.target_seasons)
    )
    print(f"Model: {GOALIE_BASELINE_VERSION}")
    print("Source policy: strict 3/3 seasons, 60/30/10 calendar weights.")
    print("Projected workload = weighted GS. W/SV/GA/SO use weighted per-start rates.")
    print("SV% and GAA are weighted source ratios. No shrink, age, or team context.")
    print("ORACLE GS replaces projected starts with actual target starts for total-rate stats.")
    print()
    print(f"{'SEASON':<9} {'ELIG':>5} {'N':>5} {'COV':>6} {'GS MAE':>7} {'GS RHO':>7}")
    for result in results:
        gs = next(metric for metric in result.metrics if metric.stat_name == "gamesStarted")
        print(
            f"{_season_label(result.target_season):<9} "
            f"{result.actual_eligible_goalies:>5} {result.evaluated_goalies:>5} "
            f"{result.coverage * 100:>5.1f}% {gs.mae:>7.2f} {_fmt(gs.spearman_rho):>7}"
        )

    print()
    print(f"Aggregate player-seasons: {aggregate.player_seasons}")
    print(
        f"{'STAT':<5} {'BASE MAE':>9} {'BASE RHO':>9} "
        f"{'ORCL MAE':>9} {'ORCL RHO':>9} {'DELTA':>8}"
    )
    for metric in aggregate.metrics:
        label = STAT_LABELS[metric.stat_name]
        delta = (
            None
            if metric.oracle_starts_mae is None
            else metric.mae - metric.oracle_starts_mae
        )
        print(
            f"{label:<5} {metric.mae:>9.3f} {_fmt(metric.spearman_rho):>9} "
            f"{_fmt(metric.oracle_starts_mae):>9} "
            f"{_fmt(metric.oracle_starts_spearman_rho):>9} {_fmt(delta):>8}"
        )

    print()
    print("DELTA = BASE MAE - ORACLE GS MAE; positive values indicate workload error to attack.")
    print("Benchmark only. No goalie projection model is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-baseline-summary":
        v42_cli.main(argv)
        return
    try:
        _draft_goalie_baseline_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie baseline error: {error}") from error
