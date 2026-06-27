from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any

from .llm import advisor_from_env
from .models import PayloadError, TelemetryPayload
from .pipe import DuplexTransport, NamedPipeServer
from .protocol import FetchRequest, encode_answer, parse_json_message

TelemetryFetcher = Callable[[FetchRequest], TelemetryPayload]
LOG = logging.getLogger(__name__)
PIPE_BREAK_EXCEPTIONS = (BrokenPipeError, ConnectionError, EOFError, OSError, RuntimeError)
RAW_TELEMETRY_LOG = Path("var/live_telemetry_raw.jsonl")


def append_raw_telemetry(message: dict[str, Any], *, path: Path | None = None) -> None:
    """Append a literal live payload before schema coercion.

    Phase-2's purpose is to observe the real X4/Lua bytes. Keep this deliberately
    separate from TelemetryPayload parsing so guessed schemas cannot hide drift.
    """
    path = path or RAW_TELEMETRY_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")


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
        if msg_type == "telemetry_raw":
            append_raw_telemetry(message)
            return json.dumps({"type": "telemetry_raw_ack", "intent": message.get("intent"), "source": message.get("source")}, ensure_ascii=False)
        if msg_type == "question":
            return self.handle_question(str(message.get("question", "")))
        if msg_type == "telemetry":
            question = str(message.get("question", ""))
            payload = TelemetryPayload.from_dict(message, default_intent=message.get("intent", "unknown"))
            answer = self.advisor.answer(question, payload)  # type: ignore[attr-defined]
            return encode_answer(question, payload, answer)
        raise PayloadError(f"unsupported message type: {msg_type}")

    def serve_transport(
        self,
        transport: DuplexTransport,
        *,
        once: bool = False,
        reconnect: bool = True,
        reconnect_delay_s: float = 1.0,
        max_sessions: int | None = None,
    ) -> None:
        """Serve a duplex transport, recreating the session after pipe breaks.

        SirNukes' X4 client cannot reattach to a dead handle after save/reload/UI reload.
        Recovery must happen server-side: close/destroy the pipe, create a new one, and wait
        for the game to handshake again. ``transport.connect()`` is therefore called once per
        session, not once for process lifetime.
        """
        sessions = 0
        while max_sessions is None or sessions < max_sessions:
            sessions += 1
            try:
                transport.connect()
                while True:
                    try:
                        raw = transport.read()
                    except PIPE_BREAK_EXCEPTIONS as exc:
                        LOG.warning("transport read failed; reconnecting: %s", exc)
                        break
                    try:
                        response = self.handle_message(raw)
                    except Exception as exc:  # noqa: BLE001 - return structured error, keep pipe alive
                        LOG.exception("message handling failed")
                        response = json.dumps({"type": "error", "error": str(exc)})
                    try:
                        transport.write(response)
                    except PIPE_BREAK_EXCEPTIONS as exc:
                        LOG.warning("transport write failed; reconnecting: %s", exc)
                        break
                    if once:
                        return
            finally:
                transport.close()
            if not reconnect:
                return
            if max_sessions is not None and sessions >= max_sessions:
                return
            sleep(reconnect_delay_s)


def serve_named_pipe(pipe_name: str = "x4_llm_copilot") -> None:
    def no_game_fetcher(request: FetchRequest) -> TelemetryPayload:
        raise PayloadError(f"No live X4 fetcher is attached for {request.intent}; send telemetry messages or wire the extension fetch path")

    X4CopilotServer(fetcher=no_game_fetcher, advisor=advisor_from_env()).serve_transport(NamedPipeServer(pipe_name))
