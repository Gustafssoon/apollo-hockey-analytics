import argparse

from apollo import cli_v40 as v40_cli
from apollo.db import Database
from apollo.draft.hit_regression_candidate import HIT_REGRESSION_PSEUDO_GAMES
from apollo.draft.projections import MODEL_VERSION, ProjectionError
from apollo.services.hit_regression_candidate import run_hit_regression_candidate_aggregate


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _metric(variant, stat_name: str):
    return next(metric for metric in variant.metrics if metric.stat_name == stat_name)


def _fmt_rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _other_exact(variant) -> bool:
    for stat_name in (
        "gamesPlayed",
        "points",
        "goals",
        "assists",
        "powerPlayPoints",
        "shots",
        "blockedShots",
    ):
        metric = _metric(variant, stat_name)
        if metric.baseline_mae != metric.candidate_mae:
            return False
        if metric.baseline_rho != metric.candidate_rho:
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = v40_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "hit-regression-candidate-summary",
        help="Compare fixed HIT pseudo-game candidates against production v0.8",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary_parser.add_argument("--min-actual-games", type=int, default=20)
    summary_parser.add_argument("--min-history-seasons", type=int, default=3)
    return parser


def _draft_hit_regression_candidate_summary(args: argparse.Namespace) -> None:
    result = run_hit_regression_candidate_aggregate(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_games=args.min_actual_games,
        min_history_seasons=args.min_history_seasons,
    )

    print("APOLLO HIT REGRESSION PSEUDO-GAME SHOOTOUT")
    print()
    print(
        "Target seasons: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print(f"Production baseline: {MODEL_VERSION}")
    print(f"Production v0.8 player-seasons: {result.baseline_player_seasons}")
    print("Production HIT pseudo-games: 0. Candidates: 5, 10, 20.")
    print("Candidates change source-season HIT regression only; all other stats stay v0.8.")
    print("F/D priors are source-only. Target HIT totals are evaluation only.")
    print()
    print(
        f"{'PSEUDO':>6} {'APPLIED':>9} {'HIT MAE':>8} {'GAIN':>7} "
        f"{'HIT+':>5} {'WORST':>8} {'HIT RHO':>7} {'OTHER':>6}"
    )
    for pseudo_games in HIT_REGRESSION_PSEUDO_GAMES:
        variant = next(item for item in result.variants if item.pseudo_games == pseudo_games)
        hits = _metric(variant, "hits")
        print(
            f"{pseudo_games:>6.1f} {variant.applied:>4}/{result.baseline_player_seasons:<4} "
            f"{hits.candidate_mae:>8.3f} {hits.mae_gain:>+7.3f} "
            f"{variant.hit_improved_years:>1}/{len(result.target_seasons):<3} "
            f"{variant.worst_hit_mae_gain:>+8.3f} {_fmt_rho(hits.candidate_rho):>7} "
            f"{'EXACT' if _other_exact(variant) else 'CHANGED':>6}"
        )

    print()
    print("OTHER checks GP/PTS/G/A/PPP/SOG/BLK against exact production v0.8.")
    print("Shootout only. No HIT pseudo-game value is promoted automatically.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "hit-regression-candidate-summary":
        v40_cli.main(argv)
        return
    try:
        _draft_hit_regression_candidate_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"HIT regression candidate summary error: {error}") from error
