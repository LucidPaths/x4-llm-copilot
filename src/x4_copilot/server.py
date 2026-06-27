from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .llm import advisor_from_env
from .models import PayloadError, TelemetryPayload
from .pipe import DuplexTransport, NamedPipeServer
from .protocol import FetchRequest, encode_answer, parse_json_message

TelemetryFetcher = Callable[[FetchRequest], TelemetryPayload]
LOG = logging.getLogger(__name__)


@dataclass
class X4CopilotServer:
    fetcher: TelemetryFetcher
    advisor: object | None = None

    def __post_init__(self) -> None:
        if self.advisor is None:
            self.advisor = advisor_from_env()

    def handle_question(self, question: str) -> str:
        request = FetchRequest.from_question(question)
        payload = self.fetcher(request)
        if payload.intent == "unknown":
            payload = TelemetryPayload(intent=request.intent, ambient=payload.ambient, data=payload.data, as_of=payload.as_of)
        answer = self.advisor.answer(question, payload)  # type: ignore[attr-defined]
        return encode_answer(question, payload, answer)

    def handle_message(self, raw: str) -> str:
        message = parse_json_message(raw)
        msg_type = message.get("type")
        if msg_type == "ping":
            return json.dumps({"type": "pong"})
        if msg_type == "question":
            return self.handle_question(str(message.get("question", "")))
        if msg_type == "telemetry":
            question = str(message.get("question", ""))
            payload = TelemetryPayload.from_dict(message, default_intent=message.get("intent", "unknown"))
            answer = self.advisor.answer(question, payload)  # type: ignore[attr-defined]
            return encode_answer(question, payload, answer)
        raise PayloadError(f"unsupported message type: {msg_type}")

    def serve_transport(self, transport: DuplexTransport, *, once: bool = False) -> None:
        transport.connect()
        try:
            while True:
                raw = transport.read()
                try:
                    response = self.handle_message(raw)
                except Exception as exc:  # noqa: BLE001
                    LOG.exception("message handling failed")
                    response = json.dumps({"type": "error", "error": str(exc)})
                transport.write(response)
                if once:
                    break
        finally:
            transport.close()


def serve_named_pipe(pipe_name: str = "x4_llm_copilot") -> None:
    def no_game_fetcher(request: FetchRequest) -> TelemetryPayload:
        raise PayloadError(f"No live X4 fetcher is attached for {request.intent}; send telemetry messages or wire the extension fetch path")

    X4CopilotServer(fetcher=no_game_fetcher, advisor=advisor_from_env()).serve_transport(NamedPipeServer(pipe_name))
