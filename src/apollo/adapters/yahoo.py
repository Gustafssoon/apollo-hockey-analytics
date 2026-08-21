from __future__ import annotations

import base64
import json
import os
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apollo.models import LeagueSnapshot, PlayerSnapshot, StatCategorySnapshot, TeamSnapshot

YahooTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    tuple[int, bytes],
]


class YahooError(RuntimeError):
    """Base class for Yahoo integration errors."""


class YahooConfigurationError(YahooError):
    """Raised when local Yahoo credentials or token state are missing."""


class YahooNetworkError(YahooError):
    """Raised when Yahoo cannot be reached."""


class YahooOAuthError(YahooError):
    """Raised when Yahoo rejects an OAuth request."""


class YahooFantasyError(YahooError):
    """Raised when the Yahoo Fantasy API rejects or cannot satisfy a request."""

    def __init__(self, status: int, description: str) -> None:
        self.status = status
        self.description = description
        super().__init__(f"Yahoo Fantasy API HTTP {status}: {description}")


@dataclass(frozen=True, slots=True)
class YahooCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str = "https://localhost:8080"

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> YahooCredentials:
        values = _read_env_file(Path(env_file))
        client_id = os.environ.get("YAHOO_CONSUMER_KEY") or values.get("YAHOO_CONSUMER_KEY")
        client_secret = os.environ.get("YAHOO_CONSUMER_SECRET") or values.get(
            "YAHOO_CONSUMER_SECRET"
        )
        redirect_uri = os.environ.get("YAHOO_REDIRECT_URI") or values.get(
            "YAHOO_REDIRECT_URI", "https://localhost:8080"
        )
        if not client_id or not client_secret:
            raise YahooConfigurationError(
                "Missing YAHOO_CONSUMER_KEY or YAHOO_CONSUMER_SECRET. "
                "Set them in the environment or a local .env file."
            )
        return cls(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


@dataclass(frozen=True, slots=True)
class YahooToken:
    access_token: str
    refresh_token: str
    expires_at: float
    token_type: str = "bearer"

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


@dataclass(frozen=True, slots=True)
class YahooLeagueInfo:
    league_key: str
    name: str


class YahooTokenStore:
    def __init__(self, path: str | Path = ".apollo/yahoo-token.json") -> None:
        self.path = Path(path)

    def load(self) -> YahooToken | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return YahooToken(
                access_token=str(payload["access_token"]),
                refresh_token=str(payload["refresh_token"]),
                expires_at=float(payload["expires_at"]),
                token_type=str(payload.get("token_type", "bearer")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise YahooConfigurationError(
                f"Invalid Yahoo token file at {self.path}; delete it and authenticate again."
            ) from error

    def save(self, token: YahooToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at,
            "token_type": token.token_type,
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class YahooOAuthClient:
    AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
    TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

    def __init__(
        self,
        credentials: YahooCredentials,
        *,
        transport: YahooTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.credentials = credentials
        self.transport = transport or _default_transport
        self.timeout = timeout

    def authorization_url(self, state: str | None = None) -> str:
        params = {
            "client_id": self.credentials.client_id,
            "redirect_uri": self.credentials.redirect_uri,
            "response_type": "code",
            "language": "en-us",
        }
        if state:
            params["state"] = state
        return f"{self.AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> YahooToken:
        if not code.strip():
            raise YahooOAuthError("Yahoo authorization code cannot be empty")
        return self._token_request(
            {
                "redirect_uri": self.credentials.redirect_uri,
                "code": code.strip(),
                "grant_type": "authorization_code",
            }
        )

    def refresh(self, refresh_token: str) -> YahooToken:
        if not refresh_token.strip():
            raise YahooOAuthError("Yahoo refresh token cannot be empty")
        return self._token_request(
            {
                "redirect_uri": self.credentials.redirect_uri,
                "refresh_token": refresh_token.strip(),
                "grant_type": "refresh_token",
            },
            existing_refresh_token=refresh_token.strip(),
        )

    def access_token(self, store: YahooTokenStore) -> str:
        token = store.load()
        if token is None:
            raise YahooConfigurationError(
                "No Yahoo OAuth token is stored. Run 'apollo yahoo auth-url', authorize the app, "
                "then run 'apollo yahoo exchange --code <code>'."
            )
        if time.time() < token.expires_at - 30:
            return token.access_token
        refreshed = self.refresh(token.refresh_token)
        store.save(refreshed)
        return refreshed.access_token

    def _token_request(
        self,
        form: Mapping[str, str],
        *,
        existing_refresh_token: str | None = None,
    ) -> YahooToken:
        basic = base64.b64encode(
            f"{self.credentials.client_id}:{self.credentials.client_secret}".encode()
        ).decode()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        status, body = self.transport(
            "POST",
            self.TOKEN_URL,
            headers,
            urlencode(dict(form)).encode(),
            self.timeout,
        )
        payload = _decode_json(body)
        if status != 200:
            description = _json_error_description(payload) or body.decode(errors="replace")
            raise YahooOAuthError(f"Yahoo OAuth HTTP {status}: {description}")

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token") or existing_refresh_token
        if not isinstance(access_token, str) or not access_token:
            raise YahooOAuthError("Yahoo token response did not contain an access_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise YahooOAuthError("Yahoo token response did not contain a refresh_token")

        raw_expires = payload.get("expires_in", 3600)
        try:
            expires_in = max(60.0, float(raw_expires))
        except (TypeError, ValueError):
            expires_in = 3600.0
        token_type = str(payload.get("token_type", "bearer"))
        return YahooToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            token_type=token_type,
        )


class YahooFantasyClient:
    BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

    def __init__(
        self,
        *,
        transport: YahooTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.transport = transport or _default_transport
        self.timeout = timeout

    def probe(self, access_token: str) -> None:
        self._get_xml("/game/nhl", access_token)

    def list_hockey_leagues(
        self,
        access_token: str,
        *,
        season: int | None = None,
    ) -> tuple[YahooLeagueInfo, ...]:
        games_filter = ";game_codes=nhl"
        if season is not None:
            games_filter += f";seasons={season}"
        root = self._get_xml(
            f"/users;use_login=1/games{games_filter}/leagues",
            access_token,
        )
        leagues: list[YahooLeagueInfo] = []
        seen: set[str] = set()
        for element in root.iter():
            if _local_name(element.tag) != "league":
                continue
            league_key = _direct_text(element, "league_key")
            name = _direct_text(element, "name")
            if league_key and name and league_key not in seen:
                seen.add(league_key)
                leagues.append(YahooLeagueInfo(league_key=league_key, name=name))
        return tuple(leagues)

    def fetch_league_snapshot(self, access_token: str, league_key: str) -> LeagueSnapshot:
        root = self._get_xml(
            f"/league/{league_key};out=settings,teams",
            access_token,
        )
        league = _first_element(root, "league")
        if league is None:
            raise YahooFantasyError(200, "League response did not contain a league resource")

        returned_key = _direct_text(league, "league_key") or league_key
        league_name = _direct_text(league, "name") or returned_key
        categories = _parse_stat_categories(league)
        team_metadata = _parse_teams(league)
        if not team_metadata:
            raise YahooFantasyError(200, "League response did not contain any teams")

        teams: list[TeamSnapshot] = []
        for team_key, team_name, is_user_team in team_metadata:
            roster_root = self._get_xml(f"/team/{team_key}/roster", access_token)
            players = _parse_players(roster_root)
            teams.append(
                TeamSnapshot(
                    external_id=team_key,
                    name=team_name,
                    is_user_team=is_user_team,
                    players=players,
                )
            )

        if sum(team.is_user_team for team in teams) != 1:
            raise YahooFantasyError(
                200,
                "Yahoo league response did not identify exactly one team owned by the current login",
            )

        return LeagueSnapshot(
            source="yahoo",
            external_id=returned_key,
            name=league_name,
            teams=tuple(teams),
            stat_categories=categories,
        )

    def _get_xml(self, path: str, access_token: str) -> ET.Element:
        headers = {
            "Accept": "application/xml",
            "Authorization": f"Bearer {access_token}",
        }
        status, body = self.transport(
            "GET",
            f"{self.BASE_URL}{path}",
            headers,
            None,
            self.timeout,
        )
        if status != 200:
            raise YahooFantasyError(status, _error_description(body))
        try:
            return ET.fromstring(body)
        except ET.ParseError as error:
            raise YahooFantasyError(200, "Yahoo Fantasy response was not valid XML") from error


class YahooLeagueAdapter:
    """Translate a live Yahoo Fantasy league into Apollo's normalized league snapshot."""

    source = "yahoo"

    def __init__(
        self,
        client: YahooFantasyClient,
        access_token: str,
        league_key: str,
    ) -> None:
        self.client = client
        self.access_token = access_token
        self.league_key = league_key

    def fetch_league(self) -> LeagueSnapshot:
        return self.client.fetch_league_snapshot(self.access_token, self.league_key)


def _parse_stat_categories(league: ET.Element) -> tuple[StatCategorySnapshot, ...]:
    settings = _first_element(league, "settings")
    if settings is None:
        return ()
    stat_categories = _first_element(settings, "stat_categories")
    if stat_categories is None:
        return ()

    categories: list[StatCategorySnapshot] = []
    seen: set[str] = set()
    for stat in stat_categories.iter():
        if _local_name(stat.tag) != "stat":
            continue
        enabled = _direct_text(stat, "enabled")
        if enabled is not None and enabled not in {"1", "true", "True"}:
            continue
        abbreviation = _direct_text(stat, "display_name") or _direct_text(stat, "name")
        display_name = _direct_text(stat, "name") or abbreviation
        if not abbreviation:
            continue
        normalized = abbreviation.strip().upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        categories.append(
            StatCategorySnapshot(
                abbr=normalized,
                display_name=(display_name or normalized).strip(),
            )
        )
    return tuple(categories)


def _parse_teams(league: ET.Element) -> tuple[tuple[str, str, bool], ...]:
    teams_element = _first_element(league, "teams")
    if teams_element is None:
        return ()
    teams: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for team in teams_element.iter():
        if _local_name(team.tag) != "team":
            continue
        team_key = _direct_text(team, "team_key")
        name = _direct_text(team, "name")
        if not team_key or not name or team_key in seen:
            continue
        seen.add(team_key)
        owned = (_direct_text(team, "is_owned_by_current_login") or "0") == "1"
        teams.append((team_key, name, owned))
    return tuple(teams)


def _parse_players(root: ET.Element) -> tuple[PlayerSnapshot, ...]:
    players: list[PlayerSnapshot] = []
    seen: set[str] = set()
    for player in root.iter():
        if _local_name(player.tag) != "player":
            continue
        player_key = _direct_text(player, "player_key")
        if not player_key or player_key in seen:
            continue
        name_element = _direct_child(player, "name")
        first_name = _direct_text(name_element, "first") if name_element is not None else None
        last_name = _direct_text(name_element, "last") if name_element is not None else None
        full_name = _direct_text(name_element, "full") if name_element is not None else None
        if not first_name or not last_name:
            first_name, last_name = _split_name(full_name)
        if not first_name or not last_name:
            continue

        position = _normalize_position(
            _direct_text(player, "display_position")
            or _direct_text(player, "primary_position")
            or "N/A"
        )
        nhl_team = _direct_text(player, "editorial_team_abbr")
        seen.add(player_key)
        players.append(
            PlayerSnapshot(
                external_id=player_key,
                first_name=first_name,
                last_name=last_name,
                primary_position=position,
                nhl_team=nhl_team.upper() if nhl_team else None,
            )
        )
    return tuple(players)


def _normalize_position(raw: str) -> str:
    first = raw.replace("/", ",").split(",", maxsplit=1)[0].strip().upper()
    return {"LW": "L", "RW": "R"}.get(first, first or "N/A")


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    if not full_name:
        return None, None
    parts = full_name.strip().split()
    if len(parts) < 2:
        return None, None
    return " ".join(parts[:-1]), parts[-1]


def _first_element(root: ET.Element, name: str) -> ET.Element | None:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return element
    return None


def _direct_child(root: ET.Element, name: str) -> ET.Element | None:
    for child in root:
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_text(root: ET.Element | None, name: str) -> str | None:
    if root is None:
        return None
    child = _direct_child(root, name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _decode_json(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_error_description(payload: Mapping[str, object]) -> str | None:
    description = payload.get("description")
    if isinstance(description, str):
        return description
    error = payload.get("error")
    if isinstance(error, dict):
        nested = error.get("description") or error.get("message")
        if isinstance(nested, str):
            return nested
    return None


def _error_description(body: bytes) -> str:
    payload = _decode_json(body)
    description = _json_error_description(payload)
    if description:
        return description
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        text = body.decode(errors="replace").strip()
        return text or "Unknown Yahoo Fantasy API error"
    for element in root.iter():
        if _local_name(element.tag) in {"description", "detail", "message"} and element.text:
            return element.text.strip()
    return "Unknown Yahoo Fantasy API error"


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    data: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    request = Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoints
            return int(response.status), response.read()
    except HTTPError as error:
        return int(error.code), error.read()
    except URLError as error:
        raise YahooNetworkError(f"Could not reach Yahoo: {error.reason}") from error
