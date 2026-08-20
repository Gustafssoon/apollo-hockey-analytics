from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import fmean, pstdev

from apollo.analytics.rankings import CategorySpec, rank_players
from apollo.db import Database


@dataclass(frozen=True, slots=True)
class WaiverTarget:
    rank: int
    name: str
    team_abbrev: str | None
    position: str
    games: int
    rostered: bool
    category_score: float
    schedule_games: int | None
    off_night_games: int | None
    schedule_z: float | None
    schedule_component: float
    trend: str
    trend_percent: float | None
    trend_component: float
    score: float
    category_values: dict[str, float]


@dataclass(frozen=True, slots=True)
class WaiverBoard:
    season: int
    schedule_season: int
    player_type: str
    mode: str
    min_games: int
    categories: tuple[CategorySpec, ...]
    start_date: date
    end_date: date
    days: int
    include_rostered: bool
    eligible_players: int
    schedule_complete: bool
    schedule_team_count: int
    expected_team_count: int
    off_night_threshold: int
    schedule_weight: float
    trend_weight: float
    off_night_bonus: float
    players: tuple[WaiverTarget, ...]


@dataclass(frozen=True, slots=True)
class _ScheduleSnapshot:
    complete: bool
    team_count: int
    expected_team_count: int
    games: dict[str, int]
    off_nights: dict[str, int]


@dataclass(frozen=True, slots=True)
class _TrendSignal:
    label: str
    percent: float | None


def _identity_key(name: str, team_abbrev: str | None) -> tuple[str, str]:
    return name.strip().casefold(), (team_abbrev or "").upper()


def _rostered_players(database: Database) -> set[tuple[str, str]]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT
                p.first_name || ' ' || p.last_name AS player_name,
                p.nhl_team
            FROM roster r
            JOIN player p ON p.id = r.player_id
            """
        ).fetchall()
    return {
        _identity_key(
            str(row["player_name"]),
            str(row["nhl_team"]) if row["nhl_team"] else None,
        )
        for row in rows
    }


def _expected_team_count(database: Database) -> int:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT p.nhl_team) AS count
            FROM player p
            JOIN nhl_player_profile profile ON profile.player_id = p.id
            WHERE profile.is_active = 1 AND p.nhl_team IS NOT NULL
            """
        ).fetchone()
    return int(row["count"]) if row is not None else 0


def _schedule_snapshot(
    database: Database,
    season: int,
    start_date: date,
    days: int,
    off_night_threshold: int,
) -> _ScheduleSnapshot:
    end_exclusive = start_date + timedelta(days=days)
    expected_teams = _expected_team_count(database)

    with database.connect() as connection:
        coverage_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM nhl_schedule_sync
            WHERE season = ? AND game_count > 0
            """,
            (season,),
        ).fetchone()
        team_count = int(coverage_row["count"]) if coverage_row is not None else 0
        if expected_teams == 0:
            expected_teams = team_count
        complete = team_count > 0 and team_count >= expected_teams
        if not complete:
            return _ScheduleSnapshot(
                complete=False,
                team_count=team_count,
                expected_team_count=expected_teams,
                games={},
                off_nights={},
            )

        rows = connection.execute(
            """
            SELECT game_date, away_team, home_team
            FROM nhl_game
            WHERE season = ?
              AND game_type = 2
              AND game_date >= ?
              AND game_date < ?
            ORDER BY game_date, game_id
            """,
            (season, start_date.isoformat(), end_exclusive.isoformat()),
        ).fetchall()

    games_per_date: dict[str, int] = {}
    for row in rows:
        game_date = str(row["game_date"])
        games_per_date[game_date] = games_per_date.get(game_date, 0) + 1

    games: dict[str, int] = {}
    off_nights: dict[str, int] = {}
    for row in rows:
        game_date = str(row["game_date"])
        is_off_night = games_per_date[game_date] <= off_night_threshold
        for raw_team in (row["away_team"], row["home_team"]):
            if not raw_team:
                continue
            team = str(raw_team).upper()
            games[team] = games.get(team, 0) + 1
            if is_off_night:
                off_nights[team] = off_nights.get(team, 0) + 1

    return _ScheduleSnapshot(
        complete=True,
        team_count=team_count,
        expected_team_count=expected_teams,
        games=games,
        off_nights=off_nights,
    )


def _trend_signals(database: Database, season: int) -> dict[tuple[str, str], _TrendSignal]:
    query = """
        WITH ranked_games AS (
            SELECT
                pg.player_id,
                g.game_id,
                ROW_NUMBER() OVER (
                    PARTITION BY pg.player_id
                    ORDER BY g.game_date DESC, g.game_id DESC
                ) AS game_rank
            FROM nhl_player_game pg
            JOIN nhl_game g ON g.game_id = pg.game_id
            WHERE g.season = ? AND g.game_type = 2
        ),
        recent AS (
            SELECT
                ranked.player_id,
                COUNT(DISTINCT ranked.game_id) AS recent_games,
                COALESCE(
                    SUM(CASE WHEN stat.stat_name = 'points' THEN stat.value ELSE 0 END),
                    0
                ) AS recent_points
            FROM ranked_games ranked
            LEFT JOIN nhl_player_game_stat stat
                ON stat.player_id = ranked.player_id
               AND stat.game_id = ranked.game_id
            WHERE ranked.game_rank <= 7
            GROUP BY ranked.player_id
        )
        SELECT
            p.first_name || ' ' || p.last_name AS player_name,
            p.nhl_team,
            recent.recent_games,
            recent.recent_points,
            season_points.value AS season_points,
            season_games.value AS season_games
        FROM recent
        JOIN player p ON p.id = recent.player_id
        LEFT JOIN nhl_player_season_stat season_points
            ON season_points.player_id = recent.player_id
           AND season_points.season = ?
           AND season_points.game_type = 2
           AND season_points.stat_name = 'points'
        LEFT JOIN nhl_player_season_stat season_games
            ON season_games.player_id = recent.player_id
           AND season_games.season = ?
           AND season_games.game_type = 2
           AND season_games.stat_name = 'gamesPlayed'
    """
    with database.connect() as connection:
        rows = connection.execute(query, (season, season, season)).fetchall()

    result: dict[tuple[str, str], _TrendSignal] = {}
    for row in rows:
        recent_games = int(row["recent_games"] or 0)
        season_games = float(row["season_games"] or 0.0)
        if recent_games <= 0 or season_games <= 0 or row["season_points"] is None:
            continue

        recent_rate = float(row["recent_points"] or 0.0) / recent_games
        season_rate = float(row["season_points"]) / season_games
        if season_rate == 0:
            label = "UP" if recent_rate > 0 else "STABLE"
            percent = None
        else:
            change = (recent_rate - season_rate) / abs(season_rate)
            percent = change * 100.0
            if change >= 0.10:
                label = "UP"
            elif change <= -0.10:
                label = "DOWN"
            else:
                label = "STABLE"

        result[
            _identity_key(
                str(row["player_name"]),
                str(row["nhl_team"]) if row["nhl_team"] else None,
            )
        ] = _TrendSignal(label=label, percent=percent)
    return result


def _position_matches(position: str, requested: str | None) -> bool:
    if requested is None:
        return True
    normalized = requested.strip().upper()
    aliases = {"LW": "L", "RW": "R"}
    normalized = aliases.get(normalized, normalized)
    actual = position.upper()
    if normalized == "F":
        return actual in {"C", "L", "R", "F"}
    return actual == normalized


def build_waiver_board(
    database: Database,
    season: int,
    *,
    schedule_season: int | None = None,
    as_of: date | None = None,
    days: int = 7,
    player_type: str = "skater",
    categories: str | None = None,
    mode: str = "per-game",
    min_games: int = 10,
    position: str | None = None,
    include_rostered: bool = False,
    schedule_weight: float = 1.0,
    trend_weight: float = 0.5,
    off_night_threshold: int = 8,
    off_night_bonus: float = 0.5,
    limit: int = 25,
) -> WaiverBoard:
    database.initialize()
    if schedule_weight < 0 or trend_weight < 0 or off_night_bonus < 0:
        raise ValueError("waiver score weights must be non-negative")

    resolved_days = max(1, days)
    resolved_min_games = max(0, min_games)
    resolved_schedule_season = schedule_season or season
    start_date = as_of or datetime.now(UTC).date()
    end_date = start_date + timedelta(days=resolved_days - 1)
    threshold = max(0, off_night_threshold)

    ranking = rank_players(
        database,
        season,
        player_type=player_type,
        categories=categories,
        mode=mode,
        min_games=resolved_min_games,
        limit=10000,
    )
    rostered = _rostered_players(database)
    candidates = [
        player
        for player in ranking.players
        if _position_matches(player.position, position)
        and (
            include_rostered
            or _identity_key(player.name, player.team_abbrev) not in rostered
        )
    ]

    schedule = _schedule_snapshot(
        database,
        resolved_schedule_season,
        start_date,
        resolved_days,
        threshold,
    )
    opportunity: dict[tuple[str, str], float] = {}
    if schedule.complete:
        for player in candidates:
            team = (player.team_abbrev or "").upper()
            games = schedule.games.get(team, 0)
            off_nights = schedule.off_nights.get(team, 0)
            opportunity[_identity_key(player.name, player.team_abbrev)] = (
                float(games) + off_night_bonus * float(off_nights)
            )

    opportunity_values = list(opportunity.values())
    opportunity_mean = fmean(opportunity_values) if opportunity_values else 0.0
    opportunity_deviation = (
        pstdev(opportunity_values) if len(opportunity_values) > 1 else 0.0
    )
    trends = _trend_signals(database, season) if player_type == "skater" else {}

    scored: list[WaiverTarget] = []
    for player in candidates:
        key = _identity_key(player.name, player.team_abbrev)
        is_rostered = key in rostered
        if schedule.complete:
            team = (player.team_abbrev or "").upper()
            schedule_games = schedule.games.get(team, 0)
            off_night_games = schedule.off_nights.get(team, 0)
            raw_opportunity = opportunity.get(key, 0.0)
            schedule_z = (
                0.0
                if opportunity_deviation == 0
                else (raw_opportunity - opportunity_mean) / opportunity_deviation
            )
            schedule_component = schedule_weight * schedule_z
        else:
            schedule_games = None
            off_night_games = None
            schedule_z = None
            schedule_component = 0.0

        trend = trends.get(key)
        trend_label = trend.label if trend is not None else "N/A"
        trend_percent = trend.percent if trend is not None else None
        if trend_label == "UP":
            trend_signal = 1.0
        elif trend_label == "DOWN":
            trend_signal = -1.0
        else:
            trend_signal = 0.0
        trend_component = trend_weight * trend_signal
        score = player.score + schedule_component + trend_component
        scored.append(
            WaiverTarget(
                rank=0,
                name=player.name,
                team_abbrev=player.team_abbrev,
                position=player.position,
                games=player.games,
                rostered=is_rostered,
                category_score=player.score,
                schedule_games=schedule_games,
                off_night_games=off_night_games,
                schedule_z=schedule_z,
                schedule_component=schedule_component,
                trend=trend_label,
                trend_percent=trend_percent,
                trend_component=trend_component,
                score=score,
                category_values=player.values,
            )
        )

    scored.sort(key=lambda player: (-player.score, player.name.casefold()))
    ranked_targets = tuple(
        WaiverTarget(
            rank=index,
            name=player.name,
            team_abbrev=player.team_abbrev,
            position=player.position,
            games=player.games,
            rostered=player.rostered,
            category_score=player.category_score,
            schedule_games=player.schedule_games,
            off_night_games=player.off_night_games,
            schedule_z=player.schedule_z,
            schedule_component=player.schedule_component,
            trend=player.trend,
            trend_percent=player.trend_percent,
            trend_component=player.trend_component,
            score=player.score,
            category_values=player.category_values,
        )
        for index, player in enumerate(scored[: max(1, limit)], start=1)
    )

    return WaiverBoard(
        season=season,
        schedule_season=resolved_schedule_season,
        player_type=player_type,
        mode=mode,
        min_games=resolved_min_games,
        categories=ranking.categories,
        start_date=start_date,
        end_date=end_date,
        days=resolved_days,
        include_rostered=include_rostered,
        eligible_players=len(scored),
        schedule_complete=schedule.complete,
        schedule_team_count=schedule.team_count,
        expected_team_count=schedule.expected_team_count,
        off_night_threshold=threshold,
        schedule_weight=schedule_weight,
        trend_weight=trend_weight,
        off_night_bonus=off_night_bonus,
        players=ranked_targets,
    )


def get_player_value(
    database: Database,
    name: str,
    season: int,
    **kwargs: object,
) -> tuple[WaiverBoard, WaiverTarget | None]:
    board = build_waiver_board(
        database,
        season,
        include_rostered=True,
        limit=10000,
        **kwargs,
    )
    wanted = name.strip().casefold()
    player = next((target for target in board.players if target.name.casefold() == wanted), None)
    return board, player
