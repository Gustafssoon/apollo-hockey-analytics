from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.assist_rate import build_assist_rate_context_ratio
from apollo.draft.deployment_signal_backtest import (
    DEPLOYMENT_SIGNAL_NAMES,
    DeploymentSignalAggregateResult,
    DeploymentSignalBacktestResult,
    DeploymentSignalPlayer,
    build_deployment_signal_aggregate_result,
    build_deployment_signal_backtest_result,
    build_weighted_deployment_signals,
    deployment_signal_value,
)
from apollo.draft.overall_finishing import build_overall_finishing_context_ratio
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)
from apollo.draft.regression import position_group
from apollo.draft.shooting_context import build_shooting_context_ratio
from apollo.services.assist_rate import load_assist_rate_priors
from apollo.services.overall_finishing import load_overall_finishing_priors
from apollo.services.regression import load_position_priors
from apollo.services.shooting_context import load_shooting_context_priors


def _build_deployment_priors(
    stats_by_player: dict[int, dict[int, dict[str, float]]],
    positions: dict[int, str],
    source_seasons: tuple[int, ...],
) -> dict[tuple[int, str, str], float]:
    totals: dict[tuple[int, str, str], float] = {}
    exposures: dict[tuple[int, str, str], float] = {}
    for player_id, seasons_by_stat in stats_by_player.items():
        group = position_group(positions.get(player_id, ""))
        for season in source_seasons:
            stats = seasons_by_stat.get(season, {})
            games_played = stats.get("gamesPlayed", 0.0)
            if games_played <= 0:
                continue
            for signal_name in DEPLOYMENT_SIGNAL_NAMES:
                value = deployment_signal_value(signal_name, stats)
                if value is None:
                    continue
                key = (season, group, signal_name)
                totals[key] = totals.get(key, 0.0) + value * games_played
                exposures[key] = exposures.get(key, 0.0) + games_played
    return {
        key: totals[key] / exposures[key]
        for key in totals
        if exposures.get(key, 0.0) > 0
    }


def run_deployment_signal_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> DeploymentSignalBacktestResult:
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
    shooting_priors = load_shooting_context_priors(database, source_seasons)
    assist_rate_priors = load_assist_rate_priors(database, source_seasons)
    overall_finishing_priors = load_overall_finishing_priors(database, source_seasons)

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

    deployment_priors = _build_deployment_priors(
        stats_by_player,
        positions,
        source_seasons,
    )
    actual_required = ("gamesPlayed", *SKATER_PROJECTION_STATS)
    baseline_eligible_players = 0
    players: list[DeploymentSignalPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat_name not in actual_stats for stat_name in actual_required):
            continue
        if actual_stats["gamesPlayed"] < min_actual_games:
            continue

        first_name, last_name, team_abbrev, position, birth_date_text = player_meta[player_id]
        group = position_group(position)
        history: list[ProjectionSeason] = []
        shooting_history: list[tuple[float, float]] = []
        assist_rate_history: list[tuple[float, float]] = []
        overall_finishing_history: list[tuple[float, float]] = []
        deployment_history: list[dict[str, float]] = []
        deployment_prior_history: list[dict[str, float]] = []
        usable_history_seasons = 0

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
            shooting_history.append(
                (
                    stats.get("shootingPct5v5", 0.0),
                    shooting_priors.get((season, group), 0.0),
                )
            )
            assist_rate_history.append(
                (
                    stats.get("assistsPer605v5", -1.0),
                    assist_rate_priors.get((season, group), 0.0),
                )
            )
            overall_finishing_history.append(
                (
                    stats.get("shotTypeShootingPct", -1.0),
                    overall_finishing_priors.get((season, group), 0.0),
                )
            )
            deployment_history.append(stats)
            deployment_prior_history.append(
                {
                    signal_name: deployment_priors[(season, group, signal_name)]
                    for signal_name in DEPLOYMENT_SIGNAL_NAMES
                    if (season, group, signal_name) in deployment_priors
                }
            )

        if usable_history_seasons < min_history_seasons:
            continue

        birth_date: date | None = None
        if birth_date_text:
            try:
                birth_date = date.fromisoformat(str(birth_date_text))
            except ValueError:
                continue

        player_name = f"{first_name} {last_name}"
        try:
            shooting_context_ratio = build_shooting_context_ratio(tuple(shooting_history))
            assist_rate_context_ratio = build_assist_rate_context_ratio(tuple(assist_rate_history))
            overall_finishing_context_ratio = build_overall_finishing_context_ratio(
                tuple(overall_finishing_history)
            )
            projection = build_skater_projection(
                player_id=player_id,
                player_name=player_name,
                team_abbrev=team_abbrev,
                position=position,
                target_season=target_season,
                history=tuple(history),
                birth_date=birth_date,
                regression_priors=regression_priors,
                shooting_context_ratio=shooting_context_ratio,
                assist_rate_context_ratio=assist_rate_context_ratio,
                overall_finishing_context_ratio=overall_finishing_context_ratio,
            )
        except (ProjectionError, ValueError):
            continue

        baseline_eligible_players += 1
        weighted_signals = build_weighted_deployment_signals(
            tuple(deployment_history),
            tuple(deployment_prior_history),
            min_signal_seasons=min_history_seasons,
        )
        players.append(
            DeploymentSignalPlayer(
                player_id=player_id,
                player_name=player_name,
                projected_stats=dict(projection.stats),
                actual_stats=actual_stats,
                weighted_signals=weighted_signals,
            )
        )

    return build_deployment_signal_backtest_result(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        players=tuple(players),
    )


def run_deployment_signal_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> DeploymentSignalAggregateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_deployment_signal_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        for season in target_seasons
    )
    return build_deployment_signal_aggregate_result(results)
