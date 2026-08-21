import argparse
import secrets
from pathlib import Path

from apollo import cli_v08 as v08_cli
from apollo.adapters import (
    YahooConfigurationError,
    YahooCredentials,
    YahooError,
    YahooFantasyClient,
    YahooFantasyError,
    YahooLeagueAdapter,
    YahooOAuthClient,
    YahooTokenStore,
)
from apollo.db import Database
from apollo.services import sync_league

ATTRIBUTION = "Fantasy data provided by Yahoo Fantasy — https://sports.yahoo.com/fantasy/"


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("Apollo CLI parser has no subcommands")


def _add_yahoo_local_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=".env", help="Local Yahoo credential env file")
    parser.add_argument(
        "--token-file",
        default=".apollo/yahoo-token.json",
        help="Local OAuth token cache; never commit this file",
    )
    parser.add_argument("--timeout", type=float, default=20.0)


def build_parser() -> argparse.ArgumentParser:
    parser = v08_cli.build_parser()
    top_level = _subparsers(parser)

    yahoo_parser = top_level.add_parser(
        "yahoo",
        help="Authenticate with and sync read-only Yahoo Fantasy data",
    )
    yahoo_subparsers = yahoo_parser.add_subparsers(dest="yahoo_command", required=True)

    auth_parser = yahoo_subparsers.add_parser(
        "auth-url",
        help="Print the Yahoo OAuth authorization URL",
    )
    _add_yahoo_local_args(auth_parser)

    exchange_parser = yahoo_subparsers.add_parser(
        "exchange",
        help="Exchange a Yahoo authorization code for local OAuth tokens",
    )
    _add_yahoo_local_args(exchange_parser)
    exchange_parser.add_argument("--code", required=True, help="One-time Yahoo authorization code")

    status_parser = yahoo_subparsers.add_parser(
        "status",
        help="Refresh OAuth if needed and probe Yahoo Fantasy API authorization",
    )
    _add_yahoo_local_args(status_parser)

    leagues_parser = yahoo_subparsers.add_parser(
        "leagues",
        help="List Yahoo Fantasy Hockey leagues available to the current login",
    )
    _add_yahoo_local_args(leagues_parser)
    leagues_parser.add_argument(
        "--season",
        type=int,
        help="Yahoo fantasy season year filter, for example 2026",
    )

    sync_parser = yahoo_subparsers.add_parser(
        "sync",
        help="Sync one live Yahoo Fantasy Hockey league into Apollo",
    )
    _add_yahoo_local_args(sync_parser)
    sync_parser.add_argument("--db", default="apollo.db", help="SQLite database path")
    sync_parser.add_argument(
        "--league-key",
        required=True,
        help="Yahoo league key, for example 473.l.12345",
    )
    return parser


def _clients(args: argparse.Namespace) -> tuple[YahooOAuthClient, YahooFantasyClient, YahooTokenStore]:
    credentials = YahooCredentials.load(args.env_file)
    oauth = YahooOAuthClient(credentials, timeout=args.timeout)
    fantasy = YahooFantasyClient(timeout=args.timeout)
    return oauth, fantasy, YahooTokenStore(args.token_file)


def _auth_url(args: argparse.Namespace) -> None:
    credentials = YahooCredentials.load(args.env_file)
    oauth = YahooOAuthClient(credentials, timeout=args.timeout)
    state = secrets.token_urlsafe(24)
    print("Apollo Yahoo OAuth")
    print()
    print("Open this URL in a browser and authorize the app:")
    print(oauth.authorization_url(state=state))
    print()
    print(f"Configured redirect URI: {credentials.redirect_uri}")
    print(
        "After Yahoo redirects there, copy the one-time value after 'code=' from the "
        "browser address bar. The localhost page itself does not need to load."
    )
    print("Then run: apollo yahoo exchange --code <CODE>")


def _exchange(args: argparse.Namespace) -> None:
    credentials = YahooCredentials.load(args.env_file)
    oauth = YahooOAuthClient(credentials, timeout=args.timeout)
    store = YahooTokenStore(args.token_file)
    token = oauth.exchange_code(args.code)
    store.save(token)
    print("Yahoo OAuth authorization complete")
    print(f"Token stored locally: {Path(args.token_file)}")
    print("Access and refresh tokens were not printed.")
    print("Next: apollo yahoo status")


def _status(args: argparse.Namespace) -> None:
    oauth, fantasy, store = _clients(args)
    access_token = oauth.access_token(store)
    print("Apollo Yahoo Status")
    print()
    print("OAuth token: available")
    try:
        fantasy.probe(access_token)
    except YahooFantasyError as error:
        if error.status == 403:
            print("Fantasy API: DENIED (HTTP 403)")
            print(
                "Yahoo accepted the OAuth flow but the application is not currently authorized "
                "for Fantasy API access. This can indicate pending approval or Yahoo allowlist "
                "provisioning."
            )
            return
        raise
    print("Fantasy API: authorized")
    print(ATTRIBUTION)


def _leagues(args: argparse.Namespace) -> None:
    oauth, fantasy, store = _clients(args)
    access_token = oauth.access_token(store)
    leagues = fantasy.list_hockey_leagues(access_token, season=args.season)
    print("Apollo Yahoo Fantasy Hockey Leagues")
    print()
    if not leagues:
        print("No matching leagues returned by Yahoo.")
        return
    print(f"{'LEAGUE KEY':<24} NAME")
    for league in leagues:
        print(f"{league.league_key:<24} {league.name}")
    print()
    print(ATTRIBUTION)


def _sync(args: argparse.Namespace) -> None:
    oauth, fantasy, store = _clients(args)
    access_token = oauth.access_token(store)
    adapter = YahooLeagueAdapter(fantasy, access_token, args.league_key)
    result = sync_league(Database(args.db), adapter)
    print("Yahoo Fantasy league synced successfully")
    print(f"League key: {args.league_key}")
    print(f"Teams: {result.teams}")
    print(f"Players: {result.players}")
    print(f"Roster entries: {result.roster_entries}")
    print(f"Roster snapshots: {result.snapshots}")
    print(ATTRIBUTION)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command != "yahoo":
        v08_cli.main(argv)
        return

    try:
        if args.yahoo_command == "auth-url":
            _auth_url(args)
        elif args.yahoo_command == "exchange":
            _exchange(args)
        elif args.yahoo_command == "status":
            _status(args)
        elif args.yahoo_command == "leagues":
            _leagues(args)
        elif args.yahoo_command == "sync":
            _sync(args)
    except (YahooConfigurationError, YahooFantasyError, YahooError) as error:
        raise SystemExit(str(error)) from error
