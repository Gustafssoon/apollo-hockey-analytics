from typing import ClassVar

from apollo.adapters.nhl_stats import NHLSeasonStatLine, NHLStatsAdapter


class NHLAdvancedStatsAdapter(NHLStatsAdapter):
    SKATER_SUMMARY_SHOOTING_FIELDS: ClassVar[dict[str, str]] = {
        "satFor": "shotAttemptsFor5v5",
        "satAgainst": "shotAttemptsAgainst5v5",
        "satTotal": "shotAttemptsTotal5v5",
        "satRelative": "shotAttemptsRelative5v5",
        "usatFor": "unblockedShotAttemptsFor5v5",
        "usatAgainst": "unblockedShotAttemptsAgainst5v5",
        "usatTotal": "unblockedShotAttemptsTotal5v5",
        "usatRelative": "unblockedShotAttemptsRelative5v5",
        "timeOnIcePerGame5v5": "timeOnIcePerGame5v5",
    }
    SKATER_PERCENTAGE_FIELDS: ClassVar[dict[str, str]] = {
        "satPercentage": "shotAttemptsPct5v5",
        "usatPercentage": "unblockedShotAttemptsPct5v5",
        "zoneStartPct5v5": "zoneStartPct5v5",
        "shootingPct5v5": "shootingPct5v5",
        "skaterSavePct5v5": "skaterSavePct5v5",
    }
    SKATER_SCORING_RATE_FIELDS: ClassVar[dict[str, str]] = {
        "goals5v5": "goals5v5",
        "assists5v5": "assists5v5",
        "points5v5": "points5v5",
        "goalsPer605v5": "goalsPer605v5",
        "assistsPer605v5": "assistsPer605v5",
        "pointsPer605v5": "pointsPer605v5",
        "primaryAssists5v5": "primaryAssists5v5",
        "primaryAssistsPer605v5": "primaryAssistsPer605v5",
        "secondaryAssists5v5": "secondaryAssists5v5",
        "secondaryAssistsPer605v5": "secondaryAssistsPer605v5",
    }

    def fetch_skater_advanced_stats(
        self,
        season: int,
        game_type: int = 2,
    ) -> tuple[NHLSeasonStatLine, ...]:
        stats_by_player: dict[int, dict[str, float]] = {}
        self._merge_report(
            stats_by_player,
            self._fetch_report("skater", "summaryshooting", season, game_type),
            self.SKATER_SUMMARY_SHOOTING_FIELDS,
        )
        self._merge_report(
            stats_by_player,
            self._fetch_report("skater", "percentages", season, game_type),
            self.SKATER_PERCENTAGE_FIELDS,
        )
        self._merge_report(
            stats_by_player,
            self._fetch_report("skater", "scoringRates", season, game_type),
            self.SKATER_SCORING_RATE_FIELDS,
        )
        return self._to_lines(stats_by_player)
