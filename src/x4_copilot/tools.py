from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import VALID_INTENTS, AmbientContext, Intent, PayloadError, TelemetryPayload
from .protocol import FetchRequest

TelemetryFetcher = Callable[[FetchRequest], TelemetryPayload]

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXAMPLES_DIR = PACKAGE_ROOT / "examples"
INTENT_FIXTURES: dict[Intent, str] = {
    "trade_in_sector": "trade_payload.json",
    "ship_status": "ship_status_payload.json",
    "faction_state": "faction_state_payload.json",
    "sector_objects": "sector_objects_payload.json",
}
READ_TOOLS = frozenset(INTENT_FIXTURES)


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

    def __init__(self, examples_dir: str | Path = DEFAULT_EXAMPLES_DIR) -> None:
        self.examples_dir = Path(examples_dir)

    def __call__(self, request: FetchRequest) -> TelemetryPayload:
        if request.intent not in INTENT_FIXTURES:
            return TelemetryPayload(
                intent="unknown",
                ambient=_ambient_from_best_available_fixture(self.examples_dir),
                data=[],
                as_of="mock fixture; unknown intent",
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

    def __init__(self, fetcher: TelemetryFetcher, *, actions_enabled: bool = False) -> None:
        self._fetcher = SerializedFetcher(fetcher)
        self.actions_enabled = actions_enabled

    def get_ambient_context(self) -> dict[str, Any]:
        payload = self._fetch(FetchRequest(intent="ship_status", args={"ambient_only": True}))
        return _payload_base(payload)["ambient"]

    def fetch_trade_offers(self, *, radar_only: bool = True, sector: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"radar_only": radar_only}
        if sector:
            args["sector"] = sector
        payload = self._fetch(FetchRequest(intent="trade_in_sector", args=args))
        result = _payload_base(payload)
        result["offers"] = payload.data
        return result

    def fetch_ship_status(self) -> dict[str, Any]:
        payload = self._fetch(FetchRequest(intent="ship_status", args={}))
        result = _payload_base(payload)
        status = payload.data[0] if payload.data else {}
        result.update({"status": status, "stale": _is_stale(payload)})
        return result

    def fetch_faction_state(self, *, since: str | None = None) -> dict[str, Any]:
        args = {"since": since} if since else {}
        payload = self._fetch(FetchRequest(intent="faction_state", args=args))
        result = _payload_base(payload)
        first = payload.data[0] if payload.data else {}
        result["relations"] = first.get("relations", []) if isinstance(first, dict) else []
        result["events"] = first.get("events", []) if isinstance(first, dict) else []
        result["stale"] = _is_stale(payload)
        return result

    def fetch_sector_objects(self, *, kinds: list[str] | None = None) -> dict[str, Any]:
        payload = self._fetch(FetchRequest(intent="sector_objects", args={"kinds": kinds or []}))
        objects = payload.data
        if kinds:
            allowed = {kind.lower() for kind in kinds}
            objects = [obj for obj in objects if str(obj.get("type", "")).lower() in allowed]
        result = _payload_base(payload)
        result["objects"] = objects
        result["stale"] = _is_stale(payload)
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

    def _fetch(self, request: FetchRequest) -> TelemetryPayload:
        if request.intent not in VALID_INTENTS:
            raise PayloadError(f"unsupported intent: {request.intent}")
        return self._fetcher(request)


def create_mock_tool_surface(examples_dir: str | Path = DEFAULT_EXAMPLES_DIR) -> X4ToolSurface:
    return X4ToolSurface(MockTelemetryFetcher(examples_dir))


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


def _payload_base(payload: TelemetryPayload) -> dict[str, Any]:
    return {
        "intent": payload.intent,
        "ambient": asdict(payload.ambient),
        "as_of": payload.as_of,
        "source": "mock" if payload.as_of and "fixture" in payload.as_of else "live_or_injected",
        "stale": _is_stale(payload),
    }


def _is_stale(payload: TelemetryPayload) -> bool:
    return bool(payload.as_of and "fixture" in payload.as_of)


def _ambient_from_best_available_fixture(examples_dir: Path) -> Any:
    for fixture in INTENT_FIXTURES.values():
        path = examples_dir / fixture
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return TelemetryPayload.from_dict(raw).ambient
        except (OSError, json.JSONDecodeError, PayloadError):
            continue
    return AmbientContext()


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
