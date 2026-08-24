AVAILABILITY_MODEL_VERSION = "apollo-availability-shrink50-v0.1"
STANDARD_NHL_GAMES = 82
SHRINK_TO_FULL_SEASON = 0.50
_SHORTENED_SEASON_GAME_LIMITS = {
    20202021: 56,
}


class AvailabilityError(ValueError):
    pass


def regular_season_game_limit(season: int) -> int:
    if season in _SHORTENED_SEASON_GAME_LIMITS:
        return _SHORTENED_SEASON_GAME_LIMITS[season]
    if season >= 20212022:
        return STANDARD_NHL_GAMES
    raise AvailabilityError(
        f"Availability model does not support NHL season {season}; "
        "add its regular-season game limit explicitly"
    )


def normalize_games_to_82(season: int, games_played: float) -> float:
    if games_played < 0:
        raise AvailabilityError("games_played must be >= 0")
    season_limit = regular_season_game_limit(season)
    availability = min(1.0, games_played / season_limit)
    return availability * STANDARD_NHL_GAMES


def project_available_games(
    history_games: tuple[tuple[int, float], ...],
    season_weights: tuple[float, ...],
) -> float:
    if not history_games:
        raise AvailabilityError("Availability projection requires historical seasons")
    if len(history_games) > len(season_weights):
        raise AvailabilityError("More history seasons supplied than configured season weights")

    weighted_values: list[tuple[float, float]] = []
    for index, (season, games_played) in enumerate(history_games):
        if games_played <= 0:
            continue
        weighted_values.append(
            (
                normalize_games_to_82(season, games_played),
                season_weights[index],
            )
        )

    weight_sum = sum(weight for _, weight in weighted_values)
    if weight_sum <= 0:
        raise AvailabilityError("Availability projection requires at least one season with games")

    weighted_games = (
        sum(games * weight for games, weight in weighted_values) / weight_sum
    )
    projected_games = (
        (1.0 - SHRINK_TO_FULL_SEASON) * weighted_games
        + SHRINK_TO_FULL_SEASON * STANDARD_NHL_GAMES
    )
    return min(float(STANDARD_NHL_GAMES), max(0.0, projected_games))
