from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.aging import adjust_rate_for_seasons
from apollo.draft.assist_rate import build_assist_rate_context_ratio
from apollo.draft.backtest import BacktestPlayer, build_backtest_result
from apollo.draft.hit_regression_candidate import (
    HIT_REGRESSION_PSEUDO_GAMES,
    HitRegressionAggregateResult,
    HitRegressionSeasonResult,
    HitRegressionVariantSeasonResult,
    build_hit_regression_aggregate_result,
    candidate_model_version,
)
from apollo.draft.overall_finishing import build_overall_finishing_context_ratio
from apollo.draft.pp_deployment import build_pp_deployment_context_ratio
from apollo.draft.projections import (
    DEFAULT_SEASON_WEIGHTS,
    SKATER_PROJECTION_STATS,
    ProjectionError,
    ProjectionSeason,
    build_skater_projection,
    previous_seasons,
)
from apollo.draft.regression import position_group, regress_rate
from apollo.draft.shooting_context import build_shooting_context_ratio
from apollo.services.assist_rate import load_assist_rate_priors
from apollo.services.overall_finishing import load_overall_finishing_priors
from apollo.services.pp_deployment import load_pp_deployment_priors
from apollo.services.regression import load_position_priors
from apollo.services.shooting_context import load_shooting_context_priors


def _candidate_hits(
    history: tuple[ProjectionSeason, ...],
    *,
    birth_date: date | None,
    position: str,
    target_season: int,
    regression_priors: dict[tuple[int, str, str], float],
    projected_games: float,
    pseudo_games: float,
    season_weights: tuple[float, ...] = DEFAULT_SEASON_WEIGHTS,
) -> tuple[float, bool]:
    group = position_group(position)
    values: list[tuple[float, float]] = []
    applied = False
    for index, season in enumerate(history):
        if index >= len(season_weights) or season.games_played <= 0:
            continue
        value = season.stats.get("hits")
        if value is None:
            continue
        rate, season_applied = regress_rate(
            value=value,
            games_played=season.games_played,
            prior_rate=regression_priors.get((season.season, group, "hits")),
            pseudo_games=pseudo_games,
        )
        applied = applied or season_applied
        if birth_date is not None:
            rate = adjust_rate_for_seasons(
                observed_rate=rate,
                birth_date=birth_date,
                source_season=season.season,
                target_season=target_season,
                position=position,
            )
        values.append((rate, season_weights[index]))
    if not values:
        raise ProjectionError("HIT regression candidate requires historical HIT data")
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        raise ProjectionError("HIT regression candidate requires positive season weights")
    rate = sum(value * weight for value, weight in values) / weight_sum
    return rate * projected_games, applied


def run_hit_regression_candidate_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> HitRegressionSeasonResult:
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
    finishing_priors = load_overall_finishing_priors(database, source_seasons)
    pp_priors = load_pp_deployment_priors(database, source_seasons)

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
    actual_eligible_players = 0
    history_counts = {count: 0 for count in range(len(source_seasons) + 1)}
    skipped_incomplete_history = 0
    baseline_players: list[BacktestPlayer] = []
    candidate_players = {value: [] for value in HIT_REGRESSION_PSEUDO_GAMES}
    applied = {value: 0 for value in HIT_REGRESSION_PSEUDO_GAMES}

    for player_id, seasons_by_stat in stats_by_player.items():
        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat_name not in actual_stats for stat_name in actual_required):
            continue
        actual_games = actual_stats["gamesPlayed"]
        if actual_games < min_actual_games:
            continue

        actual_eligible_players += 1
        first_name, last_name, team_abbrev, position, birth_date_text = player_meta[player_id]
        group = position_group(position)
        history: list[ProjectionSeason] = []
        shooting_history: list[tuple[float, float]] = []
        assist_history: list[tuple[float, float]] = []
        finishing_history: list[tuple[float, float]] = []
        pp_history: list[tuple[float, float]] = []
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
            assist_history.append(
                (
                    stats.get("assistsPer605v5", -1.0),
                    assist_rate_priors.get((season, group), 0.0),
                )
            )
            finishing_history.append(
                (
                    stats.get("shotTypeShootingPct", -1.0),
                    finishing_priors.get((season, group), 0.0),
                )
            )
            pp_history.append(
                (
                    stats.get("powerPlayTimeOnIcePerGame", -1.0),
                    pp_priors.get((season, group), 0.0),
                )
            )

        history_counts[usable_history_seasons] += 1
        if usable_history_seasons < min_history_seasons:
            continue

        birth_date: date | None = None
        if birth_date_text:
            try:
                birth_date = date.fromisoformat(str(birth_date_text))
            except ValueError:
                skipped_incomplete_history += 1
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
                shooting_context_ratio=build_shooting_context_ratio(
                    tuple(shooting_history)
                ),
                assist_rate_context_ratio=build_assist_rate_context_ratio(
                    tuple(assist_history)
                ),
                overall_finishing_context_ratio=build_overall_finishing_context_ratio(
                    tuple(finishing_history)
                ),
                pp_deployment_context_ratio=build_pp_deployment_context_ratio(
                    tuple(pp_history)
                ),
            )
        except (ProjectionError, ValueError):
            skipped_incomplete_history += 1
            continue

        baseline_stats = dict(projection.stats)
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
        for pseudo_games in HIT_REGRESSION_PSEUDO_GAMES:
            candidate_stats = dict(baseline_stats)
            try:
                hits, was_applied = _candidate_hits(
                    tuple(history),
                    birth_date=birth_date,
                    position=position,
                    target_season=target_season,
                    regression_priors=regression_priors,
                    projected_games=projection.projected_games,
                    pseudo_games=pseudo_games,
                )
            except (ProjectionError, ValueError):
                hits = baseline_stats["hits"]
                was_applied = False
            candidate_stats["hits"] = hits
            if was_applied:
                applied[pseudo_games] += 1
            candidate_players[pseudo_games].append(
                BacktestPlayer(
                    player_id=player_id,
                    player_name=player_name,
                    projected_games=projection.projected_games,
                    actual_games=actual_games,
                    projected_stats=candidate_stats,
                    actual_stats=actual_stats,
                )
            )

    common = {
        "target_season": target_season,
        "source_seasons": source_seasons,
        "actual_eligible_players": actual_eligible_players,
        "min_actual_games": min_actual_games,
        "min_history_seasons": min_history_seasons,
        "history_counts": tuple(sorted(history_counts.items())),
        "skipped_incomplete_history": skipped_incomplete_history,
    }
    baseline = build_backtest_result(players=tuple(baseline_players), **common)
    variants = tuple(
        HitRegressionVariantSeasonResult(
            pseudo_games=pseudo_games,
            model_version=candidate_model_version(pseudo_games),
            result=build_backtest_result(
                players=tuple(candidate_players[pseudo_games]),
                model_version=candidate_model_version(pseudo_games),
                **common,
            ),
            applied=applied[pseudo_games],
        )
        for pseudo_games in HIT_REGRESSION_PSEUDO_GAMES
    )
    return HitRegressionSeasonResult(
        target_season=target_season,
        baseline=baseline,
        variants=variants,
    )


def run_hit_regression_candidate_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_games: int = 20,
    min_history_seasons: int = 3,
) -> HitRegressionAggregateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_hit_regression_candidate_backtest(
            database,
            season,
            min_actual_games=min_actual_games,
            min_history_seasons=min_history_seasons,
        )
        for season in target_seasons
    )
    return build_hit_regression_aggregate_result(results)
