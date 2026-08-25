from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.goalie_baseline import GOALIE_REQUIRED_SOURCE_STATS, build_goalie_projection
from apollo.draft.goalie_workload_candidate import scheduled_team_games
from apollo.draft.goalie_workload_signal_backtest import (
    GOALIE_WORKLOAD_SIGNALS,
    GoalieWorkloadSignalAggregate,
    GoalieWorkloadSignalSeasonMetric,
    build_signal_aggregate,
    build_signal_metric,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def _target_age(birth_date: date, target_season: int) -> float:
    text = str(target_season)
    if len(text) != 8:
        raise ProjectionError(f"Invalid NHL season id: {target_season}")
    reference = date(int(text[:4]), 10, 1)
    return (reference - birth_date).days / 365.2425


def run_goalie_workload_signal_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> tuple[int, tuple[GoalieWorkloadSignalSeasonMetric, ...]]:
    if min_actual_starts < 1:
        raise ProjectionError("min_actual_starts must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, 3)
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id AS player_id,
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
              AND UPPER(COALESCE(p.primary_position, '')) = 'G'
            ORDER BY p.id, ns.season DESC, ns.stat_name
            """,
            seasons,
        ).fetchall()

    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    birth_dates: dict[int, str | None] = {}
    for row in rows:
        player_id = int(row["player_id"])
        birth_dates[player_id] = row["birth_date"]
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    signal_pairs: dict[str, list[tuple[float, float]]] = {
        signal_name: [] for signal_name in GOALIE_WORKLOAD_SIGNALS
    }
    baseline_player_seasons = 0
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        actual_starts = actual.get("gamesStarted", 0.0)
        if actual_starts < min_actual_starts:
            continue

        history: list[tuple[int, dict[str, float]]] = []
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            if stats.get("gamesStarted", 0.0) <= 0:
                break
            if any(stat_name not in stats for stat_name in source_required):
                break
            history.append((season, stats))
        if len(history) != 3:
            continue

        projection = build_goalie_projection(tuple(history))
        residual = actual_starts - projection.projected_starts
        baseline_player_seasons += 1

        shares = tuple(
            stats["gamesStarted"] / scheduled_team_games(season)
            for season, stats in history
        )
        signal_pairs["latest_start_share"].append((shares[0], residual))
        signal_pairs["start_share_trend"].append(
            (shares[0] - (shares[1] + shares[2]) / 2.0, residual)
        )

        birth_text = birth_dates.get(player_id)
        if birth_text:
            try:
                age = _target_age(date.fromisoformat(str(birth_text)), target_season)
            except ValueError:
                pass
            else:
                signal_pairs["goalie_age"].append((age, residual))

    if baseline_player_seasons <= 0:
        raise ProjectionError("Goalie workload signal screen requires evaluated goalies")

    metrics = tuple(
        build_signal_metric(signal_name, target_season, tuple(signal_pairs[signal_name]))
        for signal_name in GOALIE_WORKLOAD_SIGNALS
    )
    return baseline_player_seasons, metrics


def run_goalie_workload_signal_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieWorkloadSignalAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    season_metrics: list[GoalieWorkloadSignalSeasonMetric] = []
    baseline_player_seasons = 0
    for target_season in target_seasons:
        season_n, metrics = run_goalie_workload_signal_backtest(
            database,
            target_season,
            min_actual_starts=min_actual_starts,
        )
        baseline_player_seasons += season_n
        season_metrics.extend(metrics)
    return build_signal_aggregate(
        target_seasons,
        baseline_player_seasons,
        tuple(season_metrics),
    )
