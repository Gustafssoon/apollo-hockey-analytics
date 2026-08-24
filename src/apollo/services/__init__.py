from apollo.services.age_backtest import run_age_baseline_backtest
from apollo.services.age_model_backtest import run_age_model_aggregate, run_age_model_backtest
from apollo.services.age_stat_aggregate import run_age_stat_aggregate
from apollo.services.age_stat_backtest import run_age_stat_backtest
from apollo.services.draft_backtest import run_skater_backtest
from apollo.services.draft_projections import project_skater
from apollo.services.gp_backtest import run_gp_baseline_backtest
from apollo.services.nhl_catalog import (
    NHLGameLogSyncResult,
    NHLLeagueScheduleSyncResult,
    NHLPlayerPoolSyncResult,
    NHLScheduleSyncResult,
    sync_nhl_game_log,
    sync_nhl_player_pool,
    sync_nhl_schedule,
    sync_nhl_schedules,
)
from apollo.services.nhl_sync import NHLSyncResult, sync_nhl_players
from apollo.services.recent_form import NHLRecentFormSyncResult, sync_nhl_recent_form
from apollo.services.season_stats import NHLCategoryStatsSyncResult, sync_nhl_category_stats
from apollo.services.sync import SyncResult, sync_league

__all__ = [
    "NHLCategoryStatsSyncResult",
    "NHLGameLogSyncResult",
    "NHLLeagueScheduleSyncResult",
    "NHLPlayerPoolSyncResult",
    "NHLRecentFormSyncResult",
    "NHLScheduleSyncResult",
    "NHLSyncResult",
    "SyncResult",
    "project_skater",
    "run_age_baseline_backtest",
    "run_age_model_aggregate",
    "run_age_model_backtest",
    "run_age_stat_aggregate",
    "run_age_stat_backtest",
    "run_gp_baseline_backtest",
    "run_skater_backtest",
    "sync_league",
    "sync_nhl_category_stats",
    "sync_nhl_game_log",
    "sync_nhl_player_pool",
    "sync_nhl_players",
    "sync_nhl_recent_form",
    "sync_nhl_schedule",
    "sync_nhl_schedules",
]
