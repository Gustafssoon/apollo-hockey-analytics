import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apollo.models import NHLGame, NHLPlayerGameData, NHLStat

JSONValue = dict[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class NHLSeasonStatLine:
    nhl_player_id: int
    stats: tuple[NHLStat, ...]


def _default_fetch_json(url: str, timeout: float) -> JSONValue:
    request = Request(url, headers={"User-Agent": "Apollo-Hockey-Analytics/0.7"})
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


def _text_value(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _season_month_windows(season: int) -> tuple[tuple[str, str], ...]:
    text = str(season)
    if len(text) != 8:
        raise ValueError(f"Invalid NHL season id: {season}")
    start_year = int(text[:4])
    end_year = int(text[4:])
    if end_year != start_year + 1:
        raise ValueError(f"Invalid NHL season id: {season}")

    current = date(start_year, 9, 1)
    stop = date(end_year, 7, 1)
    windows: list[tuple[str, str]] = []
    while current < stop:
        following = _next_month(current)
        windows.append((current.isoformat(), following.isoformat()))
        current = following
    return tuple(windows)


class NHLStatsAdapter:
    STATS_BASE_URL = "https://api.nhle.com/stats/rest/en"
    GAME_REPORT_PAGE_CAP = 100
    GAME_REPORT_TOTAL_CAP = 10000
    GAME_REPORT_SORT = json.dumps(
        [
            {"property": "gameDate", "direction": "DESC"},
            {"property": "playerId", "direction": "ASC"},
            {"property": "gameId", "direction": "ASC"},
        ],
        separators=(",", ":"),
    )

    SKATER_SUMMARY_FIELDS: ClassVar[dict[str, str]] = {
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
    SKATER_REALTIME_FIELDS: ClassVar[dict[str, str]] = {
        "hits": "hits",
        "blockedShots": "blockedShots",
        "takeaways": "takeaways",
        "giveaways": "giveaways",
    }
    GOALIE_SUMMARY_FIELDS: ClassVar[dict[str, str]] = {
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
    SKATER_GAME_SUMMARY_FIELDS: ClassVar[dict[str, str]] = {
        "goals": "goals",
        "assists": "assists",
        "points": "points",
        "ppPoints": "powerPlayPoints",
        "shots": "shots",
        "plusMinus": "plusMinus",
        "penaltyMinutes": "pim",
        "timeOnIce": "timeOnIce",
        "timeOnIcePerGame": "timeOnIce",
    }
    GOALIE_GAME_SUMMARY_FIELDS: ClassVar[dict[str, str]] = {
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

    def _game_report_url(
        self,
        player_type: str,
        report: str,
        cayenne_expression: str,
        *,
        start: int,
        limit: int,
    ) -> str:
        params = urlencode(
            {
                "isAggregate": "false",
                "isGame": "true",
                "sort": self.GAME_REPORT_SORT,
                "start": start,
                "limit": limit,
                "cayenneExp": cayenne_expression,
            }
        )
        return f"{self.STATS_BASE_URL}/{player_type}/{report}?{params}"

    def _paginate_game_report(
        self,
        player_type: str,
        report: str,
        cayenne_expression: str,
        page_size: int,
        *,
        abort_on_total_cap: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        rows: list[dict[str, Any]] = []
        start = 0
        limit = min(max(1, page_size), self.GAME_REPORT_PAGE_CAP)
        total: int | None = None

        while True:
            payload = self._fetch_json(
                self._game_report_url(
                    player_type,
                    report,
                    cayenne_expression,
                    start=start,
                    limit=limit,
                )
            )
            if not isinstance(payload, dict):
                raise TypeError(
                    f"Unexpected NHL game stats response for {player_type}/{report}"
                )

            raw_total = payload.get("total")
            total = int(raw_total) if isinstance(raw_total, (int, float)) else None
            if abort_on_total_cap and start == 0 and total is not None:
                if total >= self.GAME_REPORT_TOTAL_CAP:
                    return [], total

            data = payload.get("data")
            if not isinstance(data, list):
                break
            page = [row for row in data if isinstance(row, dict)]
            if not page:
                break
            rows.extend(page)

            if total is not None and len(rows) >= total:
                break
            if total is None and len(page) < limit:
                break
            start += len(page)

        return rows, total

    def _fetch_game_report_window(
        self,
        player_type: str,
        report: str,
        cayenne_expression: str,
        page_size: int,
    ) -> list[dict[str, Any]]:
        payload = self._fetch_json(
            self._game_report_url(
                player_type,
                report,
                cayenne_expression,
                start=0,
                limit=-1,
            )
        )
        if not isinstance(payload, dict):
            raise TypeError(
                f"Unexpected NHL game stats response for {player_type}/{report}"
            )
        data = payload.get("data")
        page = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        raw_total = payload.get("total")
        total = int(raw_total) if isinstance(raw_total, (int, float)) else None

        if total is not None and total >= self.GAME_REPORT_TOTAL_CAP:
            raise RuntimeError(
                f"NHL game report window still hit the {self.GAME_REPORT_TOTAL_CAP}-row cap: "
                f"{cayenne_expression}"
            )
        if total is None or len(page) >= total:
            return page

        rows, _ = self._paginate_game_report(
            player_type,
            report,
            cayenne_expression,
            page_size,
        )
        return rows

    @staticmethod
    def _deduplicate_game_report_rows(
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        keyed: dict[tuple[int, int], dict[str, Any]] = {}
        unkeyed: list[dict[str, Any]] = []
        for row in rows:
            raw_player_id = row.get("playerId")
            raw_game_id = row.get("gameId")
            if raw_player_id is None or raw_game_id is None:
                unkeyed.append(row)
                continue
            keyed[(int(raw_player_id), int(raw_game_id))] = row
        return list(keyed.values()) + unkeyed

    def _fetch_game_report(
        self,
        player_type: str,
        report: str,
        season: int,
        game_type: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        base_expression = f"seasonId={season} and gameTypeId={game_type}"
        rows, total = self._paginate_game_report(
            player_type,
            report,
            base_expression,
            page_size,
            abort_on_total_cap=True,
        )
        if total is None or total < self.GAME_REPORT_TOTAL_CAP:
            return rows

        partitioned: list[dict[str, Any]] = []
        for window_start, window_end in _season_month_windows(season):
            expression = (
                f'{base_expression} and gameDate>="{window_start}" '
                f'and gameDate<"{window_end}"'
            )
            partitioned.extend(
                self._fetch_game_report_window(
                    player_type,
                    report,
                    expression,
                    page_size,
                )
            )
        return self._deduplicate_game_report_rows(partitioned)

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
            target.setdefault(player_id, {}).update(_extract_numeric_stats(row, field_map))

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

    @staticmethod
    def _merge_game_rows(
        target: dict[tuple[int, int], dict[str, float]],
        metadata: dict[tuple[int, int], tuple[str, str | None, str | None, str | None]],
        rows: list[dict[str, Any]],
        field_map: dict[str, str],
    ) -> None:
        for row in rows:
            raw_player_id = row.get("playerId")
            raw_game_id = row.get("gameId")
            if raw_player_id is None or raw_game_id is None:
                continue

            key = (int(raw_player_id), int(raw_game_id))
            target.setdefault(key, {}).update(_extract_numeric_stats(row, field_map))

            game_date = _text_value(row, "gameDate")
            existing = metadata.get(key)
            if game_date is None and existing is None:
                continue
            metadata[key] = (
                game_date or existing[0],
                _text_value(row, "teamAbbrev", "teamAbbrevs")
                or (existing[1] if existing else None),
                _text_value(row, "opponentTeamAbbrev", "opponentAbbrev")
                or (existing[2] if existing else None),
                _text_value(row, "homeRoadCode", "homeRoadFlag", "homeRoad")
                or (existing[3] if existing else None),
            )

    @staticmethod
    def _to_game_lines(
        stats_by_game: dict[tuple[int, int], dict[str, float]],
        metadata: dict[tuple[int, int], tuple[str, str | None, str | None, str | None]],
        season: int,
        game_type: int,
    ) -> tuple[NHLPlayerGameData, ...]:
        lines: list[NHLPlayerGameData] = []
        for (player_id, game_id), stats in sorted(stats_by_game.items()):
            game_metadata = metadata.get((player_id, game_id))
            if game_metadata is None:
                continue
            game_date, team_abbrev, opponent_abbrev, home_road = game_metadata
            lines.append(
                NHLPlayerGameData(
                    nhl_player_id=player_id,
                    game=NHLGame(
                        game_id=game_id,
                        season=season,
                        game_type=game_type,
                        game_date=game_date,
                        start_time_utc=None,
                        away_team=None,
                        home_team=None,
                        game_state="FINAL",
                    ),
                    team_abbrev=team_abbrev,
                    opponent_abbrev=opponent_abbrev,
                    home_road=home_road,
                    stats=tuple(
                        NHLStat(name=name, value=value)
                        for name, value in sorted(stats.items())
                    ),
                )
            )
        return tuple(lines)

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

    def fetch_skater_game_stats(
        self,
        season: int,
        game_type: int = 2,
        page_size: int = 100,
    ) -> tuple[NHLPlayerGameData, ...]:
        stats_by_game: dict[tuple[int, int], dict[str, float]] = {}
        metadata: dict[
            tuple[int, int],
            tuple[str, str | None, str | None, str | None],
        ] = {}
        self._merge_game_rows(
            stats_by_game,
            metadata,
            self._fetch_game_report("skater", "summary", season, game_type, page_size),
            self.SKATER_GAME_SUMMARY_FIELDS,
        )
        for stats in stats_by_game.values():
            for target_name in self.SKATER_REALTIME_FIELDS.values():
                stats.setdefault(target_name, 0.0)
        self._merge_game_rows(
            stats_by_game,
            metadata,
            self._fetch_game_report("skater", "realtime", season, game_type, page_size),
            self.SKATER_REALTIME_FIELDS,
        )
        return self._to_game_lines(stats_by_game, metadata, season, game_type)

    def fetch_goalie_game_stats(
        self,
        season: int,
        game_type: int = 2,
        page_size: int = 100,
    ) -> tuple[NHLPlayerGameData, ...]:
        stats_by_game: dict[tuple[int, int], dict[str, float]] = {}
        metadata: dict[
            tuple[int, int],
            tuple[str, str | None, str | None, str | None],
        ] = {}
        self._merge_game_rows(
            stats_by_game,
            metadata,
            self._fetch_game_report("goalie", "summary", season, game_type, page_size),
            self.GOALIE_GAME_SUMMARY_FIELDS,
        )
        return self._to_game_lines(stats_by_game, metadata, season, game_type)
