import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from .providers.base import ProviderError, ProviderTimeout, ProviderCancelled

class ProcessProviderGateway:
    def __init__(self, command=None):
        self.command = command or [sys.executable, "-m", "backend.agent_chat.provider_worker"]

    def generate(self, selector, messages, config, timeout, cancel):
        if cancel():
            raise ProviderCancelled()
        root = Path(__file__).resolve().parents[2]
        # Do not inherit provider keys, Bridge auth, proxies, or unrelated process configuration.
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1",
               "LANG": "C.UTF-8"}
        payload = json.dumps({"selector": selector, "messages": messages, "model": config.model,
            "key_file": config.key_file, "timeout": timeout})
        process = subprocess.Popen(self.command, cwd=root, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        deadline = time.monotonic() + timeout
        first = True
        try:
            while True:
                if cancel():
                    raise ProviderCancelled()
                if time.monotonic() >= deadline:
                    raise ProviderTimeout("Chat model timed out.")
                try:
                    output, _ = process.communicate(payload if first else None, timeout=min(0.1, max(0.001, deadline-time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if cancel():
                raise ProviderCancelled()
            if process.returncode or len(output) > 200000:
                raise ProviderError()
            result = json.loads(output)
            if result.get("error") == "provider_timeout":
                raise ProviderTimeout()
            if result.get("error") or not isinstance(result.get("text"), str) or not result["text"].strip():
                raise ProviderError()
            return result["text"]
        except (ProviderError, ProviderTimeout, ProviderCancelled):
            raise
        except Exception:
            raise ProviderError() from None
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
