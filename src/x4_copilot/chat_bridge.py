from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .advisor import GroundedAdvisor
from .cockpit_session import (
    CockpitSessionContext,
    CockpitSessionStore,
    SaveScope,
    SaveScopeResolver,
    sanitize_scope_id,
)
from .intent import classify
from .models import PayloadError, TelemetryPayload
from .pipe import DuplexTransport, NamedPipeServer, PipeBusyError, PipeDisconnectedError
from .protocol import FetchRequest, parse_json_message
from .tools import (
    DEFAULT_RAW_TELEMETRY_LOG,
    append_live_raw_message,
    telemetry_payload_from_raw_ambient,
    telemetry_payload_from_raw_faction_state,
    telemetry_payload_from_raw_sector_objects,
    telemetry_payload_from_raw_trade,
)


class ChatResponder(Protocol):
    def answer(self, question: str, payload: TelemetryPayload, session: CockpitSessionContext | None = None) -> str: ...


@dataclass(frozen=True)
class ChatBridgeConfig:
    pipe_name: str = "x4_llm_copilot"
    fetch_timeout_s: float = 8.0
    chat_timeout_s: float = 90.0
    raw_log_path: str = str(DEFAULT_RAW_TELEMETRY_LOG)
    bridge_log_path: str = "var/chat_bridge.jsonl"
    hermes_command: str | None = None
    chat_response_chunk_chars: int = 900
    session_state_root: str | None = None
    save_scope: str | None = None
    allow_derived_save_scope: bool = True


class HermesAgentResponder:
    """Call Hermes full-agent CLI with already-fetched live telemetry as context.

    The bridge owns the X4 pipe, so Hermes must not open its own live pipe from an MCP
    server during this call. Instead, the bridge fetches fresh telemetry over the shared
    pipe, then asks Hermes to reason over that verified snapshot. If Hermes fails or
    times out, the deterministic grounded advisor is used as a fail-closed fallback.
    """

    def __init__(self, *, command: str | None = None, timeout_s: float = 90.0) -> None:
        self.command = command or os.getenv("X4_COPILOT_HERMES_COMMAND") or "hermes"
        self.timeout_s = timeout_s
        self.fallback = GroundedAdvisor()

    def answer(self, question: str, payload: TelemetryPayload, session: CockpitSessionContext | None = None) -> str:
        session_packet: dict[str, Any] = {}
        if session is not None:
            session_packet = {
                "save_scope": session.scope.__dict__,
                "summary": session.summary,
                "recent_turns": session.recent_turns,
                "transcript_path": session.transcript_path,
            }
        prompt = (
            "You are Hermes answering inside X4: Foundations. Text out only; do not propose or perform actions. "
            "Conversation context is only for references and intent; fresh live telemetry is the only current-state authority. "
            "If telemetry and memory disagree, use telemetry and say it changed. Keep the answer concise.\n\n"
            f"Player question: {question}\n\n"
            "Save-scoped cockpit context JSON:\n"
            + json.dumps(session_packet, ensure_ascii=False)
            + "\n\n"
            "Live telemetry JSON:\n"
            + json.dumps(
                {
                    "intent": payload.intent,
                    "ambient": payload.ambient.__dict__,
                    "data": payload.data,
                    "as_of": payload.as_of,
                    "source": "x4_lua_live_pipe",
                    "stale": False,
                },
                ensure_ascii=False,
            )
        )
        env = os.environ.copy()
        command = [self.command]
        if session is not None:
            Path(session.hermes_home).mkdir(parents=True, exist_ok=True)
            env["HERMES_HOME"] = session.hermes_home
            command.extend(["--continue", f"x4-save-{session.scope.save_scope_id}"])
        command.extend(["chat", "-Q", "--source", "x4-cockpit", "--toolsets", "", "-q", prompt])
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=env,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return self.fallback.answer(question, payload)
        if completed.returncode != 0:
            return self.fallback.answer(question, payload)
        answer = completed.stdout.strip()
        return answer or self.fallback.answer(question, payload)


class ChatPipeBridge:
    """Persistent router for X4-originated chat requests and bridge-owned telemetry fetches."""

    def __init__(
        self,
        config: ChatBridgeConfig | None = None,
        *,
        transport: DuplexTransport | None = None,
        responder: ChatResponder | None = None,
    ) -> None:
        self.config = config or ChatBridgeConfig()
        self._transport = transport or NamedPipeServer(self.config.pipe_name)
        self._responder = responder or HermesAgentResponder(command=self.config.hermes_command, timeout_s=self.config.chat_timeout_s)
        self._session_store = CockpitSessionStore(self.config.session_state_root)
        self._scope_resolver = SaveScopeResolver(configured_scope=self.config.save_scope, allow_derived=self.config.allow_derived_save_scope)
        self._write_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._pending_fetch: queue.Queue[dict[str, Any]] | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._log_lock = threading.Lock()

    def serve_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self._transport.connect()
            except PipeBusyError:
                time.sleep(0.5)
                continue
            try:
                while not self._stop.is_set():
                    raw = self._transport.read()
                    self.handle_message(raw)
            except PipeDisconnectedError:
                self._transport.close()
                time.sleep(0.25)

    def stop(self) -> None:
        self._stop.set()
        self._transport.close()

    def handle_message(self, raw: str) -> None:
        stripped = str(raw or "").strip()
        if stripped == "garbage_collected":
            # SirNukes' Lua pipe wrapper writes this when UI reload/GC loses the
            # client file handle. Treat it as a hard session boundary so the
            # bridge closes and recreates the named-pipe instance for X4.
            self._log_event("pipe_client_garbage_collected")
            raise PipeDisconnectedError("named pipe client garbage collected")
        if stripped in {"", "ERROR", "TIMEOUT", "CANCELLED"}:
            # SirNukes read callbacks can surface transient pipe status strings.
            # They are not protocol messages and must not crash the persistent bridge.
            return
        try:
            message = parse_json_message(stripped)
        except PayloadError as exc:
            self._write_json({"type": "protocol_error", "error": str(exc)})
            return
        msg_type = message.get("type")
        self._log_event("message_received", type=msg_type, id=message.get("id"), intent=message.get("intent"), text=message.get("text"))
        if msg_type == "ping":
            self._write_json({"type": "pong"})
            return
        if msg_type == "telemetry_raw":
            append_live_raw_message(message, self.config.raw_log_path)
            self._write_json({"type": "telemetry_raw_ack", "intent": message.get("intent"), "source": message.get("source")})
            pending = self._pending_fetch
            if pending is not None and message.get("trigger") == "fetch_response":
                pending.put(message)
            return
        if msg_type == "chat_request":
            request_id = _required_text(message, "id")
            question = _required_text(message, "text")
            thread = threading.Thread(target=self._handle_chat_request, args=(request_id, question, message), daemon=True, name=f"x4-chat-{request_id}")
            self._threads.append(thread)
            thread.start()
            return
        # Unknown messages are explicit protocol errors, but keep the bridge alive.
        self._write_json({"type": "protocol_error", "error": f"unsupported message type: {msg_type}"})

    def _handle_chat_request(self, request_id: str, question: str, request_message: dict[str, Any]) -> None:
        try:
            self._log_event("chat_request_start", id=request_id, question=question)
            direct_answer = self.answer_direct(question)
            if direct_answer is not None:
                answer = _display_safe_text(direct_answer)
                self._log_event("chat_response_ready", id=request_id, intent="ambient_context_help", text=answer)
                self._write_chat_response(request_id, answer)
                return
            payload = self.fetch_for_question(question)
            save_binding = _save_binding_command(question)
            if save_binding is not None:
                self._scope_resolver.configured_scope = save_binding
                scope = SaveScope(sanitize_scope_id(save_binding), "configured", {"source": "chat_command", "command": "save"})
            else:
                scope = self._scope_resolver.resolve(request=request_message, payload=payload)
            command_answer = self.answer_session_command(question, scope)
            if command_answer is not None:
                answer = _display_safe_text(command_answer)
            else:
                session = self._session_store.context(scope)
                answer = _display_safe_text(self._responder.answer(question, payload, session))
                self._session_store.append_turn(scope, question=question, answer=answer, payload=payload)
            self._log_event("chat_response_ready", id=request_id, intent=payload.intent, save_scope=scope.save_scope_id, scope_confidence=scope.confidence, text=answer)
            self._write_chat_response(request_id, answer)
        except Exception as exc:  # noqa: BLE001 - surfaced to cockpit as clean error state
            self._log_event("chat_response_error", id=request_id, error=str(exc))
            self._write_chat_response(request_id, f"Hermes error: {exc}", error=str(exc))

    def _write_chat_response(self, request_id: str, text: str, *, error: str | None = None) -> None:
        chunks = _chunk_display_text(text, self.config.chat_response_chunk_chars)
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            display = chunk if total == 1 else f"[{index}/{total}] {chunk}"
            message: dict[str, Any] = {"type": "chat_response", "id": request_id, "text": display}
            if error is not None and index == total:
                message["error"] = error
            self._log_event("chat_response_chunk", id=request_id, index=index, total=total, chars=len(display))
            self._write_json(message)

    def answer_direct(self, question: str) -> str | None:
        if _normalized_command(question) != "ambient_context":
            return None
        return (
            "I can answer from live telemetry: ambient_context (sector, ship, credits, target/cargo), "
            "ship_status (hull/shield/cargo), trade_in_sector (visible buy/sell offers), "
            "faction_state (relations/events), and sector_objects (stations/gates/notable ships). "
            "Ask in plain language, e.g. 'what's selling near me?' or 'what's my ship status?'"
        )

    def answer_session_command(self, question: str, scope: SaveScope) -> str | None:
        command = _normalized_command(question)
        if command in {"session", "session_status"}:
            status = self._session_store.status(scope)
            return (
                f"X4 session {status['save_scope_id']} ({status['confidence']}), "
                f"turns={status['turn_count']}, transcript={status['transcript_path']}"
            )
        if command in {"reset", "session_reset"}:
            self._session_store.reset(scope)
            return f"Cleared X4 cockpit session for save scope {scope.save_scope_id}."
        if command in {"export", "session_export"}:
            return f"X4 cockpit session transcript: {self._session_store.status(scope)['transcript_path']}"
        save_binding = _save_binding_command(question)
        if save_binding is not None:
            return f"Bound X4 cockpit session to save scope {scope.save_scope_id}."
        return None

    def fetch_for_question(self, question: str) -> TelemetryPayload:
        routed = classify(question)
        if routed.intent == "unknown":
            self._log_event("fetch_default_ambient", question=question)
            request = FetchRequest(intent="ambient_context", args={}, question=question)
            return self.fetch_live(request)
        args: dict[str, Any] = {}
        if routed.intent == "trade_in_sector":
            args["scope"] = "radar_range"
        request = FetchRequest(intent=routed.intent, args=args, question=question)
        self._log_event("fetch_request", intent=request.intent, question=request.question, args=request.args)
        return self.fetch_live(request)

    def fetch_live(self, request: FetchRequest) -> TelemetryPayload:
        with self._fetch_lock:
            pending: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending_fetch = pending
            try:
                self._write(request.to_json())
                try:
                    raw = pending.get(timeout=self.config.fetch_timeout_s)
                except queue.Empty as exc:
                    raise PayloadError(f"live pipe fetch_response timed out after {self.config.fetch_timeout_s:g}s") from exc
            finally:
                self._pending_fetch = None
        if request.intent == "trade_in_sector":
            return telemetry_payload_from_raw_trade(raw)
        if request.intent == "faction_state":
            return telemetry_payload_from_raw_faction_state(raw)
        if request.intent == "sector_objects":
            return telemetry_payload_from_raw_sector_objects(raw)
        payload = telemetry_payload_from_raw_ambient(raw)
        if request.intent == "ship_status":
            return TelemetryPayload(intent="ship_status", ambient=payload.ambient, data=payload.data, as_of="fresh live pipe response")
        return TelemetryPayload(intent="ambient_context", ambient=payload.ambient, data=payload.data, as_of="fresh live pipe response")

    def _write_json(self, message: dict[str, Any]) -> None:
        self._log_event(
            "message_write",
            type=message.get("type"),
            id=message.get("id"),
            intent=message.get("intent"),
            error=message.get("error"),
            text=message.get("text"),
        )
        self._write(json.dumps(message, ensure_ascii=False))
        self._log_event(
            "message_write_complete",
            type=message.get("type"),
            id=message.get("id"),
            intent=message.get("intent"),
        )

    def _write(self, message: str) -> None:
        with self._write_lock:
            self._transport.write(message)

    def wait_for_workers(self, timeout_s: float = 0.1) -> None:
        deadline = time.monotonic() + timeout_s
        for thread in list(self._threads):
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)

    def _log_event(self, event: str, **fields: Any) -> None:
        record = {"ts": time.time(), "event": event, **{key: value for key, value in fields.items() if value is not None}}
        line = json.dumps(record, ensure_ascii=False)
        path = Path(self.config.bridge_log_path)
        with self._log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def _required_text(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"chat_request missing non-empty {key}")
    return value.strip()


def serve_chat_bridge(pipe_name: str = "x4_llm_copilot", *, fetch_timeout_s: float = 8.0, chat_timeout_s: float = 90.0, session_state_root: str | None = None, save_scope: str | None = None) -> None:
    bridge = ChatPipeBridge(ChatBridgeConfig(pipe_name=pipe_name, fetch_timeout_s=fetch_timeout_s, chat_timeout_s=chat_timeout_s, session_state_root=session_state_root, save_scope=save_scope))
    bridge.serve_forever()


def _chunk_display_text(text: str, max_chars: int) -> list[str]:
    text = str(text or "")
    if not text:
        return [""]
    max_chars = max(100, int(max_chars or 900))
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        split_at = max(window.rfind("\n"), window.rfind(". "), window.rfind("; "), window.rfind(", "), window.rfind(" "))
        if split_at < max_chars // 2:
            split_at = max_chars
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def _normalized_command(text: str) -> str:
    return str(text or "").strip().lower().replace("-", "_").replace(" ", "_")


def _save_binding_command(text: str) -> str | None:
    raw = str(text or "").strip()
    lowered = raw.lower()
    if lowered.startswith("save ") and raw[5:].strip():
        return raw[5:].strip()
    if lowered.startswith("session save ") and raw[13:].strip():
        return raw[13:].strip()
    return None


def _display_safe_text(text: str) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
    safe = str(text or "")
    for old, new in replacements.items():
        safe = safe.replace(old, new)
    return safe
