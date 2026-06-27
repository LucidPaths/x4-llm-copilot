import json
import threading
import time

from x4_copilot.models import AmbientContext, TelemetryPayload
from x4_copilot.protocol import FetchRequest
from x4_copilot.tools import (
    FetchProvenance,
    MockTelemetryFetcher,
    SerializedFetcher,
    X4ToolSurface,
    create_mock_tool_surface,
)


def test_mock_tool_surface_returns_all_read_tool_shapes():
    surface = create_mock_tool_surface()

    ambient = surface.get_ambient_context()
    assert ambient["sector"] == "Grand Exchange IV"
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


def test_tool_results_are_json_serializable():
    surface = X4ToolSurface(MockTelemetryFetcher())
    json.dumps(surface.fetch_trade_offers())
    json.dumps(surface.fetch_faction_state())
    json.dumps(surface.fetch_sector_objects())
