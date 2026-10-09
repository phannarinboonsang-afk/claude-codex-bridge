from ..safety import safe_text
from .http import HTTPProvider
from .base import ProviderError

class OpenAIChatProvider(HTTPProvider):
    def generate(self, messages, model, timeout):
        key = self.key()
        data = self.request("https://api.openai.com/v1/responses",
            {"model": model, "input": messages, "store": False},
            {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, timeout)
        try:
            text = "".join(item["text"] for output in data["output"] if output.get("type") == "message"
                for item in output["content"] if item.get("type") == "output_text")
            if not text.strip():
                raise ValueError()
            return safe_text(text, (key, self.key_file))
        except (KeyError, TypeError, ValueError):
            raise ProviderError("Chat model returned an invalid response.") from None
