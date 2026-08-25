import argparse

from apollo import cli_v39 as v39_cli
from apollo.db import Database
from apollo.draft.peripheral_rate_signal_backtest import PERIPHERAL_RATE_SIGNALS
from apollo.draft.projections import MODEL_VERSION, ProjectionError
from apollo.draft.regression import REGRESSION_PSEUDO_GAMES_BY_STAT
from apollo.services.peripheral_rate_signal_backtest import run_peripheral_rate_signal_aggregate

SIGNAL_LABELS = {
    "sog_pg_ratio": "SOG/GP",
    "hit_pg_ratio": "HIT/GP",
    "blk_pg_ratio": "BLK/GP",
}
SIGNAL_STATS = {
    "sog_pg_ratio": "shots",
    "hit_pg_ratio": "hits",
    "blk_pg_ratio": "blockedShots",
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(result, signal_name: str):
    return next(metric for metric in result.metrics if metric.signal_name == signal_name)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _fmt_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = v39_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "peripheral-rate-signal-summary",
        help="Screen source-only SOG/HIT/BLK rates against production v0.8 residuals",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_peripheral_rate_signal_summary(args: argparse.Namespace) -> None:
    result = run_peripheral_rate_signal_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO PERIPHERAL RATE RESIDUAL SCREEN")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production baseline: {MODEL_VERSION}")
    print(f"Production player-seasons: {result.baseline_player_seasons}")
    print("Signals are raw 60/30/10 source rates divided by source-season F/D priors.")
    print("Production v0.8 already includes current category regression before residuals are measured.")
    print("Target-season rates are never used as features; target totals only measure residuals.")
    print("Missing 3/3 signal context reduces signal N, never production baseline coverage.")
    print()
    print(f"{'SIGNAL':<8} {'PSEUDO':>6} {'N':>5} {'COV':>6} {'RHO':>7} {'YRS':>5} {'QD':>8}")
    for signal_name in PERIPHERAL_RATE_SIGNALS:
        metric = _metric(result, signal_name)
        stat_name = SIGNAL_STATS[signal_name]
        pseudo_games = REGRESSION_PSEUDO_GAMES_BY_STAT[stat_name]
        coverage = (
            metric.player_seasons / result.baseline_player_seasons
            if result.baseline_player_seasons
            else 0.0
        )
        print(
            f"{SIGNAL_LABELS[signal_name]:<8} {pseudo_games:>6.1f} "
            f"{metric.player_seasons:>5} {coverage * 100:>5.1f}% "
            f"{_fmt(metric.weighted_residual_rho):>7} {metric.year_signs:>5} "
            f"{_fmt_delta(metric.weighted_quartile_delta):>8}"
        )

    print()
    print("RHO = source rate ratio vs actual - production v0.8 category residual.")
    print("QD = top - bottom source-rate quartile residual. 0 means |rho| < 0.02.")
    print("Diagnostic only. No regression pseudo-game value is changed automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "peripheral-rate-signal-summary":
        v39_cli.main(argv)
        return
    try:
        _draft_peripheral_rate_signal_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Peripheral rate signal summary error: {error}") from error
