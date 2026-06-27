# Architecture

This repo contains the executable adapter spine for the X4 LLM co-pilot idea in `README.md`.

## Boundary

1. **X4 adapter**: Windows-only extension + named-pipe protocol. It extracts scoped telemetry from live game state and accepts later safe actions.
2. **Brain boundary**: provider-agnostic advisor layer. It can be a deterministic fallback, an OpenAI-compatible model endpoint, or a future Pantella/Mantella fork.

The adapter protocol is intentionally independent from Mantella/Pantella. That keeps the hard X4 work reusable even if the brain base changes.

## Verified external seams

- SirNukes Mod Support APIs named-pipe docs live at `bvbohnen/x4-projects/extensions/sn_mod_support_apis/documentation/Named_Pipes_API.md`.
- X4 is the named-pipe client; the external process creates `\\.\pipe\<pipe_name>`.
- X4 MD calls use bare pipe names, e.g. `x4_llm_copilot`.
- Protected UI mode must be disabled or the pipe DLL will not load/connect.
- Pipe server host modules are Python functions with `main(args)` and can be registered with `md.Pipe_Server_Host.Register_Module`.
- Mantella is AGPL-3.0; Pantella reports GPL-3.0 but is a Mantella fork, so license provenance needs review before copying code.

## Current implementation

```text
src/x4_copilot/        Python adapter spine
examples/              sample telemetry payloads
extension/x4_llm_copilot/  X4 extension skeleton
```

## Protocol

Fetch request:

```json
{"type":"fetch","intent":"trade_in_sector","args":{"router_confidence":0.75},"question":"what's selling here"}
```

Telemetry response / direct brain input:

```json
{"type":"telemetry","intent":"trade_in_sector","ambient":{"sector":"Grand Exchange IV"},"data":[{"ware":"hull_parts","buy":4500,"sell":5200,"unit":"cr/u","station":"Profit Center Alpha","dist_km":12,"stock":800}]}
```

Answer:

```json
{"type":"answer","intent":"trade_in_sector","answer":"Best visible trade: ..."}
```

## Provider configuration

The default advisor is deterministic and requires no key. To use an OpenAI-compatible endpoint:

```bash
export X4_COPILOT_OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export X4_COPILOT_API_KEY=...
export X4_COPILOT_MODEL="provider/model"
```

The key stays in environment variables. Do not commit it.
