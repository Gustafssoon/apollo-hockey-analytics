import argparse

from apollo import cli_v51 as v51_cli
from apollo.db import Database
from apollo.draft.goalie_team_context_foundation import GOALIE_TEAM_DOMINANCE_THRESHOLD
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_team_context_foundation import run_goalie_team_context_audit


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _pct(value: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{100.0 * value / denominator:.1f}%"


def build_parser() -> argparse.ArgumentParser:
    parser = v51_cli.build_parser()
    top = _subparsers(parser)
    draft = top.choices["draft"]
    draft_subparsers = _subparsers(draft)
    summary = draft_subparsers.add_parser(
        "goalie-team-context-foundation-summary",
        help="Audit historical goalie game-log and source-team coverage",
    )
    summary.add_argument("--season", type=int, required=True)
    summary.add_argument("--years", type=int, default=3)
    summary.add_argument("--db", default="apollo.db", help="SQLite database path")
    summary.add_argument("--min-actual-starts", type=int, default=20)
    return parser


def _draft_goalie_team_context_foundation_summary(args: argparse.Namespace) -> None:
    result = run_goalie_team_context_audit(
        Database(args.db),
        args.season,
        years=args.years,
        min_actual_starts=args.min_actual_starts,
    )

    print("APOLLO GOALIE TEAM-CONTEXT FOUNDATION AUDIT")
    print()
    print("Baseline cohort: goalie baseline strict 3/3 source history.")
    print("Source-team identity comes only from historical nhl_player_game rows.")
    print("Target-season team/workload is never used as a feature.")
    print(
        "Dominant source-team threshold: "
        f"{GOALIE_TEAM_DOMINANCE_THRESHOLD:.0%} of source-season game-log rows."
    )
    print("GP match means game-log row count is within +/-1 of source gamesPlayed.")
    print("Audit only. No team-context or workload correction is defined here.")
    print()
    print(
        f"{'TARGET':<10} {'N':>4} {'SRC':>5} {'LOGS':>6} {'GP':>6} {'MATCH':>6} "
        f"{'TEAM':>6} {'DOM80':>6} {'MULTI':>6} {'ALL3 T':>7} {'ALL3 D':>7}"
    )
    for season in result.seasons:
        print(
            f"{season.target_season:<10} {season.baseline_goalies:>4} "
            f"{season.source_player_seasons:>5} "
            f"{season.with_game_logs:>6} {season.with_gp_stat:>6} "
            f"{season.gp_log_match:>6} {season.team_identified:>6} "
            f"{season.dominant_team_80:>6} {season.multi_team:>6} "
            f"{season.goalies_all3_team_identified:>7} "
            f"{season.goalies_all3_dominant_80:>7}"
        )

    print()
    print("AGGREGATE COVERAGE")
    print(
        f"Source game logs: {result.with_game_logs}/{result.source_player_seasons} "
        f"({_pct(result.with_game_logs, result.source_player_seasons)})"
    )
    print(
        f"Source gamesPlayed stat: {result.with_gp_stat}/{result.source_player_seasons} "
        f"({_pct(result.with_gp_stat, result.source_player_seasons)})"
    )
    print(
        f"GP/log matches: {result.gp_log_match}/{result.with_gp_stat} "
        f"({_pct(result.gp_log_match, result.with_gp_stat)})"
    )
    print(
        f"Team identified: {result.team_identified}/{result.source_player_seasons} "
        f"({_pct(result.team_identified, result.source_player_seasons)})"
    )
    print(
        f"Dominant team >=80%: {result.dominant_team_80}/{result.source_player_seasons} "
        f"({_pct(result.dominant_team_80, result.source_player_seasons)})"
    )
    print(f"Multi-team source seasons: {result.multi_team}/{result.source_player_seasons}")
    print(
        f"Goalies with team identity in all 3 sources: "
        f"{result.goalies_all3_team_identified}/{result.baseline_goalies} "
        f"({_pct(result.goalies_all3_team_identified, result.baseline_goalies)})"
    )
    print(
        f"Goalies dominant >=80% in all 3 sources: "
        f"{result.goalies_all3_dominant_80}/{result.baseline_goalies} "
        f"({_pct(result.goalies_all3_dominant_80, result.baseline_goalies)})"
    )
    print("Next competition design is chosen from this coverage evidence, not tuned here.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-team-context-foundation-summary":
        v51_cli.main(argv)
        return
    try:
        _draft_goalie_team_context_foundation_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie team-context foundation error: {error}") from error
