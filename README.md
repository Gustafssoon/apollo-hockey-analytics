# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.8 league-specific fantasy intelligence

Apollo now uses the fantasy league configuration already stored in SQLite as an analytics input. Instead of always assuming generic categories, league-aware commands resolve the selected league's categories, compare the user's roster with the other current rosters, identify weak categories, and weight player value toward those needs.

Yahoo API access can later populate the same normalized league/settings/ownership layer directly; the v0.8 analytics engine does not depend on live Yahoo credentials.

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

# Generic analytics remain available
apollo analyze "Macklin Celebrini" --season 20252026
apollo rankings --season 20252026
apollo waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07

# v0.8 league-aware intelligence
apollo league profile
apollo league needs --season 20252026
apollo league rankings --season 20252026 --type skater --limit 20
apollo league waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07 --limit 20
```

If more than one league is stored, pass `--league-id <external-id>` to the `apollo league` commands.

Season IDs use the NHL eight-digit format, for example `20252026` for 2025-26.

## v0.8 league profile and category support

`apollo league profile` reads `league`, `fantasy_team`, `roster`, and `league_stat_category`. It reports the selected league, user team, current team count, and which configured categories Apollo can currently evaluate.

Current supported categories are:

```text
Skaters: G, A, P, PPP, SOG, HIT, BLK, PIM, +/-
Goalies: W, SV, SV%, GAA, SHO
```

A Yahoo category that is not yet supported, such as `FW`, remains visible in the profile but is excluded from scoring instead of being silently approximated. This makes missing stat coverage explicit and gives later milestones a clear list of categories to add.

League categories are current configuration rather than historical data. `sync_league` therefore replaces the stored category set on each sync so a category removed from Yahoo cannot keep affecting Apollo scoring.

## Category needs

`apollo league needs` builds category-level player z-scores from the NHL season data and sums the z-scores of players currently rostered by each fantasy team. The user's team is then ranked against the other current teams in every supported league category.

The first need weighting is deliberately transparent:

```text
best team in category  -> weight 1.00
middle of league       -> weight around 1.50
worst team in category -> weight 2.00
```

Need levels are shown as `LOW`, `MEDIUM`, or `HIGH`. This is a roster-strength model, not yet a live head-to-head matchup projection.

## League-aware rankings

`apollo league rankings` starts with the same category z-scores as the generic rankings, but multiplies each category by the user's current need weight before summing them.

```text
League player value = Σ(category z-score × category need weight)
```

The output also shows the unweighted `RAW` category score so it is visible when Apollo is moving a player up specifically because that player fills a roster need.

Skaters and goalies are ranked separately because their category distributions and the current goalie reliability model are different.

## League-aware waivers

`apollo league waivers` combines the need-weighted category score with the already validated v0.6/v0.7 schedule and recent-form layers:

```text
Apollo league waiver value
    = need-weighted category Z
    + schedule_weight × schedule Z
    + trend_weight × recent-form signal
```

Availability is scoped to the selected league. A player owned in another stored league is still treated as available in the selected league. Current roster ownership is used; historical `roster_snapshot` rows do not make a player unavailable.

Until live Yahoo sync is available, ownership is only as current as the stored league sync.

## v0.7 league-wide recent form

`apollo nhl recent` uses NHL Stats REST game-level reports (`isGame=true`) to populate normalized player-game rows league-wide. NHL's 100-row page cap and 10,000-row season-report ceiling are handled with deterministic pagination and monthly date partitioning. A full 2025-26 live validation produced 47,230 skater game rows, 2,768 goalie game rows, and all 1,312 regular-season games, with identical counts on a repeated sync.

Season / Last 30 / Last 14 / Last 7 and waiver trend are calculated from normalized stored game rows rather than permanent derived tables.

## Schedule safety

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

The NHL adapters use public NHL endpoints and do not require credentials.

## Security

Never commit Yahoo credentials, OAuth tokens, private league exports, local SQLite databases, or `.env` files. These local artifacts are excluded from version control.
