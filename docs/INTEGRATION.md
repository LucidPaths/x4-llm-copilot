# Integration Notes

## SirNukes Mod Support APIs

Install `sn_mod_support_apis` from `github.com/bvbohnen/x4-projects`.

Verified API names relevant to this repo:

- `md.Named_Pipes.Write`
- `md.Named_Pipes.Read`
- `md.Named_Pipes.Check`
- `md.Named_Pipes.Close`
- `md.Named_Pipes.Cancel_Reads`
- `md.Pipe_Server_Host.Register_Module`
- `md.Pipe_Server_Lib.Server_Reader`

Constraints:

- Windows only.
- Protected UI mode must be off.
- X4 is client; Python process is server.
- Python pipe path is `\\.\pipe\x4_llm_copilot`.
- MD pipe name is `x4_llm_copilot`.
- Messages are strings; this repo uses JSON strings.

## Running local smoke checks

```bash
uv run x4-copilot classify "what are goods selling for in this system"
uv run x4-copilot fetch-request "ship status"
uv run x4-copilot answer "what's selling here" --payload examples/trade_payload.json
uv run pytest -q
```

## Running the Windows pipe server

```bash
uv pip install -e '.[winpipe]'
uv run x4-copilot serve-pipe --pipe x4_llm_copilot
```

Then launch X4 with `sn_mod_support_apis` installed and Protected UI mode disabled.

## X4 extension skeleton

The extension is in `extension/x4_llm_copilot/`.

Copy that folder into X4's `extensions/` folder for live testing. It is intentionally a skeleton until live Lua/MD telemetry field names are observed.
