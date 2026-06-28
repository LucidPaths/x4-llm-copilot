from __future__ import annotations

import json
import queue

from x4_copilot.chat_bridge import ChatBridgeConfig, ChatPipeBridge
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
    def answer(self, question: str, payload: TelemetryPayload) -> str:
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

    fetch = json.loads(transport.writes[0])
    assert fetch["type"] == "fetch"
    assert fetch["intent"] == "trade_in_sector"
    assert fetch["args"]["scope"] == "radar_range"

    bridge.handle_message(json.dumps(_radar_trade_raw()))
    bridge.wait_for_workers(timeout_s=1.0)

    responses = [json.loads(item) for item in transport.writes if json.loads(item).get("type") == "chat_response"]
    assert responses == [{"type": "chat_response", "id": "x4chat-1", "text": "answer for what's selling near me?: trade_in_sector"}]


def test_chat_bridge_ignores_transient_pipe_status_strings() -> None:
    transport = FakeTransport()
    bridge = ChatPipeBridge(ChatBridgeConfig(fetch_timeout_s=0.01, chat_timeout_s=1.0), transport=transport, responder=EchoResponder())

    bridge.handle_message("ERROR")
    bridge.handle_message("TIMEOUT")
    bridge.handle_message("")

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
    bridge.wait_for_workers(timeout_s=1.0)

    responses = [json.loads(item) for item in transport.writes if json.loads(item).get("type") == "chat_response"]
    assert len(responses) == 1
    assert responses[0]["id"] == "x4chat-2"
    assert "error" in responses[0]
    assert "timed out" in responses[0]["text"]
