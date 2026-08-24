import argparse

from apollo import cli_v17 as v17_cli
from apollo.db import Database
from apollo.draft.projections import SKATER_PROJECTION_STATS, ProjectionError
from apollo.services.age_model_backtest import run_age_model_aggregate

STAT_LABELS = {
    "points": "PTS",
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


def build_parser() -> argparse.ArgumentParser:
    parser = v17_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    candidate_parser = draft_subparsers.add_parser(
        "age-model-summary",
        help="Compare complete age-adjusted skater model candidates across backtest seasons",
    )
    candidate_parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Most recent actual target season id",
    )
    candidate_parser.add_argument("--years", type=int, default=3)
    candidate_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    candidate_parser.add_argument("--min-actual-games", type=int, default=20)
    return parser


def _metric(candidate, stat_name: str):
    return next(metric for metric in candidate.metrics if metric.stat_name == stat_name)


def _draft_age_model_summary(args: argparse.Namespace) -> None:
    result = run_age_model_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
    )
    neutral = next(candidate for candidate in result.candidates if candidate.candidate_name == "neutral")
    neutral_pts = _metric(neutral, "points")

    print("APOLLO AGE MODEL CANDIDATE SHOOTOUT")
    print()
    seasons = ", ".join(_season_label(season) for season in result.target_seasons)
    print(f"Target seasons: {seasons}")
    print(f"Player-seasons: {result.total_player_seasons}")
    print("PTS is always derived from projected G + A.")
    print()
    print(
        f"{'MODEL':<16} {'PTS MAE':>8} {'GAIN':>8} {'PTS RHO':>8} "
        f"{'RHO D':>8} {'TOP25':>7} {'RAW+':>6} {'AVG RAW%':>9}"
    )

    ordered = sorted(result.candidates, key=lambda item: _metric(item, "points").mae)
    for candidate in ordered:
        pts = _metric(candidate, "points")
        rho_delta = None
        if pts.spearman_rho is not None and neutral_pts.spearman_rho is not None:
            rho_delta = pts.spearman_rho - neutral_pts.spearman_rho
        raw_improvements = []
        for stat in SKATER_PROJECTION_STATS:
            baseline = _metric(neutral, stat).mae
            value = _metric(candidate, stat).mae
            raw_improvements.append((baseline - value) / baseline * 100 if baseline else 0.0)
        avg_raw = sum(raw_improvements) / len(raw_improvements)
        rho_delta_text = "n/a" if rho_delta is None else f"{rho_delta:+.3f}"
        rho_text = "n/a" if pts.spearman_rho is None else f"{pts.spearman_rho:.3f}"
        print(
            f"{candidate.candidate_name:<16} {pts.mae:>8.3f} "
            f"{neutral_pts.mae - pts.mae:>+8.3f} {rho_text:>8} {rho_delta_text:>8} "
            f"{candidate.top25_overlap_rate * 100:>6.1f}% "
            f"{candidate.raw_stats_improved:>2}/6 {avg_raw:>+8.2f}%"
        )

    print()
    print("Raw-stat winners")
    print("----------------")
    print(f"{'STAT':<5} {'BEST':<16} {'MAE':>8} {'GAIN':>8}")
    for stat in SKATER_PROJECTION_STATS:
        best = min(result.candidates, key=lambda item: _metric(item, stat).mae)
        neutral_metric = _metric(neutral, stat)
        best_metric = _metric(best, stat)
        print(
            f"{STAT_LABELS[stat]:<5} {best.candidate_name:<16} {best_metric.mae:>8.3f} "
            f"{neutral_metric.mae - best_metric.mae:>+8.3f}"
        )

    print()
    print("No candidate is promoted automatically by this command.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "age-model-summary":
        v17_cli.main(argv)
        return

    try:
        _draft_age_model_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Age model summary error: {error}") from error
