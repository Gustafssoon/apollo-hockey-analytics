from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.assist_rate import build_assist_rate_context_ratio
from apollo.draft.overall_finishing import build_overall_finishing_context_ratio
from apollo.draft.peripheral_rate_signal_backtest import (
    PERIPHERAL_RATE_SIGNALS,
    PERIPHERAL_RATE_TARGETS,
    PeripheralRateSignalAggregateResult,
    PeripheralRateSignalBacktestResult,
    PeripheralRateSignalPlayer,
    build_peripheral_rate_signal_aggregate_result,
    build_peripheral_rate_signal_backtest_result,
    build_weighted_peripheral_rate_signals,
)
from apollo.draft.pp_deployment import build_pp_deployment_context_ratio
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
from apollo.services.pp_deployment import load_pp_deployment_priors
from apollo.services.regression import load_position_priors
from apollo.services.shooting_context import load_shooting_context_priors


def _peripheral_prior_history(
    regression_priors: dict[tuple[int, str, str], float],
    source_seasons: tuple[int, ...],
    group: str,
) -> tuple[dict[str, float], ...]:
    history: list[dict[str, float]] = []
    for season in source_seasons:
        season_priors: dict[str, float] = {}
        for signal_name in PERIPHERAL_RATE_SIGNALS:
            stat_name = PERIPHERAL_RATE_TARGETS[signal_name]
            prior = regression_priors.get((season, group, stat_name))
            if prior is not None:
                season_priors[signal_name] = prior
        history.append(season_priors)
    return tuple(history)


def run_peripheral_rate_signal_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> PeripheralRateSignalBacktestResult:
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
    pp_deployment_priors = load_pp_deployment_priors(database, source_seasons)

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
    baseline_eligible_players = 0
    players: list[PeripheralRateSignalPlayer] = []

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
        pp_deployment_history: list[tuple[float, float]] = []
        peripheral_history: list[dict[str, float]] = []
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
            pp_deployment_history.append(
                (
                    stats.get("powerPlayTimeOnIcePerGame", -1.0),
                    pp_deployment_priors.get((season, group), 0.0),
                )
            )
            peripheral_history.append(stats)

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
            pp_deployment_context_ratio = build_pp_deployment_context_ratio(
                tuple(pp_deployment_history)
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
                pp_deployment_context_ratio=pp_deployment_context_ratio,
            )
        except (ProjectionError, ValueError):
            continue

        baseline_eligible_players += 1
        weighted_signals = build_weighted_peripheral_rate_signals(
            tuple(peripheral_history),
            _peripheral_prior_history(regression_priors, source_seasons, group),
            min_signal_seasons=len(DEFAULT_SEASON_WEIGHTS),
        )
        players.append(
            PeripheralRateSignalPlayer(
                player_id=player_id,
                player_name=player_name,
                projected_stats=dict(projection.stats),
                actual_stats=actual_stats,
                weighted_signals=weighted_signals,
            )
        )

    return build_peripheral_rate_signal_backtest_result(
        target_season=target_season,
        source_seasons=source_seasons,
        baseline_eligible_players=baseline_eligible_players,
        players=tuple(players),
    )


def run_peripheral_rate_signal_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> PeripheralRateSignalAggregateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_peripheral_rate_signal_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        for season in target_seasons
    )
    return build_peripheral_rate_signal_aggregate_result(results)
