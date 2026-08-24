from dataclasses import dataclass
from datetime import date

AGE_MODEL_VERSION = "apollo-age-medium-v0.1"
AGE_REFERENCE_MONTH = 10
AGE_REFERENCE_DAY = 1


@dataclass(frozen=True, slots=True)
class AgeCurve:
    name: str
    forward_peak_age: float
    defense_peak_age: float
    pre_peak_slope: float
    post_peak_slope: float


PRODUCTION_AGE_CURVE = AgeCurve(
    name="medium",
    forward_peak_age=27.5,
    defense_peak_age=28.5,
    pre_peak_slope=0.015,
    post_peak_slope=0.020,
)


def season_reference_date(season: int) -> date:
    text = str(season)
    if len(text) != 8:
        raise ValueError(f"Invalid NHL season id: {season}")
    start_year = int(text[:4])
    end_year = int(text[4:])
    if end_year != start_year + 1:
        raise ValueError(f"Invalid NHL season id: {season}")
    return date(start_year, AGE_REFERENCE_MONTH, AGE_REFERENCE_DAY)


def age_on_season_reference(birth_date: date, season: int) -> float:
    return (season_reference_date(season) - birth_date).days / 365.2425


def curve_level(age: float, *, position: str, curve: AgeCurve) -> float:
    peak_age = curve.defense_peak_age if position.upper() == "D" else curve.forward_peak_age
    if age <= peak_age:
        value = 1.0 - curve.pre_peak_slope * (peak_age - age)
    else:
        value = 1.0 - curve.post_peak_slope * (age - peak_age)
    return max(0.50, value)


def adjust_rate_between_ages(
    *,
    observed_rate: float,
    source_age: float,
    target_age: float,
    position: str,
    curve: AgeCurve,
) -> float:
    source_level = curve_level(source_age, position=position, curve=curve)
    target_level = curve_level(target_age, position=position, curve=curve)
    return observed_rate * target_level / source_level


def adjust_rate_for_seasons(
    *,
    observed_rate: float,
    birth_date: date,
    source_season: int,
    target_season: int,
    position: str,
    curve: AgeCurve = PRODUCTION_AGE_CURVE,
) -> float:
    return adjust_rate_between_ages(
        observed_rate=observed_rate,
        source_age=age_on_season_reference(birth_date, source_season),
        target_age=age_on_season_reference(birth_date, target_season),
        position=position,
        curve=curve,
    )
