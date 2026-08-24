from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.age_model_backtest import (
    AgeModelAggregateResult,
    AgeModelBacktestPlayer,
    AgeModelBacktestResult,
    AgeModelHistorySeason,
    build_age_model_aggregate,
    build_age_model_backtest_result,
)
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)


def _season_start_date(season: int) -> date:
    text = str(season)
    if len(text) != 8:
        raise ProjectionError(f"Invalid NHL season id: {season}")
    return date(int(text[:4]), 10, 1)


def _age_on_date(birth_date: date, when: date) -> float:
    return (when - birth_date).days / 365.2425


def _target_seasons(latest_season: int, years: int) -> tuple[int, ...]:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    text = str(latest_season)
    if len(text) != 8:
        raise ProjectionError(f"Invalid NHL season id: {latest_season}")
    start_year = int(text[:4])
    return tuple(int(f"{year}{year + 1}") for year in range(start_year, start_year - years, -1))


def run_age_model_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
) -> AgeModelBacktestResult:
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, len(DEFAULT_SEASON_WEIGHTS))
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)

    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                profile.birth_date,
                ns.season,
                ns.stat_name,
                ns.value
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            LEFT JOIN nhl_player_profile profile
                ON profile.player_id = p.id
            JOIN nhl_player_season_stat ns
                ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({placeholders})
              AND UPPER(COALESCE(p.primary_position, '')) <> 'G'
            ORDER BY p.id, ns.season DESC, ns.stat_name
            """,
            seasons,
        ).fetchall()

    player_meta: dict[int, tuple[str, str, str | None, str, str | None]] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        player_id = int(row["id"])
        player_meta[player_id] = (
            str(row["first_name"]),
            str(row["last_name"]),
            row["nhl_team"],
            str(row["primary_position"] or ""),
            row["birth_date"],
        )
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    actual_required = ("gamesPlayed", *SKATER_PROJECTION_STATS)
    evaluated: list[AgeModelBacktestPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat not in actual_stats for stat in actual_required):
            continue
        if actual_stats["gamesPlayed"] < min_actual_games:
            continue

        history: list[ProjectionSeason] = []
        complete_history = True
        for season in source_seasons:
            season_stats = seasons_by_stat.get(season, {})
            games_played = season_stats.get("gamesPlayed", 0.0)
            if games_played <= 0 or any(
                stat not in season_stats for stat in SKATER_PROJECTION_STATS
            ):
                complete_history = False
                break
            history.append(
                ProjectionSeason(
                    season=season,
                    games_played=games_played,
                    stats=season_stats,
                )
            )
        if not complete_history:
            continue

        first_name, last_name, team_abbrev, position, birth_date_text = player_meta[player_id]
        if not birth_date_text:
            continue
        try:
            birth_date = date.fromisoformat(str(birth_date_text))
        except ValueError:
            continue

        player_name = f"{first_name} {last_name}"
        projection = build_skater_projection(
            player_id=player_id,
            player_name=player_name,
            team_abbrev=team_abbrev,
            position=position,
            target_season=target_season,
            history=tuple(history),
        )
        evaluated.append(
            AgeModelBacktestPlayer(
                player_id=player_id,
                player_name=player_name,
                position=position,
                projected_games=projection.projected_games,
                target_age=_age_on_date(birth_date, _season_start_date(target_season)),
                history=tuple(
                    AgeModelHistorySeason(
                        source_age=_age_on_date(
                            birth_date,
                            _season_start_date(season.season),
                        ),
                        games_played=season.games_played,
                        stats=season.stats,
                    )
                    for season in history
                ),
                actual_stats=actual_stats,
            )
        )

    return build_age_model_backtest_result(
        target_season=target_season,
        source_seasons=source_seasons,
        players=tuple(evaluated),
    )


def run_age_model_aggregate(
    database: Database,
    latest_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
) -> AgeModelAggregateResult:
    results = tuple(
        run_age_model_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
        )
        for season in _target_seasons(latest_season, years)
    )
    return build_age_model_aggregate(results)
