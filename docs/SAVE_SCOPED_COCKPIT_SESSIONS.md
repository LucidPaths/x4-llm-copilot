# Save-scoped cockpit sessions

Status: implemented for the cockpit bridge's local session layer. The bridge now resolves a save scope, stores per-save transcripts/summaries/facts, passes save-scoped context into Hermes, and runs Hermes under an app-owned isolated `HERMES_HOME`. A verified explicit X4 save-id API is still future work; until then, the bridge supports explicit `save_scope_id` fields, `--save-scope`, `/hermes save <name>`, and labelled derived scopes from live telemetry.

## Problem

Cockpit chat must behave like a real ongoing conversation, not a sequence of unrelated one-shot questions. Follow-ups such as "what about the other 7?" need prior dialogue. At the same time, X4 state must not pollute the operator's normal Hermes CLI/TUI/gateway sessions or default Hermes memory.

The session boundary is therefore **the X4 save**, not the Hermes app.

## Non-negotiable boundaries

- X4 cockpit conversation state is product data for this mod, not Hermes operator memory.
- Do not write X4 chat history, faction notes, trade observations, player locations, or save-derived continuity into the default Hermes profile.
- Do not mix multiple X4 saves into one chat history. A session attached to Save A must not answer from Save B's continuity.
- Use conversation memory for references and intent only. Fresh live telemetry is the only source of current game state.
- Keep the v0 surface read-only: text answers, no waypoints, no target marks, no autopilot/combat automation.

## Implemented architecture

The bridge uses an **X4-owned session store** keyed by a save scope, with Hermes used as the reasoning engine for each turn.

```text
X4 save / running game
  -> live telemetry snapshot
  -> save-scope resolver
  -> X4-owned cockpit session store
  -> isolated Hermes invocation
  -> chat_response back to X4
```

This deliberately avoids relying on the operator's default Hermes `state.db` or default memory files as the source of truth for cockpit continuity.

Code:

- `src/x4_copilot/cockpit_session.py` — `SaveScopeResolver`, `CockpitSessionStore`, default app-state root.
- `src/x4_copilot/chat_bridge.py` — resolves scope per turn, injects session context into `HermesAgentResponder`, appends completed turns, implements session/reset/export/save commands.
- `tests/test_chat_bridge.py` — isolation and no-default-Hermes-home regression coverage.

## Where state lives

Default local state root:

```text
%LOCALAPPDATA%/x4-llm-copilot/
  sessions/
    <save_scope_id>/
      transcript.jsonl
      summary.md
      facts.json
      telemetry_cache.json
  hermes-home/
    state.db
    config.yaml
    .env        # optional, ignored; no committed secrets
```

Override with:

```bash
X4_COPILOT_STATE_HOME=C:/path/to/state
# or
uv run --extra winpipe x4-copilot serve-chat --state-root C:/path/to/state
```

Repo-local `var/` remains for development logs and smoke evidence. Runtime save memory lives under the app state root, not inside the git checkout and not inside the default Hermes home.

If Hermes persistent sessions are used directly, the bridge runs them under `HERMES_HOME=<state-root>/hermes-home` and names them `x4-save-<save_scope_id>`. The hard requirement is isolation from the user's normal Hermes CLI/TUI/gateway sessions.

## Save-scope identity

The bridge needs a stable `save_scope_id` before it can choose the right session. Resolution order:

1. **Explicit save id** in the `chat_request` envelope: `save_scope_id`, `save_id`, `save_name`, `save`, or the same keys under `meta`.
2. **Configured save binding** from `--save-scope`, `X4_COPILOT_SAVE_SCOPE`, or the in-cockpit `/hermes save <name>` command.
3. **Derived universe fingerprint** from live telemetry fields currently available to the bridge (`sector`, `ship`, and whether credits are present).

The fallback fingerprint is labelled `confidence:"derived"` and is provisional. It is not a unique save identity: two saves in the same sector with the same occupied ship and credits-presence can collide and share a transcript. For parallel or similar saves, prefer explicit `save_scope_id`, `--save-scope`, or `/hermes save <name>` binding over trusting the fingerprint. Once a verified X4 save-id API is found, it should become the primary source and the derived fallback should remain only for development or emergency continuity.

Scope envelope:

```json
{
  "save_scope_id": "save-alpha",
  "confidence": "explicit|configured|derived",
  "evidence": {
    "source": "chat_request|bridge_config|chat_command|derived_from_live_telemetry"
  }
}
```

## Turn model

Every `/hermes` cockpit turn builds a prompt from three separate layers:

1. **Conversation context**: recent turns and a compact save-scoped summary from the X4 session store.
2. **Fresh telemetry**: current live pipe payloads fetched for this turn.
3. **System boundary**: read-only cockpit advisor rules, provenance, and refusal rules.

Fresh telemetry wins over remembered state. If the transcript says the player had 39,482 credits earlier but the latest live snapshot says 41,000, answer from 41,000 and optionally mention that it changed.

## Session commands

The cockpit bridge recognizes these text commands after `/hermes`:

- `/hermes session` — show current save scope, confidence, transcript path, and turn count.
- `/hermes reset` — clear the current save-scoped cockpit conversation.
- `/hermes save <name>` — bind subsequent unlabelled turns to a configured save scope.
- `/hermes export` — print the current save-scope transcript path.

All commands still go through a live ambient fetch first so they can resolve the same save-scope path as normal turns. If `allow_derived_save_scope` is disabled and no explicit/configured scope exists, the bridge fails closed with a cockpit error.

## Memory types

Keep these separate:

- `transcript.jsonl`: raw cockpit turns for the save.
- `summary.md`: compact natural-language continuity for references and player preferences inside that save.
- `facts.json`: structured, low-cardinality durable facts discovered in that save, with provenance and timestamps.
- `telemetry_cache.json`: reserved for optional last snapshots for debugging only; never current truth.

Do not store raw high-volume telemetry indefinitely. Summarize or discard it unless it is needed as audit evidence.

## Tests proving the mechanism

Implemented tests cover:

- Two fake save scopes do not share transcript or summary state.
- A follow-up question in the same save sees the prior save-scoped turn.
- Fresh telemetry is passed into the responder for the current turn and can override prior memory.
- Hermes is invoked with app-owned isolated `HERMES_HOME`, not the ambient/default Hermes home.
- Missing/ambiguous save identity fails closed when derived scopes are disabled.
- `/hermes session` reports the save-scoped transcript path under the configured state root.

## Remaining live-game gap

The bridge can consume an explicit save id if X4 provides one, but the mod has not yet verified a live X4 API that exposes the actual save file identity. Until that exists, use `--save-scope` or `/hermes save <name>` for clean per-save binding; derived scopes are useful but can change if their telemetry basis changes.
