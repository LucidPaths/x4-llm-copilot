# Roadmap

## v0.1 — executable adapter spine — done

- Python package scaffold.
- Deterministic intent router for the four initial fetch classes.
- Validated telemetry payload model.
- Grounded no-fabrication advisor fallback.
- OpenAI-compatible model wrapper with deterministic fallback.
- Windows named-pipe server transport compatible with SirNukes' client model.
- X4 extension skeleton that declares the dependency and documents MD pipe calls.
- Tests and sample telemetry payloads.

## v0.2 — live X4 telemetry reads

- Install `sn_mod_support_apis` in X4.
- Copy or symlink `extension/x4_llm_copilot` into the X4 `extensions/` folder.
- Run `x4-copilot serve-pipe --pipe x4_llm_copilot` with pywin32 installed.
- Replace skeleton MD comments with actual Lua/MD reads for player sector/position, ship status, trade offers, sector objects, and faction relation snapshots.

## v0.3 — brain integration

- Keep protocol stable.
- Add Pantella/Mantella bridge after license review.
- Prefer Pantella-style interface module for heavy X4 customization; emulate Mantella's declarative action schema for safe commands.

## v0.4 — voice and safe actions

- Add STT/TTS front-end.
- Add explicit opt-in `set_waypoint` / `mark_target` actions.
- Keep combat/autopilot automation out of scope unless deliberately re-scoped.
