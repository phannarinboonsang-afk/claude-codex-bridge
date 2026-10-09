import os
from dataclasses import dataclass
from .providers.base import ProviderError

@dataclass(frozen=True)
class ProviderConfig:
    model: str
    key_file: str

@dataclass(frozen=True)
class AgentChatConfig:
    openai: ProviderConfig
    anthropic: ProviderConfig
    provider_timeout: float = 60
    bridge_timeout: float = 180
    context_messages: int = 10
    context_chars: int = 18000

    def __post_init__(self):
        if not 0 < self.provider_timeout <= 300 or not 1 <= self.bridge_timeout <= 600:
            raise ProviderError("Invalid timeout configuration.")
        if not 1 <= self.context_messages <= 20 or not 1000 <= self.context_chars <= 20000:
            raise ProviderError("Invalid context configuration.")

    @classmethod
    def from_env(cls):
        try:
            provider_timeout = float(os.getenv("CHAT_PROVIDER_TIMEOUT_SECONDS", "60"))
            bridge_timeout = float(os.getenv("BRIDGE_TASK_TIMEOUT_SECONDS", "180"))
        except ValueError:
            raise ProviderError("Invalid timeout configuration.") from None
        return cls(
            ProviderConfig(os.getenv("CHAT_OPENAI_MODEL", ""), os.getenv("CHAT_OPENAI_API_KEY_FILE", "")),
            ProviderConfig(os.getenv("CHAT_ANTHROPIC_MODEL", ""), os.getenv("CHAT_ANTHROPIC_API_KEY_FILE", "")),
            provider_timeout, bridge_timeout)

    def provider(self, name):
        selected = {"gpt": self.openai, "claude": self.anthropic}.get(name)
        if selected is None or not selected.model:
            raise ProviderError("Selected chat model is not configured.")
        return selected
