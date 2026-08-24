ASSIST_RATE_MODEL_VERSION = "apollo-assist-rate-a60-shrink10-v0.1"
ASSIST_RATE_STRENGTH = 0.10
ASSIST_RATE_REQUIRED_SEASONS = 3
ASSIST_RATE_SEASON_WEIGHTS = (0.6, 0.3, 0.1)
MIN_CORRECTION_FACTOR = 0.80
MAX_CORRECTION_FACTOR = 1.20


def correction_factor(context_ratio: float, strength: float = ASSIST_RATE_STRENGTH) -> float:
    if context_ratio < 0:
        raise ValueError("Assist-rate context ratio must be non-negative")
    if strength < 0:
        raise ValueError("Assist-rate correction strength must be non-negative")
    factor = 1.0 - strength * (context_ratio - 1.0)
    return min(MAX_CORRECTION_FACTOR, max(MIN_CORRECTION_FACTOR, factor))


def build_assist_rate_context_ratio(
    history: tuple[tuple[float, float], ...],
    *,
    min_signal_seasons: int = ASSIST_RATE_REQUIRED_SEASONS,
    season_weights: tuple[float, ...] = ASSIST_RATE_SEASON_WEIGHTS,
) -> float | None:
    if len(history) > len(season_weights):
        raise ValueError("More assist-rate history seasons than configured season weights")
    if min_signal_seasons < 1:
        raise ValueError("min_signal_seasons must be >= 1")

    values: list[tuple[float, float]] = []
    for index, (assist_rate, prior_rate) in enumerate(history):
        if assist_rate < 0 or prior_rate <= 0:
            continue
        values.append((assist_rate / prior_rate, season_weights[index]))
    if len(values) < min_signal_seasons:
        return None
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        return None
    return sum(value * weight for value, weight in values) / weight_sum
