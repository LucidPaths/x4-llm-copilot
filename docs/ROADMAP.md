# Roadmap

## v0.1 — executable adapter spine — done

- Python package scaffold.
- Tokenized deterministic intent router for the four initial fetch classes.
- Validated telemetry payload model.
- Grounded no-fabrication advisor fallback.
- Ollama Cloud and generic OpenAI-compatible provider wrappers with deterministic fallback.
- Windows named-pipe server transport with session-level reconnect/recreate behavior for pipe breaks.
- X4 extension packaging skeleton (`content.xml` + MD load-log cue only; no unvalidated Lua/UI hooks).
- Tests and sample telemetry payloads.

## v0.1.1 — Hermes integration scaffold — done

- Verified Hermes has a native MCP client for stdio servers.
- Added `x4_copilot.tools`: a model-free, read-only-default tool surface over `TelemetryFetcher`.
- Added mock fixtures for all read intents, including dedicated `ambient_context`.
- Added `x4-copilot-mcp`: optional stdio MCP wrapper for Hermes and other MCP clients.
- Added structured provenance (`source`, `stale`) without parsing `as_of` display text.
- Added flexible faction-state extraction for nested fixture and itemized live-shaped payloads.
- Added action stubs that refuse by default and never mutate game state.
- Added committed MCP SDK/client-path tests and CI smoke coverage.
- Documented the hard boundary: Hermes path is mock-backed until the live Lua/MD telemetry reader exists.

## v0.2 — live X4 pipe and telemetry reads — partial

- Installed `sn_mod_support_apis` in X4.
- Copied `extension/x4_llm_copilot` into the X4 `extensions/` folder.
- Ran `x4-copilot serve-pipe --pipe x4_llm_copilot` with pywin32 installed for raw-log capture.
- Validated exact MD `Named_Pipes.*` call shapes in the live X4 debug log, including the key correction that Lua `AddUITriggeredEvent(..., payload)` arrives in MD as `event.param3`.
- Verified ping/pong through X4 and captured live Lua ambient payloads: sector, player money, occupied ship, hull percent, shield percent, and raw cargo shape.
- Verified `ambient_probe_v2` cargo shape: empty hold can be `[]`; non-empty hold is an object mapping ware ID to quantity, e.g. `{"water": 6}`.
- Added `RawTelemetryLogFetcher` and MCP/CLI wiring for live raw ambient/ship-status reads from `var/live_telemetry_raw.jsonl`; this is now documented as development/debug replay only.
- Added runtime on-demand live pipe mode (`--source live-pipe` / `X4_COPILOT_TELEMETRY_SOURCE=live_pipe`) that sends a `fetch` request, requires a fresh `trigger:"fetch_response"`, stamps `source:"x4_lua_live_pipe"`, and fails closed instead of replaying stale JSONL. The fetch-response wait has a wall-clock deadline, so reload-probe churn cannot livelock the call.
- Added raw-first live trade probe routing: MD passes the fetch JSON into Lua, Lua branches on `intent:"trade_in_sector"`, emits `schema:"trade_offers_probe_v1"`, and Python preserves `offers_raw` / `nontrade_offers_raw`. Live bytes at `VIG Ice Refinery I` confirmed a list of offer objects; Python now normalizes observed fields while preserving each full raw offer under `raw`.
- Verified live on-demand ambient smoke from the running game: `uv run --extra winpipe x4-copilot tool ambient --source live-pipe --timeout 60` returned `source:"x4_lua_live_pipe"`, `stale:false`, sector `Windfall I Union Summit`, credits `39482`, ship `Raleigh (Container)`.
- Added a delayed MD retry cue for startup/read-loop errors so missing pipe servers do not permanently dead-end the request loop. Current live caveat: after several reload/retry cycles, duplicate retry loop instances can still exist until the game is restarted; the live fetch path works regardless.
- Remaining in v0.2: broader trade-shape validation across more stations, sector objects, faction relation snapshots, and cleanup of duplicate idle retry loop instances.

## v0.3 — brain integration

- Keep protocol stable.
- Add Pantella/Mantella bridge after license review.
- Prefer Pantella-style interface module for heavy X4 customization; emulate Mantella's declarative action schema for safe commands.

## v0.4 — voice and safe actions

- Add STT/TTS front-end.
- Add explicit opt-in `set_waypoint` / `mark_target` actions.
- Keep combat/autopilot automation out of scope unless deliberately re-scoped.
