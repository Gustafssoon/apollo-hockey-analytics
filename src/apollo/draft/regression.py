from collections.abc import Iterable

REGRESSION_MODEL_VERSION = "apollo-regression-points-robust-v0.1"
REGRESSION_PSEUDO_GAMES_BY_STAT = {
    "goals": 5.0,
    "assists": 5.0,
    "powerPlayPoints": 0.0,
    "shots": 5.0,
    "hits": 0.0,
    "blockedShots": 10.0,
}
REGRESSION_STATS = tuple(REGRESSION_PSEUDO_GAMES_BY_STAT)


def position_group(position: str) -> str:
    return "D" if position.strip().upper().startswith("D") else "F"


def build_position_priors(
    rows: Iterable[tuple[int, str, float, dict[str, float]]],
) -> dict[tuple[int, str, str], float]:
    totals: dict[tuple[int, str, str], float] = {}
    games: dict[tuple[int, str, str], float] = {}
    for season, position, games_played, stats in rows:
        if games_played <= 0:
            continue
        group = position_group(position)
        for stat_name in REGRESSION_STATS:
            value = stats.get(stat_name)
            if value is None:
                continue
            key = (season, group, stat_name)
            totals[key] = totals.get(key, 0.0) + value
            games[key] = games.get(key, 0.0) + games_played
    return {
        key: totals[key] / games[key]
        for key in totals
        if games.get(key, 0.0) > 0
    }


def regress_rate(
    *,
    value: float,
    games_played: float,
    prior_rate: float | None,
    pseudo_games: float,
) -> tuple[float, bool]:
    if games_played <= 0:
        raise ValueError("games_played must be > 0")
    if pseudo_games < 0:
        raise ValueError("pseudo_games must be >= 0")
    raw_rate = value / games_played
    if pseudo_games == 0 or prior_rate is None:
        return raw_rate, False
    return (
        (value + prior_rate * pseudo_games) / (games_played + pseudo_games),
        True,
    )
