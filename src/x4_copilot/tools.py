from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import VALID_INTENTS, AmbientContext, Intent, PayloadError, TelemetryPayload
from .protocol import FetchRequest

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

    def fetch_trade_offers(self, *, radar_only: bool = True, sector: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"radar_only": radar_only}
        if sector:
            args["sector"] = sector
        fetched = self._fetch(FetchRequest(intent="trade_in_sector", args=args))
        result = _payload_base(fetched)
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
        return FetchedTelemetry(payload=self._fetcher(request), provenance=self._provenance)


def create_mock_tool_surface(examples_dir: str | Path = DEFAULT_EXAMPLES_DIR) -> X4ToolSurface:
    fetcher = MockTelemetryFetcher(examples_dir)
    return X4ToolSurface(fetcher)


_default_surface = create_mock_tool_surface()


def set_default_surface(surface: X4ToolSurface) -> None:
    global _default_surface
    _default_surface = surface


def get_ambient_context() -> dict[str, Any]:
    return _default_surface.get_ambient_context()


def fetch_trade_offers(radar_only: bool = True, sector: str | None = None) -> dict[str, Any]:
    return _default_surface.fetch_trade_offers(radar_only=radar_only, sector=sector)


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
    """Accept nested fixture shape and likely live itemized shapes.

    The live Lua/MD reader is not validated yet, so do not force all data into
    ``data[0].relations``. Accept both:
    - [{"relations": [...], "events": [...]}]
    - [{"kind": "relation", ...}, {"kind": "event", ...}]
    - [{"type": "relation", ...}, {"type": "combat", ...}]
    """

    relations: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in items:
        nested_relations = item.get("relations")
        nested_events = item.get("events")
        if isinstance(nested_relations, list):
            relations.extend(_dict_items(nested_relations))
        if isinstance(nested_events, list):
            events.extend(_dict_items(nested_events))
        if isinstance(nested_relations, list) or isinstance(nested_events, list):
            continue

        item_kind = str(item.get("kind") or item.get("type") or "").lower()
        if item_kind == "relation" or "standing" in item or ("faction" in item and "trend" in item):
            relations.append(item)
        elif item_kind in {"event", "combat", "diplomacy", "news"} or "summary" in item:
            events.append(item)
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
