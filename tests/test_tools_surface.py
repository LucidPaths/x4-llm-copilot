import json
import threading
import time

from x4_copilot.models import AmbientContext, TelemetryPayload
from x4_copilot.protocol import FetchRequest
from x4_copilot.tools import (
    FetchProvenance,
    LivePipeTelemetryFetcher,
    MockTelemetryFetcher,
    RawTelemetryLogFetcher,
    SerializedFetcher,
    X4ToolSurface,
    create_live_raw_log_tool_surface,
    create_mock_tool_surface,
    telemetry_payload_from_raw_ambient,
)


def test_mock_tool_surface_returns_all_read_tool_shapes():
    surface = create_mock_tool_surface()

    ambient = surface.get_ambient_context()
    assert ambient["sector"] == "Windfall I Union Summit"
    assert ambient["credits"] == 39670
    assert ambient["source"] == "mock"
    assert ambient["stale"] is True

    trade = surface.fetch_trade_offers()
    assert trade["intent"] == "trade_in_sector"
    assert trade["source"] == "mock"
    assert trade["stale"] is True
    assert trade["offers"][0]["unit"] == "cr/u"

    ship = surface.fetch_ship_status()
    assert ship["intent"] == "ship_status"
    assert ship["status"]["hull"] == "91%"

    faction = surface.fetch_faction_state(since="1h")
    assert faction["intent"] == "faction_state"
    assert faction["relations"][0]["faction"] == "Argon Federation"
    assert faction["events"]

    objects = surface.fetch_sector_objects(kinds=["station", "gate"])
    assert objects["intent"] == "sector_objects"
    assert {obj["type"] for obj in objects["objects"]} == {"station", "gate"}


def test_provenance_does_not_sniff_as_of_text():
    def live_fetcher(request: FetchRequest) -> TelemetryPayload:
        return TelemetryPayload(
            intent=request.intent,
            ambient=AmbientContext(sector="Live Sector"),
            data=[],
            as_of="live fixture word appears in a real timestamp label",
        )

    surface = X4ToolSurface(live_fetcher, provenance=FetchProvenance(source="live", stale=False))
    result = surface.fetch_trade_offers()

    assert result["as_of"] == "live fixture word appears in a real timestamp label"
    assert result["source"] == "live"
    assert result["stale"] is False


def test_faction_state_accepts_itemized_live_shape_not_only_nested_fixture_shape():
    def itemized_fetcher(request: FetchRequest) -> TelemetryPayload:
        return TelemetryPayload(
            intent=request.intent,
            ambient=AmbientContext(sector="Frontier Edge"),
            data=[
                {"kind": "relation", "faction": "Argon Federation", "standing": 10, "trend": "rising"},
                {"type": "combat", "summary": "Xenon raid repelled", "age_min": 3},
            ],
        )

    result = X4ToolSurface(itemized_fetcher).fetch_faction_state()

    assert result["relations"] == [{"kind": "relation", "faction": "Argon Federation", "standing": 10, "trend": "rising"}]
    assert result["events"] == [{"type": "combat", "summary": "Xenon raid repelled", "age_min": 3}]


def test_ambient_context_uses_dedicated_fetch_intent():
    seen: list[FetchRequest] = []

    def recording_fetcher(request: FetchRequest) -> TelemetryPayload:
        seen.append(request)
        return TelemetryPayload(intent=request.intent, ambient=AmbientContext(sector="Ambient Only"), data=[])

    ambient = X4ToolSurface(recording_fetcher).get_ambient_context()

    assert ambient["sector"] == "Ambient Only"
    assert seen == [FetchRequest(intent="ambient_context", args={"ambient_only": True})]


def test_actions_are_default_off_and_do_not_mutate():
    surface = create_mock_tool_surface()
    waypoint = surface.set_waypoint(station_id="station-1", confirm_token="yes")
    mark = surface.mark_target(object_id="object-1", confirm_token="yes")

    assert waypoint == {
        "ok": False,
        "confirmed": False,
        "action": "set_waypoint",
        "error": "actions disabled by default",
        "args": {"station_id": "station-1", "pos": None},
    }
    assert mark["ok"] is False
    assert mark["error"] == "actions disabled by default"


def test_actions_enabled_still_refuses_until_action_transport_exists():
    surface = X4ToolSurface(MockTelemetryFetcher(), actions_enabled=True)

    unconfirmed = surface.set_waypoint(station_id="station-1")
    confirmed = surface.mark_target(object_id="object-1", confirm_token="yes")

    assert unconfirmed["ok"] is False
    assert unconfirmed["error"] == "confirmation token required"
    assert confirmed["ok"] is False
    assert confirmed["confirmed"] is True
    assert confirmed["error"] == "action transport is not implemented; no game state was changed"


def test_mock_fetcher_unknown_intent_returns_empty_unknown_payload_without_fabricated_ambient():
    payload = MockTelemetryFetcher()(FetchRequest(intent="unknown", args={}))
    assert payload.intent == "unknown"
    assert payload.data == []
    assert payload.as_of == "unknown intent; no telemetry fixture selected"
    assert payload.ambient == AmbientContext()


def test_serialized_fetcher_prevents_parallel_fetcher_calls():
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_fetcher(request: FetchRequest) -> TelemetryPayload:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return TelemetryPayload(intent=request.intent, ambient=AmbientContext(), data=[])

    serialized = SerializedFetcher(slow_fetcher)
    threads = [threading.Thread(target=serialized, args=(FetchRequest(intent="ship_status", args={}),)) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1


def test_mcp_module_imports_without_optional_sdk():
    import x4_copilot.mcp_server as mcp_server

    assert callable(mcp_server.build_mcp_server)


def test_raw_ambient_probe_maps_to_stable_telemetry_payload():
    payload = telemetry_payload_from_raw_ambient(
        {
            "type": "telemetry_raw",
            "intent": "ambient_context",
            "source": "x4_lua_live",
            "schema": "ambient_probe_v2",
            "player_id": "212884ULL",
            "ship_id": "212875ULL",
            "ship_name": "Raleigh (Container)",
            "sector_raw": "Windfall I Union Summit",
            "player_money": 123456,
            "cargo_raw": {"energycells": 42},
            "hullpercent": 100,
            "shieldpercent": 100,
        }
    )

    assert payload.intent == "ambient_context"
    assert payload.ambient == AmbientContext(sector="Windfall I Union Summit", ship="Raleigh (Container)", credits=123456)
    assert payload.data == [
        {
            "kind": "ship_status",
            "player_id": "212884ULL",
            "ship_id": "212875ULL",
            "hull_percent": 100,
            "shield_percent": 100,
            "cargo_raw": {"energycells": 42},
        }
    ]


def test_live_raw_log_surface_returns_live_ambient_and_mock_fallback(tmp_path):
    raw_log = tmp_path / "live_telemetry_raw.jsonl"
    raw_log.write_text(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v2",
                "ship_name": "Raleigh (Container)",
                "sector_raw": "Windfall I Union Summit",
                "player_money": 123456,
                "cargo_raw": {"energycells": 42},
                "hullpercent": 100,
                "shieldpercent": 100,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    surface = create_live_raw_log_tool_surface(raw_log)
    ambient = surface.get_ambient_context()
    trade = surface.fetch_trade_offers()

    assert ambient["sector"] == "Windfall I Union Summit"
    assert ambient["ship"] == "Raleigh (Container)"
    assert ambient["credits"] == 123456
    assert ambient["source"] == "x4_lua_live_raw_log"
    assert ambient["stale"] is False
    assert trade["source"] == "mock"
    assert trade["stale"] is True


def test_raw_log_fetcher_refuses_unsupported_intents_without_fixture_fallback(tmp_path):
    raw_log = tmp_path / "live_telemetry_raw.jsonl"
    raw_log.write_text("{}\n", encoding="utf-8")

    try:
        RawTelemetryLogFetcher(raw_log)(FetchRequest(intent="trade_in_sector", args={}))
    except Exception as exc:  # noqa: BLE001 - exact public exception is asserted by message
        assert "only supports ambient_context/ship_status" in str(exc)
    else:
        raise AssertionError("RawTelemetryLogFetcher must not fabricate trade data")


def test_live_pipe_fetcher_uses_request_response_not_reload_probe(tmp_path):
    class FakeTransport:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.messages = [
                json.dumps(
                    {
                        "type": "telemetry_raw",
                        "intent": "ambient_context",
                        "source": "x4_lua_live",
                        "schema": "ambient_probe_v2",
                        "trigger": "reload_probe",
                        "sector_raw": "Old Sector",
                        "player_money": 1,
                        "cargo_raw": [],
                    }
                ),
                json.dumps({"type": "ping"}),
                json.dumps(
                    {
                        "type": "telemetry_raw",
                        "intent": "ambient_context",
                        "source": "x4_lua_live",
                        "schema": "ambient_probe_v2",
                        "trigger": "reload_probe",
                        "sector_raw": "Still Not It",
                        "player_money": 2,
                        "cargo_raw": [],
                    }
                ),
                json.dumps(
                    {
                        "type": "telemetry_raw",
                        "intent": "ambient_context",
                        "source": "x4_lua_live",
                        "schema": "ambient_probe_v2",
                        "trigger": "fetch_response",
                        "sector_raw": "Fresh Sector",
                        "player_money": 3,
                        "cargo_raw": {"water": 4},
                    }
                ),
            ]

        def connect(self) -> None:
            pass

        def read(self) -> str:
            return self.messages.pop(0)

        def write(self, message: str) -> None:
            self.writes.append(message)

        def close(self) -> None:
            pass

    raw_log = tmp_path / "live_telemetry_raw.jsonl"
    fetcher = LivePipeTelemetryFetcher(transport=FakeTransport(), raw_log_path=raw_log)
    payload = fetcher(FetchRequest(intent="ambient_context", args={"ambient_only": True}))

    assert payload.ambient.sector == "Fresh Sector"
    assert payload.ambient.credits == 3
    assert payload.data[0]["cargo_raw"] == {"water": 4}
    assert any('"type": "fetch"' in write for write in fetcher._transport.writes)  # type: ignore[union-attr]
    assert raw_log.read_text(encoding="utf-8").count("telemetry_raw") == 3


def test_live_pipe_fetcher_timeout_raises_instead_of_replaying_log(tmp_path):
    class HangingTransport:
        def connect(self) -> None:
            pass

        def read(self) -> str:
            time.sleep(1)
            return "{}"

        def write(self, message: str) -> None:
            pass

        def close(self) -> None:
            pass

    raw_log = tmp_path / "live_telemetry_raw.jsonl"
    raw_log.write_text(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v2",
                "sector_raw": "Stale Sector",
                "player_money": 999,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fetcher = LivePipeTelemetryFetcher(transport=HangingTransport(), raw_log_path=raw_log, timeout_s=0.01)

    try:
        fetcher(FetchRequest(intent="ambient_context", args={}))
    except Exception as exc:  # noqa: BLE001 - public contract is fail-closed, asserted by message
        assert "timed out" in str(exc)
    else:
        raise AssertionError("live pipe timeout must fail closed, not replay JSONL")


def test_tool_results_are_json_serializable():
    surface = X4ToolSurface(MockTelemetryFetcher())
    json.dumps(surface.fetch_trade_offers())
    json.dumps(surface.fetch_faction_state())
    json.dumps(surface.fetch_sector_objects())
