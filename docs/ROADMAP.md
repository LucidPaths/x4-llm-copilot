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
- Added the original fixture-backed faction-state extraction; live normalization later replaced the speculative dual-shape tolerance with the observed `faction_state_v1` raw shape.
- Added action stubs that refuse by default and never mutate game state.
- Added committed MCP SDK/client-path tests and CI smoke coverage.
- Documented the initial hard boundary: Hermes path was mock-backed until live Lua/MD telemetry reads were validated.

## v0.2 — live X4 pipe and telemetry reads — partial

- Installed `sn_mod_support_apis` in X4.
- Copied `extension/x4_llm_copilot` into the X4 `extensions/` folder.
- Ran `x4-copilot serve-pipe --pipe x4_llm_copilot` with pywin32 installed for raw-log capture.
- Validated exact MD `Named_Pipes.*` call shapes in the live X4 debug log, including the key correction that Lua `AddUITriggeredEvent(..., payload)` arrives in MD as `event.param3`.
- Verified ping/pong through X4 and captured live Lua ambient payloads: sector, player money, occupied ship, hull percent, shield percent, and raw cargo shape.
- Verified `ambient_probe_v2` cargo shape: empty hold can be `[]`; non-empty hold is an object mapping ware ID to quantity, e.g. `{"water": 6}`.
- Added `RawTelemetryLogFetcher` and MCP/CLI wiring for live raw ambient/ship-status reads from `var/live_telemetry_raw.jsonl`; this is now documented as development/debug replay only.
- Added runtime on-demand live pipe mode (`--source live-pipe` / `X4_COPILOT_TELEMETRY_SOURCE=live_pipe`) that sends a `fetch` request, requires a fresh `trigger:"fetch_response"`, stamps `source:"x4_lua_live_pipe"`, and fails closed instead of replaying stale JSONL. The fetch-response wait has a wall-clock deadline, so reload-probe churn cannot livelock the call.
- Added raw-first live trade probe routing for `scope:"docked_station"`: MD passes the fetch JSON into Lua, Lua branches on `intent:"trade_in_sector"`, emits `schema:"trade_offers_probe_v1"`, and Python preserves `offers_raw` / `nontrade_offers_raw`. Live bytes at `VIG Ice Refinery I` confirmed a list of offer objects; Python now normalizes observed fields while preserving each full raw offer under `raw`.
- Verified live on-demand ambient smoke from the running game: `uv run --extra winpipe x4-copilot tool ambient --source live-pipe --timeout 60` returned `source:"x4_lua_live_pipe"`, `stale:false`, sector `Windfall I Union Summit`, credits `39482`, ship `Raleigh (Container)`.
- Added a delayed MD retry cue for startup/read-loop errors so missing pipe servers do not permanently dead-end the request loop. Current live caveat: after several reload/retry cycles, duplicate retry loop instances can still exist until the game is restarted; the live fetch path works regardless.
- Added bounded `scope:"radar_range"` multi-station trade reads (`trade_offers_radar_v1`): Lua enumerates known in-sector stations, filters to radar-visible/within player radar radius, caps at 32 stations / 20 offers per station / 200 offers total, emits station distance in meters and km, and Python normalizes offers through the existing trade-offer mapper while preserving raw station/offer payloads.
- Added raw-first live faction-state reads (`faction_state_v1`): Lua captures `GetUIRelation` player↔faction standings plus faction ids/names/rank licence evidence and diplomacy event operations; Python normalizes observed standings/events while preserving raw payloads.
- Reconciled faction-rank normalization against live VIG data: rank is resolved by standing-gated ceremony-licence type (`ceremonyfriend` at standing >= 10, `ceremonyally` at standing >= 20), not last-licence-wins. Raw licence lists can be over-broad (VIG returned both `Syndicate Enforcer` and `Capo`), so standing is the disambiguator while raw licence evidence remains preserved.
- Preserved the faction-event boundary honestly: diplomacy event APIs returned empty in live smoke, no synthetic events are fabricated, and the event normalizer is ready for a verified event source when one is found.
- Added raw-first live sector-object reads (`sector_objects_v1`): Lua enumerates verified stations/gates/ships, applies kinds filtering through `_canonical_sector_kind`, emits required `dist_km`, and Python preserves raw object payloads while normalizing observed object fields.
- Remaining in v0.2: collectable/wreck enumeration and duplicate idle retry-loop cleanup. `GetContainedObjects` is not a Lua global and was removed; collectables/wrecks need a verified live API before being claimed.
- Known issue: `test_live_pipe_fetcher_wall_clock_timeout_on_probe_churn` is timing-flaky on slow hardware. The fail-closed behavior is correct, but the test over-specifies which timeout message fires; fix it to assert raised fail-closed behavior, not the exact message string.
- Future density optimization: radar-range normalized offers currently preserve each offer's raw object and duplicate the containing `station_raw` block per offer. Keep this while capped payloads are small; if dense-sector payload/context weight becomes a problem, split output into a deduped `stations[]` block and have offers reference a station index.

## v0.3 — in-game cockpit UI

- Build the in-game cockpit chatbox round-trip: player prompt in X4 UI -> Hermes/tool request -> response rendered back in the X4 cockpit UI. First slice uses SirNukes `Chat_Window_API` (`/hermes <question>`) because it already provides in-game hotkey-opened text input and scrollback; Simple Menu editboxes remain a fallback if a dedicated panel becomes necessary. Status: live X4 -> Hermes/Python -> X4 round-trip is verified with correlated `chat_request`/`chat_response` ids and real live telemetry fetches when needed.
- Keep the display layer separate from tool semantics: the UI should render verified telemetry/tool results and fail-closed errors, not invent game state.
- Preserve async correlation: every `chat_request`/`chat_response` carries an id; dispatch acknowledgement, still-working notices, and timeout/error states never replay stale answers or leave the chat looking frozen during the 90s Hermes timeout window.
- Current chat routing: exact `/hermes ambient_context` returns capability help without a telemetry fetch; scoped questions fetch their matching live telemetry intent; unscoped natural chat performs a cheap live `ambient_context` fetch so answers can reference verified current state. Outbound chat text normalizes display-risk punctuation for X4 while preserving UTF-8 proper nouns/accented letters at the pipe boundary.
- Ambient/unprompted Hermes display is a derivative of this v0.3 UI path: once the cockpit display can render prompted responses, it can also render bounded ambient notices.

## v0.4 — brain integration

- Keep protocol stable.
- Add Pantella/Mantella bridge after license review.
- Prefer Pantella-style interface module for heavy X4 customization; emulate Mantella's declarative action schema for safe commands.

## v0.5 — voice and safe actions

- Add STT/TTS front-end as a layer on top of the v0.3 in-game chatbox round-trip, not as a replacement for it.
- Add explicit opt-in `set_waypoint` / `mark_target` actions.
- Keep combat/autopilot automation out of scope unless deliberately re-scoped.
