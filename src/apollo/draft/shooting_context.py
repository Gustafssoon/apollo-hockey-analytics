SHOOTING_CONTEXT_MODEL_VERSION = "apollo-shooting-context-sh-offense10-v0.1"
SHOOTING_CONTEXT_STRENGTH = 0.10
SHOOTING_CONTEXT_REQUIRED_SEASONS = 3
SHOOTING_CONTEXT_SEASON_WEIGHTS = (0.6, 0.3, 0.1)
MIN_CORRECTION_FACTOR = 0.80
MAX_CORRECTION_FACTOR = 1.20


def correction_factor(context_ratio: float, strength: float = SHOOTING_CONTEXT_STRENGTH) -> float:
    if context_ratio <= 0:
        raise ValueError("Shooting context ratio must be positive")
    if strength < 0:
        raise ValueError("Shooting correction strength must be non-negative")
    factor = 1.0 - strength * (context_ratio - 1.0)
    return min(MAX_CORRECTION_FACTOR, max(MIN_CORRECTION_FACTOR, factor))


def build_shooting_context_ratio(
    history: tuple[tuple[float, float], ...],
    *,
    min_signal_seasons: int = SHOOTING_CONTEXT_REQUIRED_SEASONS,
    season_weights: tuple[float, ...] = SHOOTING_CONTEXT_SEASON_WEIGHTS,
) -> float | None:
    if len(history) > len(season_weights):
        raise ValueError("More shooting history seasons than configured season weights")
    if min_signal_seasons < 1:
        raise ValueError("min_signal_seasons must be >= 1")

    values: list[tuple[float, float]] = []
    for index, (shooting_pct, prior_pct) in enumerate(history):
        if shooting_pct <= 0 or prior_pct <= 0:
            continue
        values.append((shooting_pct / prior_pct, season_weights[index]))
    if len(values) < min_signal_seasons:
        return None
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        return None
    return sum(value * weight for value, weight in values) / weight_sum
