from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .intent import classify
from .llm import advisor_from_env, list_ollama_models, list_provider_profiles
from .models import PayloadError, TelemetryPayload
from .protocol import FetchRequest
from .server import serve_named_pipe
from .tools import (
    create_live_pipe_tool_surface,
    create_live_raw_log_tool_surface,
    create_mock_tool_surface,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x4-copilot")
    sub = parser.add_subparsers(dest="command", required=True)
    p_classify = sub.add_parser("classify", help="route a natural-language query to an adapter fetch intent")
    p_classify.add_argument("question")
    p_answer = sub.add_parser("answer", help="answer a question from a telemetry JSON payload")
    p_answer.add_argument("question")
    p_answer.add_argument("--payload", required=True, type=Path)
    p_fetch = sub.add_parser("fetch-request", help="emit the JSON fetch request for a question")
    p_fetch.add_argument("question")
    p_pipe = sub.add_parser("serve-pipe", help="serve the Windows named pipe for X4")
    p_pipe.add_argument("--pipe", default="x4_llm_copilot")

    p_tool = sub.add_parser("tool", help="call the structured tool surface")
    p_tool.add_argument("name", choices=["ambient", "trade", "ship", "faction", "objects"])
    p_tool.add_argument("--source", choices=["mock", "live-raw-log", "live-pipe"], default="mock")
    p_tool.add_argument("--raw-log", type=Path, default=None)
    p_tool.add_argument("--pipe", default="x4_llm_copilot")
    p_tool.add_argument("--timeout", type=float, default=8.0)
    p_tool.add_argument("--scope", choices=["docked_station", "radar_range"], default="docked_station", help="trade tool scope; live-pipe supports docked_station and bounded radar_range")
    p_tool.add_argument("--kinds", default="", help="comma-separated sector object kinds, e.g. station,gate,ship,collectable,wreck")

    sub.add_parser("mcp-config", help="print a Hermes stdio MCP config snippet for this repo")
    sub.add_parser("providers", help="list configured provider profiles without exposing keys")

    p_models = sub.add_parser("ollama-models", help="list Ollama Cloud models using OLLAMA_API_KEY")
    p_models.add_argument("--base-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "classify":
        result = classify(args.question)
        print(json.dumps(result.__dict__, ensure_ascii=False))
        return 0
    if args.command == "fetch-request":
        print(FetchRequest.from_question(args.question).to_json())
        return 0
    if args.command == "answer":
        with args.payload.open(encoding="utf-8") as handle:
            payload = TelemetryPayload.from_dict(json.load(handle), default_intent=classify(args.question).intent)
        print(advisor_from_env().answer(args.question, payload))
        return 0
    if args.command == "serve-pipe":
        serve_named_pipe(args.pipe)
        return 0
    if args.command == "tool":
        if args.source == "live-pipe":
            surface = (
                create_live_pipe_tool_surface(args.pipe, raw_log_path=args.raw_log, timeout_s=args.timeout)
                if args.raw_log
                else create_live_pipe_tool_surface(args.pipe, timeout_s=args.timeout)
            )
        elif args.source == "live-raw-log":
            surface = create_live_raw_log_tool_surface(args.raw_log) if args.raw_log else create_live_raw_log_tool_surface()
        else:
            surface = create_mock_tool_surface()
        calls = {
            "ambient": surface.get_ambient_context,
            "trade": lambda: surface.fetch_trade_offers(scope=args.scope),
            "ship": surface.fetch_ship_status,
            "faction": surface.fetch_faction_state,
            "objects": lambda: surface.fetch_sector_objects(kinds=[kind.strip() for kind in args.kinds.split(",") if kind.strip()] or None),
        }
        try:
            result = calls[args.name]()
        except PayloadError as exc:
            print(json.dumps({"ok": False, "source": args.source, "stale": True, "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "mcp-config":
        print(
            json.dumps(
                {
                    "mcp_servers": {
                        "x4_copilot": {
                            "command": "uv",
                            "args": ["--directory", str(Path(__file__).resolve().parents[2]), "run", "--extra", "mcp", "x4-copilot-mcp"],
                            "timeout": 30,
                            "connect_timeout": 30,
                        }
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "providers":
        print(json.dumps([profile.__dict__ for profile in list_provider_profiles()], ensure_ascii=False))
        return 0
    if args.command == "ollama-models":
        print(json.dumps(list_ollama_models(base_url=args.base_url), ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
