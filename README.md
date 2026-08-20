# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.3 NHL player pool and game data

Apollo now has three independent data paths:

```text
Mock Yahoo adapter -> normalized league/roster -> SQLite
NHL roster APIs    -> complete player pool     -> SQLite
NHL game APIs      -> schedules + game logs    -> SQLite
```

Yahoo integration will later provide fantasy ownership, availability, league settings, scoring categories, matchups, draft results, and transactions. NHL data remains an independent hockey-data layer.

## Commands

```powershell
# Existing foundation
apollo init
apollo sync --source mock
apollo roster
apollo nhl sync
apollo player "Connor McDavid"

# v0.3: import active NHL rosters
apollo nhl pool --season 20252026
apollo players --team EDM

# v0.3: player game logs
apollo nhl game-log "Connor McDavid" --season 20252026
apollo games "Connor McDavid" --season 20252026 --limit 10

# v0.3: team schedules
apollo nhl schedule EDM --season 20262027
apollo schedule EDM --season 20262027 --limit 20
```

Season IDs use the NHL eight-digit format, for example `20252026` for 2025-26.

## v0.3 data model

The NHL roster import discovers teams from the standings endpoint and imports each team's roster without calling the player landing endpoint for every player. NHL IDs remain provider identities attached to Apollo's normalized `player` records.

Schedule games are stored once in `nhl_game`. Player game context is stored in `nhl_player_game`, with per-game numeric statistics in `nhl_player_game_stat`. This structure supports later rolling 7/14/30-game analytics without duplicating game metadata.

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
