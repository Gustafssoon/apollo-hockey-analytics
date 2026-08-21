# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.7 league-wide recent form

Apollo now combines normalized hockey data, league-wide season category rankings, schedule context, and league-wide game-level recent form into its first actionable fantasy decision layer.

## Commands

```powershell
apollo init
apollo sync --source mock
apollo roster
apollo nhl sync
apollo player "Connor McDavid"
apollo nhl pool --season 20252026
apollo nhl stats --season 20252026
apollo nhl recent --season 20252026
apollo analyze "Macklin Celebrini" --season 20252026
apollo nhl schedules --season 20262027
apollo rankings --season 20252026
apollo waivers --season 20252026 --schedule-season 20262027 --as-of 2026-10-07
apollo value "Connor McDavid" --season 20252026 --schedule-season 20262027 --as-of 2026-10-07
```

Season IDs use the NHL eight-digit format, for example `20252026` for 2025-26.

## v0.7 league-wide recent form

`apollo nhl recent` uses NHL Stats REST game-level reports (`isGame=true`) to populate normalized player-game rows for the league without one request per player. Skater summary data supplies scoring and shooting, the realtime report enriches the same player/game keys with HIT and BLK, and goalie summary supplies game-level goalie results.

NHL Stats REST currently caps positive game-report pages at 100 rows. Apollo therefore uses deterministic sorting and paginates with a maximum positive page size of 100. Live validation also exposed a season-wide `total` ceiling of 10,000 rows for large skater game reports. When that ceiling is hit, Apollo partitions the season into non-overlapping monthly `gameDate` windows. Each window first requests `limit=-1`, verifies its returned row count against that window's `total`, and falls back to normal pagination if necessary. A monthly window that itself reaches the 10,000-row ceiling fails loudly rather than silently storing partial data.

The batch rows are stored through the same `nhl_game`, `nhl_player_game`, and `nhl_player_game_stat` tables used by targeted `apollo nhl game-log` syncs. No permanent derived-form table is required: Season / Last 30 / Last 14 / Last 7 and waiver trend are calculated from normalized game rows.

A successful league-wide sync should be idempotent: repeated runs against unchanged NHL data return the same report counts and replace matched player-season game rows rather than duplicating them. Completeness is validated from the source report windows before persistence, so hitting a known response ceiling cannot masquerade as a complete season.

Historical players that are not part of Apollo's current roster-derived NHL player pool are skipped safely.

## Waiver and player value

`apollo waivers` starts with the category z-score and adds schedule opportunity plus recent-form trend. By default it excludes players already present in Apollo's stored fantasy rosters. Until Yahoo ownership sync is live this is only a local availability approximation.

## Schedule safety

`apollo nhl schedules` fetches every NHL team's season schedule and deduplicates games before storing them. The waiver engine only enables schedule scoring when full team coverage is recorded.

## Category rankings

`apollo rankings` calculates category z-scores across eligible players. Default skater categories are `G,A,PPP,SOG,HIT,BLK`; default goalie categories are `W,SV%,GAA,SHO`. The goalie model remains provisional because workload/reliability is not yet fully modeled.

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
