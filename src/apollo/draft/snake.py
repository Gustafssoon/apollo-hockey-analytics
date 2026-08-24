from dataclasses import dataclass

from apollo.draft.config import DraftLeagueConfig


@dataclass(frozen=True, slots=True)
class DraftPick:
    round_number: int
    overall_pick: int


def snake_overall_pick(teams: int, slot: int, round_number: int) -> int:
    if teams < 2:
        raise ValueError("teams must be >= 2")
    if slot < 1 or slot > teams:
        raise ValueError(f"slot must be between 1 and {teams}")
    if round_number < 1:
        raise ValueError("round_number must be >= 1")

    if round_number % 2 == 1:
        return (round_number - 1) * teams + slot
    return round_number * teams - slot + 1


def draft_picks(config: DraftLeagueConfig) -> tuple[DraftPick, ...]:
    settings = config.draft
    if settings.draft_type != "snake":
        raise ValueError(f"Unsupported draft type: {settings.draft_type}")

    return tuple(
        DraftPick(
            round_number=round_number,
            overall_pick=snake_overall_pick(
                config.league.teams,
                settings.my_slot,
                round_number,
            ),
        )
        for round_number in range(1, settings.rounds + 1)
    )
