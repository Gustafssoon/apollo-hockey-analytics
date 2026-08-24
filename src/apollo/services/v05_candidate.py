from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.advanced_shooting_regression import (
    build_shooting_context_ratio,
    correction_factor,
)
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)
from apollo.draft.regression import position_group
from apollo.draft.v05_candidate import (
    V05_CANDIDATE_MODEL_VERSION,
    V05_CANDIDATE_SHOOTING_STRENGTH,
    V05CandidateAggregateResult,
    V05CandidateSeasonResult,
    build_v05_candidate_aggregate_result,
)
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


def run_v05_candidate_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> V05CandidateSeasonResult:
    if min_actual_games < 1:
        raise ProjectionError("min_actual_games must be >= 1")
    if min_history_seasons < 1 or min_history_seasons > len(DEFAULT_SEASON_WEIGHTS):
        raise ProjectionError(
            f"min_history_seasons must be between 1 and {len(DEFAULT_SEASON_WEIGHTS)}"
        )

    database.initialize()
    source_seasons = previous_seasons(target_season, len(DEFAULT_SEASON_WEIGHTS))
    regression_priors = load_position_priors(database, source_seasons)
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
    positions: dict[int, str] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
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
    actual_eligible_players = 0
    history_counts = {count: 0 for count in range(len(source_seasons) + 1)}
    skipped_incomplete_history = 0
    shooting_context_applied = 0
    baseline_players: list[BacktestPlayer] = []
    candidate_players: list[BacktestPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat_name not in actual_stats for stat_name in actual_required):
            continue
        actual_games = actual_stats["gamesPlayed"]
        if actual_games < min_actual_games:
            continue

        actual_eligible_players += 1
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

        history_counts[usable_history_seasons] += 1
        if usable_history_seasons < min_history_seasons:
            continue

        first_name, last_name, team_abbrev, position, birth_date_text = player_meta[player_id]
        player_name = f"{first_name} {last_name}"
        birth_date: date | None = None
        if birth_date_text:
            try:
                birth_date = date.fromisoformat(str(birth_date_text))
            except ValueError:
                skipped_incomplete_history += 1
                continue

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
            skipped_incomplete_history += 1
            continue

        baseline_stats = dict(projection.stats)
        candidate_stats = dict(projection.stats)
        context_ratio = build_shooting_context_ratio(
            tuple(context_history),
            min_signal_seasons=min_history_seasons,
        )
        if context_ratio is not None:
            factor = correction_factor(context_ratio, V05_CANDIDATE_SHOOTING_STRENGTH)
            candidate_stats["goals"] *= factor
            candidate_stats["assists"] *= factor
            shooting_context_applied += 1

        baseline_players.append(
            BacktestPlayer(
                player_id=player_id,
                player_name=player_name,
                projected_games=projection.projected_games,
                actual_games=actual_games,
                projected_stats=baseline_stats,
                actual_stats=actual_stats,
            )
        )
        candidate_players.append(
            BacktestPlayer(
                player_id=player_id,
                player_name=player_name,
                projected_games=projection.projected_games,
                actual_games=actual_games,
                projected_stats=candidate_stats,
                actual_stats=actual_stats,
            )
        )

    common = dict(
        target_season=target_season,
        source_seasons=source_seasons,
        actual_eligible_players=actual_eligible_players,
        min_actual_games=min_actual_games,
        min_history_seasons=min_history_seasons,
        history_counts=tuple(sorted(history_counts.items())),
        skipped_incomplete_history=skipped_incomplete_history,
    )
    baseline = build_backtest_result(
        players=tuple(baseline_players),
        **common,
    )
    candidate = build_backtest_result(
        players=tuple(candidate_players),
        model_version=V05_CANDIDATE_MODEL_VERSION,
        **common,
    )
    return V05CandidateSeasonResult(
        target_season=target_season,
        baseline=baseline,
        candidate=candidate,
        shooting_context_applied=shooting_context_applied,
    )


def run_v05_candidate_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> V05CandidateAggregateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_v05_candidate_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        for season in target_seasons
    )
    return build_v05_candidate_aggregate_result(results)
