from collections import defaultdict
from datetime import date
from statistics import fmean

from apollo.db import Database
from apollo.draft.goalie_age_candidate_gate import (
    GOALIE_AGE_GATE_COHORTS,
    GOALIE_AGE_GATE_SLOPE,
    GoalieAgeGateAggregate,
    GoalieAgeGateCohortSpec,
    GoalieAgeGateSeasonResult,
    build_goalie_age_gate_aggregate,
)
from apollo.draft.goalie_baseline import (
    GOALIE_BACKTEST_STATS,
    GOALIE_REQUIRED_SOURCE_STATS,
    GoalieBacktestPlayer,
    build_goalie_backtest_result,
    build_goalie_projection,
)
from apollo.draft.goalie_workload_context_candidate import age_factor, apply_context_factor
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


def _in_cohort(
    cohort: GoalieAgeGateCohortSpec,
    actual_starts: float,
    age: float | None,
) -> bool:
    if actual_starts < cohort.min_actual_starts:
        return False
    if cohort.min_age is not None and (age is None or age < cohort.min_age):
        return False
    return cohort.max_age is None or (age is not None and age < cohort.max_age)


def run_goalie_age_candidate_gate_season(
    database: Database,
    target_season: int,
) -> tuple[GoalieAgeGateSeasonResult, ...]:
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

    source_ages: list[float] = []
    for player_id, seasons_by_stat in stats_by_player.items():
        if seasons_by_stat.get(latest_source, {}).get("gamesStarted", 0.0) <= 0:
            continue
        birth_date = _parse_birth_date(birth_dates.get(player_id))
        if birth_date is not None:
            source_ages.append(_target_age(birth_date, target_season))
    if not source_ages:
        raise ProjectionError("Goalie age gate requires a source-active age prior")
    age_prior = fmean(source_ages)

    actual_required = set(GOALIE_BACKTEST_STATS)
    source_required = set(GOALIE_REQUIRED_SOURCE_STATS)
    eligible = {cohort.name: 0 for cohort in GOALIE_AGE_GATE_COHORTS}
    baselines: dict[str, list[GoalieBacktestPlayer]] = {
        cohort.name: [] for cohort in GOALIE_AGE_GATE_COHORTS
    }
    candidates: dict[str, list[GoalieBacktestPlayer]] = {
        cohort.name: [] for cohort in GOALIE_AGE_GATE_COHORTS
    }
    applied = {cohort.name: 0 for cohort in GOALIE_AGE_GATE_COHORTS}

    for player_id, seasons_by_stat in stats_by_player.items():
        actual = seasons_by_stat.get(target_season, {})
        actual_starts = actual.get("gamesStarted", 0.0)
        if any(stat_name not in actual for stat_name in actual_required):
            continue
        birth_date = _parse_birth_date(birth_dates.get(player_id))
        age = _target_age(birth_date, target_season) if birth_date is not None else None
        memberships = tuple(
            cohort
            for cohort in GOALIE_AGE_GATE_COHORTS
            if _in_cohort(cohort, actual_starts, age)
        )
        if not memberships:
            continue
        for cohort in memberships:
            eligible[cohort.name] += 1

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
        factor = 1.0 if age is None else age_factor(age, age_prior, GOALIE_AGE_GATE_SLOPE)
        candidate = apply_context_factor(baseline, factor)
        for cohort in memberships:
            baselines[cohort.name].append(baseline)
            candidates[cohort.name].append(candidate)
            if age is not None:
                applied[cohort.name] += 1

    return tuple(
        GoalieAgeGateSeasonResult(
            cohort=cohort,
            target_season=target_season,
            baseline=build_goalie_backtest_result(
                target_season=target_season,
                players=tuple(baselines[cohort.name]),
                actual_eligible_goalies=eligible[cohort.name],
            ),
            candidate=build_goalie_backtest_result(
                target_season=target_season,
                players=tuple(candidates[cohort.name]),
                actual_eligible_goalies=eligible[cohort.name],
            ),
            applied=applied[cohort.name],
        )
        for cohort in GOALIE_AGE_GATE_COHORTS
    )


def run_goalie_age_candidate_gate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
) -> GoalieAgeGateAggregate:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    season_results = tuple(
        result
        for target_season in target_seasons
        for result in run_goalie_age_candidate_gate_season(database, target_season)
    )
    return build_goalie_age_gate_aggregate(season_results)
