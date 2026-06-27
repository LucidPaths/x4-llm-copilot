# X4: Foundations — LLM Co-Pilot / Ship AI
## Build Specification & Standard Operating Procedure

| | |
|---|---|
| **Status** | Draft v2 — design locked, implementation not started |
| **Purpose** | Seed context for a Claude Code project. Self-contained: a cold reader (human or agent) should be able to build from this without the originating conversation. |
| **Owner** | (you) |
| **How to use** | Read top-to-bottom once. Sections 1–3 = orientation. Section 4 = the binding decisions and their reasoning. Section 5–7 = contracts and procedure. Build against Section 7 phases; each has an explicit "Done when". |

---

## 1. Problem statement

X4: Foundations is a deep space-sim sandbox with a famously obtuse UI: critical information (trade prices, faction war state, where loot is, what a target is) is buried across menus, the map, and HUD panels. The player wants an **in-game, voice-driven advisor** — a "ship computer" — that answers natural-language queries against *live* game state:

- "What are goods selling for in this system?"
- "Where's the war right now / who's losing?"
- "Best trade run from where I'm sitting?"
- "What's that ship I'm targeting?"

**This is an advisor, not an autopilot.** It reads state and talks. Issuing game commands ("set a waypoint") is an optional later phase, deliberately separated so the read-only advisor can ship and be useful on its own.

### Why this is tractable
The per-query workload is light: classify intent → fetch a *scoped* slice of game state → format it through a system prompt into a sentence or two. There is almost no heavy reasoning in the common path. That single fact drives most of the architecture below (cheap models suffice; latency budget is the real constraint, not model capability).

### Scope / non-goals
- **In scope:** read-only telemetry Q&A, voice I/O, a "political news" deltas feed, optional safe command-issuing.
- **Non-goals:** the AI playing the game, combat automation, anything that reduces the player's agency. Also out of scope: cross-platform — this is Windows-only by dependency (see 4.2).

---

## 2. Background / why the design is shaped this way

X4 is heavily moddable but its scripting (Lua + the "MD" Mission Director XML layer) is **sandboxed**: mods cannot do arbitrary network or file I/O. That single constraint is why a naive "mod calls an LLM API directly" approach is impossible, and why the whole thing is structured as **game ⇄ pipe ⇄ external process ⇄ LLM**. Everything downstream follows from "how do we get live state *out* of the sandbox cheaply."

A community proof-of-concept already exists (an alpha "AI conversation" mod demoed on the Egosoft/Steam forums) confirming feasibility. It is **not open-source / not a fork target** — treat it as existence proof only. The reusable foundation is **Mantella**, the mature open-source Skyrim/Fallout LLM-NPC framework it was modeled on (see 4.3).

---

## 3. Architecture overview

Two cleanly separated halves: a **reusable brain** and a **bespoke game adapter**. The split matters because the brain is a solved problem (don't rebuild it) and only the adapter is X4-specific (that's where the work is).

```
 [mic] ── STT ──► intent router ──► (named pipe) ──► X4 adapter (Lua/MD)
                                                          │ reads live state
        ambient context + scoped data (labeled, units)   ▼
                          ◄──────────── batched payload ──┘
                              │
                  system prompt + tiered LLM (cloud/2nd box)
                              │
                   text ── TTS ──► [speakers]
                              │
        (optional) Action ──► (named pipe) ──► X4 adapter ──► game command
```

- **Brain (reuse):** Mantella or Pantella — STT→LLM→TTS pipeline, conversation memory, context injection, multi-backend LLM routing, and an **Actions** system (tool-calling exposed to the game).
- **Adapter (build):** an X4 extension (Lua/MD) on top of the Named Pipes API that (a) reads live state and emits batched telemetry, and (b) maps brain "Actions" back to game commands. This is the analog of Skyrim/FO4's script-extender plugin.

---

## 4. Decision record (what / why / how / alternatives rejected)

> These are binding. Each records the choice, the reasoning, and what was rejected so they don't get re-litigated under time pressure.

### 4.1 Data egress = live Lua/MD layer, **not** the savegame
**Decision:** Read live in-memory game state (the same data the HUD/map/target-monitor use) and stream it out. Do not parse the savegame.
**Why:** The savegame is gzipped XML written only on save. Using it means (a) stale data between saves and (b) forcing a save per query, which causes a disk/sim hitch — unacceptable for a real-time assistant, and worse late-game when saves are slow. The Lua/MD layer has the data live, every frame.
**Rejected alternatives:** *Savegame parsing* (stale + hitchy). *Screen OCR* (fragile, high-latency, can't read off-screen state). *Debug-log tailing* as the primary channel (one-way, unstructured) — but see 4.8, it's useful as a *secondary* event source.

### 4.2 Bridge = SirNukes "Mod Support APIs" → Named Pipes API
**Decision:** Use the Named Pipes API for bidirectional IPC between X4 (client) and an external server process.
**Why:** It's the purpose-built, proven mechanism: OS named pipes, bidirectional, avoids disk overhead, already used by hotkey/time mods. An external Python (or compiled) server hosts the pipe; the brain talks to that server.
**How / constraints (these will bite if ignored):**
- **Windows-only** (no Linux pipe support) — hence the project's Windows-only scope.
- **Protected UI mode must be disabled** in X4 settings or the pipe won't connect.
- **~1-frame lua→MD delay per pipe op.** Therefore **batch one payload per query**; never do chatty per-field reads, and if sending rapid messages, coalesce them or keep multiple reads in flight.
- **Pipe dies on game save / reload / UI reload.** The Lua client cannot reattach to a dead handle. **Recovery = the *server* destroys and recreates the pipe; X4 re-handshakes on its reload cue.** (See 4.7 watchdog.)
**Rejected alternatives:** raw file polling (disk overhead, latency), a custom DLL/socket from scratch (reinventing what SirNukes already solved and maintains).

### 4.3 Brain = fork Mantella (or Pantella), don't build from scratch
**Decision:** Build on **Mantella** (`art-from-the-machine/Mantella`, MIT) or its modular fork **Pantella** (`Pathos14489/Pantella`).
**Why:** It already provides the entire orchestration layer we'd otherwise rewrite: STT (Whisper/Moonshine) → LLM → TTS (Piper/xVASynth/XTTS), persistent memory/history, context injection, multi-backend LLM routing (local / OpenAI / OpenRouter / any OpenAI-compatible), and — crucially — an **Actions** system that is exactly "tool-calling exposed to the game" (in the Skyrim/FO4 builds the LLM can already trigger loot/move/travel/report-crime etc.). MIT license = free to fork, modify, and add backends.
**How:** Pantella is the better base if heavy modification is expected — it was rebuilt into generic modules specifically so devs can add LLM backends and reshape the prompt/conversation flow without touching the rest.
**Rejected alternatives:** *Build from scratch* (months of solved-problem rework). *VoiceAttack alone* (command-mapping only, not conversational/LLM).

### 4.4 Inference runs **off** the gaming machine
**Decision:** LLM inference is cloud-hosted or on a second networked PC. Never local on the gaming rig.
**Why:** X4 is CPU- and RAM-bound; a local LLM is GPU-, VRAM-, and RAM-bound (context caching). Co-locating them creates direct resource contention that degrades both the game and inference. The known alpha mod offloads to a separate PC for exactly this reason.
**Rejected alternatives:** local model on the gaming box (contention).

### 4.5 Provider-agnostic, key-based backend; provider is a config choice
**Decision:** The LLM backend is abstract and swappable. Default to whatever is cheapest/fastest that week (cheap hosted models — GLM, Kimi, DeepSeek, Haiku, or a hosted small model). Route through OpenRouter or a thin OpenAI-compatible shim so swapping a provider is a config line, not a rewrite.
**Why:** The task is provider-insensitive (read stats, follow system prompt). Abstraction means a single lab's pricing, rate-limits, or policy stance never blocks the project — reroute to another. This is the entire payoff of keeping the backend abstract.
**Note (narrow, non-blocking):** repurposing a *consumer chat subscription's OAuth* (Claude.ai / Claude Code / ChatGPT Plus login) as a programmatic backend is the one auth path that is provider-policy-gray and prone to breaking. Provider-billed/subscription **API keys** (e.g. Ollama Cloud, OpenRouter credits, the cheap-model providers) are fine and normal. If a given lab's subscription-token route is used, expect occasional break-and-reroute — already mitigated by 4.5's abstraction. Don't let this note constrain the design; the abstraction absorbs it.

### 4.6 Tiered model routing
**Decision:** Cheap/fast model handles the common path; escalate to a larger model only for genuinely reasoning-heavy queries.
**Why:** ~90% of queries are lookups (cheap, latency-sensitive). A minority ("analyze the war, who should I betray") want real reasoning. Paying big-model cost/latency for "what's the price here" is waste.
**How:**
| Query class | Tier |
|---|---|
| Price lookup, nearest X, target ID | cheap/fast (Haiku / GLM / Kimi / DeepSeek / hosted small) |
| Short trade-run planning | mid |
| War analysis, multi-factor strategy | larger (e.g. Sonnet/Opus-class) |

Route all via OpenRouter (or shim) for hot-swap.

### 4.7 Retrieval = two-tier routing + ambient context + scoped, labeled data
**Decision:**
1. **Cheap deterministic intent router** (keyword/local classifier) picks *which* batched fetch to request. Avoids spending an LLM round-trip just to choose a fetch (latency). LLM tool-calling is the fallback only for phrasing the router can't classify.
2. **Ambient context, always injected:** current sector, player position, current target, ship/cargo/credits — resolves deictic queries ("this system", "around here", "that station").
3. **Scoped data per intent:** only the slice the query needs.
4. **Keep labels + units; never feed bare numbers.** `{ware: hull_parts, buy: 4500cr/u, sell: 5200cr/u, station:"…", dist_km:12, stock:800}` not `4500`. Cheap tokens; prevents hallucinated meaning.
**Why:** Full-state dumps waste tokens and latency and bury the signal. Pure-LLM-tool-calling adds a round-trip per query. The hybrid gets low latency on the common path and flexibility on the long tail.

### 4.8 "Political news" = relation diffs (+ optional log tail)
**Decision:** Snapshot faction relations on a timer, diff over time, narrate deltas. Optionally tail the `-debug all` log for combat/diplomacy events as a richer event source.
**Why:** Faction relations are live-queryable; a diff is a cheap, reliable "news" signal ("Argon lost two sectors to the Xenon this hour") without needing the game to expose a news API.

---

## 5. Interface contracts

> **Status: illustrative.** Exact field names, Lua function names, and the precise shape of Mantella's Action interface must be confirmed against the live code before relying on them. Treat as the *shape*, not the literal API.

**Telemetry-in (brain requests → adapter responds, one batched message):**
```json
// request
{ "type": "fetch", "intent": "trade_in_sector", "args": { "radar_only": true } }

// response
{
  "ambient": { "sector": "Grand Exchange IV", "pos": [x,y,z], "credits": 184000,
               "ship": "Kestrel Vanguard", "target": null },
  "data": [ { "ware": "hull_parts", "buy": 4500, "sell": 5200, "unit": "cr/u",
              "station": "Profit Center Alpha", "dist_km": 12, "stock": 800 } ]
}
```

**Action-out (brain → adapter → game), optional later phase:**
```json
{ "type": "action", "name": "set_waypoint", "args": { "station_id": "…" } }
```
Map onto Mantella's existing Action interface (define X4 equivalents: `set_waypoint`, `dock_at`, `mark_target`, …).

**Intent → fetch table (initial set):**
| Intent (router output) | Adapter fetch |
|---|---|
| `trade_in_sector` | trade offers of stations in radar range |
| `faction_state` | current faction relations + recent combat/diplomacy events |
| `ship_status` | hull/shield/cargo/credits/fuel of player ship |
| `sector_objects` | stations/gates/lockboxes/wrecks in current sector |

---

## 6. Operational concerns

- **Watchdog / reconnect:** background loop; on pipe break → destroy + recreate the pipe object server-side, wait for X4's reload-cue re-handshake. **Not** a read-only health poll. (4.2)
- **Latency budget (voice):** STT → route → pipe fetch (mind the per-op frame delay; batch) → LLM → TTS. Keep the common path on the cheap/fast tier. Target conversational feel; measure each stage.
- **Failure modes & handling:**
  - *Empty/failed fetch* → brain must say "no data / scanner can't see that," never fabricate. (Dangerous default: proceeding on empty.)
  - *Stale data* → trade info has a short shelf-life in-game; surface "as of last scan" rather than implying live truth where it isn't.
  - *Pipe break* → watchdog (above); queue/replay the in-flight query after re-handshake.
  - *Model timeout / provider outage* → failover to next provider (4.5); degrade to cheaper tier rather than hang.
- **Observability:** log every (intent, fetch, payload size, model, latency, cost) tuple. You'll want this to tune the router and tiering.
- **Cost:** negligible for the common path (a Mantella dev cited ~$3.40/month part-time). Tiering keeps the big-model spend rare.

---

## 7. Implementation procedure (phased; build in order)

**Prereqs:** Windows; X4 with Protected UI mode **off**; SirNukes Mod Support APIs installed; the Named Pipes Python/exe server runnable; a chosen LLM provider key reachable via OpenRouter or shim.

1. **Pipe echo.**
   *Do:* install SirNukes APIs, run the pipe server, get a ping/pong round-trip; add the watchdog.
   *Done when:* a string sent from an external script appears in X4 and a reply returns, and killing/restarting the server auto-recovers.
2. **Telemetry read.**
   *Do:* Lua/MD reads player position + sector + radar-range trade offers; batch → pipe → log server-side.
   *Done when:* a real `trade_in_sector` payload (ambient + data, labeled+units) is logged from a live game.
3. **Brain wire-up (text-only).**
   *Do:* fork Mantella/Pantella; point its backend at OpenRouter (cheap model); feed the telemetry payload as context; text-in/text-out.
   *Done when:* typing "what's selling here" returns a correct sentence grounded in the live payload.
4. **Voice.**
   *Do:* enable STT/TTS (or front with VoiceAttack); tune latency.
   *Done when:* spoken query → spoken answer at conversational speed.
5. **Intent router + scoped fetches.**
   *Do:* build the keyword→fetch table; implement the 4 fetch types in §5.
   *Done when:* all 4 intents route correctly and return scoped payloads.
6. **Actions-out (optional).**
   *Do:* map 1–2 safe commands (`set_waypoint`, `mark_target`) onto Mantella Actions through the pipe.
   *Done when:* a spoken command moves a game state safely, with confirmation.
7. **Political-news feed.**
   *Do:* timer-snapshot faction relations + diff (+ optional log tail); narrate deltas.
   *Done when:* a faction territory change produces a spoken news line within one snapshot interval.

> **Requirements-gathering note:** Phase 2's schema is best finalized *after* real play — you must see the actual shape of "trade offer" / "sector" data in the live Lua before locking field names. Play first; let the game define the contract.

---

## 8. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Lua/MD field names differ from assumptions | High | §5 marked illustrative; confirm in-game (Phase 2) before locking |
| Pipe instability across game updates | Med | watchdog + reconnect; pin SirNukes API version |
| Latency feels sluggish | Med | cheap-tier default; batch fetches; measure per-stage |
| Provider rate-limit / policy change | Med | provider-agnostic backend (4.5); failover routing |
| Brain hallucinates on empty fetch | Med | explicit "no data" path; never fabricate (6) |
| Scope creep into autopilot | Low/Med | hard non-goal (1); actions phase is opt-in and minimal |

---

## 9. Open questions / TBD
- In-game persona tone/voice (flavor — undecided).
- Exact Lua function names for each fetch (confirm in-code).
- Mantella vs Pantella final choice (lean Pantella if heavy modification expected).
- Mantella Action interface specifics (inspect before §6).
- STT/TTS engine choice + whether to front with VoiceAttack.

---

## 10. Glossary
- **MD (Mission Director):** X4's XML scripting layer for game logic/events.
- **Lua layer:** X4's UI/scripting runtime; holds live state the HUD reads. Sandboxed (no arbitrary I/O).
- **Named Pipes API:** SirNukes module enabling bidirectional IPC between X4 and an external process.
- **Brain / adapter:** the reusable LLM-orchestration half vs the X4-specific bridge half.
- **Mantella / Pantella:** open-source (MIT) STT→LLM→TTS NPC-conversation frameworks; the brain base.
- **Actions:** Mantella's mechanism for letting the LLM trigger in-game effects (= tool-calling to the game).
- **Ambient context:** always-injected player state (position/sector/target/ship) that resolves deictic queries.
- **Protected UI mode:** an X4 setting that, if on, blocks pipe connections — must be off.

---

## 11. References
- SirNukes Mod Support APIs (Named Pipes): `github.com/bvbohnen/x4-projects` → `sn_mod_support_apis`
- Mantella (MIT): `github.com/art-from-the-machine/Mantella`
- Pantella (modular fork): `github.com/Pathos14489/Pantella`
- X4 modding internals (Lua fn list / events / debug log flags): h2odragon "HOWTO-hackx4f", Egosoft wiki
- Feasibility reference (no public source): alpha "AI conversation" mod, Egosoft/Steam forum WIP demo

---
*Design captured from an ideation session. Player context: pirate/smuggler/trader route (Stranded start), limited weekend playtime, strong-CPU gaming box (→ keep inference off-box), comfortable writing custom provider/auth routing. Build read-only advisor first; let real play define the telemetry schema before locking §5.*
