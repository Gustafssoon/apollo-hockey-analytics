from apollo import cli_v24
from apollo.adapters.nhl_advanced_stats import NHLAdvancedStatsAdapter
from apollo.adapters.nhl_stats import NHLSeasonStatLine
from apollo.db import Database
from apollo.models import NHLStat
from apollo.services.advanced_stats import sync_nhl_advanced_stats


def _stats_by_name(line):
    return {stat.name: stat.value for stat in line.stats}


def test_advanced_adapter_merges_shooting_rates_and_shot_types():
    requested_urls: list[str] = []

    def fake_fetch_json(url: str):
        requested_urls.append(url)
        if "/skater/summaryshooting?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "satFor": 900,
                        "satAgainst": 700,
                        "satTotal": 1600,
                        "satRelative": 4.25,
                        "usatFor": 680,
                        "usatAgainst": 560,
                        "usatTotal": 1240,
                        "usatRelative": 3.1,
                        "timeOnIcePerGame5v5": 920,
                    }
                ]
            }
        if "/skater/percentages?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "satPercentage": 0.5625,
                        "usatPercentage": 0.5484,
                        "zoneStartPct5v5": 0.61,
                        "shootingPct5v5": 0.143,
                        "skaterSavePct5v5": 0.914,
                    }
                ]
            }
        if "/skater/scoringRates?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "goals5v5": 24,
                        "assists5v5": 46,
                        "points5v5": 70,
                        "goalsPer605v5": 1.31,
                        "assistsPer605v5": 2.51,
                        "pointsPer605v5": 3.82,
                        "primaryAssists5v5": 31,
                        "primaryAssistsPer605v5": 1.69,
                        "secondaryAssists5v5": 15,
                        "secondaryAssistsPer605v5": 0.82,
                    }
                ]
            }
        if "/skater/shottype?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "goals": 44,
                        "shots": 286,
                        "shootingPct": 0.1538,
                        "shotsOnNetBackhand": 18,
                        "goalsBackhand": 3,
                        "shootingPctBackhand": 0.1667,
                        "shotsOnNetSlap": 14,
                        "goalsSlap": 2,
                        "shootingPctSlap": 0.1429,
                        "shotsOnNetSnap": 72,
                        "goalsSnap": 13,
                        "shootingPctSnap": 0.1806,
                        "shotsOnNetTipIn": 8,
                        "goalsTipIn": 3,
                        "shootingPctTipIn": 0.375,
                        "shotsOnNetDeflected": 5,
                        "goalsDeflected": 2,
                        "shootingPctDeflected": 0.4,
                        "shotsOnNetWrist": 169,
                        "goalsWrist": 21,
                        "shootingPctWrist": 0.1243,
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_advanced_stats(
        20252026
    )

    assert len(lines) == 1
    stats = _stats_by_name(lines[0])
    assert stats["shotAttemptsFor5v5"] == 900.0
    assert stats["unblockedShotAttemptsPct5v5"] == 0.5484
    assert stats["shootingPct5v5"] == 0.143
    assert stats["goalsPer605v5"] == 1.31
    assert stats["primaryAssistsPer605v5"] == 1.69
    assert stats["secondaryAssistsPer605v5"] == 0.82
    assert stats["shotTypeGoals"] == 44.0
    assert stats["shotTypeShots"] == 286.0
    assert stats["shotsOnNetSnap"] == 72.0
    assert stats["goalsTipIn"] == 3.0
    assert stats["shootingPctDeflected"] == 0.4
    assert stats["shotsOnNetWrist"] == 169.0
    assert "goals" not in stats
    assert "shots" not in stats
    assert any("/skater/summaryshooting?" in url for url in requested_urls)
    assert any("/skater/percentages?" in url for url in requested_urls)
    assert any("/skater/scoringRates?" in url for url in requested_urls)
    assert any("/skater/shottype?" in url for url in requested_urls)


def test_empty_percentage_report_preserves_other_advanced_stats():
    def fake_fetch_json(url: str):
        if "/skater/summaryshooting?" in url:
            return {"data": [{"playerId": 1, "satFor": 500, "usatFor": 400}]}
        if "/skater/percentages?" in url:
            return {"data": []}
        if "/skater/scoringRates?" in url:
            return {"data": [{"playerId": 1, "goalsPer605v5": 1.2}]}
        if "/skater/shottype?" in url:
            return {"data": [{"playerId": 1, "shotsOnNetWrist": 100}]}
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_advanced_stats(
        20252026
    )

    stats = _stats_by_name(lines[0])
    assert stats["shotAttemptsFor5v5"] == 500.0
    assert stats["unblockedShotAttemptsFor5v5"] == 400.0
    assert stats["goalsPer605v5"] == 1.2
    assert stats["shotsOnNetWrist"] == 100.0
    assert "shotAttemptsPct5v5" not in stats


def test_advanced_reports_can_add_players_missing_from_other_reports():
    def fake_fetch_json(url: str):
        if "/skater/summaryshooting?" in url or "/skater/percentages?" in url:
            return {"data": []}
        if "/skater/scoringRates?" in url:
            return {"data": [{"playerId": 55, "goalsPer605v5": 0.75}]}
        if "/skater/shottype?" in url:
            return {"data": [{"playerId": 66, "shotsOnNetTipIn": 12, "goalsTipIn": 4}]}
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_advanced_stats(
        20252026
    )

    assert [line.nhl_player_id for line in lines] == [55, 66]
    assert _stats_by_name(lines[0]) == {"goalsPer605v5": 0.75}
    assert _stats_by_name(lines[1]) == {"shotsOnNetTipIn": 12.0, "goalsTipIn": 4.0}


def test_advanced_stats_sync_upserts_only_matched_nhl_players(tmp_path):
    database = Database(tmp_path / "apollo.db")
    database.initialize()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO player (first_name, last_name, primary_position, nhl_team)
            VALUES ('Matched', 'Skater', 'C', 'EDM')
            """
        )
        player_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO player_external_id (player_id, provider, external_id)
            VALUES (?, 'nhl', '1001')
            """,
            (player_id,),
        )

    class StubAdapter:
        def fetch_skater_advanced_stats(self, season, game_type=2):
            assert season == 20252026
            assert game_type == 2
            return (
                NHLSeasonStatLine(
                    nhl_player_id=1001,
                    stats=(
                        NHLStat(name="shotAttemptsPct5v5", value=0.55),
                        NHLStat(name="zoneStartPct5v5", value=0.60),
                    ),
                ),
                NHLSeasonStatLine(
                    nhl_player_id=9999,
                    stats=(NHLStat(name="shotAttemptsPct5v5", value=0.50),),
                ),
            )

    result = sync_nhl_advanced_stats(database, StubAdapter(), 20252026)

    assert result.skaters == 2
    assert result.matched == 1
    assert result.unmatched == 1
    assert result.stats_written == 2
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT stat_name, value
            FROM nhl_player_season_stat
            WHERE player_id = ? AND season = 20252026 AND game_type = 2
            ORDER BY stat_name
            """,
            (player_id,),
        ).fetchall()
    assert [(row["stat_name"], row["value"]) for row in rows] == [
        ("shotAttemptsPct5v5", 0.55),
        ("zoneStartPct5v5", 0.60),
    ]


def test_advanced_cli_parser_contract():
    args = cli_v24.build_parser().parse_args(
        ["nhl", "advanced", "--season", "20252026", "--game-type", "2"]
    )

    assert args.command == "nhl"
    assert args.nhl_command == "advanced"
    assert args.season == 20252026
    assert args.game_type == 2
