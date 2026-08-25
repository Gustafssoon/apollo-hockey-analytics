from collections import defaultdict
from datetime import date

from apollo.db import Database
from apollo.draft.assist_rate import build_assist_rate_context_ratio
from apollo.draft.backtest import BacktestPlayer, ProjectionBacktestResult, build_backtest_result
from apollo.draft.overall_finishing_candidate_gate import (
    OVERALL_SHOOTING_CANDIDATE_VERSION,
    OVERALL_SHOOTING_SIGNAL,
    OVERALL_SHOOTING_STRENGTH,
    ROBUSTNESS_COHORTS,
    OverallFinishingGateResult,
    build_overall_finishing_gate_cohort,
    build_overall_finishing_gate_result,
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
from apollo.draft.scoring_rate_regression import build_rate_context_ratio, correction_factor
from apollo.draft.shooting_context import build_shooting_context_ratio
from apollo.draft.shot_type_signal_backtest import signal_value
from apollo.services.assist_rate import load_assist_rate_priors
from apollo.services.regression import load_position_priors
from apollo.services.shooting_context import load_shooting_context_priors
from apollo.services.shot_type_finishing_candidate import _build_finishing_priors


def _run_gate_slice(
    database: Database,
    target_season: int,
    *,
    min_actual_games: int,
    min_history_seasons: int,
    position_group_filter: str | None,
) -> tuple[ProjectionBacktestResult, ProjectionBacktestResult, int]:
    if position_group_filter not in (None, "F", "D"):
        raise ProjectionError("position_group_filter must be F, D, or None")

    source_seasons = previous_seasons(target_season, len(DEFAULT_SEASON_WEIGHTS))
    seasons = (target_season, *source_seasons)
    placeholders = ", ".join("?" for _ in seasons)
    regression_priors = load_position_priors(database, source_seasons)
    shooting_priors = load_shooting_context_priors(database, source_seasons)
    assist_rate_priors = load_assist_rate_priors(database, source_seasons)

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

    finishing_priors = _build_finishing_priors(stats_by_player, positions, source_seasons)
    actual_required = ("gamesPlayed", *SKATER_PROJECTION_STATS)
    actual_eligible_players = 0
    history_counts = {count: 0 for count in range(len(source_seasons) + 1)}
    skipped_incomplete_history = 0
    applied = 0
    baseline_players: list[BacktestPlayer] = []
    candidate_players: list[BacktestPlayer] = []

    for player_id, seasons_by_stat in stats_by_player.items():
        first_name, last_name, team_abbrev, position, birth_date_text = player_meta[player_id]
        group = position_group(position)
        if position_group_filter is not None and group != position_group_filter:
            continue

        actual_stats = seasons_by_stat.get(target_season, {})
        if any(stat_name not in actual_stats for stat_name in actual_required):
            continue
        actual_games = actual_stats["gamesPlayed"]
        if actual_games < min_actual_games:
            continue

        actual_eligible_players += 1
        history: list[ProjectionSeason] = []
        shooting_history: list[tuple[float, float]] = []
        assist_rate_history: list[tuple[float, float]] = []
        finishing_history: list[tuple[float, float]] = []
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
            finishing = signal_value(OVERALL_SHOOTING_SIGNAL, stats)
            finishing_history.append(
                (
                    finishing if finishing is not None else -1.0,
                    finishing_priors.get((season, group, OVERALL_SHOOTING_SIGNAL), 0.0),
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
            shooting_context_ratio = build_shooting_context_ratio(tuple(shooting_history))
            assist_rate_context_ratio = build_assist_rate_context_ratio(
                tuple(assist_rate_history)
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
            )
        except (ProjectionError, ValueError):
            skipped_incomplete_history += 1
            continue

        baseline_stats = dict(projection.stats)
        candidate_stats = dict(baseline_stats)
        finishing_ratio = build_rate_context_ratio(
            tuple(finishing_history),
            min_signal_seasons=len(DEFAULT_SEASON_WEIGHTS),
        )
        if finishing_ratio is not None:
            candidate_stats["goals"] *= correction_factor(
                finishing_ratio,
                OVERALL_SHOOTING_STRENGTH,
            )
            applied += 1

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
    candidate = build_backtest_result(
        players=tuple(candidate_players),
        model_version=OVERALL_SHOOTING_CANDIDATE_VERSION,
        **common,
    )
    return baseline, candidate, applied


def run_overall_finishing_candidate_gate(
    database: Database,
    latest_target_season: int,
    *,
    years: int = 3,
    min_history_seasons: int = 3,
) -> OverallFinishingGateResult:
    if years < 1:
        raise ProjectionError("years must be >= 1")
    if min_history_seasons < 1 or min_history_seasons > len(DEFAULT_SEASON_WEIGHTS):
        raise ProjectionError(
            f"min_history_seasons must be between 1 and {len(DEFAULT_SEASON_WEIGHTS)}"
        )

    database.initialize()
    target_seasons = (
        latest_target_season,
        *previous_seasons(latest_target_season, years - 1),
    )
    cohorts = []
    for label, min_actual_games, group_filter in ROBUSTNESS_COHORTS:
        season_results = tuple(
            _run_gate_slice(
                database,
                season,
                min_actual_games=min_actual_games,
                min_history_seasons=min_history_seasons,
                position_group_filter=group_filter,
            )
            for season in target_seasons
        )
        cohorts.append(
            build_overall_finishing_gate_cohort(
                label=label,
                min_actual_games=min_actual_games,
                position_group=group_filter,
                season_results=season_results,
            )
        )

    return build_overall_finishing_gate_result(
        latest_target_season=latest_target_season,
        target_seasons=target_seasons,
        cohorts=tuple(cohorts),
    )
