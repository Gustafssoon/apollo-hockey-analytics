# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.9 live Yahoo league sync

Apollo can now authenticate directly with Yahoo OAuth 2.0 and translate a read-only Yahoo Fantasy Hockey league into the same normalized league/settings/ownership model that the v0.8 analytics engine already uses.

The live integration intentionally separates three states:

1. Yahoo OAuth authentication succeeds.
2. The Yahoo Fantasy API authorizes the application.
3. A selected Fantasy Hockey league is normalized and synced into Apollo.

This distinction matters because a valid OAuth token does not itself prove that the application has been provisioned for Fantasy API access. `apollo yahoo status` makes that boundary explicit and reports HTTP 403 authorization denial separately from OAuth failure.

## Commands

```powershell
# Foundation and NHL data
apollo init
apollo sync --source mock
apollo roster
apollo nhl pool --season 20252026
apollo nhl stats --season 20252026
apollo nhl recent --season 20252026
apollo nhl schedules --season 20262027

# Generic analytics
apollo analyze "Macklin Celebrini" --season 20252026
apollo rankings --season 20252026
apollo waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07

# League-aware intelligence
apollo league profile
apollo league needs --season 20252026
apollo league rankings --season 20252026 --type skater --limit 20
apollo league waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07 --limit 20

# v0.9 live Yahoo integration
apollo yahoo auth-url
apollo yahoo exchange --code "<ONE-TIME-CODE>"
apollo yahoo status
apollo yahoo leagues
apollo yahoo sync --league-key "<YAHOO-LEAGUE-KEY>"
```

Season IDs used by NHL analytics use the eight-digit NHL format, for example `20252026` for 2025-26. The optional `apollo yahoo leagues --season 2026` filter uses Yahoo's fantasy season year.

## Yahoo setup

Create a local `.env` from `.env.example` and fill in the credentials from your own Yahoo Developer application:

```text
YAHOO_CONSUMER_KEY=your-client-id
YAHOO_CONSUMER_SECRET=your-client-secret
YAHOO_REDIRECT_URI=https://localhost:8080
```

The redirect URI must match the Yahoo application registration. `.env` is ignored by Git and must never be committed.

Apollo does not depend on YFPY or another Yahoo wrapper. The OAuth and Fantasy requests are implemented directly with Python's standard library so Apollo keeps its MIT licensing boundary and has no runtime dependency on a GPL Yahoo client.

## Yahoo OAuth flow

Run:

```powershell
apollo yahoo auth-url
```

Open the printed Yahoo authorization URL in a browser and approve access. Yahoo redirects to the configured redirect URI. For the default localhost URI the page itself does not need to load; copy the one-time `code` query parameter from the browser address bar, then exchange it:

```powershell
apollo yahoo exchange --code "<ONE-TIME-CODE>"
```

Apollo stores access and refresh tokens locally in `.apollo/yahoo-token.json`. The client secret is not written to that token file, tokens are never printed by the CLI, and `.apollo/` is ignored by Git. Expired access tokens are refreshed automatically when a refresh token is available.

Check the two authorization layers with:

```powershell
apollo yahoo status
```

A healthy integration reports both an available OAuth token and an authorized Fantasy API. If Yahoo accepts OAuth but returns HTTP 403 from the Fantasy endpoint, Apollo reports `Fantasy API: DENIED (HTTP 403)` instead of treating the OAuth flow as broken. Fantasy API access may require separate Yahoo approval/provisioning outside Apollo.

## Discover and sync the live league

Once `apollo yahoo status` reports Fantasy API authorization, list the Fantasy Hockey leagues visible to the current Yahoo login:

```powershell
apollo yahoo leagues
```

Optionally filter by Yahoo fantasy season:

```powershell
apollo yahoo leagues --season 2026
```

Then sync the selected league key:

```powershell
apollo yahoo sync --league-key "<YAHOO-LEAGUE-KEY>"
```

The live adapter imports:

- league identity and name
- current scoring categories
- fantasy teams and the team owned by the current login
- current team rosters/ownership
- Yahoo player keys, names, NHL team abbreviations, and a normalized primary position

The sync feeds the existing `league`, `fantasy_team`, `league_stat_category`, `roster`, and `roster_snapshot` model. No separate live-Yahoo analytics path is required.

A Yahoo player is reconciled to an existing Apollo player when the NHL-backed identity is uniquely resolvable by name and NHL team. This lets a database previously seeded by the mock adapter transition to live Yahoo player keys without duplicating already matched NHL players. Historical roster snapshots remain attached to Apollo's internal player ID.

After sync, select the live Yahoo league explicitly if the database also contains the mock league:

```powershell
apollo league profile --league-id "<YAHOO-LEAGUE-KEY>"
apollo league needs --league-id "<YAHOO-LEAGUE-KEY>" --season 20252026
apollo league rankings --league-id "<YAHOO-LEAGUE-KEY>" --season 20252026 --type skater
apollo league waivers --league-id "<YAHOO-LEAGUE-KEY>" --season 20252026 --schedule-season 20262027 --as-of 2026-10-07
```

## Yahoo attribution

Any product surface displaying Yahoo Fantasy data should include the attribution:

**Fantasy data provided by Yahoo Fantasy** — https://sports.yahoo.com/fantasy/

The CLI includes this attribution after live Yahoo league/listing output. A future graphical UI must also follow Yahoo's current developer branding and attribution requirements.

## League-specific fantasy intelligence

`apollo league profile` reports the selected league, user team, current team count, and which configured categories Apollo can currently evaluate.

Supported categories are:

```text
Skaters: G, A, P, PPP, SOG, HIT, BLK, PIM, +/-
Goalies: W, SV, SV%, GAA, SHO
```

Unsupported league categories remain visible but are excluded from scoring rather than silently approximated.

`apollo league needs` sums category-level player z-scores for each current fantasy roster and ranks the user's team against the league. Need weights are transparent:

```text
best team in category  -> weight 1.00
middle of league       -> weight around 1.50
worst team in category -> weight 2.00
```

League-aware player value is:

```text
League player value = Σ(category z-score × category need weight)
```

League-aware waiver value is:

```text
Apollo league waiver value
    = need-weighted category Z
    + schedule_weight × schedule Z
    + trend_weight × recent-form signal
```

## NHL data foundation

`apollo nhl recent` uses NHL Stats REST game-level reports to populate normalized player-game rows league-wide. NHL's observed 100-row page cap and 10,000-row season-report ceiling are handled with deterministic pagination and monthly date partitioning. A full 2025-26 live validation produced 47,230 skater game rows, 2,768 goalie game rows, and all 1,312 regular-season games, with identical counts on a repeated sync.

`apollo nhl schedules` fetches every NHL team's season schedule and deduplicates games before storing them. Schedule scoring is disabled unless full team coverage is recorded, preventing unsynced teams from being treated as having zero games.

## Development

Apollo targets Python 3.13.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest -q
ruff check .
```

The NHL adapters use public NHL endpoints and do not require credentials. Yahoo integration requires the user's own Yahoo Developer application credentials and whatever Fantasy API access Yahoo has provisioned for that application.

## Security

Never commit Yahoo credentials, OAuth tokens, private league exports, local SQLite databases, or `.env` files. `.env`, `.apollo/`, and local database files are excluded from version control.
