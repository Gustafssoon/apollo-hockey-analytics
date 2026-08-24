import pytest

from apollo.adapters.nhl_advanced_stats import NHLAdvancedStatsAdapter


def _stats_by_name(line):
    return {stat.name: stat.value for stat in line.stats}


def test_advanced_adapter_retries_transient_report_timeout():
    scoring_rate_attempts = 0

    def fake_fetch_json(url: str):
        nonlocal scoring_rate_attempts
        if "/skater/scoringRates?" in url:
            scoring_rate_attempts += 1
            if scoring_rate_attempts == 1:
                raise TimeoutError("temporary NHL timeout")
            return {"data": [{"playerId": 8478402, "goalsPer605v5": 1.31}]}
        return {"data": []}

    lines = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json).fetch_skater_advanced_stats(
        20252026
    )

    assert scoring_rate_attempts == 2
    assert len(lines) == 1
    assert _stats_by_name(lines[0])["goalsPer605v5"] == pytest.approx(1.31)


def test_advanced_adapter_stops_after_three_timeouts():
    scoring_rate_attempts = 0

    def fake_fetch_json(url: str):
        nonlocal scoring_rate_attempts
        if "/skater/scoringRates?" in url:
            scoring_rate_attempts += 1
            raise TimeoutError("persistent NHL timeout")
        return {"data": []}

    adapter = NHLAdvancedStatsAdapter(fetch_json=fake_fetch_json)

    with pytest.raises(
        TimeoutError,
        match=r"timed out after 3 attempts: skater/scoringRates",
    ):
        adapter.fetch_skater_advanced_stats(20252026)

    assert scoring_rate_attempts == 3
