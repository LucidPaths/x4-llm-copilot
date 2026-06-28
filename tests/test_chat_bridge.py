from __future__ import annotations

import json
import queue
import time

from x4_copilot.chat_bridge import ChatBridgeConfig, ChatPipeBridge, HermesAgentResponder
from x4_copilot.cockpit_session import CockpitSessionContext
from x4_copilot.models import TelemetryPayload
from x4_copilot.pipe import PipeBusyError, PipeDisconnectedError


class FakeTransport:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.reads: queue.Queue[str] = queue.Queue()
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def read(self) -> str:
        return self.reads.get(timeout=1)

    def write(self, message: str) -> None:
        self.writes.append(message)

    def close(self) -> None:
        self.connected = False


class EchoResponder:
    def answer(self, question: str, payload: TelemetryPayload, session: CockpitSessionContext | None = None) -> str:
        return f"answer for {question}: {payload.intent}"


def _radar_trade_raw() -> dict:
    return {
        "type": "telemetry_raw",
        "intent": "trade_in_sector",
        "source": "x4_lua_live_pipe",
        "schema": "trade_offers_radar_v1",
        "trigger": "fetch_response",
        "sector_raw": "Windfall I Union Summit",
        "ship_name": "Raleigh (Container)",
        "stations_raw": [
            {
                "station_id": "station-1",
                "station_name": "VIG Ice Refinery I",
                "distance_m": 2500,
                "distance_km": 2.5,
                "offers_raw": [
                    {
                        "id": "offer-1",
                        "ware": "ice",
                        "name": "Ice",
                        "isselloffer": True,
                        "price": 42,
                        "amount": 1200,
                        "stationname": "VIG Ice Refinery I",
                        "distance_km": 2.5,
                    }
                ],
            }
        ],
    }


def test_chat_bridge_routes_chat_request_by_correlation_id() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "x4chat-1", "text": "what's selling near me?"}))
    fetch = _wait_for_fetch(transport)
    assert fetch["type"] == "fetch"
    assert fetch["intent"] == "trade_in_sector"
    assert fetch["question"] == "what's selling near me?"
    assert fetch["args"]["scope"] == "radar_range"

    bridge.handle_message(json.dumps(_radar_trade_raw()))
    responses = _wait_for_responses(transport, 1)
    assert responses == [{"type": "chat_response", "id": "x4chat-1", "text": "answer for what's selling near me?: trade_in_sector"}]


def test_chat_bridge_answers_ambient_context_with_capability_help_without_fetch() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "x4chat-help", "text": "ambient_context"}))
    _wait_for_responses(transport, 1)

    writes = [json.loads(item) for item in transport.writes]
    assert [item for item in writes if item.get("type") == "fetch"] == []
    responses = [item for item in writes if item.get("type") == "chat_response"]
    assert len(responses) == 1
    assert responses[0]["id"] == "x4chat-help"
    assert "ambient_context" in responses[0]["text"]
    assert "trade_in_sector" in responses[0]["text"]
    assert "faction_state" in responses[0]["text"]
    assert "sector_objects" in responses[0]["text"]



def test_chat_bridge_defaults_unknown_chat_to_live_ambient_fetch() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "x4chat-smoke", "text": "test"}))
    fetch = _wait_for_fetch(transport)
    assert fetch["type"] == "fetch"
    assert fetch["intent"] == "ambient_context"
    assert fetch["question"] == "test"

    bridge.handle_message(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v2",
                "trigger": "fetch_response",
                "sector_raw": "Windfall I Union Summit",
                "ship_name": "Raleigh (Container)",
                "player_money": 39362,
            }
        )
    )
    responses = _wait_for_responses(transport, 1)
    assert responses == [{"type": "chat_response", "id": "x4chat-smoke", "text": "answer for test: ambient_context"}]


class CurlyResponder:
    def answer(self, question: str, payload: TelemetryPayload, session: CockpitSessionContext | None = None) -> str:
        return "you’re in Président’s range — don’t panic…"


def test_chat_bridge_normalizes_response_text_for_x4_chat_display() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0), transport=transport, responder=CurlyResponder())

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "x4chat-ascii", "text": "hallo"}))
    _wait_for_fetch(transport)
    bridge.handle_message(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v2",
                "trigger": "fetch_response",
                "sector_raw": "Windfall I Union Summit",
                "ship_name": "Raleigh (Container)",
                "player_money": 39362,
            }
        )
    )
    responses = _wait_for_responses(transport, 1)
    assert responses == [{"type": "chat_response", "id": "x4chat-ascii", "text": "you're in Président's range - don't panic..."}]



class LongResponder:
    def answer(self, question: str, payload: TelemetryPayload, session: CockpitSessionContext | None = None) -> str:
        return " ".join(f"segment-{index:03d}" for index in range(180))


def test_chat_bridge_chunks_long_chat_responses() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0, chat_response_chunk_chars=300), transport=transport, responder=LongResponder())

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "x4chat-long", "text": "hallo"}))
    _wait_for_fetch(transport)
    bridge.handle_message(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v2",
                "trigger": "fetch_response",
                "sector_raw": "Windfall I Union Summit",
            }
        )
    )
    responses = _wait_for_complete_chunked_response(transport)
    assert len(responses) > 1
    assert responses[0]["text"].startswith("[1/")
    assert responses[-1]["text"].startswith(f"[{len(responses)}/{len(responses)}]")
    assert all(len(item["text"]) <= 310 for item in responses)



def test_chat_bridge_ignores_transient_pipe_status_strings() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=0.01, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    bridge.handle_message("ERROR")
    bridge.handle_message("TIMEOUT")
    bridge.handle_message("")

    assert transport.writes == []


def test_chat_bridge_raises_disconnect_on_lua_garbage_collected_signal() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=0.01, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    try:
        bridge.handle_message("garbage_collected")
    except PipeDisconnectedError:
        pass
    else:  # pragma: no cover - assertion path
        raise AssertionError("garbage_collected must force pipe reconnect")

    assert transport.writes == []


def test_chat_bridge_reconnects_after_pipe_disconnect() -> None:
    class DisconnectingTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.connect_count = 0
            self.read_count = 0
            self.bridge: ChatPipeBridge | None = None

        def connect(self) -> None:
            self.connect_count += 1
            super().connect()

        def read(self) -> str:
            self.read_count += 1
            if self.read_count == 2 and self.bridge is not None:
                self.bridge.stop()
            raise PipeDisconnectedError("test disconnect")

    transport = DisconnectingTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=0.01, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())
    transport.bridge = bridge

    bridge.serve_forever()

    assert transport.connect_count == 2
    assert transport.read_count == 2


def test_chat_bridge_retries_when_pipe_instance_is_busy() -> None:
    class BusyTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.connect_count = 0
            self.bridge: ChatPipeBridge | None = None

        def connect(self) -> None:
            self.connect_count += 1
            if self.connect_count == 1:
                raise PipeBusyError("test busy")
            if self.bridge is not None:
                self.bridge.stop()
            super().connect()

    transport = BusyTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=0.01, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())
    transport.bridge = bridge

    bridge.serve_forever()

    assert transport.connect_count == 2


def test_chat_bridge_times_out_fail_closed_without_stale_answer() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=0.01, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "x4chat-2", "text": "what's selling near me?"}))
    responses = _wait_for_responses(transport, 1)
    assert len(responses) == 1
    assert responses[0]["id"] == "x4chat-2"
    assert "error" in responses[0]
    assert "timed out" in responses[0]["text"]



class SessionAwareResponder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TelemetryPayload, CockpitSessionContext | None]] = []

    def answer(self, question: str, payload: TelemetryPayload, session: CockpitSessionContext | None = None) -> str:
        self.calls.append((question, payload, session))
        prior = len(session.recent_turns) if session is not None else -1
        return f"scope={session.scope.save_scope_id if session else 'none'} prior={prior} credits={payload.ambient.credits}"


def _ambient_raw(*, credits: int = 100, sector: str = "Windfall I", ship_name: str = "Raleigh") -> dict:
    return {
        "type": "telemetry_raw",
        "intent": "ambient_context",
        "source": "x4_lua_live",
        "schema": "ambient_probe_v2",
        "trigger": "fetch_response",
        "sector_raw": sector,
        "ship_name": ship_name,
        "player_money": credits,
    }


def _wait_for_fetch(transport: FakeTransport, *, count: int = 1, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        fetches = []
        for item in transport.writes:
            parsed = json.loads(item)
            if parsed.get("type") == "fetch":
                fetches.append(parsed)
        if len(fetches) >= count:
            return fetches[count - 1]
        time.sleep(0.01)
    raise AssertionError(f"fetch {count} not written; saw {len(fetches) if 'fetches' in locals() else 0}")


def _wait_for_responder_calls(responder: SessionAwareResponder, count: int, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if len(responder.calls) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"expected {count} responder calls, saw {len(responder.calls)}")


def _responses(transport: FakeTransport) -> list[dict]:
    return [json.loads(item) for item in transport.writes if json.loads(item).get("type") == "chat_response"]


def _wait_for_responses(transport: FakeTransport, count: int, *, timeout_s: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        responses = _responses(transport)
        if len(responses) >= count:
            return responses
        time.sleep(0.01)
    raise AssertionError(f"expected {count} chat responses, saw {len(_responses(transport))}")


def _wait_for_complete_chunked_response(transport: FakeTransport, *, timeout_s: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        responses = _responses(transport)
        if responses and responses[-1]["text"].startswith(f"[{len(responses)}/{len(responses)}]"):
            return responses
        time.sleep(0.01)
    responses = _responses(transport)
    raise AssertionError(f"chunked response incomplete; saw {len(responses)} chunks")


def test_chat_bridge_persists_save_scoped_transcript_and_followup_context(tmp_path) -> None:
    transport = FakeTransport()
    responder = SessionAwareResponder()
    bridge = ChatPipeBridge(
        ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0, session_state_root=str(tmp_path)),
        transport=transport,
        responder=responder,
    )

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "a1", "save_scope_id": "Save Alpha", "text": "hello"}))
    _wait_for_fetch(transport, count=1)
    bridge.handle_message(json.dumps(_ambient_raw(credits=100)))
    _wait_for_responder_calls(responder, 1)

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "a2", "save_scope_id": "Save Alpha", "text": "what about that?"}))
    _wait_for_fetch(transport, count=2)
    bridge.handle_message(json.dumps(_ambient_raw(credits=150)))
    _wait_for_responder_calls(responder, 2)

    assert responder.calls[0][2] is not None
    assert responder.calls[0][2].scope.save_scope_id == "save-alpha"
    assert responder.calls[0][2].recent_turns == []
    assert responder.calls[1][2] is not None
    assert len(responder.calls[1][2].recent_turns) == 1
    assert responder.calls[1][1].ambient.credits == 150
    transcript = tmp_path / "sessions" / "save-alpha" / "transcript.jsonl"
    assert transcript.exists()
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert [record["question"] for record in records] == ["hello", "what about that?"]


def test_chat_bridge_isolates_different_save_scopes(tmp_path) -> None:
    transport = FakeTransport()
    responder = SessionAwareResponder()
    bridge = ChatPipeBridge(
        ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0, session_state_root=str(tmp_path)),
        transport=transport,
        responder=responder,
    )

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "a1", "save_scope_id": "Save A", "text": "remember this"}))
    _wait_for_fetch(transport, count=1)
    bridge.handle_message(json.dumps(_ambient_raw(credits=10)))
    _wait_for_responder_calls(responder, 1)

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "b1", "save_scope_id": "Save B", "text": "what did I say?"}))
    _wait_for_fetch(transport, count=2)
    bridge.handle_message(json.dumps(_ambient_raw(credits=20)))
    _wait_for_responder_calls(responder, 2)

    assert responder.calls[0][2].scope.save_scope_id == "save-a"
    assert responder.calls[1][2].scope.save_scope_id == "save-b"
    assert responder.calls[1][2].recent_turns == []
    assert (tmp_path / "sessions" / "save-a" / "transcript.jsonl").exists()
    assert (tmp_path / "sessions" / "save-b" / "transcript.jsonl").exists()


def test_session_commands_use_save_scoped_store(tmp_path) -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(
        ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0, session_state_root=str(tmp_path)),
        transport=transport,
        responder=SessionAwareResponder(),
    )

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "s1", "save_scope_id": "Save A", "text": "session"}))
    _wait_for_fetch(transport)
    bridge.handle_message(json.dumps(_ambient_raw(credits=1)))
    text = _wait_for_responses(transport, 1)[0]["text"]
    assert "save-a" in text
    assert "transcript=" in text
    assert str(tmp_path) in text


def test_save_command_binds_subsequent_unlabelled_turns(tmp_path) -> None:
    transport = FakeTransport()
    responder = SessionAwareResponder()
    bridge = ChatPipeBridge(
        ChatBridgeConfig(fetch_timeout_s=1.0, chat_timeout_s=1.0, session_state_root=str(tmp_path)),
        transport=transport,
        responder=responder,
    )

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "bind", "text": "save Campaign One"}))
    _wait_for_fetch(transport, count=1)
    bridge.handle_message(json.dumps(_ambient_raw(credits=1)))
    _wait_for_responses(transport, 1)

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "next", "text": "hello again"}))
    _wait_for_fetch(transport, count=2)
    bridge.handle_message(json.dumps(_ambient_raw(credits=2)))
    _wait_for_responder_calls(responder, 1)

    assert _responses(transport)[0]["text"] == "Bound X4 cockpit session to save scope campaign-one."
    assert responder.calls[0][2].scope.save_scope_id == "campaign-one"
    assert (tmp_path / "sessions" / "campaign-one" / "transcript.jsonl").exists()


def test_hermes_responder_uses_isolated_hermes_home_and_continue(monkeypatch, tmp_path) -> None:

    from x4_copilot.cockpit_session import CockpitSessionStore, SaveScope

    calls = []

    class Completed:
        returncode = 0
        stdout = "ok from hermes\n"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    store = CockpitSessionStore(tmp_path)
    scope = SaveScope("save-a", "explicit", {"source": "test"})
    session = store.context(scope)
    payload = TelemetryPayload.from_dict({"intent": "ambient_context", "ambient": {"sector": "new", "credits": 999}})

    answer = HermesAgentResponder(command="hermes-test", timeout_s=5).answer("credits?", payload, session)

    assert answer == "ok from hermes"
    command, kwargs = calls[0]
    assert command[:3] == ["hermes-test", "--continue", "x4-save-save-a"]
    assert kwargs["env"]["HERMES_HOME"] == str(tmp_path / "hermes-home")
    assert kwargs["env"]["HERMES_HOME"] != __import__("os").environ.get("HERMES_HOME")
    assert str(tmp_path / "hermes-home") in session.hermes_home


def test_missing_save_scope_fails_closed_when_derivation_disabled(tmp_path) -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(
        ChatBridgeConfig(
            fetch_timeout_s=1.0,
            chat_timeout_s=1.0,
            session_state_root=str(tmp_path),
            allow_derived_save_scope=False,
        ),
        transport=transport,
        responder=SessionAwareResponder(),
    )

    bridge.handle_message(json.dumps({"type": "chat_request", "id": "no-save", "text": "hello"}))
    _wait_for_fetch(transport)
    bridge.handle_message(json.dumps(_ambient_raw(credits=1)))
    response = _wait_for_responses(transport, 1)[0]
    assert "error" in response
    assert "missing save scope" in response["text"]
    assert not (tmp_path / "sessions").exists()
