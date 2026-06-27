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

## v0.2 — live X4 pipe and telemetry reads

- Install `sn_mod_support_apis` in X4.
- Copy or symlink `extension/x4_llm_copilot` into the X4 `extensions/` folder.
- Run `x4-copilot serve-pipe --pipe x4_llm_copilot` with pywin32 installed.
- Validate exact MD `Named_Pipes.*` call shapes in the live X4 debug log.
- Add the first real read/write ping through X4, then telemetry reads for player sector/position, ship status, trade offers, sector objects, and faction relation snapshots.

## v0.3 — brain integration

- Keep protocol stable.
- Add Pantella/Mantella bridge after license review.
- Prefer Pantella-style interface module for heavy X4 customization; emulate Mantella's declarative action schema for safe commands.

## v0.4 — voice and safe actions

- Add STT/TTS front-end.
- Add explicit opt-in `set_waypoint` / `mark_target` actions.
- Keep combat/autopilot automation out of scope unless deliberately re-scoped.
