import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apollo.models import NHLStat

JSONValue = dict[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class NHLSeasonStatLine:
    nhl_player_id: int
    stats: tuple[NHLStat, ...]


def _default_fetch_json(url: str, timeout: float) -> JSONValue:
    request = Request(url, headers={"User-Agent": "Apollo-Hockey-Analytics/0.5"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _extract_numeric_stats(
    row: dict[str, Any],
    field_map: dict[str, str],
) -> dict[str, float]:
    stats: dict[str, float] = {}
    for source_name, target_name in field_map.items():
        value = row.get(source_name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        stats[target_name] = float(value)
    return stats


class NHLStatsAdapter:
    STATS_BASE_URL = "https://api.nhle.com/stats/rest/en"

    SKATER_SUMMARY_FIELDS = {
        "gamesPlayed": "gamesPlayed",
        "goals": "goals",
        "assists": "assists",
        "points": "points",
        "ppPoints": "powerPlayPoints",
        "shots": "shots",
        "plusMinus": "plusMinus",
        "penaltyMinutes": "pim",
        "timeOnIcePerGame": "timeOnIcePerGame",
    }
    SKATER_REALTIME_FIELDS = {
        "hits": "hits",
        "blockedShots": "blockedShots",
        "takeaways": "takeaways",
        "giveaways": "giveaways",
    }
    GOALIE_SUMMARY_FIELDS = {
        "gamesPlayed": "gamesPlayed",
        "gamesStarted": "gamesStarted",
        "wins": "wins",
        "losses": "losses",
        "otLosses": "otLosses",
        "shotsAgainst": "shotsAgainst",
        "saves": "saves",
        "goalsAgainst": "goalsAgainst",
        "savePct": "savePctg",
        "goalsAgainstAverage": "goalsAgainstAvg",
        "shutouts": "shutouts",
        "timeOnIce": "timeOnIce",
    }

    def __init__(
        self,
        fetch_json: Callable[[str], JSONValue] | None = None,
        *,
        timeout: float = 20.0,
    ) -> None:
        self.timeout = timeout
        self._fetch_json = fetch_json or (lambda url: _default_fetch_json(url, timeout))

    def _fetch_report(
        self,
        player_type: str,
        report: str,
        season: int,
        game_type: int,
    ) -> list[dict[str, Any]]:
        params = urlencode(
            {
                "isAggregate": "true",
                "isGame": "false",
                "start": 0,
                "limit": -1,
                "cayenneExp": f"seasonId={season} and gameTypeId={game_type}",
            }
        )
        payload = self._fetch_json(
            f"{self.STATS_BASE_URL}/{player_type}/{report}?{params}"
        )
        if not isinstance(payload, dict):
            raise TypeError(f"Unexpected NHL stats response for {player_type}/{report}")
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]

    @staticmethod
    def _merge_report(
        target: dict[int, dict[str, float]],
        rows: list[dict[str, Any]],
        field_map: dict[str, str],
    ) -> None:
        for row in rows:
            raw_player_id = row.get("playerId")
            if raw_player_id is None:
                continue
            player_id = int(raw_player_id)
            target.setdefault(player_id, {}).update(
                _extract_numeric_stats(row, field_map)
            )

    @staticmethod
    def _to_lines(
        stats_by_player: dict[int, dict[str, float]],
    ) -> tuple[NHLSeasonStatLine, ...]:
        return tuple(
            NHLSeasonStatLine(
                nhl_player_id=player_id,
                stats=tuple(
                    NHLStat(name=name, value=value)
                    for name, value in sorted(stats.items())
                ),
            )
            for player_id, stats in sorted(stats_by_player.items())
        )

    def fetch_skater_stats(
        self,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLSeasonStatLine, ...]:
        stats_by_player: dict[int, dict[str, float]] = {}
        self._merge_report(
            stats_by_player,
            self._fetch_report("skater", "summary", season, game_type),
            self.SKATER_SUMMARY_FIELDS,
        )
        for stats in stats_by_player.values():
            for target_name in self.SKATER_REALTIME_FIELDS.values():
                stats.setdefault(target_name, 0.0)
        self._merge_report(
            stats_by_player,
            self._fetch_report("skater", "realtime", season, game_type),
            self.SKATER_REALTIME_FIELDS,
        )
        return self._to_lines(stats_by_player)

    def fetch_goalie_stats(
        self,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLSeasonStatLine, ...]:
        stats_by_player: dict[int, dict[str, float]] = {}
        self._merge_report(
            stats_by_player,
            self._fetch_report("goalie", "summary", season, game_type),
            self.GOALIE_SUMMARY_FIELDS,
        )
        return self._to_lines(stats_by_player)
