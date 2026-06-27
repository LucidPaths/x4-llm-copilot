# Hermes Integration

Status: implemented as a mock-backed, read-only tool surface plus optional stdio MCP wrapper. Live X4 telemetry is still blocked on the unimplemented Lua/MD read path.

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
- `fetch_trade_offers(radar_only=True, sector=None)`
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

The tool surface currently uses `MockTelemetryFetcher` and fixture files in `examples/`:

- `ambient_context_payload.json`
- `trade_payload.json`
- `ship_status_payload.json`
- `faction_state_payload.json`
- `sector_objects_payload.json`

Every mock result is marked via structured provenance (`FetchProvenance(source="mock", stale=True)`) and surfaced as `source: "mock"` / `stale: true`. The tool layer does **not** parse `as_of` text to infer provenance. Real game data requires replacing the mock fetcher with the live pipe-backed fetcher after the Lua/MD telemetry reader exists.

## Why MCP over direct Hermes tool now?

The handoff's risk was that Hermes might not consume MCP. Verified current Hermes docs say it does. MCP therefore earns its place as the least-invasive integration: this repo ships a stdio server, Hermes supervises it, and no Hermes source/profile plugin needs to be edited. The core functions remain direct-import clean, so the decision is reversible.

## Not implemented yet

- Live X4 Lua/MD telemetry reads.
- Pipe-backed `TelemetryFetcher` for the tool surface.
- Reflex STT/TTS path.
- Hermes memory feed for reflex Q/A.
- Mutating actions (`set_waypoint`, `mark_target`).

Those are intentionally not faked.
