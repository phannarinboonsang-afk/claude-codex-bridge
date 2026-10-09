import pytest
from backend.agent_chat.config import AgentChatConfig
from backend.agent_chat.providers.base import ProviderError
def test_models_explicit_no_fallback(monkeypatch):
    monkeypatch.setenv("CHAT_OPENAI_MODEL","configured-gpt");monkeypatch.setenv("CHAT_ANTHROPIC_MODEL","configured-claude")
    cfg=AgentChatConfig.from_env()
    assert cfg.provider("gpt").model=="configured-gpt"
    assert cfg.provider("claude").model=="configured-claude"
    with pytest.raises(ProviderError):cfg.provider("unknown")
def test_missing_model_explicit(monkeypatch):
    monkeypatch.delenv("CHAT_OPENAI_MODEL",raising=False)
    with pytest.raises(ProviderError):AgentChatConfig.from_env().provider("gpt")

def test_bad_timeout_config_safe(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER_TIMEOUT_SECONDS","not-a-number")
    with pytest.raises(ProviderError):AgentChatConfig.from_env()
