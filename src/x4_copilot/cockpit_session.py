from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TelemetryPayload


@dataclass(frozen=True)
class SaveScope:
    save_scope_id: str
    confidence: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class CockpitSessionContext:
    scope: SaveScope
    summary: str
    recent_turns: list[dict[str, Any]]
    transcript_path: str
    hermes_home: str


def default_state_root() -> Path:
    configured = os.getenv("X4_COPILOT_STATE_HOME")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "x4-llm-copilot"
    return Path.home() / ".x4-llm-copilot"


def sanitize_scope_id(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value).strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    if not cleaned:
        raise ValueError("save scope id is empty after sanitization")
    return cleaned[:96]


class SaveScopeResolver:
    def __init__(self, *, configured_scope: str | None = None, allow_derived: bool = True) -> None:
        self.configured_scope = configured_scope or os.getenv("X4_COPILOT_SAVE_SCOPE")
        self.allow_derived = allow_derived

    def resolve(self, *, request: dict[str, Any], payload: TelemetryPayload) -> SaveScope:
        explicit = self._explicit_scope(request)
        if explicit:
            return SaveScope(sanitize_scope_id(explicit), "explicit", {"source": "chat_request", "field": self._explicit_field(request)})
        if self.configured_scope:
            return SaveScope(sanitize_scope_id(self.configured_scope), "configured", {"source": "bridge_config"})
        if not self.allow_derived:
            raise ValueError("missing save scope; provide save_scope_id/save_id/save_name or --save-scope")
        return self._derive(payload)

    @staticmethod
    def _explicit_field(request: dict[str, Any]) -> str | None:
        for key in ("save_scope_id", "save_id", "save_name", "save"):
            if isinstance(request.get(key), str) and request[key].strip():
                return key
        meta = request.get("meta")
        if isinstance(meta, dict):
            for key in ("save_scope_id", "save_id", "save_name", "save"):
                if isinstance(meta.get(key), str) and meta[key].strip():
                    return f"meta.{key}"
        return None

    @classmethod
    def _explicit_scope(cls, request: dict[str, Any]) -> str | None:
        field = cls._explicit_field(request)
        if field is None:
            return None
        if field.startswith("meta."):
            return str(request["meta"][field.split(".", 1)[1]])
        return str(request[field])

    @staticmethod
    def _derive(payload: TelemetryPayload) -> SaveScope:
        ambient = payload.ambient
        evidence = {
            "source": "derived_from_live_telemetry",
            "fields": {
                "sector": ambient.sector,
                "ship": ambient.ship,
                "credits_present": ambient.credits is not None,
            },
        }
        basis = json.dumps(evidence["fields"], sort_keys=True, ensure_ascii=False)
        if basis in {"{}", "null"} or not any(evidence["fields"].values()):
            raise ValueError("missing save scope and live telemetry lacks stable fields for derived scope")
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        return SaveScope(f"derived-{digest}", "derived", evidence)


class CockpitSessionStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else default_state_root()

    @property
    def hermes_home(self) -> Path:
        return self.root / "hermes-home"

    def context(self, scope: SaveScope, *, recent_limit: int = 8) -> CockpitSessionContext:
        session_dir = self._session_dir(scope)
        summary_path = session_dir / "summary.md"
        transcript_path = session_dir / "transcript.jsonl"
        return CockpitSessionContext(
            scope=scope,
            summary=summary_path.read_text(encoding="utf-8") if summary_path.exists() else "",
            recent_turns=self._read_recent(transcript_path, limit=recent_limit),
            transcript_path=str(transcript_path),
            hermes_home=str(self.hermes_home),
        )

    def append_turn(self, scope: SaveScope, *, question: str, answer: str, payload: TelemetryPayload) -> None:
        session_dir = self._session_dir(scope)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "save_scope_id": scope.save_scope_id,
            "scope_confidence": scope.confidence,
            "question": question,
            "answer": answer,
            "telemetry": self._telemetry_digest(payload),
        }
        transcript = session_dir / "transcript.jsonl"
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._write_summary(session_dir, scope, self._read_recent(transcript, limit=12))
        self._write_facts(session_dir, scope, payload)

    def reset(self, scope: SaveScope) -> None:
        session_dir = self._session_dir(scope)
        for name in ("transcript.jsonl", "summary.md", "facts.json", "telemetry_cache.json"):
            path = session_dir / name
            if path.exists():
                path.unlink()

    def status(self, scope: SaveScope) -> dict[str, Any]:
        session_dir = self._session_dir(scope)
        transcript = session_dir / "transcript.jsonl"
        turns = self._read_recent(transcript, limit=10_000) if transcript.exists() else []
        return {
            "save_scope_id": scope.save_scope_id,
            "confidence": scope.confidence,
            "transcript_path": str(transcript),
            "turn_count": len(turns),
            "last_turn_ts": turns[-1]["ts"] if turns else None,
            "hermes_home": str(self.hermes_home),
        }

    def _session_dir(self, scope: SaveScope) -> Path:
        return self.root / "sessions" / sanitize_scope_id(scope.save_scope_id)

    @staticmethod
    def _read_recent(path: Path, *, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records[-limit:]

    @staticmethod
    def _telemetry_digest(payload: TelemetryPayload) -> dict[str, Any]:
        return {
            "intent": payload.intent,
            "ambient": payload.ambient.__dict__,
            "as_of": payload.as_of,
            "data_count": len(payload.data),
        }

    def _write_summary(self, session_dir: Path, scope: SaveScope, turns: list[dict[str, Any]]) -> None:
        lines = [
            f"# X4 cockpit session: {scope.save_scope_id}",
            "",
            f"Scope confidence: {scope.confidence}",
            "",
            "Recent turns:",
        ]
        for turn in turns[-8:]:
            lines.append(f"- Player: {turn.get('question', '')}")
            lines.append(f"  Hermes: {turn.get('answer', '')}")
        (session_dir / "summary.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def _write_facts(self, session_dir: Path, scope: SaveScope, payload: TelemetryPayload) -> None:
        facts = {
            "save_scope_id": scope.save_scope_id,
            "scope_confidence": scope.confidence,
            "last_seen": time.time(),
            "last_live_telemetry": self._telemetry_digest(payload),
        }
        (session_dir / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
