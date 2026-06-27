from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .advisor import GroundedAdvisor
from .models import TelemetryPayload

SYSTEM_PROMPT = """You are an X4: Foundations ship computer. Answer in one or two concise sentences.
Use only supplied telemetry. If data is empty or stale, say so. Keep labels and units."""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 20.0

    @classmethod
    def from_env(cls) -> OpenAICompatibleConfig | None:
        base_url = os.getenv("X4_COPILOT_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("X4_COPILOT_API_KEY") or os.getenv("OPENAI_API_KEY")
        model = os.getenv("X4_COPILOT_MODEL") or os.getenv("OPENAI_MODEL")
        if not (base_url and api_key and model):
            return None
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model)


class OpenAICompatibleAdvisor:
    def __init__(self, config: OpenAICompatibleConfig):
        self.config = config
        self.fallback = GroundedAdvisor()

    def answer(self, question: str, payload: TelemetryPayload) -> str:
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"question": question, "intent": payload.intent, "ambient": payload.ambient.__dict__, "data": payload.data, "as_of": payload.as_of}, ensure_ascii=False)},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:  # noqa: S310
                raw = json.loads(resp.read().decode("utf-8"))
            message = raw.get("choices", [{}])[0].get("message", {})
            content = message.get("content") or message.get("reasoning")
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError):
            return self.fallback.answer(question, payload)
        if not isinstance(content, str) or not content.strip():
            return self.fallback.answer(question, payload)
        return content.strip()


def advisor_from_env() -> GroundedAdvisor | OpenAICompatibleAdvisor:
    config = OpenAICompatibleConfig.from_env()
    if config is None:
        return GroundedAdvisor()
    return OpenAICompatibleAdvisor(config)
