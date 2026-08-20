# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.4 fantasy analytics engine

Apollo now has a normalized hockey-data layer plus its first analytics layer:

```text
Mock Yahoo adapter -> normalized league/roster -> SQLite
NHL roster APIs    -> complete player pool     -> SQLite
NHL game APIs      -> schedules + game logs    -> SQLite
                                                |
                                                v
                                    rolling fantasy analytics
```

Yahoo integration will later provide fantasy ownership, availability, league settings, scoring categories, matchups, draft results, and transactions. NHL data remains an independent hockey-data layer.

## Commands

```powershell
# Foundation
apollo init
apollo sync --source mock
apollo roster
apollo nhl sync
apollo player "Connor McDavid"

# NHL player pool
apollo nhl pool --season 20252026
apollo players --team EDM

# Player game logs
apollo nhl game-log "Connor McDavid" --season 20252026
apollo games "Connor McDavid" --season 20252026 --limit 10

# Team schedules
apollo nhl schedule EDM --season 20262027
apollo schedule EDM --season 20262027 --limit 20

# v0.4 fantasy analytics
apollo analyze "Connor McDavid" --season 20252026
apollo analyze "Connor McDavid" --season 20252026 --schedule-season 20262027 --as-of 2026-10-07
```

Season IDs use the NHL eight-digit format, for example `20252026` for 2025-26.

## v0.4 analytics

`apollo analyze` reads stored per-game data and computes four windows: the full regular-season game log, Last 30, Last 14, and Last 7. Each window contains per-game rates for the fantasy-relevant statistics available in the NHL game-log response.

For skaters the CLI currently surfaces goals, assists, points, shots, hits, and blocked shots per game. For goalies it surfaces saves, shots against, goals against, and save percentage when those fields are available.

The first trend signal compares the player's Last-7 rate with the full-season baseline. Skaters prefer points per game as the trend metric; goalies prefer save percentage and fall back to saves or wins. A change of at least 10% is labeled `UP` or `DOWN`; smaller changes are `STABLE`.

Schedule density is intentionally separate from performance. If the player's team schedule is stored, Apollo counts regular-season games in a configurable upcoming calendar window. If no schedule has been synced, the result is reported as unavailable rather than incorrectly reported as zero games.

## NHL data model

The NHL roster import discovers teams from the standings endpoint and imports each team's roster without calling the player landing endpoint for every player. NHL IDs remain provider identities attached to Apollo's normalized `player` records.

Schedule games are stored once in `nhl_game`. Player game context is stored in `nhl_player_game`, with per-game numeric statistics in `nhl_player_game_stat`. The analytics engine reads this normalized game data rather than storing derived rolling values, so windows can be recalculated without schema churn.

## Architecture

```text
Yahoo API (later)                 NHL public APIs
       |                                |
       v                                v
  Yahoo adapter                    NHL adapter
       |                       roster / game / schedule
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

The NHL adapter uses public NHL endpoints and does not require credentials.

## Security

Never commit Yahoo credentials, OAuth tokens, private league exports, local SQLite databases, or `.env` files. These local artifacts are excluded from version control.
