from collections import defaultdict
from datetime import date
from statistics import fmean

from apollo.db import Database
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    GoalieBacktestPlayer,
    build_goalie_backtest_result,
    build_goalie_projection,
)
from apollo.draft.goalie_workload_candidate import scheduled_team_games
from apollo.draft.goalie_workload_context_candidate import (
    GOALIE_WORKLOAD_CONTEXT_VARIANTS,
    GoalieWorkloadContextAggregate,
    GoalieWorkloadContextSeasonResult,
    GoalieWorkloadContextSeasonVariant,
    age_factor,
    apply_context_factor,
    build_goalie_workload_context_aggregate,
    latest_share_factor,
)
from apollo.draft.projections import ProjectionError, previous_seasons


def _target_age(birth_date: date, target_season: int) -> float:
    text = str(target_season)
    if len(text) != 8:
        raise ProjectionError(f"Invalid NHL season id: {target_season}")
    reference = date(int(text[:4]), 10, 1)
    return (reference - birth_date).days / 365.2425


def _parse_birth_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def run_goalie_workload_context_candidate_backtest(
    database: Database,
    target_season: int,
    *,
    min_actual_starts: int = 20,
) -> GoalieWorkloadContextSeasonResult:
    if min_actual_starts < 1:
        raise ProjectionError("min_actual_starts must be >= 1")

    database.initialize()
    source_seasons = previous_seasons(target_season, 3)
    latest_source = source_seasons[0]
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                p.id AS player_id,
                p.first_name,
                p.last_name,
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

    names: dict[int, str] = {}
    birth_dates: dict[int, str | None] = {}
    stats_by_player: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        player_id = int(row["player_id"])
        names[player_id] = f"{row['first_name']} {row['last_name']}"
        birth_dates[player_id] = row["birth_date"]
        stats_by_player[player_id][int(row["season"])][str(row["stat_name"])] = float(
            row["value"]
        )

    latest_shares: list[float] = []
    source_ages: list[float] = []
    for player_id, seasons_by_stat in stats_by_player.items():
        latest_stats = seasons_by_stat.get(latest_source, {})
        latest_starts = latest_stats.get("gamesStarted", 0.0)
        if latest_starts <= 0:
            continue
        latest_shares.append(latest_starts / scheduled_team_games(latest_source))
        birth_date = _parse_birth_date(birth_dates.get(player_id))
        if birth_date is not None:
            source_ages.append(_target_age(birth_date, target_season))

    if not latest_shares or not source_ages:
        raise ProjectionError("Goalie workload context candidates require source priors")
    latest_share_prior = fmean(latest_shares)
    age_prior = fmean(source_ages)

    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)
    eligible = 0
    baseline_players: list[GoalieBacktestPlayer] = []
    candidate_players = {spec.name: [] for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS}
    applied = {spec.name: 0 for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS}

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
        latest_share = history[0][1]["gamesStarted"] / scheduled_team_games(history[0][0])
        birth_date = _parse_birth_date(birth_dates.get(player_id))
        age = _target_age(birth_date, target_season) if birth_date is not None else None

        for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS:
            factor = 1.0
            has_context = True
            if spec.signal == "latest_share":
                factor = latest_share_factor(latest_share, latest_share_prior, spec.parameter)
            elif spec.signal == "age":
                if age is None:
                    has_context = False
                else:
                    factor = age_factor(age, age_prior, spec.parameter)
            else:
                raise ProjectionError(f"Unknown goalie workload context signal: {spec.signal}")
            candidate_players[spec.name].append(apply_context_factor(baseline, factor))
            if has_context:
                applied[spec.name] += 1

    baseline_result = build_goalie_backtest_result(
        target_season=target_season,
        players=tuple(baseline_players),
        actual_eligible_goalies=eligible,
    )
    variants = tuple(
        GoalieWorkloadContextSeasonVariant(
            spec=spec,
            result=build_goalie_backtest_result(
                target_season=target_season,
                players=tuple(candidate_players[spec.name]),
                actual_eligible_goalies=eligible,
            ),
            applied=applied[spec.name],
        )
        for spec in GOALIE_WORKLOAD_CONTEXT_VARIANTS
    )
    return GoalieWorkloadContextSeasonResult(
        target_season=target_season,
        baseline=baseline_result,
        variants=variants,
        latest_share_prior=latest_share_prior,
        age_prior=age_prior,
    )


def run_goalie_workload_context_candidate_aggregate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_actual_starts: int = 20,
) -> GoalieWorkloadContextAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    results = tuple(
        run_goalie_workload_context_candidate_backtest(
            database,
            target_season,
            min_actual_starts=min_actual_starts,
        )
        for target_season in target_seasons
    )
    return build_goalie_workload_context_aggregate(results)
