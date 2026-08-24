from apollo.adapters.nhl_stats import NHLStatsAdapter


def _stats_by_name(line):
    return {stat.name: stat.value for stat in line.stats}


def test_skater_stats_merge_strength_toi_report():
    requested_urls: list[str] = []

    def fake_fetch_json(url: str):
        requested_urls.append(url)
        if "/skater/summary?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "gamesPlayed": 82,
                        "goals": 40,
                        "assists": 80,
                        "points": 120,
                        "ppPoints": 40,
                        "shots": 300,
                        "plusMinus": 20,
                        "penaltyMinutes": 30,
                        "timeOnIcePerGame": 1300,
                    }
                ]
            }
        if "/skater/realtime?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "hits": 50,
                        "blockedShots": 30,
                        "takeaways": 60,
                        "giveaways": 70,
                    }
                ]
            }
        if "/skater/timeonice?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "evTimeOnIce": 85000,
                        "evTimeOnIcePerGame": 1037,
                        "ppTimeOnIce": 18000,
                        "ppTimeOnIcePerGame": 220,
                        "shTimeOnIce": 3500,
                        "shTimeOnIcePerGame": 43,
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_stats(20252026)

    assert len(lines) == 1
    stats = _stats_by_name(lines[0])
    assert stats["evenStrengthTimeOnIce"] == 85000.0
    assert stats["evenStrengthTimeOnIcePerGame"] == 1037.0
    assert stats["powerPlayTimeOnIce"] == 18000.0
    assert stats["powerPlayTimeOnIcePerGame"] == 220.0
    assert stats["shortHandedTimeOnIce"] == 3500.0
    assert stats["shortHandedTimeOnIcePerGame"] == 43.0
    assert any("/skater/timeonice?" in url for url in requested_urls)


def test_empty_strength_toi_report_preserves_existing_skater_stats():
    def fake_fetch_json(url: str):
        if "/skater/summary?" in url:
            return {
                "data": [
                    {
                        "playerId": 8478402,
                        "gamesPlayed": 82,
                        "goals": 40,
                        "assists": 80,
                        "points": 120,
                        "ppPoints": 40,
                        "shots": 300,
                        "plusMinus": 20,
                        "penaltyMinutes": 30,
                        "timeOnIcePerGame": 1300,
                    }
                ]
            }
        if "/skater/realtime?" in url:
            return {"data": []}
        if "/skater/timeonice?" in url:
            return {"data": []}
        raise AssertionError(f"Unexpected URL: {url}")

    lines = NHLStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_stats(20252026)

    assert len(lines) == 1
    stats = _stats_by_name(lines[0])
    assert stats["points"] == 120.0
    assert stats["timeOnIcePerGame"] == 1300.0
    assert stats["hits"] == 0.0
    assert "powerPlayTimeOnIcePerGame" not in stats
