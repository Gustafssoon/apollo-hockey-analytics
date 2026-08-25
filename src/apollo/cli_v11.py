import argparse

from apollo import cli_v10 as v10_cli
from apollo.db import Database
from apollo.draft.projections import ProjectionError
from apollo.services.draft_projections import project_skater

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


def build_parser() -> argparse.ArgumentParser:
    parser = v10_cli.build_parser()
    top_level = _subparsers(parser)
    draft_parser = top_level.choices["draft"]
    draft_subparsers = _subparsers(draft_parser)

    project_parser = draft_subparsers.add_parser(
        "project",
        help="Build a baseline NHL skater projection from historical season stats",
    )
    project_parser.add_argument("name", help="Exact player name, for example Connor McDavid")
    project_parser.add_argument("--season", type=int, required=True, help="Target NHL season id")
    project_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    return parser


def _draft_project(args: argparse.Namespace) -> None:
    projection = project_skater(Database(args.db), args.name, args.season)

    print("APOLLO DRAFT PROJECTION")
    print()
    identity = f"{projection.player_name} | {projection.position}"
    if projection.team_abbrev:
        identity += f" | {projection.team_abbrev}"
    print(identity)
    print(f"Target season: {_season_label(projection.target_season)}")
    print()
    print(f"Projected GP   {projection.projected_games:.1f}")
    for stat_name, label in STAT_LABELS.items():
        print(f"{label:<14} {projection.stats[stat_name]:.1f}")
    print()
    seasons = ", ".join(_season_label(season) for season in projection.source_seasons)
    print(f"Source seasons: {seasons}")
    print(f"Model: {projection.model_version}")
    print(f"Availability: {projection.availability_model_version}")
    age_model = projection.age_model_version or "not applied (birth date unavailable)"
    print(f"Age: {age_model}")
    regression_model = projection.regression_model_version or "not applied (priors unavailable)"
    print(f"Regression: {regression_model}")
    shooting_model = projection.shooting_context_model_version or "not applied (context unavailable)"
    print(f"Shooting context: {shooting_model}")
    assist_rate_model = projection.assist_rate_model_version or "not applied (context unavailable)"
    print(f"Assist rate: {assist_rate_model}")
    finishing_model = projection.overall_finishing_model_version or (
        "not applied (context unavailable)"
    )
    print(f"Overall finishing: {finishing_model}")
    pp_deployment_model = projection.pp_deployment_model_version or (
        "not applied (context unavailable)"
    )
    print(f"PP deployment: {pp_deployment_model}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft" or args.draft_command != "project":
        v10_cli.main(argv)
        return

    try:
        _draft_project(args)
    except ProjectionError as error:
        raise SystemExit(f"Projection error: {error}") from error
