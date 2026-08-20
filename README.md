# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.2 NHL data foundation

Apollo now proves two independent data paths:

```text
Mock Yahoo adapter -> normalized models -> SQLite -> roster CLI
NHL public APIs    -> player matching   -> SQLite -> player CLI
```

### Commands

```powershell
apollo init
apollo sync --source mock
apollo roster
apollo nhl sync
apollo player "Connor McDavid"
```

The mock league sync reads `fixtures/mock_league.json`, creates `apollo.db`, stores a normalized league/roster model, and records roster snapshots for historical analysis.

`apollo nhl sync` matches Apollo players to NHL player IDs, retrieves the NHL player landing data, and stores current regular-season summary stats. NHL identity is kept separate from fantasy-provider identity so Yahoo integration can be added without changing the core player model.

## Architecture

```text
Yahoo API (later)          NHL public APIs
       |                         |
       v                         v
  Yahoo adapter              NHL adapter
       |                         |
       +------------+------------+
                    v
             normalized models
                    |
                    v
                 SQLite
                    |
                    v
               analytics
                    |
                    v
                AI tools
```

## Development

Apollo currently targets Python 3.13 while Yahoo/YFPY dependencies catch up with Python 3.14.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
ruff check .
```

The NHL adapter uses the public NHL web/search endpoints and does not require credentials.

## Security

Never commit Yahoo credentials, OAuth tokens, private league exports, local SQLite databases, or `.env` files. The repository ignores these local artifacts.
