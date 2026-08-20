import json
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apollo.models import NHLPlayerData, NHLStat

JSONValue = dict[str, Any] | list[Any]


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_like = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_like.casefold().split())


def _localized_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        default = value.get("default")
        return default if isinstance(default, str) else None
    return None


def _default_fetch_json(url: str, timeout: float) -> JSONValue:
    request = Request(url, headers={"User-Agent": "Apollo-Hockey-Analytics/0.2"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed NHL HTTPS endpoints
        return json.load(response)


class NHLAdapter:
    SEARCH_URL = "https://search.d3.nhle.com/api/v1/search/player"
    WEB_BASE_URL = "https://api-web.nhle.com/v1"

    def __init__(
        self,
        fetch_json: Callable[[str], JSONValue] | None = None,
        *,
        timeout: float = 20.0,
    ) -> None:
        self.timeout = timeout
        self._fetch_json = fetch_json or (lambda url: _default_fetch_json(url, timeout))

    def find_player(
        self,
        first_name: str,
        last_name: str,
        team_abbrev: str | None = None,
    ) -> NHLPlayerData | None:
        full_name = f"{first_name} {last_name}"
        params = urlencode({"culture": "en-us", "limit": 20, "q": full_name})
        payload = self._fetch_json(f"{self.SEARCH_URL}?{params}")

        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            raw_candidates = payload.get("data") or payload.get("results") or []
            candidates = raw_candidates if isinstance(raw_candidates, list) else []
        else:
            candidates = []

        target_name = _normalize_name(full_name)
        exact_matches: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_name = (
                _localized_text(candidate.get("name"))
                or _localized_text(candidate.get("fullName"))
                or ""
            )
            if _normalize_name(candidate_name) == target_name:
                exact_matches.append(candidate)

        if not exact_matches:
            return None

        selected: dict[str, Any] | None = None
        if team_abbrev:
            target_team = team_abbrev.upper()
            team_matches = [
                candidate
                for candidate in exact_matches
                if str(
                    candidate.get("teamAbbrev")
                    or candidate.get("lastTeamAbbrev")
                    or candidate.get("team")
                    or ""
                ).upper()
                == target_team
            ]
            if len(team_matches) == 1:
                selected = team_matches[0]

        if selected is None and len(exact_matches) == 1:
            selected = exact_matches[0]

        if selected is None:
            return None

        raw_player_id = selected.get("playerId") or selected.get("id")
        if raw_player_id is None:
            return None
        return self.fetch_player(int(raw_player_id))

    def fetch_player(self, nhl_player_id: int) -> NHLPlayerData:
        payload = self._fetch_json(f"{self.WEB_BASE_URL}/player/{nhl_player_id}/landing")
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected NHL player response for {nhl_player_id}")

        first_name = _localized_text(payload.get("firstName")) or ""
        last_name = _localized_text(payload.get("lastName")) or ""
        team_abbrev = payload.get("currentTeamAbbrev")
        position = payload.get("position")
        sweater_number = payload.get("sweaterNumber")
        birth_date = payload.get("birthDate")

        featured = payload.get("featuredStats")
        season: int | None = None
        stat_values: list[NHLStat] = []
        if isinstance(featured, dict):
            raw_season = featured.get("season")
            if raw_season is not None:
                season = int(raw_season)
            regular = featured.get("regularSeason")
            if isinstance(regular, dict):
                sub_season = regular.get("subSeason")
                if isinstance(sub_season, dict):
                    for name, value in sorted(sub_season.items()):
                        if isinstance(value, bool) or not isinstance(value, (int, float)):
                            continue
                        stat_values.append(NHLStat(name=name, value=float(value)))

        return NHLPlayerData(
            nhl_player_id=int(payload.get("playerId", nhl_player_id)),
            first_name=first_name,
            last_name=last_name,
            team_abbrev=str(team_abbrev) if team_abbrev else None,
            position=str(position) if position else None,
            is_active=bool(payload.get("isActive", False)),
            sweater_number=int(sweater_number) if sweater_number is not None else None,
            birth_date=str(birth_date) if birth_date else None,
            season=season,
            stats=tuple(stat_values),
        )
