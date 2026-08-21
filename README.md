# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.7 league-wide recent form

Apollo now combines normalized NHL data, season category value, league-wide game-level form, and schedule opportunity into an actionable fantasy decision layer:

```text
Mock Yahoo adapter -> normalized league/roster -> SQLite
NHL roster APIs    -> complete player pool     -> SQLite
NHL Stats REST     -> season category stats    -> SQLite
NHL Stats REST     -> league-wide game stats   -> SQLite
NHL schedule APIs  -> complete schedules       -> SQLite
                                                |
                                                v
                      category value + recent form + schedule
                                                |
                                                v
                                  waiver / streamer value
```

Yahoo integration will later replace mock roster ownership and generic categories with real league ownership, availability, settings, scoring categories, matchups, draft results, and transactions. NHL data remains an independent hockey-data layer.

## Commands

```powershell
# Foundation
apollo init
apollo sync --source mock
apollo roster
apollo nhl pool --season 20252026
apollo players --team EDM

# League-wide season and game-level data
apollo nhl stats --season 20252026
apollo nhl recent --season 20252026

# Rolling player analytics now work from the league-wide recent-form sync
apollo games "Connor McDavid" --season 20252026 --limit 10
apollo analyze "Connor McDavid" --season 20252026

# Individual game-log sync remains available for targeted refresh/debugging
apollo nhl game-log "Connor McDavid" --season 20252026

# One-team or league-wide schedules
apollo nhl schedule EDM --season 20262027
apollo nhl schedules --season 20262027
apollo schedule EDM --season 20262027 --limit 20

# Category rankings
apollo rankings --season 20252026
apollo rankings --season 20252026 --categories G,A,PPP,SOG,HIT,BLK --mode per-game
apollo rankings --season 20252026 --type goalie
apollo leaders --stat SOG --season 20252026 --limit 10
apollo compare "Connor McDavid" "Nathan MacKinnon" --season 20252026

# Waiver / streamer value
apollo waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07
apollo waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07 --categories HIT,BLK
apollo waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07 --position D
apollo value "Connor McDavid" --season 20252026 --schedule-season 20262027 --as-of 2026-10-07
```

Season IDs use the NHL eight-digit format, for example `20252026` for 2025-26.

## v0.7 league-wide recent form

`apollo nhl recent` uses the NHL Stats REST game view (`isGame=true`) and paginates league-wide skater and goalie reports. It does not make one HTTP request per player.

For skaters Apollo combines the game-level `summary` and `realtime` reports. This provides goals, assists, points, power-play points, shots, plus/minus, penalty minutes, hits, blocked shots, takeaways, and giveaways when exposed by the NHL reports. Goalie game rows include saves, shots against, goals against, save percentage, decisions, shutouts, and related summary fields.

The batch rows are matched through the existing NHL provider identity and stored in the same normalized `nhl_game`, `nhl_player_game`, and `nhl_player_game_stat` tables used by individual game-log sync. Historical players that are not in Apollo's current roster-derived NHL pool are counted as unmatched and skipped rather than creating ambiguous identities.

Because the storage path is shared, existing analytics benefit immediately: `apollo analyze` can calculate Season / Last 30 / Last 14 / Last 7 for any matched player with batch game data, while the waiver engine can calculate Last-7 trend signals league-wide instead of showing `TREND N/A` for nearly every player.

The individual `apollo nhl game-log` command remains available for targeted refreshes and debugging, but it is no longer required to populate rolling form one player at a time.

## Waiver and player value

`apollo waivers` starts with the category z-score and adds two transparent opportunity signals:

```text
Apollo value = category Z + schedule_weight * schedule Z + trend_weight * trend signal
```

The default schedule weight is `1.0`. Schedule opportunity is the number of games in the selected window plus a configurable bonus for games played on off-nights. By default, a date with eight or fewer NHL games is treated as an off-night and each off-night game adds `0.5` opportunity before the schedule z-score is calculated.

The default trend weight is `0.5`. Skater trend compares Last-7 points per game with the season baseline: at least +10% is `UP`, at most -10% is `DOWN`, otherwise `STABLE`. Missing game-level data produces no trend bonus or penalty rather than fabricated form data.

By default the waiver board excludes players currently present in Apollo's `roster` table. Until Yahoo sync is live this only reflects roster data already stored locally, such as the mock league. `--include-rostered` can be used for a full player-value board.

`--categories` supports targeted category needs. For example `--categories HIT,BLK` ranks players specifically for those categories, while `--position D` limits the result to defensemen. `LW`/`RW` are normalized to the NHL roster codes `L`/`R`, and `F` matches centers and wings.

## Schedule safety

`apollo nhl schedules` fetches every NHL team's season schedule and deduplicates games before storing them. Explicit per-team sync provenance is stored so the waiver engine can distinguish complete league coverage from a partial schedule cache.

If only one or a few team schedules are stored, the schedule component is disabled for everyone rather than treating missing teams as having zero games. Once full coverage is present, Apollo counts regular-season games only and calculates schedule density and off-night value from the shared `nhl_game` table.

## Category stat coverage and rankings

`apollo nhl stats` uses league-wide NHL Stats REST reports for season data. Skater summary data provides games played, goals, assists, points, power-play points, shots, plus/minus, penalty minutes, and time on ice; realtime adds hits, blocked shots, takeaways, and giveaways. Goalie summary data includes starts, wins, losses, saves, shots against, goals against, save percentage, goals-against average, and shutouts.

`apollo rankings` calculates category z-scores across eligible players and sums them into an overall score. The default skater categories are `G,A,PPP,SOG,HIT,BLK`; the default goalie categories are `W,SV%,GAA,SHO`. Rankings default to per-game rates with a 10-game minimum, while `--mode total` is available for accumulated season value.

The current goalie value model remains provisional: workload and relief appearances can distort per-game value. A later revision should add games-started and reliability/workload weighting.

## NHL data model

NHL IDs remain provider identities attached to Apollo's normalized `player` records. Schedule and historical games are stored once in `nhl_game`. Player game context is stored in `nhl_player_game`, with per-game numeric statistics in `nhl_player_game_stat`. Season category data remains in `nhl_player_season_stat`.

Derived rankings, rolling windows, trends, and waiver values are recalculated from normalized stored data rather than persisted as permanent derived tables. This lets the scoring model evolve without schema churn.

## Architecture

```text
Yahoo API (later)                 NHL public APIs
       |                                |
       v                                v
  Yahoo adapter              roster / game / stats adapters
       |                                |
       +---------------+----------------+
                       v
                normalized models
                       |
                       v
                    SQLite
                       |
                       v
                 analytics engine
                       |
        +--------------+--------------+
        v              v              v
     waivers         trades        lineups
                       |
                       v
                    Apollo AI
```

## Development

Apollo targets Python 3.13.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest -q
ruff check .
```

The NHL adapters use public NHL endpoints and do not require credentials.

## Security

Never commit Yahoo credentials, OAuth tokens, private league exports, local SQLite databases, or `.env` files. These local artifacts are excluded from version control.
