# Architecture

This repo contains the executable adapter spine for the X4 LLM co-pilot idea in `README.md`.

## Boundary

1. **X4 adapter**: Windows-only extension + named-pipe protocol. It extracts scoped telemetry from live game state and accepts later safe actions.
2. **Brain boundary**: provider-agnostic advisor layer. It can be a deterministic fallback, an Ollama Cloud wrapper, a generic OpenAI-compatible model endpoint, or a future Pantella/Mantella fork.

The adapter protocol is intentionally independent from Mantella/Pantella. That keeps the hard X4 work reusable even if the brain base changes. The provider runtime was cannibalized from the World Engine pattern: environment profile selection, Ollama Cloud as a first-class profile, `/v1/models` model discovery, no key echoing, and provider-specific output quirks kept behind the provider wrapper.

## Source-grounded external seams

- SirNukes Mod Support APIs named-pipe docs/source live at `bvbohnen/x4-projects/extensions/sn_mod_support_apis/`.
- Docs/source indicate X4 is the named-pipe client; the external process creates `\\.\pipe\<pipe_name>`.
- Docs/source indicate X4 MD calls use bare pipe names, e.g. `x4_llm_copilot`.
- Protected UI mode must be disabled or the pipe DLL will not load/connect.
- Pipe server host modules are Python functions with `main(args)` and can be registered with `md.Pipe_Server_Host.Register_Module`.
- Exact MD parameter shapes and any Lua/UI loader hooks remain live-game validation tasks; this repo no longer labels them verified until a running X4 debug-log smoke proves them.
- Mantella is AGPL-3.0; Pantella reports GPL-3.0 but is a Mantella fork, so license provenance needs review before copying code.

## Current implementation

```text
src/x4_copilot/        Python adapter spine
examples/              sample telemetry payloads
extension/x4_llm_copilot/  X4 extension skeleton
```

Additional Hermes-facing surfaces:

- `x4_copilot.tools`: importable, interface-agnostic read tool layer over `TelemetryFetcher`.
- `x4_copilot.mcp_server`: optional stdio MCP wrapper for Hermes' native MCP client.
- `x4-copilot tool <name>`: local structured smoke for the mock-backed tool surface.
- `x4-copilot-mcp`: stdio MCP server entry point, installed via the `mcp` extra.

The tool layer is deliberately model-free: no provider routing, no keys, no prose generation. Hermes owns model routing on the MCP path; `llm.py` remains only for the separate reflex advisor path.

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

The default advisor is deterministic and requires no key.

Ollama Cloud first-class path:

```bash
export X4_COPILOT_PROVIDER="ollama"
export OLLAMA_API_KEY="..."
export OLLAMA_MODEL="glm-5.2"
uv run x4-copilot providers
uv run x4-copilot ollama-models
```

Generic OpenAI-compatible path:

```bash
export X4_COPILOT_OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export X4_COPILOT_API_KEY=...
export X4_COPILOT_MODEL="provider/model"
```

The key stays in environment variables. Provider/profile commands never print it.
