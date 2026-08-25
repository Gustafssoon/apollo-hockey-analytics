from dataclasses import dataclass

from apollo.draft.overall_finishing_candidate_gate import OverallFinishingGateCohort


@dataclass(frozen=True, slots=True)
class OverallFinishingProductionSeasonCheck:
    target_season: int
    evaluated_players: int
    applied: int
    exact_candidate_equivalence: bool


@dataclass(frozen=True, slots=True)
class OverallFinishingProductionGateResult:
    target_seasons: tuple[int, ...]
    season_checks: tuple[OverallFinishingProductionSeasonCheck, ...]
    aggregate: OverallFinishingGateCohort

    @property
    def exact_candidate_equivalence(self) -> bool:
        return all(check.exact_candidate_equivalence for check in self.season_checks)
