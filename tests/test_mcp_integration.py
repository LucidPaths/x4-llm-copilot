import asyncio
import json
import os
import sys

import pytest

pytest.importorskip("mcp")


def test_mcp_server_builds_with_sdk_and_exposes_expected_tools():
    from x4_copilot.mcp_server import build_mcp_server

    server = build_mcp_server()
    tool_manager = server._tool_manager  # noqa: SLF001 - FastMCP exposes no stable sync list helper
    tool_names = set(tool_manager._tools)  # noqa: SLF001

    assert {
        "get_ambient_context",
        "fetch_trade_offers",
        "fetch_ship_status",
        "fetch_faction_state",
        "fetch_sector_objects",
        "set_waypoint",
        "mark_target",
    } <= tool_names


def test_stdio_mcp_client_can_call_fetch_trade_offers():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def call_tool() -> dict:
        params = StdioServerParameters(command=sys.executable, args=["-m", "x4_copilot.mcp_server"])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("fetch_trade_offers", {"radar_only": True})
            return json.loads(result.content[0].text)

    payload = asyncio.run(call_tool())

    assert payload["intent"] == "trade_in_sector"
    assert payload["source"] == "mock"
    assert payload["stale"] is True
    assert payload["offers"][0]["ware"] == "hull_parts"


def test_stdio_mcp_client_can_call_live_raw_ambient(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    raw_log = tmp_path / "live_telemetry_raw.jsonl"
    raw_log.write_text(
        json.dumps(
            {
                "type": "telemetry_raw",
                "intent": "ambient_context",
                "source": "x4_lua_live",
                "schema": "ambient_probe_v1",
                "ship_name": "Raleigh (Container)",
                "sector_raw": "Windfall I Union Summit",
                "hullpercent": 100,
                "shieldpercent": 100,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    async def call_tool() -> dict:
        env = os.environ.copy()
        env["X4_COPILOT_TELEMETRY_SOURCE"] = "live_raw_log"
        env["X4_COPILOT_RAW_TELEMETRY_LOG"] = str(raw_log)
        params = StdioServerParameters(command=sys.executable, args=["-m", "x4_copilot.mcp_server"], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("get_ambient_context", {})
            return json.loads(result.content[0].text)

    payload = asyncio.run(call_tool())

    assert payload["sector"] == "Windfall I Union Summit"
    assert payload["ship"] == "Raleigh (Container)"
    assert payload["source"] == "x4_lua_live_raw_log"
    assert payload["stale"] is False
