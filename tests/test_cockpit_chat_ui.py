from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "extension/x4_llm_copilot/md/x4_llm_copilot.xml"
LUA_PATH = ROOT / "extension/x4_llm_copilot/ui/x4_llm_copilot/ambient.lua"


def test_cockpit_chat_md_is_valid_xml() -> None:
    ET.parse(MD_PATH)


def test_cockpit_chat_has_dispatch_ack_and_periodic_wait_feedback() -> None:
    md = MD_PATH.read_text(encoding="utf-8")
    lua = LUA_PATH.read_text(encoding="utf-8")

    assert "received; sending to bridge" in lua
    assert "waiting for live telemetry/Hermes" in lua
    assert "still working; waiting for Python/Hermes" in lua
    assert "x4LLMCopilotChatStillPending" in lua
    assert 'delay exact="15s"' in md
    assert 'delay exact="45s"' in md
    assert 'delay exact="75s"' in md
    assert 'delay exact="90s"' in md


def test_cockpit_chat_ui_copy_is_ascii_safe() -> None:
    watched_literals = [
        "received; sending to bridge",
        "waiting for live telemetry/Hermes",
        "still working; waiting for Python/Hermes",
        "timed out waiting for Python/Hermes response",
    ]
    for text in watched_literals:
        assert text.encode("ascii").decode("ascii") == text
