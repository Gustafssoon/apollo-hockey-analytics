# Apollo Hockey Analytics

Apollo is a fantasy hockey analytics platform for league sync, player evaluation, waiver analysis, matchup optimization, and AI-assisted decision support.

## Current milestone: v0.5 category rankings and stat coverage

Apollo now has a normalized hockey-data layer, rolling player analytics, and a first league-wide fantasy ranking layer:

```text
Mock Yahoo adapter -> normalized league/roster -> SQLite
NHL roster APIs    -> complete player pool     -> SQLite
NHL game APIs      -> schedules + game logs    -> SQLite
NHL Stats REST     -> season category stats    -> SQLite
                                                |
                                                v
                         rolling analytics + category rankings
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

# Rolling player analytics
apollo analyze "Connor McDavid" --season 20252026
apollo analyze "Connor McDavid" --season 20252026 --schedule-season 20262027 --as-of 2026-10-07

# v0.5: league-wide fantasy category data
apollo nhl stats --season 20252026

# v0.5: rankings, leaders, and comparisons
apollo rankings --season 20252026
apollo rankings --season 20252026 --categories G,A,PPP,SOG,HIT,BLK --mode per-game
apollo rankings --season 20252026 --type goalie
apollo leaders --stat SOG --season 20252026 --limit 10
apollo compare "Connor McDavid" "Nathan MacKinnon" --season 20252026
```

Season IDs use the NHL eight-digit format, for example `20252026` for 2025-26.

## v0.5 category stat coverage

`apollo nhl stats` uses the NHL Stats REST reports rather than making one player request at a time. Skater summary data provides games played, goals, assists, points, power-play points, shots, plus/minus, penalty minutes, and time on ice. The realtime report adds hits, blocked shots, takeaways, and giveaways. Goalie summary data adds games played, starts, wins, losses, overtime losses, saves, shots against, goals against, save percentage, goals-against average, and shutouts.

The league-wide season data is stored in the existing `nhl_player_season_stat` table and linked to Apollo players through the normalized NHL provider identity. This keeps season totals independent from Yahoo ownership and league configuration.

## Fantasy rankings

`apollo rankings` calculates category z-scores across eligible players and sums them into an overall score. The default skater categories are `G,A,PPP,SOG,HIT,BLK`; the default goalie categories are `W,SV%,GAA,SHO`. Lower goals-against average is automatically treated as better.

Rankings default to per-game rates with a 10-game minimum so players with different game counts can be compared on rate performance. `--mode total` is available for accumulated season value, and both the category list and minimum-games threshold are configurable. These defaults are intentionally generic: when Yahoo league settings are available, Apollo will supply the actual league categories instead.

`apollo leaders` ranks one category directly, while `apollo compare` shows the same normalized category values side by side for two players.

## Rolling analytics

`apollo analyze` reads stored per-game data and computes four windows: the full regular-season game log, Last 30, Last 14, and Last 7. Each window contains per-game rates for the fantasy-relevant statistics available in the NHL game-log response.

The first trend signal compares the player's Last-7 rate with the full-season baseline. Skaters prefer points per game as the trend metric; goalies prefer save percentage and fall back to saves or wins. A change of at least 10% is labeled `UP` or `DOWN`; smaller changes are `STABLE`.

Schedule density is intentionally separate from performance. If the player's team schedule is stored, Apollo counts regular-season games in a configurable upcoming calendar window. If no schedule has been synced, the result is reported as unavailable rather than incorrectly reported as zero games.

Season-level HIT and BLK coverage comes from NHL Stats REST in v0.5. Rolling Last-7/14/30 HIT and BLK still depend on per-game source coverage and will remain unavailable where the player game-log endpoint does not provide those fields. A later milestone can enrich game-level peripherals from boxscore data.

## NHL data model

The NHL roster import discovers teams from the standings endpoint and imports each team's roster without calling the player landing endpoint for every player. NHL IDs remain provider identities attached to Apollo's normalized `player` records.

Schedule games are stored once in `nhl_game`. Player game context is stored in `nhl_player_game`, with per-game numeric statistics in `nhl_player_game_stat`. The analytics engine reads this normalized game data rather than storing derived rolling values, so windows can be recalculated without schema churn.

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
