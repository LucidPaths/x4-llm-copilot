from __future__ import annotations

import contextlib
import json
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import VALID_INTENTS, AmbientContext, Intent, PayloadError, TelemetryPayload
from .pipe import DuplexTransport, NamedPipeServer
from .protocol import FetchRequest, parse_json_message

TelemetryFetcher = Callable[[FetchRequest], TelemetryPayload]

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXAMPLES_DIR = PACKAGE_ROOT / "examples"
INTENT_FIXTURES: dict[Intent, str] = {
    "ambient_context": "ambient_context_payload.json",
    "trade_in_sector": "trade_payload.json",
    "ship_status": "ship_status_payload.json",
    "faction_state": "faction_state_payload.json",
    "sector_objects": "sector_objects_payload.json",
}
READ_TOOLS = frozenset(INTENT_FIXTURES)
DEFAULT_RAW_TELEMETRY_LOG = PACKAGE_ROOT / "var" / "live_telemetry_raw.jsonl"


@dataclass(frozen=True)
class FetchProvenance:
    """Structured provenance for tool results.

    This is deliberately separate from ``TelemetryPayload.as_of``. ``as_of`` is a
    human/display timestamp supplied by the adapter; it must not be parsed to
    decide whether data is mock, live, or stale.
    """

    source: str = "live_or_injected"
    stale: bool = False


@dataclass(frozen=True)
class FetchedTelemetry:
    payload: TelemetryPayload
    provenance: FetchProvenance


class SerializedFetcher:
    """Thread-safe wrapper for the single X4 pipe/fetcher chokepoint."""

    def __init__(self, fetcher: TelemetryFetcher) -> None:
        self._fetcher = fetcher
        self._lock = threading.Lock()

    def __call__(self, request: FetchRequest) -> TelemetryPayload:
        with self._lock:
            return self._fetcher(request)

    def provenance_for(self, request: FetchRequest) -> FetchProvenance | None:
        provenance_for = getattr(self._fetcher, "provenance_for", None)
        if callable(provenance_for):
            return provenance_for(request)
        return None


class MockTelemetryFetcher:
    """Fixture-backed fetcher for tool/MCP wiring before live X4 telemetry exists."""

    provenance = FetchProvenance(source="mock", stale=True)

    def __init__(self, examples_dir: str | Path = DEFAULT_EXAMPLES_DIR) -> None:
        self.examples_dir = Path(examples_dir)

    def __call__(self, request: FetchRequest) -> TelemetryPayload:
        if request.intent not in INTENT_FIXTURES:
            return TelemetryPayload(
                intent="unknown",
                ambient=AmbientContext(),
                data=[],
                as_of="unknown intent; no telemetry fixture selected",
            )
        path = self.examples_dir / INTENT_FIXTURES[request.intent]
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PayloadError(f"missing mock fixture for {request.intent}: {path}") from exc
        payload = TelemetryPayload.from_dict(raw, default_intent=request.intent)
        if payload.intent == "unknown":
            return TelemetryPayload(intent=request.intent, ambient=payload.ambient, data=payload.data, as_of=payload.as_of)
        return payload


class RawTelemetryLogFetcher:
    """Replay the latest literal live X4 Lua probe captured by the pipe server.

    This remains useful for schema capture and offline debugging, but it is not a
    live runtime fetcher: it can serve stale data if the last pipe write failed.
    Use ``LivePipeTelemetryFetcher`` when the tool call itself must trigger the
    X4 read and fail closed on pipe errors.
    """

    provenance = FetchProvenance(source="x4_lua_live_raw_log", stale=False)
    supported_intents = frozenset({"ambient_context", "ship_status"})

    def __init__(self, path: str | Path = DEFAULT_RAW_TELEMETRY_LOG) -> None:
        self.path = Path(path)

    def __call__(self, request: FetchRequest) -> TelemetryPayload:
        if request.intent not in {"ambient_context", "ship_status"}:
            raise PayloadError(f"live raw telemetry only supports ambient_context/ship_status, got {request.intent}")
        raw = read_latest_raw_telemetry(self.path)
        payload = telemetry_payload_from_raw_ambient(raw)
        if request.intent == "ship_status":
            return TelemetryPayload(intent="ship_status", ambient=payload.ambient, data=payload.data, as_of=payload.as_of)
        return payload


class LivePipeTelemetryFetcher:
    """On-demand live fetcher: request -> X4 Lua read -> direct response.

    The JSONL file is append-only observability, not the source of truth. A failed
    pipe transaction raises ``PayloadError`` instead of replaying the last good
    capture.
    """

    provenance = FetchProvenance(source="x4_lua_live_pipe", stale=False)
    supported_intents = frozenset({"ambient_context", "ship_status", "trade_in_sector", "faction_state"})

    def __init__(
        self,
        pipe_name: str = "x4_llm_copilot",
        *,
        raw_log_path: str | Path = DEFAULT_RAW_TELEMETRY_LOG,
        timeout_s: float = 8.0,
        transport: DuplexTransport | None = None,
    ) -> None:
        self.pipe_name = pipe_name
        self.raw_log_path = Path(raw_log_path)
        self.timeout_s = timeout_s
        self._transport = transport
        self._connected = False
        self._ready = False

    def __call__(self, request: FetchRequest) -> TelemetryPayload:
        if request.intent not in self.supported_intents:
            raise PayloadError(f"live pipe telemetry only supports ambient_context/ship_status/trade_in_sector/faction_state, got {request.intent}")
        if request.intent == "trade_in_sector":
            requested_scope = request.args.get("scope")
            if requested_scope is None and "radar_only" in request.args:
                requested_scope = "radar_range" if request.args.get("radar_only") else "docked_station"
            if requested_scope not in {None, "docked_station", "radar_range"}:
                raise PayloadError(f"unsupported live pipe trade scope: {requested_scope}")
        self._ensure_ready()
        self._write(request.to_json(), phase="send fetch request")
        raw = self._read_raw_fetch_response()
        if request.intent == "trade_in_sector":
            return telemetry_payload_from_raw_trade(raw)
        if request.intent == "faction_state":
            return telemetry_payload_from_raw_faction_state(raw)
        payload = telemetry_payload_from_raw_ambient(raw)
        if request.intent == "ship_status":
            return TelemetryPayload(intent="ship_status", ambient=payload.ambient, data=payload.data, as_of="fresh live pipe response")
        return TelemetryPayload(intent="ambient_context", ambient=payload.ambient, data=payload.data, as_of="fresh live pipe response")

    def _ensure_ready(self) -> None:
        transport = self._transport_or_raise()
        if not self._connected:
            self._call_transport(transport.connect, phase="connect to X4 pipe")
            self._connected = True
        self._ready = True

    def _read_raw_fetch_response(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._connected = False
                self._ready = False
                with contextlib.suppress(Exception):
                    self._transport_or_raise().close()
                raise PayloadError(f"live pipe fetch_response timed out after {self.timeout_s:g}s")
            message = parse_json_message(self._read(phase="read fetch response", timeout_s=remaining))
            msg_type = message.get("type")
            if msg_type == "ping":
                self._write(json.dumps({"type": "pong"}), phase="reply to ping", timeout_s=remaining)
                continue
            if msg_type != "telemetry_raw":
                raise PayloadError(f"expected telemetry_raw response from X4, got {msg_type}")
            append_live_raw_message(message, self.raw_log_path)
            self._write(_raw_ack(message), phase="ack fetch response", timeout_s=remaining)
            if message.get("trigger") == "fetch_response":
                return message
            # Development reload probes are useful evidence but must not satisfy
            # an on-demand fetch, or stale replay wins again.

    def _read(self, *, phase: str, timeout_s: float | None = None) -> str:
        return self._call_transport(self._transport_or_raise().read, phase=phase, timeout_s=timeout_s)

    def _write(self, message: str, *, phase: str, timeout_s: float | None = None) -> None:
        self._call_transport(lambda: self._transport_or_raise().write(message), phase=phase, timeout_s=timeout_s)

    def _call_transport(self, func: Callable[[], Any], *, phase: str, timeout_s: float | None = None) -> Any:
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result_queue.put((True, func()))
            except Exception as exc:  # noqa: BLE001 - cross-thread transport error propagation
                result_queue.put((False, exc))

        thread = threading.Thread(target=run, name=f"x4-live-pipe-{phase}", daemon=True)
        thread.start()
        effective_timeout = self.timeout_s if timeout_s is None else max(0.001, timeout_s)
        try:
            ok, value = result_queue.get(timeout=effective_timeout)
        except queue.Empty as exc:
            self._connected = False
            self._ready = False
            with contextlib.suppress(Exception):
                self._transport_or_raise().close()
            raise PayloadError(f"live pipe {phase} timed out after {effective_timeout:g}s") from exc
        if ok:
            return value
        self._connected = False
        self._ready = False
        raise PayloadError(f"live pipe {phase} failed: {value}") from value

    def _transport_or_raise(self) -> DuplexTransport:
        if self._transport is None:
            self._transport = NamedPipeServer(self.pipe_name, timeout_s=self.timeout_s)
        return self._transport


def append_live_raw_message(message: dict[str, Any], path: str | Path = DEFAULT_RAW_TELEMETRY_LOG) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")


def _raw_ack(message: dict[str, Any]) -> str:
    return json.dumps({"type": "telemetry_raw_ack", "intent": message.get("intent"), "source": message.get("source")}, ensure_ascii=False)


def read_latest_raw_telemetry(path: str | Path = DEFAULT_RAW_TELEMETRY_LOG) -> dict[str, Any]:
    path = Path(path)
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError as exc:
        raise PayloadError(f"live raw telemetry log not found: {path}") from exc
    if not lines:
        raise PayloadError(f"live raw telemetry log is empty: {path}")
    try:
        raw = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise PayloadError(f"latest raw telemetry line is not JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise PayloadError("latest raw telemetry line must be an object")
    return raw


def telemetry_payload_from_raw_ambient(raw: dict[str, Any]) -> TelemetryPayload:
    if raw.get("type") != "telemetry_raw":
        raise PayloadError("raw telemetry type must be telemetry_raw")
    if raw.get("intent") != "ambient_context":
        raise PayloadError("raw telemetry intent must be ambient_context")
    if raw.get("source") != "x4_lua_live":
        raise PayloadError("raw telemetry source must be x4_lua_live")
    if raw.get("schema") not in {"ambient_probe_v1", "ambient_probe_v2"}:
        raise PayloadError("raw telemetry schema must be ambient_probe_v1 or ambient_probe_v2")

    data: list[dict[str, Any]] = [
        {
            "kind": "ship_status",
            "player_id": _optional_raw_str(raw.get("player_id")),
            "ship_id": _optional_raw_str(raw.get("ship_id")),
            "hull_percent": _optional_raw_number(raw.get("hullpercent"), "hullpercent"),
            "shield_percent": _optional_raw_number(raw.get("shieldpercent"), "shieldpercent"),
            "cargo_raw": _optional_raw_json(raw.get("cargo_raw")),
        }
    ]
    return TelemetryPayload(
        intent="ambient_context",
        ambient=AmbientContext(
            sector=_optional_raw_str(raw.get("sector_raw")),
            ship=_optional_raw_str(raw.get("ship_name")),
            credits=_optional_raw_int(raw.get("player_money"), "player_money"),
        ),
        data=data,
        as_of="latest live raw Lua ambient probe",
    )


def telemetry_payload_from_raw_trade(raw: dict[str, Any]) -> TelemetryPayload:
    if raw.get("type") != "telemetry_raw":
        raise PayloadError("raw trade telemetry type must be telemetry_raw")
    if raw.get("intent") != "trade_in_sector":
        raise PayloadError("raw trade telemetry intent must be trade_in_sector")
    if raw.get("source") not in {"x4_lua_live", "x4_lua_live_pipe"}:
        raise PayloadError("raw trade telemetry source must be x4_lua_live or x4_lua_live_pipe")
    schema = raw.get("schema")
    if schema == "trade_offers_probe_v1":
        data = _normalize_docked_trade_payload(raw)
    elif schema == "trade_offers_radar_v1":
        data = _normalize_radar_trade_payload(raw)
    else:
        raise PayloadError("raw trade telemetry schema must be trade_offers_probe_v1 or trade_offers_radar_v1")

    return TelemetryPayload(
        intent="trade_in_sector",
        ambient=AmbientContext(
            sector=_optional_raw_str(raw.get("sector_raw")),
            ship=_optional_raw_str(raw.get("ship_name")),
            credits=_optional_raw_int(raw.get("player_money"), "player_money"),
        ),
        data=data,
        as_of="fresh live raw Lua trade probe",
    )


def telemetry_payload_from_raw_faction_state(raw: dict[str, Any]) -> TelemetryPayload:
    if raw.get("type") != "telemetry_raw":
        raise PayloadError("raw faction telemetry type must be telemetry_raw")
    if raw.get("intent") != "faction_state":
        raise PayloadError("raw faction telemetry intent must be faction_state")
    if raw.get("source") != "x4_lua_live_pipe":
        raise PayloadError("raw faction telemetry source must be x4_lua_live_pipe")
    if raw.get("schema") != "faction_state_v1":
        raise PayloadError("raw faction telemetry schema must be faction_state_v1")
    if raw.get("error"):
        raise PayloadError(f"raw faction telemetry error: {raw.get('error')}")

    data = _normalize_faction_state_payload(raw)
    return TelemetryPayload(
        intent="faction_state",
        ambient=AmbientContext(),
        data=data,
        as_of="fresh live raw Lua faction probe",
    )


def _normalize_faction_state_payload(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    standings_raw = raw.get("standings_raw")
    if isinstance(standings_raw, list):
        for standing in standings_raw:
            data.append(_normalize_faction_standing(standing))
    elif standings_raw is not None:
        data.append({"kind": "faction_standings_raw", "standings_raw": standings_raw})

    events_raw = raw.get("events_raw")
    if isinstance(events_raw, list):
        for event in events_raw:
            data.append(_normalize_faction_event(event))
    elif events_raw is not None:
        data.append({"kind": "faction_events_raw", "events_raw": events_raw})

    if not data:
        data.append({"kind": "faction_state_metadata", "standings_raw": [], "events_raw": []})
    return data


def _normalize_faction_standing(standing: Any) -> dict[str, Any]:
    if not isinstance(standing, dict):
        return {"kind": "faction_standing_raw", "raw": standing}
    standing_value = _optional_raw_int(standing.get("standing"), "standing")
    licences_raw = standing.get("licences_raw") if standing.get("licences_raw") is not None else []
    return {
        "kind": "faction_standing",
        "faction": _optional_raw_str(standing.get("faction")),
        "faction_name": _optional_raw_str(standing.get("faction_name")),
        "faction_shortname": _optional_raw_str(standing.get("faction_shortname")),
        "standing": standing_value,
        "relation_name": _optional_raw_str(standing.get("relation_name")),
        "rank_title": _current_rank_title(standing_value, licences_raw) or _optional_raw_str(standing.get("rank_title")),
        "rank_title_raw": _optional_raw_str(standing.get("rank_title")),
        "licences_raw": licences_raw,
        "raw": standing,
    }


def _current_rank_title(standing: int | None, licences_raw: Any) -> str | None:
    if standing is None or not isinstance(licences_raw, list):
        return None
    preferred_type = "ceremonyally" if standing >= 20 else "ceremonyfriend" if standing >= 10 else None
    if preferred_type is None:
        return None
    for licence in licences_raw:
        if isinstance(licence, dict) and licence.get("type") == preferred_type:
            return _optional_raw_str(licence.get("name"))
    return None


def _normalize_faction_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"kind": "faction_event_raw", "raw": event}
    event_kind = str(event.get("kind") or "diplomacy")
    summary = event.get("event_name") or event.get("event_desc") or event.get("outcome") or event.get("summary")
    return {
        "kind": _classify_faction_event_kind(event),
        "faction": _optional_raw_str(event.get("faction")),
        "otherfaction": _optional_raw_str(event.get("otherfaction")),
        "summary": _optional_raw_str(summary),
        "event_id": _optional_raw_str(event.get("eventid")),
        "active": bool(event.get("active")),
        "age_s": _optional_raw_number(event.get("age_s"), "age_s"),
        "raw_kind": event_kind,
        "raw": event,
    }


def _classify_faction_event_kind(event: dict[str, Any]) -> str:
    text = " ".join(str(event.get(key) or "") for key in ("eventid", "event_name", "event_desc", "outcome", "kind")).lower()
    if "promot" in text or "rank" in text or "licence" in text or "license" in text:
        return "promotion"
    if "territor" in text or "sector" in text or "claim" in text:
        return "territory"
    if "combat" in text or "war" in text or "attack" in text:
        return "combat"
    if "relation" in text or "diplomacy" in text:
        return "relation_change"
    return "diplomacy"


def _normalize_docked_trade_payload(raw: dict[str, Any]) -> list[dict[str, Any]]:
    offers_raw = raw.get("offers_raw")
    nontrade_raw = raw.get("nontrade_offers_raw")
    data: list[dict[str, Any]] = []
    if isinstance(offers_raw, list):
        for offer in offers_raw:
            data.append(_normalize_trade_offer(offer))
    elif offers_raw is not None:
        data.append({"kind": "trade_offers_raw", "offers_raw": offers_raw})
    if nontrade_raw not in (None, []):
        data.append({"kind": "nontrade_offers_raw", "offers_raw": nontrade_raw})
    if not data:
        data.append(
            {
                "kind": "trade_probe_metadata",
                "docked": raw.get("docked"),
                "trade_container_id": _optional_raw_str(raw.get("trade_container_id")),
                "trade_container_name": _optional_raw_str(raw.get("trade_container_name")),
                "offers_raw": offers_raw if offers_raw is not None else [],
                "nontrade_offers_raw": nontrade_raw if nontrade_raw is not None else [],
            }
        )
    return data


def _normalize_radar_trade_payload(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    stations_raw = raw.get("stations_raw")
    if isinstance(stations_raw, list):
        for station in stations_raw:
            if not isinstance(station, dict):
                data.append({"kind": "trade_station_raw", "station_raw": station})
                continue
            offers = station.get("offers_raw")
            if isinstance(offers, list):
                for offer in offers:
                    normalized = _normalize_trade_offer(offer)
                    normalized["scope"] = "radar_range"
                    normalized["station_distance_m"] = _optional_raw_number(station.get("distance_m"), "distance_m")
                    normalized["station_distance_km"] = _optional_raw_number(station.get("distance_km"), "distance_km")
                    normalized["distance_unit"] = "meters_from_player_ship_position; km derived by /1000"
                    normalized["station_raw"] = station
                    data.append(normalized)
            elif offers is not None:
                data.append({"kind": "trade_offers_raw", "station_raw": station, "offers_raw": offers})
    elif stations_raw is not None:
        data.append({"kind": "trade_stations_raw", "stations_raw": stations_raw})
    if not data:
        data.append(
            {
                "kind": "trade_radar_metadata",
                "scope": "radar_range",
                "stations_raw": stations_raw if stations_raw is not None else [],
                "station_count": _optional_raw_int(raw.get("station_count"), "station_count"),
                "station_cap": _optional_raw_int(raw.get("station_cap"), "station_cap"),
                "offer_cap": _optional_raw_int(raw.get("offer_cap"), "offer_cap"),
            }
        )
    return data


def _normalize_trade_offer(offer: Any) -> dict[str, Any]:
    if not isinstance(offer, dict):
        return {"kind": "trade_offer_raw", "offer_raw": offer}
    side = "buy" if offer.get("isbuyoffer") else "sell" if offer.get("isselloffer") else "unknown"
    normalized = {
        "kind": "trade_offer",
        "id": _optional_raw_str(offer.get("id")),
        "ware": _optional_raw_str(offer.get("ware")),
        "name": _optional_raw_str(offer.get("name")),
        "side": side,
        "price": _optional_raw_number(offer.get("price"), "price"),
        "market_price": _optional_raw_number(offer.get("marketprice"), "marketprice"),
        "amount": _optional_raw_int(offer.get("amount"), "amount"),
        "min_amount": _optional_raw_int(offer.get("minamount"), "minamount"),
        "desired_amount": _optional_raw_int(offer.get("desiredamount"), "desiredamount"),
        "station_id": _optional_raw_str(offer.get("station")),
        "station": _optional_raw_str(offer.get("stationname")),
        "station_sector_id": _optional_raw_str(offer.get("stationsectorid")),
        "faction": _optional_raw_str(offer.get("factionname")),
        "is_supply": bool(offer.get("issupply")),
        "is_shady": bool(offer.get("isshady")),
        "is_mission": bool(offer.get("ismissionoffer")),
        "raw": offer,
    }
    if "distance_m" in offer:
        normalized["station_distance_m"] = _optional_raw_number(offer.get("distance_m"), "distance_m")
    if "distance_km" in offer:
        normalized["station_distance_km"] = _optional_raw_number(offer.get("distance_km"), "distance_km")
    return normalized


def _optional_raw_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_raw_number(value: Any, label: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PayloadError(f"{label} must be a number")
    if isinstance(value, int | float):
        return value
    try:
        number = float(str(value))
    except ValueError as exc:
        raise PayloadError(f"{label} must be a number") from exc
    return int(number) if number.is_integer() else number


def _optional_raw_int(value: Any, label: str) -> int | None:
    number = _optional_raw_number(value, label)
    if number is None:
        return None
    return int(number)


def _optional_raw_json(value: Any) -> Any:
    return value


class OverlayTelemetryFetcher:
    """Route supported live intents to a primary fetcher and fall back to fixtures.

    This makes MCP useful immediately: ambient, ship status, and docked-station
    trade can be live while faction and sector-object tools remain explicit
    mock/stale fixture data until their Lua read paths exist.
    """

    def __init__(self, primary: TelemetryFetcher, fallback: TelemetryFetcher) -> None:
        self.primary = primary
        self.fallback = fallback

    def __call__(self, request: FetchRequest) -> TelemetryPayload:
        supported = getattr(self.primary, "supported_intents", frozenset())
        if request.intent in supported:
            return self.primary(request)
        return self.fallback(request)

    def provenance_for(self, request: FetchRequest) -> FetchProvenance:
        supported = getattr(self.primary, "supported_intents", frozenset())
        if request.intent in supported:
            return getattr(self.primary, "provenance", FetchProvenance())
        return getattr(self.fallback, "provenance", FetchProvenance())


class X4ToolSurface:
    """Dumb, read-only-default tool surface over a TelemetryFetcher.

    This layer contains no model calls, provider routing, credentials, or prose generation.
    It returns structured telemetry dictionaries for Hermes or an MCP wrapper to reason over.
    """

    def __init__(
        self,
        fetcher: TelemetryFetcher,
        *,
        actions_enabled: bool = False,
        provenance: FetchProvenance | None = None,
    ) -> None:
        self._provenance = provenance or getattr(fetcher, "provenance", FetchProvenance())
        self._fetcher = SerializedFetcher(fetcher)
        self.actions_enabled = actions_enabled

    def get_ambient_context(self) -> dict[str, Any]:
        fetched = self._fetch(FetchRequest(intent="ambient_context", args={"ambient_only": True}))
        result = _payload_base(fetched)
        return result["ambient"] | {"source": result["source"], "stale": result["stale"], "as_of": result["as_of"]}

    def fetch_trade_offers(
        self,
        *,
        scope: str = "docked_station",
        radar_only: bool | None = None,
        sector: str | None = None,
    ) -> dict[str, Any]:
        if radar_only is not None:
            scope = "radar_range" if radar_only else "docked_station"
        args: dict[str, Any] = {"scope": scope}
        if sector:
            args["sector"] = sector
        fetched = self._fetch(FetchRequest(intent="trade_in_sector", args=args))
        result = _payload_base(fetched)
        result["scope"] = scope
        result["offers"] = fetched.payload.data
        return result

    def fetch_ship_status(self) -> dict[str, Any]:
        fetched = self._fetch(FetchRequest(intent="ship_status", args={}))
        result = _payload_base(fetched)
        result["status"] = _merge_mapping_items(fetched.payload.data)
        return result

    def fetch_faction_state(self, *, since: str | None = None) -> dict[str, Any]:
        args = {"since": since} if since else {}
        fetched = self._fetch(FetchRequest(intent="faction_state", args=args))
        result = _payload_base(fetched)
        relations, events = _extract_faction_state(fetched.payload.data)
        result["relations"] = relations
        result["events"] = events
        return result

    def fetch_sector_objects(self, *, kinds: list[str] | None = None) -> dict[str, Any]:
        fetched = self._fetch(FetchRequest(intent="sector_objects", args={"kinds": kinds or []}))
        objects = fetched.payload.data
        if kinds:
            allowed = {kind.lower() for kind in kinds}
            objects = [obj for obj in objects if str(obj.get("type", "")).lower() in allowed]
        result = _payload_base(fetched)
        result["objects"] = objects
        return result

    def set_waypoint(
        self,
        *,
        station_id: str | None = None,
        pos: list[float] | None = None,
        confirm_token: str | None = None,
    ) -> dict[str, Any]:
        return _refuse_action(
            "set_waypoint",
            self.actions_enabled,
            confirm_token,
            args={"station_id": station_id, "pos": pos},
        )

    def mark_target(self, *, object_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        return _refuse_action(
            "mark_target",
            self.actions_enabled,
            confirm_token,
            args={"object_id": object_id},
        )

    def _fetch(self, request: FetchRequest) -> FetchedTelemetry:
        if request.intent not in VALID_INTENTS:
            raise PayloadError(f"unsupported intent: {request.intent}")
        payload = self._fetcher(request)
        provenance = self._fetcher.provenance_for(request) or self._provenance
        return FetchedTelemetry(payload=payload, provenance=provenance)


def create_mock_tool_surface(examples_dir: str | Path = DEFAULT_EXAMPLES_DIR) -> X4ToolSurface:
    fetcher = MockTelemetryFetcher(examples_dir)
    return X4ToolSurface(fetcher)


def create_live_raw_log_tool_surface(
    path: str | Path = DEFAULT_RAW_TELEMETRY_LOG,
    *,
    examples_dir: str | Path = DEFAULT_EXAMPLES_DIR,
) -> X4ToolSurface:
    fetcher = OverlayTelemetryFetcher(RawTelemetryLogFetcher(path), MockTelemetryFetcher(examples_dir))
    return X4ToolSurface(fetcher, provenance=fetcher.provenance_for(FetchRequest(intent="ambient_context", args={})))


def create_live_pipe_tool_surface(
    pipe_name: str = "x4_llm_copilot",
    *,
    raw_log_path: str | Path = DEFAULT_RAW_TELEMETRY_LOG,
    examples_dir: str | Path = DEFAULT_EXAMPLES_DIR,
    timeout_s: float = 8.0,
) -> X4ToolSurface:
    fetcher = OverlayTelemetryFetcher(
        LivePipeTelemetryFetcher(pipe_name=pipe_name, raw_log_path=raw_log_path, timeout_s=timeout_s),
        MockTelemetryFetcher(examples_dir),
    )
    return X4ToolSurface(fetcher, provenance=fetcher.provenance_for(FetchRequest(intent="ambient_context", args={})))


def create_tool_surface_from_env() -> X4ToolSurface:
    source = os.getenv("X4_COPILOT_TELEMETRY_SOURCE", "mock").strip().lower()
    if source in {"live_pipe", "pipe", "on_demand"}:
        return create_live_pipe_tool_surface(
            os.getenv("X4_COPILOT_PIPE_NAME", "x4_llm_copilot"),
            raw_log_path=os.getenv("X4_COPILOT_RAW_TELEMETRY_LOG", DEFAULT_RAW_TELEMETRY_LOG),
            timeout_s=float(os.getenv("X4_COPILOT_PIPE_TIMEOUT_S", "8")),
        )
    if source in {"live_raw_log", "raw_log", "live"}:
        return create_live_raw_log_tool_surface(os.getenv("X4_COPILOT_RAW_TELEMETRY_LOG", DEFAULT_RAW_TELEMETRY_LOG))
    return create_mock_tool_surface()


_default_surface = create_tool_surface_from_env()


def set_default_surface(surface: X4ToolSurface) -> None:
    global _default_surface
    _default_surface = surface


def get_ambient_context() -> dict[str, Any]:
    return _default_surface.get_ambient_context()


def fetch_trade_offers(
    *,
    scope: str = "docked_station",
    radar_only: bool | None = None,
    sector: str | None = None,
) -> dict[str, Any]:
    return _default_surface.fetch_trade_offers(scope=scope, radar_only=radar_only, sector=sector)


def fetch_ship_status() -> dict[str, Any]:
    return _default_surface.fetch_ship_status()


def fetch_faction_state(since: str | None = None) -> dict[str, Any]:
    return _default_surface.fetch_faction_state(since=since)


def fetch_sector_objects(kinds: list[str] | None = None) -> dict[str, Any]:
    return _default_surface.fetch_sector_objects(kinds=kinds)


def set_waypoint(
    station_id: str | None = None,
    pos: list[float] | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    return _default_surface.set_waypoint(station_id=station_id, pos=pos, confirm_token=confirm_token)


def mark_target(object_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    return _default_surface.mark_target(object_id=object_id, confirm_token=confirm_token)


def _payload_base(fetched: FetchedTelemetry) -> dict[str, Any]:
    return {
        "intent": fetched.payload.intent,
        "ambient": asdict(fetched.payload.ambient),
        "as_of": fetched.payload.as_of,
        "source": fetched.provenance.source,
        "stale": fetched.provenance.stale,
    }


def _merge_mapping_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        merged.update(item)
    return merged


def _extract_faction_state(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relations: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in items:
        item_kind = str(item.get("kind") or "").lower()
        if item_kind == "faction_standing":
            relations.append(item)
        elif item_kind in {"relation_change", "combat", "promotion", "territory", "diplomacy", "faction_event_raw"}:
            events.append(item)
        elif item_kind == "faction_state_metadata":
            continue
        else:
            # Mock fixture compatibility only; live faction_state_v1 is normalized above.
            nested_relations = item.get("relations")
            nested_events = item.get("events")
            if isinstance(nested_relations, list):
                relations.extend(_dict_items(nested_relations))
            if isinstance(nested_events, list):
                events.extend(_dict_items(nested_events))
    return relations, events


def _dict_items(items: list[Any]) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item, dict)]


def _refuse_action(
    name: str,
    actions_enabled: bool,
    confirm_token: str | None,
    *,
    args: dict[str, Any],
) -> dict[str, Any]:
    if not actions_enabled:
        return {"ok": False, "confirmed": False, "action": name, "error": "actions disabled by default", "args": args}
    if not confirm_token:
        return {"ok": False, "confirmed": False, "action": name, "error": "confirmation token required", "args": args}
    return {
        "ok": False,
        "confirmed": True,
        "action": name,
        "error": "action transport is not implemented; no game state was changed",
        "args": args,
    }
