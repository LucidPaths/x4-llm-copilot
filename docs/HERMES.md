# Hermes Integration

Status: implemented as a read-only tool surface plus optional stdio MCP wrapper. Ambient/ship-status have verified runtime on-demand named-pipe fetches. Trade has verified live `docked_station` (`trade_offers_probe_v1`) and bounded `radar_range` (`trade_offers_radar_v1`) scopes with normalized observed offer fields plus raw preservation. Faction state has a raw-first live pipe reader (`faction_state_v1`) for player↔faction standings and diplomacy/event operations. Sector objects now use live `sector_objects_v1` reads for bounded stations/gates/notable ships with per-object distance; collectable/wreck enumeration remains a documented widening seam.

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

Verified runtime live pipe reads currently cover ambient context, ship status via the ambient payload, docked/radar-range trade offers, faction-state reads, and bounded sector-object reads for stations, gates, and notable ships.

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
uv run --extra winpipe x4-copilot tool objects --source live-pipe
uv run --extra winpipe x4-copilot tool objects --source live-pipe --kinds station,gate
```

`tool trade --source live-pipe --scope docked_station` reads the trade container the player ship is currently docked at. `--scope radar_range` enumerates known in-sector stations that are radar-visible or within the player ship radar radius, reads each station via the same `GetTradeList(station, ship)` path, and tags each normalized offer with station identity plus distance.

The docked-station reader sends `intent:"trade_in_sector"`; Lua emits `schema:"trade_offers_probe_v1"` with `offers_raw` / `nontrade_offers_raw`; Python maps observed fields (`ware`, `name`, `side`, `price`, `market_price`, `amount`, `station`, `faction`) while keeping the full raw offer under `raw`. Radar-range emits `schema:"trade_offers_radar_v1"`, `source:"x4_lua_live_pipe"`, `stations_raw[]`, and per-offer `distance_m`/`distance_km`; Python normalizes each station offer through the same `_normalize_trade_offer` path and preserves both offer raw and station raw.

The faction-state reader sends `intent:"faction_state"`; Lua emits `schema:"faction_state_v1"`, `source:"x4_lua_live_pipe"`, `standings_raw[]`, and `events_raw[]`. Standings use the game's UI relation integer (`GetUIRelation(faction.id)`, expected -30..+30), faction id/name/shortname from `GetLibrary("factions")`/`GetFactionData`, rank/title evidence from held ceremony licences when present, and preserve each raw standing. Python derives the current `rank_title` from the standing threshold (`ceremonyfriend` at +10, `ceremonyally` at +20) and keeps Lua's raw licence-order guess as `rank_title_raw`. Live VIG validation after promotion showed why: `standing:10`/`Friend` with `ceremonyfriend:Syndicate Enforcer` and also `ceremonyally:Capo`; the raw last-rank guess can say `Capo` even though the standing-gated current rank is `Syndicate Enforcer`. Events use diplomacy event definitions/operations where available and preserve full raw event operations. Python normalizes to per-faction `faction_standing` rows and event rows (`relation_change`/`combat`/`promotion`/`territory`/`diplomacy`) while keeping `raw`.

The sector-objects reader sends `intent:"sector_objects"`; Lua emits `schema:"sector_objects_v1"`, `source:"x4_lua_live_pipe"`, and `objects_raw[]`. It reuses the radar trade station enumeration (`GetContainedStations(sector, true)`) for stations and uses verified live APIs `GetGates(sector)` plus `GetContainedShips(sector, true)` for gates and notable ships. Each included object must have a distance from the player ship position (`distance_m`, normalized `dist_km`), id/name/type/class, optional owner/faction/idcode, and full `raw`. `kinds` is load-bearing: `--kinds station,gate` is forwarded in the fetch request and filtered in Lua, then defensively re-filtered in Python. Payload discipline: total cap 160 objects; per-kind caps are station 64, gate 16, ship 40, collectable 32, wreck 32; generic mass traffic/docked ships are skipped. Runtime mode still requires a direct fresh `trigger:"fetch_response"`; timeout/transport failure raises an error and never falls back to JSONL. Collectable/wreck widening is still pending an observed live API; `GetContainedObjects` is not available as a global in this Lua environment.

### v0.3 cockpit chat bridge

Status: verified live round-trip from X4 chat to Hermes/Python and back to X4. A running game emitted `/hermes` chat requests as `chat_request` messages with correlation ids, the persistent bridge fetched live telemetry when needed, generated `chat_response` messages, and X4's continuous read loop rendered the matching responses back in the cockpit chat window. This is a real live connection, not fixture replay.

The first cockpit UI slice uses SirNukes `Chat_Window_API`, not `Simple_Menu_API`: Simple Menu can host edit boxes, but X4's chat window already provides the cockpit text input hotkey, scrollback, and print surface with less custom UI risk. The command is registered as `/hermes` and used as `/hermes <question>` in the in-game chat window; slash commands are consumed by the command layer and do not echo as normal chat, so the bridge prints its own `You [id]` / `thinking...` feedback when the callback fires. This is text-in/text-out only. It does not call `set_waypoint`, `mark_target`, autopilot, or any game mutation.

Protocol on the same `x4_llm_copilot` pipe now has two request classes plus probes:

- Python-originated telemetry fetch: `{"type":"fetch", ...}` -> Lua `telemetry_raw` with `trigger:"fetch_response"`.
- X4-originated cockpit chat: `{"type":"chat_request","id":"x4chat-N","text":"..."}` -> Python/Hermes -> `{"type":"chat_response","id":"x4chat-N","text":"..."}`.
- Development probes/reload telemetry remain allowed but cannot satisfy a runtime fetch.

Run the persistent chat bridge, not the one-shot `tool --source live-pipe` owner, when using cockpit chat:

```bash
uv run --extra winpipe x4-copilot serve-chat --pipe x4_llm_copilot --fetch-timeout 8 --chat-timeout 90
```

The bridge owns the single named-pipe server, serializes bridge-owned telemetry fetches, and routes chat responses by correlation id. A slow Hermes answer happens after the live telemetry snapshot is fetched, so it does not hold the telemetry fetch lock. If Python/Hermes is absent, the cockpit-side pending id times out and prints an explicit error instead of replaying an old answer.

Chat routing is intentionally live but bounded:

- `/hermes ambient_context` is a help/capability probe. It answers directly with the telemetry categories the bridge can read and does not consume the pipe with a fetch.
- Known scoped questions fetch their matching live intent: trade questions -> `trade_in_sector`; ship/status questions -> `ship_status`; faction/politics questions -> `faction_state`; nearby/station/gate/object questions -> `sector_objects`.
- Unknown natural chat, such as `/hermes hallo`, performs a cheap live `ambient_context` fetch before answering, so smalltalk can still reference verified current sector/ship/credits/cargo instead of inventing state.
- All chat answers remain text-only. The bridge does not mutate X4 state, set waypoints, mark targets, autopilot, or run combat actions.

Player feedback is explicit across the 90s Hermes timeout window:

- Lua prints `You [x4chat-N]: ...` immediately so the command never disappears silently.
- Lua immediately prints `Hermes [x4chat-N]: received; sending to bridge...` and `Hermes [x4chat-N]: waiting for live telemetry/Hermes...` before the named-pipe write completes.
- MD raises still-pending notices at 15s, 45s, and 75s while the correlation id remains pending, then the existing 90s timeout prints a fail-closed error if no final `chat_response` arrived.
- Python normalizes outbound chat text to ASCII-safe punctuation before writing `chat_response` JSON, avoiding visible mojibake from curly apostrophes/dashes in X4's chat renderer.

Pipe ownership rule: this live mode creates the named-pipe server for `x4_llm_copilot`. Do **not** also run `x4-copilot serve-pipe --pipe x4_llm_copilot`, `x4-copilot serve-chat --pipe x4_llm_copilot`, a one-shot live-pipe tool call, or another live fetcher on the same pipe name at the same time; only one server can own that pipe.

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

Docked/radar-range trade, faction state, and sector objects are available through runtime live pipe mode. The mixed live/mock surface reports provenance per result, so Hermes can see which tools are real.

## Why MCP over direct Hermes tool now?

The handoff's risk was that Hermes might not consume MCP. Verified current Hermes docs say it does. MCP therefore earns its place as the least-invasive integration: this repo ships a stdio server, Hermes supervises it, and no Hermes source/profile plugin needs to be edited. The core functions remain direct-import clean, so the decision is reversible.

## Not implemented yet

- Wider live validation across sectors plus a verified live collectable/wreck enumeration API.
- Expanded rank derivation beyond standard faction ceremony licence tiers.
- Reflex STT/TTS path.
- Hermes memory feed for reflex Q/A.
- Mutating actions (`set_waypoint`, `mark_target`).

Those are intentionally not faked.
