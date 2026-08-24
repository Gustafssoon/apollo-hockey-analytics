import argparse

from apollo import cli_v22 as v22_cli
from apollo.db import Database
from apollo.draft.projections import SKATER_PROJECTION_STATS, ProjectionError
from apollo.services.regression_model_backtest import run_regression_model_aggregate

STAT_LABELS = {
    "goals": "G",
    "assists": "A",
    "powerPlayPoints": "PPP",
    "shots": "SOG",
    "hits": "HIT",
    "blockedShots": "BLK",
}


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


def _metric(candidate, stat_name: str):
    return next(metric for metric in candidate.metrics if metric.stat_name == stat_name)


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _strategy_label(strategy_name: str) -> str:
    return {
        "baseline_v03": "0",
        "regress_pos_5": "5",
        "regress_pos_10": "10",
        "regress_pos_20": "20",
        "regress_pos_40": "40",
    }.get(strategy_name, strategy_name)


def build_parser() -> argparse.ArgumentParser:
    parser = v22_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "regression-model-summary",
        help="Compare complete production-shaped regression candidates across seasons",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_regression_model_summary(args: argparse.Namespace) -> None:
    result = run_regression_model_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    baseline = next(
        candidate for candidate in result.candidates if candidate.candidate_name == "baseline_v03"
    )
    baseline_pts = _metric(baseline, "points")

    print("APOLLO REGRESSION MODEL CANDIDATE SHOOTOUT")
    print()
    seasons = ", ".join(_season_label(season) for season in result.target_seasons)
    print(f"Target seasons: {seasons}")
    print(f"Player-seasons: {result.total_player_seasons}")
    print("PTS is always derived from projected G + A.")
    print("MAE/rho aggregates are weighted by evaluated skaters per season.")
    print()
    print(
        f"{'MODEL':<18} {'PTS MAE':>8} {'GAIN':>8} {'PTS RHO':>8} {'RHO D':>8} "
        f"{'TOP25':>7} {'RAW+':>6} {'AVG RAW%':>9} {'WORST D':>8}"
    )

    ordered = sorted(
        result.candidates,
        key=lambda candidate: (
            _metric(candidate, "points").weighted_mae,
            -candidate.average_raw_improvement_pct,
        ),
    )
    for candidate in ordered:
        pts = _metric(candidate, "points")
        rho_text = "n/a" if pts.weighted_rho is None else f"{pts.weighted_rho:.3f}"
        print(
            f"{candidate.candidate_name:<18} {pts.weighted_mae:>8.3f} "
            f"{baseline_pts.weighted_mae - pts.weighted_mae:>+8.3f} {rho_text:>8} "
            f"{_format_optional(pts.rho_delta):>8} {candidate.top25_overlap_rate * 100:>6.1f}% "
            f"{candidate.raw_stats_improved:>2}/6 {candidate.average_raw_improvement_pct:>+8.2f}% "
            f"{_format_optional(candidate.worst_raw_rho_delta):>8}"
        )

    print()
    print("Regression mapping (pseudo-games; 0 = v0.3 baseline)")
    print("---------------------------------------------------")
    print(
        f"{'MODEL':<18} "
        + " ".join(f"{STAT_LABELS[stat]:>4}" for stat in SKATER_PROJECTION_STATS)
    )
    for candidate in result.candidates:
        values = " ".join(
            f"{_strategy_label(candidate.stat_strategy_map[stat]):>4}"
            for stat in SKATER_PROJECTION_STATS
        )
        print(f"{candidate.candidate_name:<18} {values}")

    print()
    print("No regression model candidate is promoted automatically by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "regression-model-summary":
        v22_cli.main(argv)
        return

    try:
        _draft_regression_model_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Regression model summary error: {error}") from error
