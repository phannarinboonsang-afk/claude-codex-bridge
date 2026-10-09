from typing import Protocol

class ProviderError(Exception):
    code = "provider_failed"
    def __init__(self, message="Chat model request failed."):
        super().__init__(message)

class ProviderTimeout(ProviderError):
    code = "provider_timeout"

class ProviderCancelled(ProviderError):
    code = "user_cancelled"

class ChatModelProvider(Protocol):
    def generate(self, messages: list[dict], model: str, timeout: float) -> str: ...
