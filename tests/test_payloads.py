import json
from pathlib import Path

from x4_copilot.advisor import GroundedAdvisor
from x4_copilot.models import PayloadError, TelemetryPayload

ROOT = Path(__file__).resolve().parents[1]


def test_trade_payload_parses_and_keeps_units():
    raw = json.loads((ROOT / "examples" / "trade_payload.json").read_text(encoding="utf-8"))
    payload = TelemetryPayload.from_dict(raw)
    offers = payload.trade_offers()
    assert offers[0].ware == "hull_parts"
    assert offers[0].unit == "cr/u"
    assert offers[0].spread == 700


def test_advisor_refuses_to_fabricate_empty_trade_data():
    payload = TelemetryPayload.from_dict({"intent": "trade_in_sector", "ambient": {"sector": "Foo"}, "data": []})
    answer = GroundedAdvisor().answer("prices?", payload)
    assert "won't invent" in answer


def test_malformed_position_is_rejected():
    try:
        TelemetryPayload.from_dict({"ambient": {"pos": [1, 2]}, "data": []})
    except PayloadError as exc:
        assert "pos" in str(exc)
    else:
        raise AssertionError("expected PayloadError")
