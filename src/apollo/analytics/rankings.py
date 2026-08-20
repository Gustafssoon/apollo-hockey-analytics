from dataclasses import dataclass
from statistics import fmean, pstdev

from apollo.db import Database


@dataclass(frozen=True, slots=True)
class CategorySpec:
    label: str
    stat_name: str
    higher_is_better: bool = True
    ratio: bool = False


@dataclass(frozen=True, slots=True)
class PlayerSeasonProfile:
    player_id: int
    name: str
    team_abbrev: str | None
    position: str
    games: int
    stats: dict[str, float]


@dataclass(frozen=True, slots=True)
class RankedPlayer:
    rank: int
    name: str
    team_abbrev: str | None
    position: str
    games: int
    score: float
    values: dict[str, float]
    z_scores: dict[str, float]


@dataclass(frozen=True, slots=True)
class RankingTable:
    player_type: str
    season: int
    mode: str
    categories: tuple[CategorySpec, ...]
    players: tuple[RankedPlayer, ...]


@dataclass(frozen=True, slots=True)
class StatLeader:
    rank: int
    name: str
    team_abbrev: str | None
    position: str
    games: int
    value: float


@dataclass(frozen=True, slots=True)
class PlayerComparison:
    name: str
    team_abbrev: str | None
    position: str
    games: int
    values: dict[str, float]


CATEGORY_SPECS = {
    "G": CategorySpec("G", "goals"),
    "A": CategorySpec("A", "assists"),
    "P": CategorySpec("P", "points"),
    "PPP": CategorySpec("PPP", "powerPlayPoints"),
    "SOG": CategorySpec("SOG", "shots"),
    "HIT": CategorySpec("HIT", "hits"),
    "BLK": CategorySpec("BLK", "blockedShots"),
    "PIM": CategorySpec("PIM", "pim"),
    "+/-": CategorySpec("+/-", "plusMinus"),
    "W": CategorySpec("W", "wins"),
    "SV": CategorySpec("SV", "saves"),
    "SV%": CategorySpec("SV%", "savePctg", ratio=True),
    "GAA": CategorySpec("GAA", "goalsAgainstAvg", higher_is_better=False, ratio=True),
    "SHO": CategorySpec("SHO", "shutouts"),
}

DEFAULT_SKATER_CATEGORIES = ("G", "A", "PPP", "SOG", "HIT", "BLK")
DEFAULT_GOALIE_CATEGORIES = ("W", "SV%", "GAA", "SHO")


def _validate_player_type(player_type: str) -> None:
    if player_type not in {"skater", "goalie"}:
        raise ValueError("player_type must be 'skater' or 'goalie'")


def _validate_mode(mode: str) -> None:
    if mode not in {"total", "per-game"}:
        raise ValueError("mode must be 'total' or 'per-game'")


def resolve_categories(
    raw_categories: str | None,
    player_type: str,
) -> tuple[CategorySpec, ...]:
    _validate_player_type(player_type)
    if raw_categories:
        labels = tuple(item.strip().upper() for item in raw_categories.split(",") if item.strip())
    elif player_type == "goalie":
        labels = DEFAULT_GOALIE_CATEGORIES
    else:
        labels = DEFAULT_SKATER_CATEGORIES

    if not labels:
        raise ValueError("At least one fantasy category is required")

    categories: list[CategorySpec] = []
    seen: set[str] = set()
    for label in labels:
        category = CATEGORY_SPECS.get(label)
        if category is None:
            supported = ", ".join(CATEGORY_SPECS)
            raise ValueError(f'Unknown fantasy category "{label}". Supported: {supported}')
        if category.label in seen:
            continue
        seen.add(category.label)
        categories.append(category)
    return tuple(categories)


def _load_profiles(
    database: Database,
    season: int,
    game_type: int = 2,
) -> list[PlayerSeasonProfile]:
    database.initialize()
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT
                p.id AS player_id,
                p.first_name,
                p.last_name,
                p.nhl_team,
                p.primary_position,
                s.stat_name,
                s.value
            FROM player p
            JOIN player_external_id nhl
                ON nhl.player_id = p.id AND nhl.provider = 'nhl'
            JOIN nhl_player_season_stat s ON s.player_id = p.id
            WHERE s.season = ? AND s.game_type = ?
            ORDER BY p.id, s.stat_name
            """,
            (season, game_type),
        ).fetchall()

    grouped: dict[int, dict[str, object]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        record = grouped.setdefault(
            player_id,
            {
                "name": f"{row['first_name']} {row['last_name']}",
                "team": str(row["nhl_team"]) if row["nhl_team"] else None,
                "position": str(row["primary_position"]),
                "stats": {},
            },
        )
        stats = record["stats"]
        assert isinstance(stats, dict)
        stats[str(row["stat_name"])] = float(row["value"])

    profiles: list[PlayerSeasonProfile] = []
    for player_id, record in grouped.items():
        stats = record["stats"]
        assert isinstance(stats, dict)
        games = int(float(stats.get("gamesPlayed", 0.0)))
        profiles.append(
            PlayerSeasonProfile(
                player_id=player_id,
                name=str(record["name"]),
                team_abbrev=(str(record["team"]) if record["team"] else None),
                position=str(record["position"]),
                games=games,
                stats={str(key): float(value) for key, value in stats.items()},
            )
        )
    return profiles


def _matches_player_type(profile: PlayerSeasonProfile, player_type: str) -> bool:
    is_goalie = profile.position.upper() == "G"
    return is_goalie if player_type == "goalie" else not is_goalie


def _category_value(
    profile: PlayerSeasonProfile,
    category: CategorySpec,
    mode: str,
) -> float | None:
    value = profile.stats.get(category.stat_name)
    if value is None:
        return None
    if mode == "per-game" and not category.ratio:
        if profile.games <= 0:
            return None
        return value / profile.games
    return value


def _eligible_profiles(
    database: Database,
    season: int,
    player_type: str,
    categories: tuple[CategorySpec, ...],
    mode: str,
    min_games: int,
) -> list[tuple[PlayerSeasonProfile, dict[str, float]]]:
    eligible: list[tuple[PlayerSeasonProfile, dict[str, float]]] = []
    for profile in _load_profiles(database, season):
        if not _matches_player_type(profile, player_type) or profile.games < min_games:
            continue
        values: dict[str, float] = {}
        for category in categories:
            value = _category_value(profile, category, mode)
            if value is None:
                break
            values[category.label] = value
        else:
            eligible.append((profile, values))
    return eligible


def rank_players(
    database: Database,
    season: int,
    *,
    player_type: str = "skater",
    categories: str | None = None,
    mode: str = "per-game",
    min_games: int = 10,
    limit: int = 25,
) -> RankingTable:
    _validate_player_type(player_type)
    _validate_mode(mode)

    resolved = resolve_categories(categories, player_type)
    eligible = _eligible_profiles(
        database,
        season,
        player_type,
        resolved,
        mode,
        max(0, min_games),
    )

    distributions: dict[str, tuple[float, float]] = {}
    for category in resolved:
        values = [row_values[category.label] for _, row_values in eligible]
        if not values:
            distributions[category.label] = (0.0, 0.0)
            continue
        distributions[category.label] = (
            fmean(values),
            pstdev(values) if len(values) > 1 else 0.0,
        )

    scored: list[tuple[PlayerSeasonProfile, dict[str, float], dict[str, float], float]] = []
    for profile, values in eligible:
        z_scores: dict[str, float] = {}
        for category in resolved:
            mean, deviation = distributions[category.label]
            z_score = 0.0 if deviation == 0 else (values[category.label] - mean) / deviation
            if not category.higher_is_better:
                z_score *= -1
            z_scores[category.label] = z_score
        scored.append((profile, values, z_scores, sum(z_scores.values())))

    scored.sort(key=lambda item: (-item[3], item[0].name.casefold()))
    ranked = tuple(
        RankedPlayer(
            rank=index,
            name=profile.name,
            team_abbrev=profile.team_abbrev,
            position=profile.position,
            games=profile.games,
            score=score,
            values=values,
            z_scores=z_scores,
        )
        for index, (profile, values, z_scores, score) in enumerate(
            scored[: max(1, limit)],
            start=1,
        )
    )
    return RankingTable(
        player_type=player_type,
        season=season,
        mode=mode,
        categories=resolved,
        players=ranked,
    )


def leaderboard(
    database: Database,
    season: int,
    stat: str,
    *,
    player_type: str = "skater",
    mode: str = "total",
    min_games: int = 1,
    limit: int = 20,
) -> tuple[CategorySpec, tuple[StatLeader, ...]]:
    _validate_player_type(player_type)
    _validate_mode(mode)
    category = resolve_categories(stat, player_type)[0]
    eligible = _eligible_profiles(
        database,
        season,
        player_type,
        (category,),
        mode,
        max(0, min_games),
    )
    eligible.sort(
        key=lambda item: (
            -item[1][category.label] if category.higher_is_better else item[1][category.label],
            item[0].name.casefold(),
        )
    )
    leaders = tuple(
        StatLeader(
            rank=index,
            name=profile.name,
            team_abbrev=profile.team_abbrev,
            position=profile.position,
            games=profile.games,
            value=values[category.label],
        )
        for index, (profile, values) in enumerate(eligible[: max(1, limit)], start=1)
    )
    return category, leaders


def compare_players(
    database: Database,
    season: int,
    names: tuple[str, ...],
    *,
    player_type: str = "skater",
    categories: str | None = None,
    mode: str = "per-game",
) -> tuple[tuple[CategorySpec, ...], tuple[PlayerComparison, ...]]:
    _validate_player_type(player_type)
    _validate_mode(mode)
    resolved = resolve_categories(categories, player_type)
    wanted = {name.strip().casefold() for name in names}
    order = {name.strip().casefold(): index for index, name in enumerate(names)}
    comparisons: list[PlayerComparison] = []
    for profile in _load_profiles(database, season):
        if profile.name.casefold() not in wanted or not _matches_player_type(profile, player_type):
            continue
        values: dict[str, float] = {}
        for category in resolved:
            value = _category_value(profile, category, mode)
            if value is not None:
                values[category.label] = value
        comparisons.append(
            PlayerComparison(
                name=profile.name,
                team_abbrev=profile.team_abbrev,
                position=profile.position,
                games=profile.games,
                values=values,
            )
        )
    comparisons.sort(key=lambda item: order.get(item.name.casefold(), len(order)))
    return resolved, tuple(comparisons)
