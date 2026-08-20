from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from apollo.db import Database


@dataclass(frozen=True, slots=True)
class WindowSummary:
    label: str
    games: int
    totals: dict[str, float]
    per_game: dict[str, float]


@dataclass(frozen=True, slots=True)
class PlayerAnalysis:
    player_name: str
    team_abbrev: str | None
    position: str | None
    season: int
    windows: tuple[WindowSummary, ...]
    trend_metric: str | None
    trend: str
    trend_percent: float | None
    schedule_season: int
    schedule_start: date
    schedule_end: date
    upcoming_games: int | None


def _summarize(label: str, games: list[tuple[object, dict[str, float]]]) -> WindowSummary:
    totals: dict[str, float] = {}
    for _, stats in games:
        for name, value in stats.items():
            totals[name] = totals.get(name, 0.0) + float(value)

    game_count = len(games)
    per_game = {
        name: value / game_count
        for name, value in totals.items()
        if game_count > 0
    }
    return WindowSummary(label=label, games=game_count, totals=totals, per_game=per_game)


def _select_trend_metric(position: str | None, season: WindowSummary) -> str | None:
    if position == "G":
        for candidate in ("savePctg", "saves", "wins"):
            if candidate in season.per_game:
                return candidate
        return None

    for candidate in ("points", "goals", "shots"):
        if candidate in season.per_game:
            return candidate
    return None


def _trend(
    position: str | None,
    season: WindowSummary,
    recent: WindowSummary,
) -> tuple[str | None, str, float | None]:
    metric = _select_trend_metric(position, season)
    if metric is None or metric not in recent.per_game:
        return metric, "N/A", None

    baseline = season.per_game[metric]
    current = recent.per_game[metric]
    if baseline == 0:
        if current > 0:
            return metric, "UP", None
        return metric, "STABLE", 0.0

    change = (current - baseline) / abs(baseline)
    if change >= 0.10:
        label = "UP"
    elif change <= -0.10:
        label = "DOWN"
    else:
        label = "STABLE"
    return metric, label, change * 100.0


def _schedule_count(
    database: Database,
    team_abbrev: str | None,
    season: int,
    start: date,
    days: int,
) -> int | None:
    if not team_abbrev:
        return None

    end_exclusive = start + timedelta(days=days)
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM nhl_game
            WHERE season = ?
              AND game_type = 2
              AND (away_team = ? OR home_team = ?)
            """,
            (season, team_abbrev, team_abbrev),
        ).fetchone()["count"]
        if int(stored) == 0:
            return None

        upcoming = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM nhl_game
            WHERE season = ?
              AND game_type = 2
              AND game_date >= ?
              AND game_date < ?
              AND (away_team = ? OR home_team = ?)
            """,
            (
                season,
                start.isoformat(),
                end_exclusive.isoformat(),
                team_abbrev,
                team_abbrev,
            ),
        ).fetchone()["count"]
    return int(upcoming)


def analyze_player(
    database: Database,
    name: str,
    season: int,
    *,
    as_of: date | None = None,
    schedule_season: int | None = None,
    schedule_days: int = 7,
) -> PlayerAnalysis:
    database.initialize()
    identity = database.get_nhl_identity_by_name(name)
    if identity is None:
        raise LookupError(
            f'NHL identity not found for "{name}". '
            f'Run "apollo nhl pool --season {season}" first.'
        )

    card = database.get_player_card(name)
    position = str(card[0]["primary_position"]) if card is not None else None
    games = database.get_player_game_log(name, season, limit=2000)
    if not games:
        raise LookupError(
            f'No stored game log for "{name}" in {season}. '
            f'Run "apollo nhl game-log \"{name}\" --season {season}" first.'
        )

    season_summary = _summarize("Season", games)
    last_30 = _summarize("Last 30", games[:30])
    last_14 = _summarize("Last 14", games[:14])
    last_7 = _summarize("Last 7", games[:7])
    trend_metric, trend_label, trend_percent = _trend(position, season_summary, last_7)

    schedule_start = as_of or datetime.now(UTC).date()
    schedule_length = max(1, schedule_days)
    resolved_schedule_season = schedule_season or season
    upcoming_games = _schedule_count(
        database,
        str(identity["nhl_team"]) if identity["nhl_team"] else None,
        resolved_schedule_season,
        schedule_start,
        schedule_length,
    )

    return PlayerAnalysis(
        player_name=f"{identity['first_name']} {identity['last_name']}",
        team_abbrev=str(identity["nhl_team"]) if identity["nhl_team"] else None,
        position=position,
        season=season,
        windows=(season_summary, last_30, last_14, last_7),
        trend_metric=trend_metric,
        trend=trend_label,
        trend_percent=trend_percent,
        schedule_season=resolved_schedule_season,
        schedule_start=schedule_start,
        schedule_end=schedule_start + timedelta(days=schedule_length - 1),
        upcoming_games=upcoming_games,
    )
