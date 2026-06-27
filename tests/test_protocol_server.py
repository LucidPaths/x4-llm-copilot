import json
from pathlib import Path

from x4_copilot.models import TelemetryPayload
from x4_copilot.protocol import FetchRequest
from x4_copilot.server import X4CopilotServer

ROOT = Path(__file__).resolve().parents[1]


def test_fetch_request_from_question_is_json_fetch():
    msg = json.loads(FetchRequest.from_question("best trade run here").to_json())
    assert msg["type"] == "fetch"
    assert msg["intent"] == "trade_in_sector"


def test_server_answers_telemetry_message():
    def fetcher(_request):
        raise AssertionError("telemetry message should not call fetcher")

    raw = json.loads((ROOT / "examples" / "trade_payload.json").read_text(encoding="utf-8"))
    raw["question"] = "what's selling here?"
    response = json.loads(X4CopilotServer(fetcher=fetcher).handle_message(json.dumps(raw)))
    assert response["type"] == "answer"
    assert response["intent"] == "trade_in_sector"
    assert "hull_parts" in response["answer"]


def test_server_question_uses_fetcher():
    def fetcher(request):
        assert request.intent == "ship_status"
        raw = json.loads((ROOT / "examples" / "ship_status_payload.json").read_text(encoding="utf-8"))
        return TelemetryPayload.from_dict(raw)

    response = json.loads(X4CopilotServer(fetcher=fetcher).handle_question("ship status"))
    assert "Raleigh Condensate" in response["answer"]
