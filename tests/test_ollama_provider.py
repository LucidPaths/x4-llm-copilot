import json
from contextlib import contextmanager

from x4_copilot.llm import (
    OLLAMA_CLOUD_BASE_URL,
    OllamaAdvisor,
    OpenAICompatibleConfig,
    ProviderConfig,
    advisor_from_env,
    list_ollama_models,
    list_provider_profiles,
)
from x4_copilot.models import TelemetryPayload


@contextmanager
def patch_env(monkeypatch, **values):
    keys = [
        "X4_COPILOT_PROVIDER",
        "LLM_PROVIDER",
        "X4_COPILOT_OPENAI_BASE_URL",
        "OPENAI_BASE_URL",
        "X4_COPILOT_API_KEY",
        "OPENAI_API_KEY",
        "X4_COPILOT_MODEL",
        "OPENAI_MODEL",
        "X4_COPILOT_OLLAMA_API_KEY",
        "OLLAMA_API_KEY",
        "X4_COPILOT_OLLAMA_MODEL",
        "OLLAMA_MODEL",
        "X4_COPILOT_OLLAMA_BASE_URL",
        "OLLAMA_BASE_URL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def sample_payload():
    return TelemetryPayload.from_dict(
        {
            "intent": "trade_in_sector",
            "ambient": {"sector": "Grand Exchange IV"},
            "data": [
                {"ware": "hull_parts", "buy": 4500, "sell": 5200, "unit": "cr/u", "station": "Profit Center Alpha"}
            ],
        }
    )


def test_ollama_config_cannibalizes_world_engine_env_shape(monkeypatch):
    with patch_env(
        monkeypatch,
        LLM_PROVIDER="ollama",
        OLLAMA_API_KEY="test-key",
        OLLAMA_MODEL="glm-5.2",
    ):
        config = ProviderConfig.from_env()
    assert config.provider == "ollama"
    assert config.base_url == OLLAMA_CLOUD_BASE_URL
    assert config.api_key == "test-key"
    assert config.model == "glm-5.2"
    assert config.configured


def test_openai_compatible_env_path_still_works(monkeypatch):
    with patch_env(
        monkeypatch,
        X4_COPILOT_OPENAI_BASE_URL="https://example.test/v1",
        X4_COPILOT_API_KEY="test-key",
        X4_COPILOT_MODEL="cheap-model",
    ):
        config = ProviderConfig.from_env()
    assert config.provider == "openai-compatible"
    assert config.chat_base_url == "https://example.test/v1"


def test_v0_1_openai_config_constructor_still_works():
    config = OpenAICompatibleConfig(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="cheap-model",
    )
    assert config.provider == "openai-compatible"
    assert config.chat_base_url == "https://example.test/v1"
    assert config.configured


def test_advisor_from_env_selects_ollama(monkeypatch):
    with patch_env(
        monkeypatch,
        X4_COPILOT_PROVIDER="ollama",
        X4_COPILOT_OLLAMA_API_KEY="test-key",
        X4_COPILOT_OLLAMA_MODEL="glm-5.2",
    ):
        advisor = advisor_from_env()
    assert isinstance(advisor, OllamaAdvisor)


def test_ollama_advisor_uses_chat_completions_content_only():
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(req.header_items())
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({"choices": [{"message": {"content": "Buy hull parts, captain."}}]})

    advisor = OllamaAdvisor(
        ProviderConfig(provider="ollama", api_key="test-key", model="glm-5.2"),
        urlopen=fake_urlopen,
    )
    answer = advisor.answer("what's selling here", sample_payload())
    assert answer == "Buy hull parts, captain."
    assert seen["url"] == "https://ollama.com/v1/chat/completions"
    assert seen["body"]["model"] == "glm-5.2"
    assert "response_format" not in seen["body"]
    assert seen["headers"]["Authorization"] == "Bearer test-key"


def test_reasoning_field_is_not_leaked_as_answer():
    def fake_urlopen(req, timeout):
        return FakeResponse({"choices": [{"message": {"reasoning": "hidden chain of thought"}}]})

    advisor = OllamaAdvisor(
        ProviderConfig(provider="ollama", api_key="test-key", model="glm-5.2"),
        urlopen=fake_urlopen,
    )
    answer = advisor.answer("what's selling here", sample_payload())
    assert "hidden chain" not in answer
    assert "Best visible trade" in answer


def test_list_ollama_models_accepts_id_or_name_and_sorts():
    def fake_urlopen(req, timeout):
        assert req.full_url == "https://ollama.com/v1/models"
        assert dict(req.header_items())["Authorization"] == "Bearer test-key"
        return FakeResponse({"data": [{"name": "zeta"}, {"id": "alpha"}]})

    assert list_ollama_models("test-key", urlopen=fake_urlopen) == ["alpha", "zeta"]


def test_provider_profiles_do_not_expose_keys(monkeypatch):
    with patch_env(monkeypatch, X4_COPILOT_PROVIDER="ollama", OLLAMA_API_KEY="secret", OLLAMA_MODEL="glm-5.2"):
        rendered = json.dumps([profile.__dict__ for profile in list_provider_profiles()])
    assert "secret" not in rendered
    assert "ollama-cloud" in rendered
