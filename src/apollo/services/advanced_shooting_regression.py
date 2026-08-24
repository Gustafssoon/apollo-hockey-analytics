from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.advanced_shooting_regression import (
    ShootingRegressionAggregateResult,
    ShootingRegressionBacktestResult,
    ShootingRegressionPlayer,
    build_shooting_context_ratio,
    build_shooting_regression_aggregate_result,
    build_shooting_regression_backtest_result,
)
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)
from apollo.draft.regression import position_group
from apollo.services.regression import load_position_priors


def _build_shooting_priors(
    stats_by_player: dict[int, dict[int, dict[str, float]]],
    positions: dict[int, str],
    source_seasons: tuple[int, ...],
) -> dict[tuple[int, str], float]:
    totals: dict[tuple[int, str], float] = {}
    games: dict[tuple[int, str], float] = {}
    for player_id, seasons_by_stat in stats_by_player.items():
        group = position_group(positions.get(player_id, ""))
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            games_played = stats.get("gamesPlayed", 0.0)
            shooting_pct = stats.get("shootingPct5v5")
            if games_played <= 0 or shooting_pct is None or shooting_pct <= 0:
                continue
            key = (season, group)
            totals[key] = totals.get(key, 0.0) + shooting_pct * games_played
            games[key] = games.get(key, 0.0) + games_played
    return {
        key: totals[key] / games[key]
        for key in totals
        if games.get(key, 0.0) > 0
    }


def run_shooting_regression_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> ShootingRegressionBacktestResult:
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
    regression_priors = load_position_priors(database, source_seasons)

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
    positions: dict[int, str] = {}
    for row in rows:
        player_id = int(row["id"])
        position = str(row["primary_position"] or "")
        positions[player_id] = position
        player_meta[player_id] = (
            str(row["first_name"]),
            str(row["last_name"]),
            row["nhl_team"],
            position,
            row["birth_date"],
        )
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    shooting_priors = _build_shooting_priors(stats_by_player, positions, source_seasons)
    actual_required = ("gamesPlayed", *SKATER_PROJECTION_STATS)
    baseline_eligible_players = 0
    players: list[ShootingRegressionPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat_name not in actual_stats for stat_name in actual_required):
            continue
        if actual_stats["gamesPlayed"] < min_actual_games:
            continue

        history: list[ProjectionSeason] = []
        context_history: list[tuple[float, float]] = []
        usable_history_seasons = 0
        group = position_group(positions[player_id])
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            games_played = stats.get("gamesPlayed", 0.0)
            if games_played > 0:
                usable_history_seasons += 1
            history.append(
                ProjectionSeason(
                    season=season,
                    games_played=games_played,
                    stats=stats,
                )
            )
            context_history.append(
                (
                    stats.get("shootingPct5v5", 0.0),
                    shooting_priors.get((season, group), 0.0),
                )
            )
        if usable_history_seasons < min_history_seasons:
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
            projection = build_skater_projection(
                player_id=player_id,
                player_name=player_name,
                team_abbrev=team_abbrev,
                position=position,
                target_season=target_season,
                history=tuple(history),
                birth_date=birth_date,
                regression_priors=regression_priors,
            )
        except ProjectionError:
            continue

        baseline_eligible_players += 1
        context_ratio = build_shooting_context_ratio(
            tuple(context_history),
            min_signal_seasons=min_history_seasons,
        )
        if context_ratio is None:
            continue
        players.append(
            ShootingRegressionPlayer(
                player_id=player_id,
                player_name=player_name,
                baseline_goals=projection.stats["goals"],
                baseline_assists=projection.stats["assists"],
                actual_goals=actual_stats["goals"],
                actual_assists=actual_stats["assists"],
                shooting_context_ratio=context_ratio,
            )
        )

    return build_shooting_regression_backtest_result(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        players=tuple(players),
    )


def run_shooting_regression_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> ShootingRegressionAggregateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_shooting_regression_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        for season in target_seasons
    )
    return build_shooting_regression_aggregate_result(results)
