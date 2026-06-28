# Save-scoped cockpit sessions

Status: design decision / implementation target. The current live bridge is verified for text-in/text-out cockpit chat with fresh telemetry, but the real persistent-session layer described here is not implemented yet.

## Problem

Cockpit chat must behave like a real ongoing conversation, not a sequence of unrelated one-shot questions. Follow-ups such as "what about the other 7?" need prior dialogue. At the same time, X4 state must not pollute the operator's normal Hermes CLI/TUI/gateway sessions or default Hermes memory.

The session boundary is therefore **the X4 save**, not the Hermes app.

## Non-negotiable boundaries

- X4 cockpit conversation state is product data for this mod, not Hermes operator memory.
- Do not write X4 chat history, faction notes, trade observations, player locations, or save-derived continuity into the default Hermes profile.
- Do not mix multiple X4 saves into one chat history. A session attached to Save A must not answer from Save B's continuity.
- Use conversation memory for references and intent only. Fresh live telemetry is the only source of current game state.
- Keep the v0 surface read-only: text answers, no waypoints, no target marks, no autopilot/combat automation.

## Architecture decision

Use an **X4-owned session store** keyed by a save scope, with Hermes used as the reasoning engine for each turn.

```text
X4 save / running game
  -> live telemetry snapshot
  -> save-scope resolver
  -> X4-owned cockpit session store
  -> isolated Hermes invocation
  -> chat_response back to X4
```

This deliberately avoids relying on the operator's default Hermes `state.db` or default memory files as the source of truth for cockpit continuity.

## Where state lives

Preferred local state root:

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

Repo-local `var/` remains for development logs and smoke evidence. Runtime save memory should live under `%LOCALAPPDATA%/x4-llm-copilot`, not inside the git checkout and not inside the default Hermes home.

If Hermes persistent sessions are used directly, run them under an isolated `HERMES_HOME` rooted in this app state directory. A dedicated Hermes profile is only acceptable as an explicit advanced configuration, and it must still be visibly labelled as X4 Copilot state. The hard requirement is isolation from the user's normal Hermes CLI/TUI/gateway sessions.

## Save-scope identity

The bridge needs a stable `save_scope_id` before it can choose the right session. Resolution order:

1. **Verified explicit save id** from a live X4/Lua/MD API, if one is found and smoke-tested.
2. **User-selected save slot/profile hint** supplied through bridge config or CLI when explicit save identity is unavailable.
3. **Derived universe fingerprint** as a fallback, composed from stable live facts such as player id, player name if available, game start time, known headquarters/player-owned object ids, and current save/load metadata if exposed.

The fallback fingerprint must be labelled `confidence:"derived"` and should be treated as provisional. Do not silently merge histories when the resolver is uncertain; ask the cockpit user to select or confirm a save scope.

Recommended envelope:

```json
{
  "save_scope_id": "x4save_<stable-or-derived-hash>",
  "confidence": "explicit|configured|derived",
  "evidence": {
    "source": "x4_lua_live_pipe",
    "fields": ["..."]
  }
}
```

## Turn model

Every `/hermes` cockpit turn should build a prompt from three separate layers:

1. **Conversation context**: recent turns and a compact save-scoped summary from the X4 session store.
2. **Fresh telemetry**: current live pipe payloads fetched for this turn.
3. **System boundary**: read-only cockpit advisor rules, provenance, and refusal rules.

Fresh telemetry wins over remembered state. If the transcript says the player had 39,482 credits earlier but the latest live snapshot says 41,000, answer from 41,000 and optionally mention that it changed.

## Hermes isolation options

### Option A — X4-owned transcript + Hermes one-shot reasoning

The bridge stores transcript/summary/facts itself, then invokes Hermes one-shot with the current turn package.

Pros:
- No pollution of Hermes app sessions.
- Save-scope routing is fully explicit and testable.
- Easy to prune/export/delete per save.

Cons:
- Requires this repo to implement summarization/compaction and transcript management.

### Option B — isolated Hermes home per app or save

The bridge invokes Hermes with an isolated app-owned `HERMES_HOME` and a deterministic session name per save.

Example shape:

```bash
HERMES_HOME=%LOCALAPPDATA%/x4-llm-copilot/hermes-home \
hermes chat --continue x4-save-<save_scope_id> --source x4-cockpit --toolsets "" -q "..."
```

Pros:
- Uses Hermes' native session machinery.
- Lower initial implementation cost.

Cons:
- Still needs careful `HERMES_HOME` isolation.
- Session naming and pruning must be owned by this repo.
- Must prevent accidental fallback to the default Hermes home.

### Recommended path

Start with Option A for correctness: X4 owns save-scoped transcripts and summaries, Hermes is a stateless reasoning process over an explicit context packet. Once the save-scope resolver is proven, Option B can be added as an optimization if native Hermes session continuation is worth it.

## Memory types

Keep these separate:

- `transcript.jsonl`: raw cockpit turns for the save.
- `summary.md`: compact natural-language continuity for references and player preferences inside that save.
- `facts.json`: structured, low-cardinality durable facts discovered in that save, with provenance and timestamps.
- `telemetry_cache.json`: optional last snapshots for debugging only; never current truth.

Do not store raw high-volume telemetry indefinitely. Summarize or discard it unless it is needed as audit evidence.

## Reset / lifecycle commands

Required user-visible controls:

- `/hermes session` — show current save scope, confidence, transcript path, and last turn time.
- `/hermes reset` — clear the current save-scoped cockpit conversation after confirmation.
- `/hermes save <name>` — manually bind/rename the current save scope when automatic identity is ambiguous.
- `/hermes export` — write a portable transcript/summary bundle for the current save.

## Test requirements

Implementation is not done until tests prove:

- Two fake save scopes do not share transcript or summary state.
- A follow-up question in the same save can resolve a previous answer.
- A follow-up question after switching save scope cannot see the prior save's context.
- Fresh telemetry overrides remembered facts.
- Missing/ambiguous save identity fails closed with an explicit cockpit message.
- The bridge never writes X4 memory into the default Hermes home during tests.

## Current gap

The current bridge invokes Hermes with `--source x4-cockpit` and an already-fetched telemetry snapshot, but it does not yet persist a real save-scoped conversation store. Until this document's architecture is implemented, cockpit chat should be described as live telemetry Q/A with bounded routing and deterministic fallbacks, not as durable save memory.
