from __future__ import annotations

import json
from typing import Any

from .tools import create_tool_surface_from_env


def build_mcp_server():
    """Build the stdio MCP server for Hermes/other MCP clients.

    The MCP SDK is optional so the core adapter and tests stay dependency-light.
    Install with `uv pip install -e '.[mcp]'` before running this module.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised by CLI smoke without mcp extra
        raise RuntimeError("MCP support requires the optional 'mcp' extra: uv pip install -e '.[mcp]'") from exc

    surface = create_tool_surface_from_env()
    mcp = FastMCP("x4-llm-copilot")

    @mcp.tool()
    def get_ambient_context() -> dict[str, Any]:
        """Return current ambient player context. Mock by default; live when X4_COPILOT_TELEMETRY_SOURCE=live_raw_log."""
        return surface.get_ambient_context()

    @mcp.tool()
    def fetch_trade_offers(
        scope: str = "docked_station",
        radar_only: bool | None = None,
        sector: str | None = None,
    ) -> dict[str, Any]:
        """Return structured trade offers. Live pipe currently supports scope='docked_station'; radar_range is future work."""
        return surface.fetch_trade_offers(scope=scope, radar_only=radar_only, sector=sector)

    @mcp.tool()
    def fetch_ship_status() -> dict[str, Any]:
        """Return structured player ship status."""
        return surface.fetch_ship_status()

    @mcp.tool()
    def fetch_faction_state(since: str | None = None) -> dict[str, Any]:
        """Return structured faction relations/events."""
        return surface.fetch_faction_state(since=since)

    @mcp.tool()
    def fetch_sector_objects(kinds: list[str] | None = None) -> dict[str, Any]:
        """Return structured sector objects, optionally filtered by type/kind."""
        return surface.fetch_sector_objects(kinds=kinds)

    @mcp.tool()
    def set_waypoint(station_id: str | None = None, pos: list[float] | None = None, confirm_token: str | None = None) -> dict[str, Any]:
        """Gated future action. Refuses by default and never mutates game state in this build."""
        return surface.set_waypoint(station_id=station_id, pos=pos, confirm_token=confirm_token)

    @mcp.tool()
    def mark_target(object_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """Gated future action. Refuses by default and never mutates game state in this build."""
        return surface.mark_target(object_id=object_id, confirm_token=confirm_token)

    return mcp


def main() -> None:
    build_mcp_server().run()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        raise SystemExit(2) from exc
