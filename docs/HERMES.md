# Hermes Integration

Status: implemented as a read-only tool surface plus optional stdio MCP wrapper. Ambient/ship-status have verified runtime on-demand named-pipe fetches. Trade has verified live `docked_station` (`trade_offers_probe_v1`) and bounded `radar_range` (`trade_offers_radar_v1`) scopes with normalized observed offer fields plus raw preservation. Faction state now has a raw-first live pipe reader (`faction_state_v1`) for player↔faction standings and diplomacy/event operations.

## Verdict

This is possible with the current stack.

Verified from Hermes docs/skill: Hermes has a native MCP client. It can launch stdio MCP servers from `mcp_servers` config, discover their tools at startup, and expose them as first-class Hermes tools with `mcp_<server>_<tool>` names. That resolves the handoff's open seam: **use stdio MCP for Hermes integration**, while keeping the core X4 surface as plain importable Python (`x4_copilot.tools`) so a direct Hermes custom tool can still wrap it later without redesign.

## Boundary decisions

- Single box, local IPC only.
- No HTTP listener, no local auth token, no network surface between Hermes and this repo.
- Tool layer is dumb and stateless: it calls a `TelemetryFetcher` and returns structured dictionaries.
- Tool layer contains no LLM calls, no provider routing, no API keys, and no prose generation.
- Hermes owns deep reasoning and model routing on the Hermes path.
- `llm.py` remains for the separate reflex/fast path; it is not used by the Hermes/MCP tools.
- Actions are present only as default-off refusal stubs. They do not mutate game state.

## Implemented surfaces

Core importable module: `src/x4_copilot/tools.py`

Read tools:

- `get_ambient_context()`
- `fetch_trade_offers(scope="docked_station", radar_only=None, sector=None)`
- `fetch_ship_status()`
- `fetch_faction_state(since=None)`
- `fetch_sector_objects(kinds=None)`

Gated action stubs:

- `set_waypoint(station_id=None, pos=None, confirm_token=None)`
- `mark_target(object_id, confirm_token=None)`

MCP wrapper: `src/x4_copilot/mcp_server.py`

CLI helpers:

```bash
uv run x4-copilot tool trade
uv run x4-copilot tool faction
uv run x4-copilot mcp-config
uv run --extra mcp x4-copilot-mcp
```

## Hermes config snippet

From the repo checkout, add this to Hermes config under `mcp_servers`:

```yaml
mcp_servers:
  x4_copilot:
    command: "uv"
    args: ["--directory", "C:/Users/lc77/Projects/x4-llm-copilot", "run", "--extra", "mcp", "x4-copilot-mcp"]
    timeout: 30
    connect_timeout: 30
```

Use `uv run x4-copilot mcp-config` to print the checkout-specific absolute path instead of copying this path blindly.

Then restart Hermes. Discovered tool names should be prefixed like:

- `mcp_x4_copilot_fetch_trade_offers`
- `mcp_x4_copilot_fetch_ship_status`
- `mcp_x4_copilot_fetch_faction_state`
- `mcp_x4_copilot_fetch_sector_objects`

The MCP SDK is optional. Install/run with the `mcp` extra when you want the stdio server path.

## Current data source

By default the tool surface uses `MockTelemetryFetcher` and fixture files in `examples/`:

- `ambient_context_payload.json` (now normalized from the first verified live Lua `ambient_probe_v2` payload: sector, player money, ship hull/shield, and raw cargo shape)
- `trade_payload.json`
- `ship_status_payload.json`
- `faction_state_payload.json`
- `sector_objects_payload.json`

Every mock result is marked via structured provenance (`FetchProvenance(source="mock", stale=True)`) and surfaced as `source: "mock"` / `stale: true`. The tool layer does **not** parse `as_of` text to infer provenance.

Verified runtime live pipe reads currently cover ambient context, ship status via the ambient payload, docked/radar-range trade offers, and faction-state reads. Sector objects remain mock/stale fixtures until their Lua read path exists.

### Runtime live pipe mode

This is the source of truth for real co-pilot calls:

1. On `md.Named_Pipes.Reloaded`, X4 MD starts a `md.Named_Pipes.Read` request loop directly and retries transient read errors instead of blocking on an X4→Python ping. Startup/read errors are routed through a delayed retry cue so a missing pipe server does not permanently dead-end the loop.
2. `get_ambient_context()` / `fetch_ship_status()` with `X4_COPILOT_TELEMETRY_SOURCE=live_pipe` writes a `{"type":"fetch", ...}` request down the pipe.
3. MD receives that request and raises the Lua event `x4LLMCopilotFetchAmbient`.
4. Lua reads fresh game state and emits `telemetry_raw` with `trigger:"fetch_response"`.
5. Python returns that response directly and appends it to `var/live_telemetry_raw.jsonl` only as a debug/audit log.

A failed or missing pipe response raises an error. It does **not** replay the last JSONL line. The `fetch_response` wait is bounded by a single wall-clock deadline, so background probe churn cannot keep the call alive forever by repeatedly resetting per-read timeouts.

Runtime precondition: X4 must be running with SirNukes Named Pipes loaded and the `x4_llm_copilot` extension active. If the game/API is not started, the Windows named-pipe server waits for X4 to attach; use an outer command timeout for operator smoke checks in that state. That no-game case is distinct from an in-game fetch timeout after the pipe has attached.

Verified live smoke from the running game:

```bash
uv run --extra winpipe x4-copilot tool ambient --source live-pipe --timeout 60
# {"sector":"Windfall I Union Summit", "credits":39482, "ship":"Raleigh (Container)", "source":"x4_lua_live_pipe", "stale":false, ...}

uv run --extra winpipe x4-copilot tool trade --source live-pipe --timeout 60
# VIG Ice Refinery I live offers: buy energycells/foodrations/ice/medicalsupplies; sell water amount=75763 price=30.8 market_price=32.42
```

Known live caveat: repeated UI/game reloads can leave multiple idle retry-loop instances until a clean game restart. This can add duplicate `request read failed; retrying after delay` log lines when no pipe server is present, but on-demand live fetches still complete and return fresh `fetch_response` payloads.

Use on-demand live ambient in the CLI:

```bash
uv run --extra winpipe x4-copilot tool ambient --source live-pipe
uv run --extra winpipe x4-copilot tool ship --source live-pipe
uv run --extra winpipe x4-copilot tool trade --source live-pipe --scope docked_station
uv run --extra winpipe x4-copilot tool trade --source live-pipe --scope radar_range
uv run --extra winpipe x4-copilot tool faction --source live-pipe
```

`tool trade --source live-pipe --scope docked_station` reads the trade container the player ship is currently docked at. `--scope radar_range` enumerates known in-sector stations that are radar-visible or within the player ship radar radius, reads each station via the same `GetTradeList(station, ship)` path, and tags each normalized offer with station identity plus distance.

The docked-station reader sends `intent:"trade_in_sector"`; Lua emits `schema:"trade_offers_probe_v1"` with `offers_raw` / `nontrade_offers_raw`; Python maps observed fields (`ware`, `name`, `side`, `price`, `market_price`, `amount`, `station`, `faction`) while keeping the full raw offer under `raw`. Radar-range emits `schema:"trade_offers_radar_v1"`, `source:"x4_lua_live_pipe"`, `stations_raw[]`, and per-offer `distance_m`/`distance_km`; Python normalizes each station offer through the same `_normalize_trade_offer` path and preserves both offer raw and station raw.

The faction-state reader sends `intent:"faction_state"`; Lua emits `schema:"faction_state_v1"`, `source:"x4_lua_live_pipe"`, `standings_raw[]`, and `events_raw[]`. Standings use the game's UI relation integer (`GetUIRelation(faction.id)`, expected -30..+30), faction id/name/shortname from `GetLibrary("factions")`/`GetFactionData`, rank/title evidence from held ceremony licences when present, and preserve each raw standing. Python derives the current `rank_title` from the standing threshold (`ceremonyfriend` at +10, `ceremonyally` at +20) and keeps Lua's raw rank guess as `rank_title_raw`. Events use diplomacy event definitions/operations where available and preserve full raw event operations. Python normalizes to per-faction `faction_standing` rows and event rows (`relation_change`/`combat`/`promotion`/`territory`/`diplomacy`) while keeping `raw`. Runtime mode still requires a direct fresh `trigger:"fetch_response"`; timeout/transport failure raises an error and never falls back to JSONL.

Pipe ownership rule: this live mode creates the named-pipe server for `x4_llm_copilot`. Do **not** also run `x4-copilot serve-pipe --pipe x4_llm_copilot` or another live fetcher on the same pipe name at the same time; only one server can own that pipe.

Use on-demand live ambient in the MCP server:

```bash
X4_COPILOT_TELEMETRY_SOURCE=live_pipe \
X4_COPILOT_PIPE_NAME=x4_llm_copilot \
X4_COPILOT_RAW_TELEMETRY_LOG=var/live_telemetry_raw.jsonl \
uv run --extra mcp --extra winpipe x4-copilot-mcp
```

### Development raw-log replay mode

This remains useful for schema capture and offline debugging, but it is not the runtime source of truth:

1. X4 Lua emits `telemetry_raw` with `schema: "ambient_probe_v2"` and `trigger:"reload_probe"` on UI/game reload.
2. The MD layer forwards `event.param3` to `md.Named_Pipes.Write`.
3. `x4-copilot serve-pipe --pipe x4_llm_copilot` ACKs and appends the literal JSON to `var/live_telemetry_raw.jsonl`.
4. `RawTelemetryLogFetcher` maps the latest raw line into `TelemetryPayload` for `ambient_context` and `ship_status`. `player_money` becomes `ambient.credits`; `cargo_raw` remains raw/unresolved until a non-empty live cargo payload defines the ware-ID shape and the ID-resolution boundary is chosen.

Use live raw ambient in the CLI:

```bash
uv run x4-copilot tool ambient --source live-raw-log
```

Use live raw ambient in the MCP server:

```bash
X4_COPILOT_TELEMETRY_SOURCE=live_raw_log \
X4_COPILOT_RAW_TELEMETRY_LOG=var/live_telemetry_raw.jsonl \
uv run --extra mcp x4-copilot-mcp
```

Docked/radar-range trade and faction state are available through runtime live pipe mode. Sector-object tools remain mock/stale until their Lua read paths exist. The mixed live/mock surface reports provenance per result, so Hermes can see which tools are real.

## Why MCP over direct Hermes tool now?

The handoff's risk was that Hermes might not consume MCP. Verified current Hermes docs say it does. MCP therefore earns its place as the least-invasive integration: this repo ships a stdio server, Hermes supervises it, and no Hermes source/profile plugin needs to be edited. The core functions remain direct-import clean, so the decision is reversible.

## Not implemented yet

- Radar-range/multi-station live X4 trade reads.
- Live X4 faction-state and sector-object Lua reads.
- Direct request/response pipe-backed `TelemetryFetcher` support beyond ambient, ship-status, and docked-station trade.
- Reflex STT/TTS path.
- Hermes memory feed for reflex Q/A.
- Mutating actions (`set_waypoint`, `mark_target`).

Those are intentionally not faked.
