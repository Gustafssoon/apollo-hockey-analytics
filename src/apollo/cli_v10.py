import argparse

from apollo import cli_v09 as v09_cli
from apollo.draft import DraftConfigError, draft_picks, load_draft_config


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def build_parser() -> argparse.ArgumentParser:
    parser = v09_cli.build_parser()
    top_level = _subparsers(parser)

    draft_parser = top_level.add_parser(
        "draft",
        help="Plan and run fantasy hockey drafts",
    )
    draft_subparsers = draft_parser.add_subparsers(dest="draft_command", required=True)

    config_parser = draft_subparsers.add_parser(
        "config",
        help="Inspect draft league configuration",
    )
    config_subparsers = config_parser.add_subparsers(dest="draft_config_command", required=True)

    show_parser = config_subparsers.add_parser(
        "show",
        help="Validate and display a draft league configuration",
    )
    show_parser.add_argument("--config", required=True, help="Draft league YAML configuration path")
    return parser


def _print_categories(title: str, categories) -> None:
    print(title)
    print("-" * len(title))
    for category in categories:
        print(f"{category.stat:<8} {category.points:g}")
    print()


def _draft_config_show(args: argparse.Namespace) -> None:
    config = load_draft_config(args.config)

    print("APOLLO DRAFT CONFIG")
    print()
    print("League")
    print("------")
    print(f"Name:        {config.league.name}")
    print(f"Teams:       {config.league.teams}")
    print()

    print("Draft")
    print("-----")
    print(f"Type:        {config.draft.draft_type}")
    print(f"Your slot:   #{config.draft.my_slot}")
    print(f"Rounds:      {config.draft.rounds}")
    print()

    print("Your Picks")
    print("----------")
    for pick in draft_picks(config):
        print(f"R{pick.round_number:<3} #{pick.overall_pick}")
    print()

    print("Roster")
    print("------")
    for slot in config.roster:
        print(f"{slot.name:<8} {slot.count}")
    print()

    _print_categories("Scoring - Skaters", config.scoring.skaters)
    _print_categories("Scoring - Goalies", config.scoring.goalies)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "draft":
        v09_cli.main(argv)
        return

    try:
        if args.draft_command == "config" and args.draft_config_command == "show":
            _draft_config_show(args)
    except DraftConfigError as error:
        raise SystemExit(f"Draft config error: {error}") from error
