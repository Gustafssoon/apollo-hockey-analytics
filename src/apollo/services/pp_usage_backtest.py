from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.pp_usage_backtest import (
    PPUsageBacktestPlayer,
    PPUsageBacktestResult,
    PPUsageHistorySeason,
    build_pp_usage_backtest_result,
)
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)


def run_pp_usage_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> PPUsageBacktestResult:
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")
    if min_history_seasons < 1 or min_history_seasons > len(DEFAULT_SEASON_WEIGHTS):
        raise ProjectionError(
            f"min_history_seasons must be between 1 and {len(DEFAULT_SEASON_WEIGHTS)}"
        )

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

    base_eligible_players = 0
    evaluated: list[PPUsageBacktestPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if "gamesPlayed" not in actual_stats or "powerPlayPoints" not in actual_stats:
            continue
        if actual_stats["gamesPlayed"] < min_actual_games:
            continue

        projection_history: list[ProjectionSeason] = []
        pp_history: list[PPUsageHistorySeason] = []
        usable_history_seasons = 0
        complete_projection_history = True
        complete_pp_history = True
        for season in source_seasons:
            season_stats = seasons_by_stat.get(season, {})
            games_played = season_stats.get("gamesPlayed", 0.0)
            projection_history.append(
                ProjectionSeason(
                    season=season,
                    games_played=games_played,
                    stats=season_stats,
                )
            )
            if games_played > 0:
                usable_history_seasons += 1
            if games_played <= 0 or any(
                stat_name not in season_stats for stat_name in SKATER_PROJECTION_STATS
            ):
                complete_projection_history = False

            pp_toi = season_stats.get("powerPlayTimeOnIcePerGame", 0.0)
            ppp = season_stats.get("powerPlayPoints")
            if games_played <= 0 or pp_toi <= 0 or ppp is None:
                complete_pp_history = False
            pp_history.append(
                PPUsageHistorySeason(
                    season=season,
                    games_played=games_played,
                    pp_time_on_ice_per_game=pp_toi,
                    power_play_points=0.0 if ppp is None else ppp,
                )
            )

        if usable_history_seasons < min_history_seasons or not complete_projection_history:
            continue
        base_eligible_players += 1
        if not complete_pp_history or "powerPlayTimeOnIcePerGame" not in actual_stats:
            continue

        first_name, last_name, team_abbrev, position, birth_date_text = player_meta[player_id]
        birth_date: date | None = None
        if birth_date_text:
            try:
                birth_date = date.fromisoformat(str(birth_date_text))
            except ValueError:
                continue

        player_name = f"{first_name} {last_name}"
        try:
            baseline = build_skater_projection(
                player_id=player_id,
                player_name=player_name,
                team_abbrev=team_abbrev,
                position=position,
                target_season=target_season,
                history=tuple(projection_history),
                birth_date=birth_date,
            )
        except ProjectionError:
            continue

        evaluated.append(
            PPUsageBacktestPlayer(
                player_id=player_id,
                player_name=player_name,
                position=position,
                target_season=target_season,
                birth_date=birth_date,
                projected_games=baseline.projected_games,
                baseline_power_play_points=baseline.stats["powerPlayPoints"],
                history=tuple(pp_history),
                actual_pp_time_on_ice_per_game=actual_stats["powerPlayTimeOnIcePerGame"],
                actual_power_play_points=actual_stats["powerPlayPoints"],
            )
        )

    if base_eligible_players <= 0:
        raise ProjectionError("PP usage backtest found no eligible skaters")

    return build_pp_usage_backtest_result(
        target_season=target_season,
        source_seasons=source_seasons,
        players=tuple(evaluated),
        base_eligible_players=base_eligible_players,
    )
