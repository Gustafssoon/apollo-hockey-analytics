import argparse

from apollo import cli_v41 as v41_cli
from apollo.db import Database
from apollo.draft.goalie_foundation import GOALIE_CORE_SOURCE_FIELDS
from apollo.draft.projections import ProjectionError
from apollo.services.goalie_foundation import run_goalie_foundation_audit

FIELD_LABELS = (
    ("gamesStarted", "GS"),
    ("wins", "W"),
    ("saves", "SV"),
    ("goalsAgainst", "GA"),
    ("shotsAgainst", "SA"),
    ("shutouts", "SO"),
    ("savePctg", "SV%"),
    ("goalsAgainstAvg", "GAA"),
    ("timeOnIce", "TOI"),
)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _season_label(season: int) -> str:
    text = str(season)
    return f"{text[:4]}-{text[6:]}" if len(text) == 8 else text


def _count(coverage, field: str) -> int:
    return next(count for name, count in coverage.field_counts if name == field)


def _coverage_text(count: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{count}/{denominator}"


def build_parser() -> argparse.ArgumentParser:
    parser = v41_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    summary_parser = draft_subparsers.add_parser(
        "goalie-foundation-summary",
        help="Audit goalie season-stat and source-history coverage before projection modeling",
    )
    summary_parser.add_argument("--season", type=int, required=True)
    summary_parser.add_argument("--years", type=int, default=3)
    summary_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    return parser


def _draft_goalie_foundation_summary(args: argparse.Namespace) -> None:
    result = run_goalie_foundation_audit(
        Database(args.db),
        args.season,
        years=args.years,
    )

    print("APOLLO GOALIE PROJECTION FOUNDATION AUDIT")
    print()
    print(
        "Target backtests: "
        + ", ".join(_season_label(season) for season in result.target_seasons)
    )
    print("Data seasons: " + ", ".join(_season_label(season) for season in result.data_seasons))
    print("Core source fields: " + ", ".join(GOALIE_CORE_SOURCE_FIELDS))
    print("Audit only. No goalie projection model is defined or promoted here.")
    print()

    print("SEASON STAT COVERAGE (denominator = goalies with GP > 0)")
    labels = " ".join(f"{label:>7}" for _, label in FIELD_LABELS)
    print(f"{'SEASON':<9} {'GP>0':>5} {'GS>0':>5} {'CORE':>5} {labels} {'SHOT-ID':>11}")
    for coverage in result.season_coverage:
        denominator = coverage.goalies_with_games
        values = " ".join(
            f"{_coverage_text(_count(coverage, field), denominator):>7}"
            for field, _ in FIELD_LABELS
        )
        shot_identity = _coverage_text(
            coverage.shot_identity_exact,
            coverage.shot_identity_checked,
        )
        print(
            f"{_season_label(coverage.season):<9} {coverage.goalies_with_games:>5} "
            f"{coverage.goalies_with_starts:>5} {coverage.complete_core:>5} "
            f"{values} {shot_identity:>11}"
        )

    print()
    print("SOURCE-HISTORY COVERAGE")
    print(
        f"{'TARGET':<9} {'MIN GS':>6} {'ACTUAL':>6} {'>=1 SRC':>9} "
        f"{'>=2 SRC':>9} {'3/3 SRC':>9}"
    )
    for coverage in result.history_coverage:
        denominator = coverage.actual_eligible
        print(
            f"{_season_label(coverage.target_season):<9} {coverage.min_actual_starts:>6} "
            f"{denominator:>6} "
            f"{_coverage_text(coverage.at_least_one_source, denominator):>9} "
            f"{_coverage_text(coverage.at_least_two_sources, denominator):>9} "
            f"{_coverage_text(coverage.three_sources, denominator):>9}"
        )

    print()
    print("SHOT-ID checks SA = SV + GA within 0.5 on rows where all three fields exist.")
    print("Next model design is chosen from this coverage evidence, not tuned in this audit.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "draft" or args.draft_command != "goalie-foundation-summary":
        v41_cli.main(argv)
        return
    try:
        _draft_goalie_foundation_summary(args)
    except ProjectionError as error:
        raise SystemExit(f"Goalie foundation summary error: {error}") from error
