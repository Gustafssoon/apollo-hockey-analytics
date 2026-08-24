import argparse

from apollo import cli_v30 as v30_cli
from apollo.db import Database
from apollo.draft.assist_rate_candidate import ASSIST_RATE_CANDIDATE_STRENGTHS
from apollo.draft.projections import ProjectionError
from apollo.services.assist_rate_candidate import run_assist_rate_candidate_aggregate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(result, stat_name: str):
    return next(metric for metric in result.metrics if metric.stat_name == stat_name)


def _aggregate_metric(variant, stat_name: str):
    return next(metric for metric in variant.metrics if metric.stat_name == stat_name)


def _variant(season_result, strength: float):
    return next(variant for variant in season_result.variants if variant.strength == strength)


def _top25(result) -> float:
    return next(
        overlap.overlap_rate
        for overlap in result.top_k_points
        if overlap.requested_k == 25
    )


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _untouched_exact(variant) -> bool:
    for stat_name in (
        "gamesPlayed",
        "goals",
        "powerPlayPoints",
        "shots",
        "hits",
        "blockedShots",
    ):
        metric = _aggregate_metric(variant, stat_name)
        if metric.baseline_mae != metric.candidate_mae:
            return False
        if metric.baseline_rho != metric.candidate_rho:
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = v30_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "assist-rate-candidate-summary",
        help="Gate 10% vs 20% individual 5v5 A/60 mean reversion against production v0.5",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_assist_rate_candidate_summary(args: argparse.Namespace) -> None:
    result = run_assist_rate_candidate_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )
    strength10, strength20 = ASSIST_RATE_CANDIDATE_STRENGTHS

    print("APOLLO ASSIST-RATE v0.6 CANDIDATE GATE")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production v0.5 player-seasons: {result.baseline_player_seasons}")
    print("Candidates: 10% and 20% source-only individual 5v5 A/60 mean reversion.")
    print("Missing 3/3 A/60 context uses exact production v0.5 fallback.")
    print()
    print(
        f"{'SEASON':<9} {'N':>4} {'A60':>4} {'B PTS':>7} "
        f"{'10 PTS':>7} {'G10':>7} {'20 PTS':>7} {'G20':>7} "
        f"{'B T25':>6} {'T10':>6} {'T20':>6}"
    )
    for season_result in result.season_results:
        variant10 = _variant(season_result, strength10)
        variant20 = _variant(season_result, strength20)
        baseline_pts = _metric(season_result.baseline, "points")
        pts10 = _metric(variant10.result, "points")
        pts20 = _metric(variant20.result, "points")
        print(
            f"{_season_label(season_result.target_season):<9} "
            f"{season_result.baseline.evaluated_players:>4} {variant10.applied:>4} "
            f"{baseline_pts.mae:>7.3f} {pts10.mae:>7.3f} "
            f"{baseline_pts.mae - pts10.mae:>+7.3f} "
            f"{pts20.mae:>7.3f} {baseline_pts.mae - pts20.mae:>+7.3f} "
            f"{_top25(season_result.baseline) * 100:>5.1f}% "
            f"{_top25(variant10.result) * 100:>5.1f}% "
            f"{_top25(variant20.result) * 100:>5.1f}%"
        )

    print()
    print(
        f"{'MODEL':<10} {'APPLIED':>9} {'PTS MAE':>8} {'GAIN':>8} {'YRS+':>5} "
        f"{'WORST':>8} {'PTS RHO':>8} {'TOP25':>7} {'A MAE':>7} {'A+':>7} "
        f"{'A YRS+':>6} {'A WORST':>8} {'OTHER':>6}"
    )
    for variant in result.variants:
        pts = _aggregate_metric(variant, "points")
        assists = _aggregate_metric(variant, "assists")
        print(
            f"a60_{int(variant.strength * 100):<5} "
            f"{variant.applied:>4}/{result.baseline_player_seasons:<4} "
            f"{pts.candidate_mae:>8.3f} {pts.mae_gain:>+8.3f} "
            f"{variant.points_improved_years:>2}/{len(result.target_seasons):<2} "
            f"{variant.worst_points_mae_gain:>+8.3f} "
            f"{_fmt_rho(pts.candidate_rho):>8} "
            f"{variant.candidate_top25_overlap_rate * 100:>6.1f}% "
            f"{assists.candidate_mae:>7.3f} {assists.mae_gain:>+7.3f} "
            f"{variant.assists_improved_years:>2}/{len(result.target_seasons):<2} "
            f"{variant.worst_assists_mae_gain:>+8.3f} "
            f"{'EXACT' if _untouched_exact(variant) else 'CHANGED':>6}"
        )

    print()
    print("OTHER checks GP/G/PPP/SOG/HIT/BLK MAE and rho against exact production v0.5.")
    print("Candidate gate only. Production remains apollo-skater-baseline-v0.5.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "assist-rate-candidate-summary":
        v30_cli.main(argv)
        return

    try:
        _draft_assist_rate_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Assist-rate candidate summary error: {error}") from error
