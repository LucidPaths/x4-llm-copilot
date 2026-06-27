from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .intent import classify
from .llm import advisor_from_env, list_ollama_models, list_provider_profiles
from .models import TelemetryPayload
from .protocol import FetchRequest
from .server import serve_named_pipe


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
    if args.command == "providers":
        print(json.dumps([profile.__dict__ for profile in list_provider_profiles()], ensure_ascii=False))
        return 0
    if args.command == "ollama-models":
        print(json.dumps(list_ollama_models(base_url=args.base_url), ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
