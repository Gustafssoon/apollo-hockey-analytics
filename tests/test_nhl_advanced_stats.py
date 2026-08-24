from apollo import cli_v24
from apollo.adapters.nhl_advanced_stats import NHLAdvancedStatsAdapter
from apollo.adapters.nhl_stats import NHLSeasonStatLine
from apollo.db import Database
from apollo.models import NHLStat
from apollo.services.advanced_stats import sync_nhl_advanced_stats


def _stats_by_name(line):
    return {stat.name: stat.value for stat in line.stats}


def test_advanced_adapter_merges_summaryshooting_and_percentages():
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
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_advanced_stats(
        20252026
    )

    assert len(lines) == 1
    stats = _stats_by_name(lines[0])
    assert stats["shotAttemptsFor5v5"] == 900.0
    assert stats["shotAttemptsAgainst5v5"] == 700.0
    assert stats["shotAttemptsRelative5v5"] == 4.25
    assert stats["unblockedShotAttemptsFor5v5"] == 680.0
    assert stats["unblockedShotAttemptsPct5v5"] == 0.5484
    assert stats["timeOnIcePerGame5v5"] == 920.0
    assert stats["zoneStartPct5v5"] == 0.61
    assert stats["shootingPct5v5"] == 0.143
    assert stats["skaterSavePct5v5"] == 0.914
    assert any("/skater/summaryshooting?" in url for url in requested_urls)
    assert any("/skater/percentages?" in url for url in requested_urls)


def test_empty_percentage_report_preserves_shooting_stats():
    def fake_fetch_json(url: str):
        if "/skater/summaryshooting?" in url:
            return {"data": [{"playerId": 1, "satFor": 500, "usatFor": 400}]}
        if "/skater/percentages?" in url:
            return {"data": []}
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_advanced_stats(
        20252026
    )

    stats = _stats_by_name(lines[0])
    assert stats["shotAttemptsFor5v5"] == 500.0
    assert stats["unblockedShotAttemptsFor5v5"] == 400.0
    assert "shotAttemptsPct5v5" not in stats


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
