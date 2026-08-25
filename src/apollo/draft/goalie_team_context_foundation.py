from dataclasses import dataclass

from apollo.draft.projections import ProjectionError

GOALIE_TEAM_DOMINANCE_THRESHOLD = 0.80


@dataclass(frozen=True, slots=True)
class GoalieTeamContextSeasonAudit:
    target_season: int
    baseline_goalies: int
    source_player_seasons: int
    with_game_logs: int
    with_gp_stat: int
    gp_log_match: int
    team_identified: int
    dominant_team_80: int
    multi_team: int
    goalies_all3_team_identified: int
    goalies_all3_dominant_80: int


@dataclass(frozen=True, slots=True)
class GoalieTeamContextAggregate:
    target_seasons: tuple[int, ...]
    seasons: tuple[GoalieTeamContextSeasonAudit, ...]
    baseline_goalies: int
    source_player_seasons: int
    with_game_logs: int
    with_gp_stat: int
    gp_log_match: int
    team_identified: int
    dominant_team_80: int
    multi_team: int
    goalies_all3_team_identified: int
    goalies_all3_dominant_80: int


def build_goalie_team_context_aggregate(
    seasons: tuple[GoalieTeamContextSeasonAudit, ...],
) -> GoalieTeamContextAggregate:
    if not seasons:
        raise ProjectionError("Goalie team-context audit requires season results")
    return GoalieTeamContextAggregate(
        target_seasons=tuple(item.target_season for item in seasons),
        seasons=seasons,
        baseline_goalies=sum(item.baseline_goalies for item in seasons),
        source_player_seasons=sum(item.source_player_seasons for item in seasons),
        with_game_logs=sum(item.with_game_logs for item in seasons),
        with_gp_stat=sum(item.with_gp_stat for item in seasons),
        gp_log_match=sum(item.gp_log_match for item in seasons),
        team_identified=sum(item.team_identified for item in seasons),
        dominant_team_80=sum(item.dominant_team_80 for item in seasons),
        multi_team=sum(item.multi_team for item in seasons),
        goalies_all3_team_identified=sum(item.goalies_all3_team_identified for item in seasons),
        goalies_all3_dominant_80=sum(item.goalies_all3_dominant_80 for item in seasons),
    )
