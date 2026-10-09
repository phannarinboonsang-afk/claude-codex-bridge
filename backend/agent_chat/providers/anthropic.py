from ..safety import safe_text
from .http import HTTPProvider
from .base import ProviderError

class AnthropicChatProvider(HTTPProvider):
    def generate(self, messages, model, timeout):
        key = self.key()
        body = {"model": model, "max_tokens": 4096,
            "messages": [m for m in messages if m["role"] != "system"]}
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        if system:
            body["system"] = system
        data = self.request("https://api.anthropic.com/v1/messages", body,
            {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, timeout)
        try:
            text = "".join(item["text"] for item in data["content"] if item.get("type") == "text")
            if not text.strip():
                raise ValueError()
            return safe_text(text, (key, self.key_file))
        except (KeyError, TypeError, ValueError):
            raise ProviderError("Chat model returned an invalid response.") from None
