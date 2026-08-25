from dataclasses import dataclass

from apollo.draft.pp_deployment_candidate_gate import PPDeploymentGateCohort


@dataclass(frozen=True, slots=True)
class PPDeploymentProductionSeasonCheck:
    target_season: int
    evaluated_players: int
    applied: int
    exact_candidate_equivalence: bool


@dataclass(frozen=True, slots=True)
class PPDeploymentProductionGateResult:
    target_seasons: tuple[int, ...]
    season_checks: tuple[PPDeploymentProductionSeasonCheck, ...]
    aggregate: PPDeploymentGateCohort

    @property
    def exact_candidate_equivalence(self) -> bool:
        return all(check.exact_candidate_equivalence for check in self.season_checks)
