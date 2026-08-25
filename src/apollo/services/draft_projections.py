from datetime import date

from apollo.db import Database
from apollo.draft.assist_rate import build_assist_rate_context_ratio
from apollo.draft.overall_finishing import build_overall_finishing_context_ratio
from apollo.draft.projections import (
    ProjectionError,
    ProjectionSeason,
    SkaterProjection,
    build_skater_projection,
    previous_seasons,
)
from apollo.draft.regression import position_group
from apollo.draft.shooting_context import build_shooting_context_ratio
from apollo.services.assist_rate import load_assist_rate_priors
from apollo.services.overall_finishing import load_overall_finishing_priors
from apollo.services.regression import load_position_priors
from apollo.services.shooting_context import load_shooting_context_priors


def project_skater(
    database: Database,
    name: str,
    target_season: int,
) -> SkaterProjection:
    player_name = name.strip()
    if not player_name:
        raise ProjectionError("Player name must not be empty")

    database.initialize()
    source_seasons = previous_seasons(target_season)
    placeholders = ", ".join("?" for _ in source_seasons)
    regression_priors = load_position_priors(database, source_seasons)
    shooting_priors = load_shooting_context_priors(database, source_seasons)
    assist_rate_priors = load_assist_rate_priors(database, source_seasons)
    overall_finishing_priors = load_overall_finishing_priors(database, source_seasons)

    with database.connect() as connection:
        players = connection.execute(
            """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.primary_position,
                p.nhl_team,
                profile.birth_date
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            LEFT JOIN nhl_player_profile profile
                ON profile.player_id = p.id
            WHERE LOWER(p.first_name || ' ' || p.last_name) = LOWER(?)
            """,
            (player_name,),
        ).fetchall()

        if not players:
            raise ProjectionError(f"Player not found in Apollo NHL data: {player_name}")
        if len(players) > 1:
            raise ProjectionError(f"Player name is ambiguous in Apollo NHL data: {player_name}")

        player = players[0]
        position = str(player["primary_position"] or "")
        if position.upper() == "G":
            raise ProjectionError("Goalie projections are not implemented")

        rows = connection.execute(
            f"""
            SELECT season, stat_name, value
            FROM nhl_player_season_stat
            WHERE player_id = ?
              AND game_type = 2
              AND season IN ({placeholders})
            ORDER BY season DESC, stat_name
            """,
            (player["id"], *source_seasons),
        ).fetchall()

    full_name = f"{player['first_name']} {player['last_name']}"
    if not rows:
        raise ProjectionError(f"No historical NHL season data available for {full_name}")

    birth_date: date | None = None
    if player["birth_date"]:
        try:
            birth_date = date.fromisoformat(str(player["birth_date"]))
        except ValueError as exc:
            raise ProjectionError(f"Invalid birth date for {full_name}: {player['birth_date']}") from exc

    by_season: dict[int, dict[str, float]] = {}
    for row in rows:
        season = int(row["season"])
        by_season.setdefault(season, {})[str(row["stat_name"])] = float(row["value"])

    history: list[ProjectionSeason] = []
    group = position_group(position)
    shooting_history: list[tuple[float, float]] = []
    assist_rate_history: list[tuple[float, float]] = []
    overall_finishing_history: list[tuple[float, float]] = []
    for season in source_seasons:
        stats = by_season.get(season, {})
        history.append(
            ProjectionSeason(
                season=season,
                games_played=stats.get("gamesPlayed", 0.0),
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

    try:
        shooting_context_ratio = build_shooting_context_ratio(tuple(shooting_history))
        assist_rate_context_ratio = build_assist_rate_context_ratio(tuple(assist_rate_history))
        overall_finishing_context_ratio = build_overall_finishing_context_ratio(
            tuple(overall_finishing_history)
        )
    except ValueError as exc:
        raise ProjectionError(str(exc)) from exc

    return build_skater_projection(
        player_id=int(player["id"]),
        player_name=full_name,
        team_abbrev=player["nhl_team"],
        position=position,
        target_season=target_season,
        history=tuple(history),
        birth_date=birth_date,
        regression_priors=regression_priors,
        shooting_context_ratio=shooting_context_ratio,
        assist_rate_context_ratio=assist_rate_context_ratio,
        overall_finishing_context_ratio=overall_finishing_context_ratio,
    )
