from collections import defaultdict

from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    GOALIE_SEASON_WEIGHTS,
    GoalieBacktestPlayer,
    build_goalie_backtest_result,
    build_goalie_projection,
)
from apollo.draft.goalie_rate_candidate import (
    GOALIE_RATE_VARIANTS,
    GoalieRateAggregate,
    GoalieRateSeasonResult,
    GoalieRateSeasonVariant,
    apply_rate_regression,
    build_goalie_rate_aggregate,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def _weighted(values: tuple[float, float, float]) -> float:
    return sum(
        value * weight
        for value, weight in zip(values, GOALIE_SEASON_WEIGHTS, strict=True)
    )


def _build_source_priors(
    stats_by_player: dict[int, dict[int, dict[str, float]]],
    source_seasons: tuple[int, ...],
) -> tuple[float, float]:
    save_pct_priors: list[float] = []
    gaa_priors: list[float] = []
    for season in source_seasons:
        saves = 0.0
        shots = 0.0
        gaa_weighted = 0.0
        gaa_exposure = 0.0
        for seasons_by_stat in stats_by_player.values():
            stats = seasons_by_stat.get(season, {})
            if stats.get("gamesStarted", 0.0) <= 0:
                continue
            season_saves = stats.get("saves")
            season_ga = stats.get("goalsAgainst")
            if season_saves is not None and season_ga is not None:
                season_shots = season_saves + season_ga
                if season_shots > 0:
                    saves += season_saves
                    shots += season_shots
            gaa = stats.get("goalsAgainstAvg")
            toi = stats.get("timeOnIce")
            if gaa is not None and toi is not None and toi > 0:
                gaa_weighted += gaa * toi
                gaa_exposure += toi
        if shots <= 0 or gaa_exposure <= 0:
            raise ProjectionError("Goalie rate candidates require source SV% and GAA priors")
        save_pct_priors.append(saves / shots)
        gaa_priors.append(gaa_weighted / gaa_exposure)

    if len(save_pct_priors) != 3 or len(gaa_priors) != 3:
        raise ProjectionError("Goalie rate candidates require exactly three source priors")
    return (
        _weighted(tuple(save_pct_priors)),
        _weighted(tuple(gaa_priors)),
    )


def run_goalie_rate_candidate_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> GoalieRateSeasonResult:
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
                p.first_name,
                p.last_name,
                ns.season,
                ns.stat_name,
                ns.value
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat ns
                ON ns.player_id = p.id
            WHERE ns.game_type = 2
              AND ns.season IN ({placeholders})
              AND UPPER(COALESCE(p.primary_position, '')) = 'G'
            ORDER BY p.id, ns.season DESC, ns.stat_name
            """,
            seasons,
        ).fetchall()

    names: dict[int, str] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        player_id = int(row["player_id"])
        names[player_id] = f"{row['first_name']} {row['last_name']}"
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    save_pct_prior, gaa_prior = _build_source_priors(stats_by_player, source_seasons)
    priors = {"savePctg": save_pct_prior, "goalsAgainstAvg": gaa_prior}

    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)
    eligible = 0
    baseline_players: list[GoalieBacktestPlayer] = []
    candidate_players = {spec.name: [] for spec in GOALIE_RATE_VARIANTS}

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        actual_starts = actual.get("gamesStarted", 0.0)
        if actual_starts < min_actual_starts:
            continue
        if any(stat_name not in actual for stat_name in actual_required):
            continue
        eligible += 1

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
        baseline = GoalieBacktestPlayer(
            player_id=player_id,
            player_name=names[player_id],
            projected_starts=projection.projected_starts,
            actual_starts=actual_starts,
            projected_stats=projection.stats,
            actual_stats=actual,
        )
        baseline_players.append(baseline)
        for spec in GOALIE_RATE_VARIANTS:
            candidate_players[spec.name].append(
                apply_rate_regression(baseline, spec, priors[spec.stat_name])
            )

    baseline_result = build_goalie_backtest_result(
        target_season=target_season,
        players=tuple(baseline_players),
        actual_eligible_goalies=eligible,
    )
    variants = tuple(
        GoalieRateSeasonVariant(
            spec=spec,
            result=build_goalie_backtest_result(
                target_season=target_season,
                players=tuple(candidate_players[spec.name]),
                actual_eligible_goalies=eligible,
            ),
        )
        for spec in GOALIE_RATE_VARIANTS
    )
    return GoalieRateSeasonResult(
        target_season=target_season,
        baseline=baseline_result,
        variants=variants,
        save_pct_prior=save_pct_prior,
        gaa_prior=gaa_prior,
    )


def run_goalie_rate_candidate_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieRateAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_goalie_rate_candidate_backtest(
            database,
            target_season,
            min_actual_starts=min_actual_starts,
        )
        for target_season in target_seasons
    )
    return build_goalie_rate_aggregate(results)
