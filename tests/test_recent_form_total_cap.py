from urllib.parse import parse_qs, urlparse

from apollo.adapters import NHLStatsAdapter

SEASON = 20252026


def test_game_report_total_cap_partitions_by_month():
    summary_rows = [
        {
            "playerId": 1,
            "gameId": 100,
            "gameDate": "2025-10-08",
            "teamAbbrev": "EDM",
            "opponentTeamAbbrev": "CGY",
            "points": 2,
            "shots": 4,
        },
        {
            "playerId": 2,
            "gameId": 101,
            "gameDate": "2025-10-09",
            "teamAbbrev": "COL",
            "opponentTeamAbbrev": "UTA",
            "points": 1,
            "shots": 3,
        },
    ]
    realtime_rows = [
        {"playerId": 1, "gameId": 100, "hits": 2, "blockedShots": 1},
        {"playerId": 2, "gameId": 101, "hits": 1, "blockedShots": 2},
    ]
    expressions: list[str] = []

    def fake_fetch_json(url: str):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        expression = query["cayenneExp"][0]
        expressions.append(expression)

        if "gameDate" not in expression:
            return {"total": 10000, "data": [summary_rows[0]]}

        is_october = (
            'gameDate>="2025-10-01"' in expression
            and 'gameDate<"2025-11-01"' in expression
        )
        if not is_october:
            return {"total": 0, "data": []}

        if "/skater/summary" in parsed.path:
            rows = summary_rows
        elif "/skater/realtime" in parsed.path:
            rows = realtime_rows
        else:
            raise AssertionError(url)
        assert int(query["limit"][0]) == -1
        return {"total": len(rows), "data": rows}

    adapter = NHLStatsAdapter(fetch_json=fake_fetch_json)
    rows = adapter.fetch_skater_game_stats(SEASON)

    assert len(rows) == 2
    assert any("gameDate" not in expression for expression in expressions)
    assert any('gameDate>="2025-10-01"' in expression for expression in expressions)
    first_stats = {stat.name: stat.value for stat in rows[0].stats}
    assert first_stats["points"] == 2
    assert first_stats["hits"] == 2
    assert first_stats["blockedShots"] == 1
