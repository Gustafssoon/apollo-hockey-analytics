# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.7 league-wide recent form

Apollo now combines normalized hockey data, league-wide season category rankings, schedule context, and league-wide game-level recent form into its first actionable fantasy decision layer:

```text
Mock Yahoo adapter -> normalized league/roster -> SQLite
NHL roster APIs    -> complete player pool     -> SQLite
NHL game APIs      -> schedules                -> SQLite
NHL Stats REST     -> season + game-level stats -> SQLite
                                                 |
                                                 v
                         category value + recent trend + schedule
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
apollo nhl sync
apollo player "Connor McDavid"

# NHL player pool and category data
apollo nhl pool --season 20252026
apollo nhl stats --season 20252026
apollo players --team EDM

# v0.7 league-wide recent form
apollo nhl recent --season 20252026
apollo analyze "Macklin Celebrini" --season 20252026

# Targeted player game logs remain available
apollo nhl game-log "Connor McDavid" --season 20252026
apollo games "Connor McDavid" --season 20252026 --limit 10

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

`apollo nhl recent` uses NHL Stats REST game-level reports (`isGame=true`) to populate normalized player-game rows for the league without one request per player. Skater summary data supplies scoring and shooting, the realtime report enriches the same player/game keys with HIT and BLK, and goalie summary supplies game-level goalie results.

NHL Stats REST currently caps positive game-report pages at 100 rows even if a larger limit is requested. Apollo therefore uses deterministic sorting and paginates with a maximum page size of 100, following the response `total` until every page is collected. This is intentionally slower than the original single-page prototype, but prevents silent partial datasets and unstable repeated-sync results.

The batch rows are stored through the same `nhl_game`, `nhl_player_game`, and `nhl_player_game_stat` tables used by targeted `apollo nhl game-log` syncs. No permanent derived-form table is required: Season / Last 30 / Last 14 / Last 7 and waiver trend are calculated from normalized game rows.

Historical players that are not part of Apollo's current roster-derived NHL player pool are skipped safely. Re-running the same recent-form sync replaces each matched player's season game rows rather than creating duplicates.

## Waiver and player value

`apollo waivers` starts with the category z-score and adds two transparent opportunity signals:

```text
Apollo value = category Z + schedule_weight * schedule Z + trend_weight * trend signal
```

The default schedule weight is `1.0`. Schedule opportunity is the number of games in the selected window plus a configurable bonus for games played on off-nights. By default, a date with eight or fewer NHL games is treated as an off-night and each off-night game adds `0.5` opportunity before the schedule z-score is calculated.

The default trend weight is `0.5`. For skaters, Last-7 points per game is compared with the season baseline: at least +10% is `UP`, at most -10% is `DOWN`, otherwise `STABLE`. League-wide v0.7 game data supplies this signal after one batch sync; players without matching stored game rows still receive no fabricated bonus or penalty.

By default the waiver board excludes players currently present in Apollo's `roster` table. Until Yahoo sync is live this only reflects roster data already stored locally, such as the mock league. `--include-rostered` can be used for a full player-value board. When Yahoo ownership is available, the same boundary will become the real free-agent/waiver pool.

`--categories` makes the engine immediately useful for category needs. For example `--categories HIT,BLK` ranks players specifically for those categories, while `--position D` limits the result to defensemen. `LW`/`RW` are normalized to the NHL roster codes `L`/`R`, and `F` matches centers and wings.

## Schedule safety

`apollo nhl schedules` fetches every NHL team's season schedule and deduplicates games before storing them. The waiver engine checks schedule coverage before assigning schedule value. If only one or a few team schedules are stored, the schedule component is disabled for everyone rather than treating missing teams as having zero games.

Once full coverage is present, Apollo counts regular-season games only. Schedule density and off-night value are calculated from the shared `nhl_game` table, so the same game is never counted twice even though both teams' schedules contain it.

## Category stat coverage and rankings

`apollo nhl stats` uses NHL Stats REST reports rather than making one player request at a time. Skater summary data provides games played, goals, assists, points, power-play points, shots, plus/minus, penalty minutes, and time on ice. The realtime report adds hits, blocked shots, takeaways, and giveaways. Goalie summary data adds games played, starts, wins, losses, overtime losses, saves, shots against, goals against, save percentage, goals-against average, and shutouts.

`apollo rankings` calculates category z-scores across eligible players and sums them into an overall score. The default skater categories are `G,A,PPP,SOG,HIT,BLK`; the default goalie categories are `W,SV%,GAA,SHO`. Lower goals-against average is automatically treated as better. Rankings default to per-game rates with a 10-game minimum, while `--mode total` is available for accumulated season value.

The current goalie model is intentionally provisional: wins are normalized by games played in per-game mode, so workload and relief appearances can distort value. A later revision should add games-started and reliability/workload weighting.

## NHL data model

NHL IDs remain provider identities attached to Apollo's normalized `player` records. Schedule games are stored once in `nhl_game`. Player game context is stored in `nhl_player_game`, with per-game numeric statistics in `nhl_player_game_stat`. Season category data remains in `nhl_player_season_stat`.

Derived rankings, rolling windows, and waiver values are recalculated from normalized stored data rather than persisted as permanent derived tables. This lets the scoring model evolve without schema churn.

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
