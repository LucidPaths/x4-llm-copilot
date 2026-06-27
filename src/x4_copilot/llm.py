from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .advisor import GroundedAdvisor
from .models import TelemetryPayload

SYSTEM_PROMPT = """You are an X4: Foundations ship computer. Answer in one or two concise sentences.
Use only supplied telemetry. If data is empty or stale, say so. Keep labels and units."""

OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"
ProviderKind = Literal["mock", "openai-compatible", "ollama"]
UrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    provider: ProviderKind
    base_url: str | None
    model: str | None
    configured: bool
    source: Literal["environment", "default"]


@dataclass(frozen=True)
class ProviderConfig:
    provider: ProviderKind
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout_s: float = 20.0

    @property
    def configured(self) -> bool:
        return self.provider == "mock" or bool(self.api_key and self.model)

    @property
    def chat_base_url(self) -> str:
        if self.provider == "ollama":
            return (self.base_url or OLLAMA_CLOUD_BASE_URL).rstrip("/")
        if not self.base_url:
            raise ValueError("OpenAI-compatible provider requires a base URL")
        return self.base_url.rstrip("/")

    @classmethod
    def from_env(cls) -> ProviderConfig:
        provider = _provider_from_env()
        if provider == "mock":
            return cls(provider="mock")
        if provider == "ollama":
            return cls(
                provider="ollama",
                base_url=os.getenv("X4_COPILOT_OLLAMA_BASE_URL")
                or os.getenv("OLLAMA_BASE_URL")
                or OLLAMA_CLOUD_BASE_URL,
                api_key=os.getenv("X4_COPILOT_OLLAMA_API_KEY")
                or os.getenv("OLLAMA_API_KEY")
                or os.getenv("X4_COPILOT_API_KEY"),
                model=os.getenv("X4_COPILOT_OLLAMA_MODEL")
                or os.getenv("OLLAMA_MODEL")
                or os.getenv("X4_COPILOT_MODEL"),
            )
        return cls(
            provider="openai-compatible",
            base_url=os.getenv("X4_COPILOT_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("X4_COPILOT_API_KEY") or os.getenv("OPENAI_API_KEY"),
            model=os.getenv("X4_COPILOT_MODEL") or os.getenv("OPENAI_MODEL"),
        )


class OpenAICompatibleConfig(ProviderConfig):
    """Backward-compatible v0.1 config shape for generic OpenAI-compatible endpoints."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 20.0):
        object.__setattr__(self, "provider", "openai-compatible")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "timeout_s", timeout_s)

    @classmethod
    def from_env(cls) -> OpenAICompatibleConfig | None:
        base_url = os.getenv("X4_COPILOT_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("X4_COPILOT_API_KEY") or os.getenv("OPENAI_API_KEY")
        model = os.getenv("X4_COPILOT_MODEL") or os.getenv("OPENAI_MODEL")
        if not (base_url and api_key and model):
            return None
        return cls(base_url=base_url, api_key=api_key, model=model)


class ProviderBackedAdvisor:
    def __init__(self, config: ProviderConfig, *, urlopen: UrlOpen | None = None):
        self.config = config
        self.fallback = GroundedAdvisor()
        self._urlopen = urlopen or urllib.request.urlopen

    def answer(self, question: str, payload: TelemetryPayload) -> str:
        if not self.config.configured:
            return self.fallback.answer(question, payload)
        try:
            body = self._chat_request_body(question, payload)
            req = urllib.request.Request(
                f"{self.config.chat_base_url}/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with self._urlopen(req, timeout=self.config.timeout_s) as resp:  # noqa: S310 - user-configured endpoint
                raw = json.loads(resp.read().decode("utf-8"))
            message = raw.get("choices", [{}])[0].get("message", {})
            content = message.get("content") or message.get("reasoning")
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, ValueError):
            return self.fallback.answer(question, payload)
        if not isinstance(content, str) or not content.strip():
            return self.fallback.answer(question, payload)
        return content.strip()

    def _chat_request_body(self, question: str, payload: TelemetryPayload) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "intent": payload.intent,
                            "ambient": payload.ambient.__dict__,
                            "data": payload.data,
                            "as_of": payload.as_of,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.2,
        }


class OpenAICompatibleAdvisor(ProviderBackedAdvisor):
    def __init__(self, config: ProviderConfig, *, urlopen: UrlOpen | None = None):
        super().__init__(config, urlopen=urlopen)


class OllamaAdvisor(ProviderBackedAdvisor):
    def __init__(self, config: ProviderConfig | None = None, *, urlopen: UrlOpen | None = None):
        super().__init__(config or ProviderConfig.from_env(), urlopen=urlopen)


def _provider_from_env() -> ProviderKind:
    raw = (
        os.getenv("X4_COPILOT_PROVIDER")
        or os.getenv("LLM_PROVIDER")
        or os.getenv("AI_PROVIDER")
        or "openai-compatible"
    ).strip().lower()
    if raw in {"mock", "deterministic", "none"}:
        return "mock"
    if raw in {"ollama", "ollama-cloud"}:
        return "ollama"
    return "openai-compatible"


def list_provider_profiles() -> list[ProviderProfile]:
    env = ProviderConfig.from_env()
    profiles = [
        ProviderProfile(
            id="environment",
            label="Environment provider",
            provider=env.provider,
            base_url=env.base_url,
            model=env.model,
            configured=env.configured,
            source="environment",
        ),
        ProviderProfile(
            id="ollama-cloud",
            label="Ollama Cloud",
            provider="ollama",
            base_url=OLLAMA_CLOUD_BASE_URL,
            model=os.getenv("X4_COPILOT_OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL"),
            configured=bool(os.getenv("X4_COPILOT_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY")),
            source="default",
        ),
    ]
    return profiles


def list_ollama_models(api_key: str | None = None, *, base_url: str | None = None, urlopen: UrlOpen | None = None) -> list[str]:
    key = (api_key or os.getenv("X4_COPILOT_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY") or "").strip()
    if not key:
        raise ValueError("Ollama API key not configured")
    opener = urlopen or urllib.request.urlopen
    req = urllib.request.Request(
        f"{(base_url or OLLAMA_CLOUD_BASE_URL).rstrip('/')}/models",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    with opener(req, timeout=20.0) as resp:  # noqa: S310 - user-configured endpoint
        status = getattr(resp, "status", 200)
        if status >= 400:
            raise ValueError(f"Ollama model list failed with HTTP {status}")
        body = json.loads(resp.read().decode("utf-8"))
    models = body.get("data", [])
    if not isinstance(models, list):
        return []
    return sorted(
        model_id
        for item in models
        if isinstance(item, dict)
        for model_id in [item.get("id") or item.get("name")]
        if isinstance(model_id, str) and model_id
    )


def advisor_from_env() -> GroundedAdvisor | ProviderBackedAdvisor:
    config = ProviderConfig.from_env()
    if config.provider == "mock" or not config.configured:
        return GroundedAdvisor()
    if config.provider == "ollama":
        return OllamaAdvisor(config)
    return OpenAICompatibleAdvisor(config)
