"""Private provider subprocess: key is read here, never sent over IPC."""
import json
import sys
from .providers.openai import OpenAIChatProvider
from .providers.anthropic import AnthropicChatProvider
from .providers.base import ProviderError

def main():
    try:
        payload = json.load(sys.stdin)
        cls = {"gpt": OpenAIChatProvider, "claude": AnthropicChatProvider}[payload["selector"]]
        text = cls(payload["key_file"]).generate(payload["messages"], payload["model"], payload["timeout"])
        print(json.dumps({"text": text}))
    except ProviderError as error:
        print(json.dumps({"error": error.code}))
    except Exception:
        print(json.dumps({"error": "provider_failed"}))

if __name__ == "__main__":
    main()
