OVERALL_FINISHING_MODEL_VERSION = "apollo-overall-shooting-shrink5-v0.1"
OVERALL_FINISHING_STRENGTH = 0.05
OVERALL_FINISHING_REQUIRED_SEASONS = 3
OVERALL_FINISHING_SEASON_WEIGHTS = (0.6, 0.3, 0.1)
MIN_CORRECTION_FACTOR = 0.80
MAX_CORRECTION_FACTOR = 1.20


def correction_factor(
    context_ratio: float,
    strength: float = OVERALL_FINISHING_STRENGTH,
) -> float:
    if context_ratio < 0:
        raise ValueError("Overall finishing context ratio must be non-negative")
    if strength < 0:
        raise ValueError("Overall finishing correction strength must be non-negative")
    factor = 1.0 - strength * (context_ratio - 1.0)
    return min(MAX_CORRECTION_FACTOR, max(MIN_CORRECTION_FACTOR, factor))


def build_overall_finishing_context_ratio(
    history: tuple[tuple[float, float], ...],
    *,
    min_signal_seasons: int = OVERALL_FINISHING_REQUIRED_SEASONS,
    season_weights: tuple[float, ...] = OVERALL_FINISHING_SEASON_WEIGHTS,
) -> float | None:
    if len(history) > len(season_weights):
        raise ValueError("More overall finishing history seasons than configured season weights")
    if min_signal_seasons < 1:
        raise ValueError("min_signal_seasons must be >= 1")

    values: list[tuple[float, float]] = []
    for index, (shooting_pct, prior_pct) in enumerate(history):
        if shooting_pct < 0 or prior_pct <= 0:
            continue
        values.append((shooting_pct / prior_pct, season_weights[index]))
    if len(values) < min_signal_seasons:
        return None
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        return None
    return sum(value * weight for value, weight in values) / weight_sum
