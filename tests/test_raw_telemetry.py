import json

from x4_copilot.server import X4CopilotServer, append_raw_telemetry


def test_raw_telemetry_message_is_acknowledged_and_logged(tmp_path, monkeypatch):
    log_path = tmp_path / "raw.jsonl"
    monkeypatch.setattr("x4_copilot.server.RAW_TELEMETRY_LOG", log_path)

    server = X4CopilotServer(fetcher=lambda request: None, advisor=object())  # type: ignore[arg-type]
    response = server.handle_message(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v1",
                "ship_name": "Raven",
                "sector_raw": "Windfall I Union Summit",
                "hullpercent": 91.5,
                "shieldpercent": 42,
            }
        )
    )

    assert json.loads(response) == {
        "type": "telemetry_raw_ack",
        "intent": "ambient_context",
        "source": "x4_lua_live",
    }
    logged = json.loads(log_path.read_text(encoding="utf-8"))
    assert logged["type"] == "telemetry_raw"
    assert logged["ship_name"] == "Raven"


def test_append_raw_telemetry_creates_parent_directory(tmp_path):
    log_path = tmp_path / "nested" / "raw.jsonl"
    append_raw_telemetry({"type": "telemetry_raw", "value": "literal"}, path=log_path)
    assert json.loads(log_path.read_text(encoding="utf-8"))["value"] == "literal"
