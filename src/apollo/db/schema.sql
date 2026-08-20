PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS league (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS fantasy_team (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    is_user_team INTEGER NOT NULL DEFAULT 0 CHECK (is_user_team IN (0, 1)),
    FOREIGN KEY (league_id) REFERENCES league(id) ON DELETE CASCADE,
    UNIQUE (league_id, external_id)
);

CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    primary_position TEXT NOT NULL,
    nhl_team TEXT
);

CREATE TABLE IF NOT EXISTS player_external_id (
    player_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    FOREIGN KEY (player_id) REFERENCES player(id) ON DELETE CASCADE,
    PRIMARY KEY (provider, external_id),
    UNIQUE (player_id, provider)
);

CREATE TABLE IF NOT EXISTS league_stat_category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    abbr TEXT NOT NULL,
    display_name TEXT NOT NULL,
    FOREIGN KEY (league_id) REFERENCES league(id) ON DELETE CASCADE,
    UNIQUE (league_id, abbr)
);

CREATE TABLE IF NOT EXISTS roster (
    fantasy_team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY (fantasy_team_id) REFERENCES fantasy_team(id) ON DELETE CASCADE,
    FOREIGN KEY (player_id) REFERENCES player(id) ON DELETE CASCADE,
    PRIMARY KEY (fantasy_team_id, player_id)
);

CREATE TABLE IF NOT EXISTS roster_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    fantasy_team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY (fantasy_team_id) REFERENCES fantasy_team(id) ON DELETE CASCADE,
    FOREIGN KEY (player_id) REFERENCES player(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_roster_snapshot_team_time
    ON roster_snapshot(fantasy_team_id, captured_at);

CREATE INDEX IF NOT EXISTS idx_roster_snapshot_player_time
    ON roster_snapshot(player_id, captured_at);
