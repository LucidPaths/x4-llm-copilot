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
    telemetry_payload_from_raw_faction_state,
    telemetry_payload_from_raw_trade,
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


def test_faction_state_normalizes_observed_live_raw_shape_and_preserves_raw():
    raw = {
        "type": "telemetry_raw",
        "intent": "faction_state",
        "source": "x4_lua_live_pipe",
        "schema": "faction_state_v1",
        "trigger": "fetch_response",
        "standings_raw": [
            {
                "faction": "loanshark",
                "faction_name": "Vigor Syndicate",
                "faction_shortname": "VIG",
                "standing": 10,
                "relation_name": "Friend",
                "rank_title": "Syndicate Enforcer",
                "licences_raw": [{"type": "ceremonyfriend", "name": "Syndicate Enforcer", "isrank": True}],
            }
        ],
        "events_raw": [
            {
                "kind": "diplomacy",
                "eventid": "promotion_loanshark",
                "event_name": "Syndicate Enforcer promotion",
                "faction": "loanshark",
                "outcome": "promoted",
                "active": False,
            }
        ],
    }

    payload = telemetry_payload_from_raw_faction_state(raw)
    result = X4ToolSurface(lambda request: payload, provenance=FetchProvenance(source="x4_lua_live_pipe")).fetch_faction_state()

    assert result["relations"] == [
        {
            "kind": "faction_standing",
            "faction": "loanshark",
            "faction_name": "Vigor Syndicate",
            "faction_shortname": "VIG",
            "standing": 10,
            "relation_name": "Friend",
            "rank_title": "Syndicate Enforcer",
            "rank_title_raw": "Syndicate Enforcer",
            "licences_raw": [{"type": "ceremonyfriend", "name": "Syndicate Enforcer", "isrank": True}],
            "raw": raw["standings_raw"][0],
        }
    ]
    assert result["events"][0]["kind"] == "promotion"
    assert result["events"][0]["faction"] == "loanshark"
    assert result["events"][0]["raw"] == raw["events_raw"][0]


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


def test_raw_trade_probe_preserves_offer_shape_before_schema_lock():
    payload = telemetry_payload_from_raw_trade(
        {
            "type": "telemetry_raw",
            "intent": "trade_in_sector",
            "source": "x4_lua_live",
            "schema": "trade_offers_probe_v1",
            "ship_name": "Raleigh (Container)",
            "sector_raw": "Windfall I Union Summit",
            "player_money": 39392,
            "trade_container_id": "12345",
            "trade_container_name": "Test Station",
            "offers_raw": [
                {"ware": "water", "amount": 12, "price": 30, "isbuyoffer": True},
                {"ware": "energycells", "amount": 99, "price": 10, "isselloffer": True},
            ],
            "nontrade_offers_raw": [],
        }
    )

    assert payload.intent == "trade_in_sector"
    assert payload.ambient == AmbientContext(sector="Windfall I Union Summit", ship="Raleigh (Container)", credits=39392)
    assert payload.data == [
        {
            "kind": "trade_offer",
            "id": None,
            "ware": "water",
            "name": None,
            "side": "buy",
            "price": 30,
            "market_price": None,
            "amount": 12,
            "min_amount": None,
            "desired_amount": None,
            "station_id": None,
            "station": None,
            "station_sector_id": None,
            "faction": None,
            "is_supply": False,
            "is_shady": False,
            "is_mission": False,
            "raw": {"ware": "water", "amount": 12, "price": 30, "isbuyoffer": True},
        },
        {
            "kind": "trade_offer",
            "id": None,
            "ware": "energycells",
            "name": None,
            "side": "sell",
            "price": 10,
            "market_price": None,
            "amount": 99,
            "min_amount": None,
            "desired_amount": None,
            "station_id": None,
            "station": None,
            "station_sector_id": None,
            "faction": None,
            "is_supply": False,
            "is_shady": False,
            "is_mission": False,
            "raw": {"ware": "energycells", "amount": 99, "price": 10, "isselloffer": True},
        },
    ]


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


def test_live_pipe_fetcher_routes_trade_request_to_raw_trade_payload(tmp_path):
    class FakeTradeTransport:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.messages = [
                json.dumps(
                    {
                        "type": "telemetry_raw",
                        "intent": "trade_in_sector",
                        "source": "x4_lua_live",
                        "schema": "trade_offers_probe_v1",
                        "trigger": "fetch_response",
                        "sector_raw": "Windfall I Union Summit",
                        "ship_name": "Raleigh (Container)",
                        "player_money": 39392,
                        "offers_raw": [{"ware": "water", "amount": 7, "price": 30}],
                        "nontrade_offers_raw": [],
                    }
                )
            ]

        def connect(self) -> None:
            pass

        def read(self) -> str:
            return self.messages.pop(0)

        def write(self, message: str) -> None:
            self.writes.append(message)

        def close(self) -> None:
            pass

    fetcher = LivePipeTelemetryFetcher(transport=FakeTradeTransport(), raw_log_path=tmp_path / "raw.jsonl")
    payload = fetcher(FetchRequest(intent="trade_in_sector", args={"scope": "docked_station"}))

    assert payload.intent == "trade_in_sector"
    assert payload.data[0] | {"raw": None} == {
        "kind": "trade_offer",
        "id": None,
        "ware": "water",
        "name": None,
        "side": "unknown",
        "price": 30,
        "market_price": None,
        "amount": 7,
        "min_amount": None,
        "desired_amount": None,
        "station_id": None,
        "station": None,
        "station_sector_id": None,
        "faction": None,
        "is_supply": False,
        "is_shady": False,
        "is_mission": False,
        "raw": None,
    }
    assert payload.data[0]["raw"] == {"ware": "water", "amount": 7, "price": 30}
    assert any('"intent": "trade_in_sector"' in write for write in fetcher._transport.writes)  # type: ignore[union-attr]
    assert any('"scope": "docked_station"' in write for write in fetcher._transport.writes)  # type: ignore[union-attr]


def test_live_pipe_fetcher_accepts_radar_range_trade_schema(tmp_path):
    class FakeRadarTransport:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.messages = [
                json.dumps(
                    {
                        "type": "telemetry_raw",
                        "intent": "trade_in_sector",
                        "source": "x4_lua_live",
                        "schema": "trade_offers_radar_v1",
                        "trigger": "fetch_response",
                        "sector_raw": "Windfall I Union Summit",
                        "ship_name": "Raleigh (Container)",
                        "player_money": 39392,
                        "station_cap": 32,
                        "offer_cap": 200,
                        "stations_raw": [
                            {
                                "id": "12345",
                                "name": "VIG Ice Refinery I",
                                "sectorid": "99",
                                "distance_m": 3210,
                                "distance_km": 3.21,
                                "offers_raw": [
                                    {"ware": "ice", "amount": 7, "price": 30, "station": "12345", "stationname": "VIG Ice Refinery I", "distance_m": 3210, "distance_km": 3.21}
                                ],
                            }
                        ],
                    }
                )
            ]

        def connect(self) -> None:
            pass

        def read(self) -> str:
            return self.messages.pop(0)

        def write(self, message: str) -> None:
            self.writes.append(message)

        def close(self) -> None:
            pass

    fetcher = LivePipeTelemetryFetcher(transport=FakeRadarTransport(), raw_log_path=tmp_path / "raw.jsonl")
    payload = fetcher(FetchRequest(intent="trade_in_sector", args={"scope": "radar_range"}))

    assert payload.intent == "trade_in_sector"
    assert payload.data[0]["scope"] == "radar_range"
    assert payload.data[0]["station"] == "VIG Ice Refinery I"
    assert payload.data[0]["station_distance_m"] == 3210
    assert payload.data[0]["station_distance_km"] == 3.21
    assert payload.data[0]["raw"]["ware"] == "ice"
    assert any('"scope": "radar_range"' in write for write in fetcher._transport.writes)  # type: ignore[union-attr]


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


def test_live_pipe_fetcher_wall_clock_timeout_on_probe_churn(tmp_path):
    class ProbeChurnTransport:
        def __init__(self) -> None:
            self.closed = False

        def connect(self) -> None:
            pass

        def read(self) -> str:
            time.sleep(0.004)
            return json.dumps(
                {
                    "type": "telemetry_raw",
                    "intent": "ambient_context",
                    "source": "x4_lua_live",
                    "schema": "ambient_probe_v2",
                    "trigger": "reload_probe",
                    "sector_raw": "Probe Churn",
                    "player_money": 1,
                    "cargo_raw": [],
                }
            )

        def write(self, message: str) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    transport = ProbeChurnTransport()
    fetcher = LivePipeTelemetryFetcher(transport=transport, raw_log_path=tmp_path / "raw.jsonl", timeout_s=0.02)

    try:
        fetcher(FetchRequest(intent="ambient_context", args={}))
    except Exception as exc:  # noqa: BLE001 - public contract is fail-closed, asserted by message
        assert "fetch_response timed out" in str(exc)
    else:
        raise AssertionError("probe churn must hit the wall-clock fetch_response deadline")
    assert transport.closed is True


def test_tool_results_are_json_serializable():
    surface = X4ToolSurface(MockTelemetryFetcher())
    json.dumps(surface.fetch_trade_offers())
    json.dumps(surface.fetch_faction_state())
    json.dumps(surface.fetch_sector_objects())
