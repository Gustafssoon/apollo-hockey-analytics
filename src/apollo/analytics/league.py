from dataclasses import dataclass
from datetime import date

from apollo.analytics.rankings import CATEGORY_SPECS, RankedPlayer, rank_players
from apollo.analytics.waivers import WaiverBoard, WaiverTarget, build_waiver_board
from apollo.db import Database

GOALIE_CATEGORIES = frozenset({"W", "SV", "SV%", "GAA", "SHO"})


@dataclass(frozen=True, slots=True)
class LeagueCategory:
    label: str
    display_name: str
    supported: bool
    player_type: str | None


@dataclass(frozen=True, slots=True)
class LeagueContext:
    league_id: int
    source: str
    external_id: str
    name: str
    team_count: int
    user_team_id: int
    user_team_name: str
    categories: tuple[LeagueCategory, ...]


@dataclass(frozen=True, slots=True)
class CategoryNeed:
    label: str
    display_name: str
    player_type: str
    team_score: float
    rank: int
    team_count: int
    weight: float
    level: str


@dataclass(frozen=True, slots=True)
class CategoryNeeds:
    league: LeagueContext
    season: int
    mode: str
    min_games: int
    needs: tuple[CategoryNeed, ...]


@dataclass(frozen=True, slots=True)
class LeagueRankedPlayer:
    rank: int
    name: str
    team_abbrev: str | None
    position: str
    games: int
    score: float
    raw_score: float
    values: dict[str, float]
    z_scores: dict[str, float]


@dataclass(frozen=True, slots=True)
class LeagueRanking:
    league: LeagueContext
    needs: CategoryNeeds
    player_type: str
    season: int
    mode: str
    categories: tuple[str, ...]
    weights: dict[str, float]
    players: tuple[LeagueRankedPlayer, ...]


@dataclass(frozen=True, slots=True)
class LeagueWaiverBoard:
    league: LeagueContext
    needs: CategoryNeeds
    weights: dict[str, float]
    board: WaiverBoard


def _identity_key(name: str, team_abbrev: str | None) -> tuple[str, str]:
    return name.strip().casefold(), (team_abbrev or "").upper()


def _category_player_type(label: str) -> str:
    return "goalie" if label in GOALIE_CATEGORIES else "skater"


def load_league_context(
    database: Database,
    league_external_id: str | None = None,
) -> LeagueContext:
    database.initialize()
    with database.connect() as connection:
        if league_external_id:
            league_rows = connection.execute(
                """
                SELECT id, source, external_id, name
                FROM league
                WHERE external_id = ?
                ORDER BY id
                """,
                (league_external_id,),
            ).fetchall()
        else:
            league_rows = connection.execute(
                """
                SELECT id, source, external_id, name
                FROM league
                ORDER BY id
                """
            ).fetchall()

        if not league_rows:
            raise ValueError("No stored fantasy league matches the request")
        if len(league_rows) > 1:
            options = ", ".join(str(row["external_id"]) for row in league_rows)
            raise ValueError(f"Multiple leagues are stored; choose --league-id from: {options}")

        league = league_rows[0]
        league_id = int(league["id"])
        teams = connection.execute(
            """
            SELECT id, name, is_user_team
            FROM fantasy_team
            WHERE league_id = ?
            ORDER BY id
            """,
            (league_id,),
        ).fetchall()
        user_teams = [row for row in teams if int(row["is_user_team"]) == 1]
        if len(user_teams) != 1:
            raise ValueError(
                "League must contain exactly one user team before league-specific analysis"
            )

        category_rows = connection.execute(
            """
            SELECT abbr, display_name
            FROM league_stat_category
            WHERE league_id = ?
            ORDER BY id
            """,
            (league_id,),
        ).fetchall()

    categories = tuple(
        LeagueCategory(
            label=str(row["abbr"]).upper(),
            display_name=str(row["display_name"]),
            supported=str(row["abbr"]).upper() in CATEGORY_SPECS,
            player_type=(
                _category_player_type(str(row["abbr"]).upper())
                if str(row["abbr"]).upper() in CATEGORY_SPECS
                else None
            ),
        )
        for row in category_rows
    )
    user_team = user_teams[0]
    return LeagueContext(
        league_id=league_id,
        source=str(league["source"]),
        external_id=str(league["external_id"]),
        name=str(league["name"]),
        team_count=len(teams),
        user_team_id=int(user_team["id"]),
        user_team_name=str(user_team["name"]),
        categories=categories,
    )


def _roster_ownership(
    database: Database,
    league_id: int,
) -> tuple[dict[tuple[str, str], int], dict[int, str]]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT
                ft.id AS team_id,
                ft.name AS team_name,
                p.first_name || ' ' || p.last_name AS player_name,
                p.nhl_team
            FROM fantasy_team ft
            LEFT JOIN roster r ON r.fantasy_team_id = ft.id
            LEFT JOIN player p ON p.id = r.player_id
            WHERE ft.league_id = ?
            ORDER BY ft.id, p.id
            """,
            (league_id,),
        ).fetchall()

    ownership: dict[tuple[str, str], int] = {}
    team_names: dict[int, str] = {}
    for row in rows:
        team_id = int(row["team_id"])
        team_names[team_id] = str(row["team_name"])
        if row["player_name"] is None:
            continue
        ownership[
            _identity_key(
                str(row["player_name"]),
                str(row["nhl_team"]) if row["nhl_team"] else None,
            )
        ] = team_id
    return ownership, team_names


def _need_level(rank: int, team_count: int) -> str:
    if team_count <= 1:
        return "NEUTRAL"
    percentile = (rank - 1) / (team_count - 1)
    if percentile >= 2 / 3:
        return "HIGH"
    if percentile >= 1 / 3:
        return "MEDIUM"
    return "LOW"


def _need_weight(rank: int, team_count: int) -> float:
    if team_count <= 1:
        return 1.0
    weakness = (rank - 1) / (team_count - 1)
    return 1.0 + weakness


def _supported_labels(
    league: LeagueContext,
    player_type: str,
) -> tuple[str, ...]:
    return tuple(
        category.label
        for category in league.categories
        if category.supported and category.player_type == player_type
    )


def calculate_category_needs(
    database: Database,
    season: int,
    *,
    league_external_id: str | None = None,
    mode: str = "per-game",
    min_games: int = 10,
) -> CategoryNeeds:
    league = load_league_context(database, league_external_id)
    ownership, team_names = _roster_ownership(database, league.league_id)
    team_ids = tuple(sorted(team_names))
    category_lookup = {category.label: category for category in league.categories}
    scores: dict[str, dict[int, float]] = {}

    for player_type in ("skater", "goalie"):
        labels = _supported_labels(league, player_type)
        if not labels:
            continue
        ranking = rank_players(
            database,
            season,
            player_type=player_type,
            categories=",".join(labels),
            mode=mode,
            min_games=min_games,
            limit=10000,
        )
        for label in labels:
            scores[label] = {team_id: 0.0 for team_id in team_ids}
        for player in ranking.players:
            team_id = ownership.get(_identity_key(player.name, player.team_abbrev))
            if team_id is None:
                continue
            for label in labels:
                scores[label][team_id] += player.z_scores[label]

    needs: list[CategoryNeed] = []
    for category in league.categories:
        if not category.supported or category.player_type is None:
            continue
        team_scores = scores.get(category.label, {team_id: 0.0 for team_id in team_ids})
        ordered = sorted(
            team_ids,
            key=lambda team_id: (-team_scores.get(team_id, 0.0), team_names[team_id].casefold()),
        )
        rank = ordered.index(league.user_team_id) + 1
        team_count = len(ordered)
        needs.append(
            CategoryNeed(
                label=category.label,
                display_name=category_lookup[category.label].display_name,
                player_type=category.player_type,
                team_score=team_scores.get(league.user_team_id, 0.0),
                rank=rank,
                team_count=team_count,
                weight=_need_weight(rank, team_count),
                level=_need_level(rank, team_count),
            )
        )

    return CategoryNeeds(
        league=league,
        season=season,
        mode=mode,
        min_games=max(0, min_games),
        needs=tuple(needs),
    )


def _weights_for_type(needs: CategoryNeeds, player_type: str) -> dict[str, float]:
    return {
        need.label: need.weight
        for need in needs.needs
        if need.player_type == player_type
    }


def _weighted_score(player: RankedPlayer, weights: dict[str, float]) -> float:
    return sum(player.z_scores[label] * weight for label, weight in weights.items())


def build_league_ranking(
    database: Database,
    season: int,
    *,
    league_external_id: str | None = None,
    player_type: str = "skater",
    mode: str = "per-game",
    min_games: int = 10,
    limit: int = 25,
) -> LeagueRanking:
    needs = calculate_category_needs(
        database,
        season,
        league_external_id=league_external_id,
        mode=mode,
        min_games=min_games,
    )
    weights = _weights_for_type(needs, player_type)
    if not weights:
        raise ValueError(f"League has no supported {player_type} categories")

    ranking = rank_players(
        database,
        season,
        player_type=player_type,
        categories=",".join(weights),
        mode=mode,
        min_games=min_games,
        limit=10000,
    )
    scored = [
        LeagueRankedPlayer(
            rank=0,
            name=player.name,
            team_abbrev=player.team_abbrev,
            position=player.position,
            games=player.games,
            score=_weighted_score(player, weights),
            raw_score=player.score,
            values=player.values,
            z_scores=player.z_scores,
        )
        for player in ranking.players
    ]
    scored.sort(key=lambda player: (-player.score, player.name.casefold()))
    players = tuple(
        LeagueRankedPlayer(
            rank=index,
            name=player.name,
            team_abbrev=player.team_abbrev,
            position=player.position,
            games=player.games,
            score=player.score,
            raw_score=player.raw_score,
            values=player.values,
            z_scores=player.z_scores,
        )
        for index, player in enumerate(scored[: max(1, limit)], start=1)
    )
    return LeagueRanking(
        league=needs.league,
        needs=needs,
        player_type=player_type,
        season=season,
        mode=mode,
        categories=tuple(weights),
        weights=weights,
        players=players,
    )


def build_league_waiver_board(
    database: Database,
    season: int,
    *,
    league_external_id: str | None = None,
    schedule_season: int | None = None,
    as_of: date | None = None,
    days: int = 7,
    player_type: str = "skater",
    mode: str = "per-game",
    min_games: int = 10,
    position: str | None = None,
    schedule_weight: float = 1.0,
    trend_weight: float = 0.5,
    off_night_threshold: int = 8,
    off_night_bonus: float = 0.5,
    limit: int = 25,
) -> LeagueWaiverBoard:
    needs = calculate_category_needs(
        database,
        season,
        league_external_id=league_external_id,
        mode=mode,
        min_games=min_games,
    )
    weights = _weights_for_type(needs, player_type)
    if not weights:
        raise ValueError(f"League has no supported {player_type} categories")

    categories = ",".join(weights)
    ranking = rank_players(
        database,
        season,
        player_type=player_type,
        categories=categories,
        mode=mode,
        min_games=min_games,
        limit=10000,
    )
    weighted_scores = {
        _identity_key(player.name, player.team_abbrev): _weighted_score(player, weights)
        for player in ranking.players
    }
    board = build_waiver_board(
        database,
        season,
        schedule_season=schedule_season,
        as_of=as_of,
        days=days,
        player_type=player_type,
        categories=categories,
        mode=mode,
        min_games=min_games,
        position=position,
        include_rostered=False,
        schedule_weight=schedule_weight,
        trend_weight=trend_weight,
        off_night_threshold=off_night_threshold,
        off_night_bonus=off_night_bonus,
        limit=10000,
    )

    adjusted: list[WaiverTarget] = []
    for player in board.players:
        weighted = weighted_scores.get(
            _identity_key(player.name, player.team_abbrev),
            player.category_score,
        )
        total = player.score - player.category_score + weighted
        adjusted.append(
            WaiverTarget(
                rank=0,
                name=player.name,
                team_abbrev=player.team_abbrev,
                position=player.position,
                games=player.games,
                rostered=player.rostered,
                category_score=weighted,
                schedule_games=player.schedule_games,
                off_night_games=player.off_night_games,
                schedule_z=player.schedule_z,
                schedule_component=player.schedule_component,
                trend=player.trend,
                trend_percent=player.trend_percent,
                trend_component=player.trend_component,
                score=total,
                category_values=player.category_values,
            )
        )
    adjusted.sort(key=lambda player: (-player.score, player.name.casefold()))
    players = tuple(
        WaiverTarget(
            rank=index,
            name=player.name,
            team_abbrev=player.team_abbrev,
            position=player.position,
            games=player.games,
            rostered=player.rostered,
            category_score=player.category_score,
            schedule_games=player.schedule_games,
            off_night_games=player.off_night_games,
            schedule_z=player.schedule_z,
            schedule_component=player.schedule_component,
            trend=player.trend,
            trend_percent=player.trend_percent,
            trend_component=player.trend_component,
            score=player.score,
            category_values=player.category_values,
        )
        for index, player in enumerate(adjusted[: max(1, limit)], start=1)
    )
    adjusted_board = WaiverBoard(
        season=board.season,
        schedule_season=board.schedule_season,
        player_type=board.player_type,
        mode=board.mode,
        min_games=board.min_games,
        categories=board.categories,
        start_date=board.start_date,
        end_date=board.end_date,
        days=board.days,
        include_rostered=board.include_rostered,
        eligible_players=board.eligible_players,
        schedule_complete=board.schedule_complete,
        schedule_team_count=board.schedule_team_count,
        expected_team_count=board.expected_team_count,
        off_night_threshold=board.off_night_threshold,
        schedule_weight=board.schedule_weight,
        trend_weight=board.trend_weight,
        off_night_bonus=board.off_night_bonus,
        players=players,
    )
    return LeagueWaiverBoard(
        league=needs.league,
        needs=needs,
        weights=weights,
        board=adjusted_board,
    )
