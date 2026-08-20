# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.1 foundation

The first milestone proves the full data path without requiring live API access:

```text
Mock Yahoo adapter -> normalized models -> SQLite -> query -> CLI
```

### v0.1 commands

```powershell
apollo init
apollo sync --source mock
apollo roster
```

The mock sync reads `fixtures/mock_league.json`, creates `apollo.db`, stores a normalized league/roster model, and records a roster snapshot for historical analysis.

## Architecture

```text
Yahoo API (later)       NHL API (later)
       |                       |
       v                       v
  Yahoo adapter           NHL adapter
       |                       |
       +----------+------------+
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
```

## Security

Never commit Yahoo credentials, OAuth tokens, private league exports, or `.env` files. The repository's Python `.gitignore` excludes `.env` by default.
