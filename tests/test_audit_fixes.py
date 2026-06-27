import json

from x4_copilot.advisor import GroundedAdvisor
from x4_copilot.intent import classify
from x4_copilot.models import PayloadError, TelemetryPayload
from x4_copilot.server import X4CopilotServer


class FlakyTransport:
    def __init__(self):
        self.connects = 0
        self.closed = 0
        self.writes = []

    def connect(self):
        self.connects += 1

    def read(self):
        if self.connects == 1:
            raise BrokenPipeError("simulated X4 reload")
        return json.dumps({"type": "ping"})

    def write(self, message: str):
        self.writes.append(message)

    def close(self):
        self.closed += 1


def test_transport_reconnects_after_read_break_before_success():
    server = X4CopilotServer(fetcher=lambda _request: (_ for _ in ()).throw(AssertionError("not used")))
    transport = FlakyTransport()
    server.serve_transport(transport, once=True, max_sessions=2)
    assert transport.connects == 2
    assert transport.closed == 2
    assert json.loads(transport.writes[-1]) == {"type": "pong"}


def test_intent_router_does_not_double_count_substrings_or_warehouse_war():
    trade = classify("what are goods selling for in this system")
    assert trade.intent == "trade_in_sector"
    assert trade.matched == ("selling", "goods")
    assert trade.confidence < 0.95
    assert classify("warehouse inventory").intent == "unknown"
    assert classify("split the cargo evenly").intent != "faction_state"


def test_trade_ranking_handles_zero_values_without_falsy_defaults():
    payload = TelemetryPayload.from_dict(
        {
            "intent": "trade_in_sector",
            "ambient": {"sector": "Test"},
            "data": [
                {"ware": "near_zero", "buy": 100, "sell": 100, "station": "Dock", "dist_km": 0},
                {"ware": "far_negative", "buy": 100, "sell": 99, "station": "Far", "dist_km": 99},
            ],
        }
    )
    answer = GroundedAdvisor().answer("best trade", payload)
    assert "near_zero" in answer
    assert "0 km" in answer


def test_bad_credits_raises_payload_error():
    try:
        TelemetryPayload.from_dict({"ambient": {"credits": "not-a-number"}, "data": []})
    except PayloadError as exc:
        assert "credits" in str(exc)
    else:
        raise AssertionError("expected PayloadError")
