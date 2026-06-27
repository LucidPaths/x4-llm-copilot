from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .intent import classify
from .models import Intent, PayloadError, TelemetryPayload

MessageType = Literal["fetch", "telemetry", "answer", "action", "error", "ping", "pong"]


@dataclass(frozen=True)
class FetchRequest:
    intent: Intent
    args: dict[str, Any]
    question: str = ""

    @classmethod
    def from_question(cls, question: str) -> FetchRequest:
        result = classify(question)
        return cls(intent=result.intent, args={"router_confidence": result.confidence, "matched": list(result.matched)}, question=question)

    def to_json(self) -> str:
        return json.dumps({"type": "fetch", "intent": self.intent, "args": self.args, "question": self.question}, ensure_ascii=False)


def parse_json_message(raw: str) -> dict[str, Any]:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"invalid JSON message: {exc}") from exc
    if not isinstance(msg, dict):
        raise PayloadError("message must be an object")
    return msg


def encode_answer(question: str, payload: TelemetryPayload, answer: str) -> str:
    return json.dumps({"type": "answer", "question": question, "intent": payload.intent, "answer": answer, "ambient": asdict(payload.ambient)}, ensure_ascii=False)
