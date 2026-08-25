PP_DEPLOYMENT_MODEL_VERSION = "apollo-pp-deployment-shrink5-v0.1"
PP_DEPLOYMENT_STRENGTH = 0.05
PP_DEPLOYMENT_MIN_SEASONS = 3
PP_DEPLOYMENT_SEASON_WEIGHTS = (0.6, 0.3, 0.1)
MIN_CORRECTION_FACTOR = 0.80
MAX_CORRECTION_FACTOR = 1.20


def correction_factor(context_ratio: float) -> float:
    if context_ratio < 0:
        raise ValueError("PP deployment context ratio must be non-negative")
    factor = 1.0 - PP_DEPLOYMENT_STRENGTH * (context_ratio - 1.0)
    return min(MAX_CORRECTION_FACTOR, max(MIN_CORRECTION_FACTOR, factor))


def build_pp_deployment_context_ratio(
    history: tuple[tuple[float, float], ...],
    *,
    min_signal_seasons: int = PP_DEPLOYMENT_MIN_SEASONS,
    season_weights: tuple[float, ...] = PP_DEPLOYMENT_SEASON_WEIGHTS,
) -> float | None:
    if len(history) > len(season_weights):
        raise ValueError("More PP deployment history seasons than configured weights")
    if min_signal_seasons < 1 or min_signal_seasons > len(season_weights):
        raise ValueError(
            f"min_signal_seasons must be between 1 and {len(season_weights)}"
        )

    values: list[tuple[float, float]] = []
    for index, (pp_toi_per_game, prior_pp_toi_per_game) in enumerate(history):
        if pp_toi_per_game < 0 or prior_pp_toi_per_game <= 0:
            continue
        values.append((pp_toi_per_game / prior_pp_toi_per_game, season_weights[index]))
    if len(values) < min_signal_seasons:
        return None
    weight_sum = sum(weight for _, weight in values)
    if weight_sum <= 0:
        return None
    return sum(value * weight for value, weight in values) / weight_sum
