"""Loopback-only MCP client. Credentials never enter task arguments."""
import json
import os
from urllib.parse import urlsplit
import requests
from .providers.credentials import read_provider_key
from .providers.base import ProviderError
from .safety import safe_text

class BridgeError(Exception):
    code = "bridge_unavailable"
    def __init__(self, code="bridge_unavailable"):
        self.code = code
        super().__init__("Agent execution is unavailable.")

class BridgeClient:
    def __init__(self, url=None, auth_file=None, transport_factory=requests.Session, rpc_timeout=10):
        self.url = url or os.getenv("AGENT_CHAT_BRIDGE_URL", "http://127.0.0.1:5077/mcp")
        parsed = urlsplit(self.url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.path != "/mcp" or parsed.query or parsed.fragment:
            raise BridgeError("bridge_configuration")
        self.auth_file = auth_file or os.getenv("BRIDGE_AUTH_HEADER_FILE", "")
        self.transport = transport_factory()
        self.transport.trust_env = False
        self.rpc_timeout = rpc_timeout
        self.session_id = None
        self.sequence = 0
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self.initialized = False

    def __enter__(self):
        try:
            # Auth file format permits an Authorization header, not arbitrary headers.
            value = read_provider_key_header(self.auth_file)
            self.headers["Authorization"] = value
            result = self._rpc("initialize", {"protocolVersion": "2025-03-26",
                "capabilities": {}, "clientInfo": {"name": "agent-chat", "version": "1"}})
            version = result.get("protocolVersion")
            if version not in ("2025-03-26", "2025-06-18", "2025-11-25"):
                raise BridgeError("bridge_protocol")
            self.headers["MCP-Protocol-Version"] = version
            self._rpc("notifications/initialized", {}, notification=True)
            self.initialized = True
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()

    def close(self):
        # No deletion of server task state. MCP connection cleanup only.
        if self.session_id and self.headers.get("Authorization"):
            try:
                response = self.transport.delete(self.url, headers=dict(self.headers), timeout=self.rpc_timeout, allow_redirects=False)
                response.close()
            except Exception:
                pass
        self.session_id = None
        self.headers.pop("Authorization", None)
        self.transport.close()

    def _rpc(self, method, params, notification=False):
        self.sequence += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = self.sequence
        response = None
        try:
            response = self.transport.post(self.url, json=body, headers=dict(self.headers),
                timeout=self.rpc_timeout, allow_redirects=False)
            if response.status_code not in (200, 202):
                raise BridgeError("bridge_auth" if response.status_code in (401,403) else "bridge_unavailable")
            if response.headers.get("Mcp-Session-Id"):
                self.session_id = response.headers["Mcp-Session-Id"]
                self.headers["Mcp-Session-Id"] = self.session_id
            if notification:
                return {}
            try:
                data = response.json()
            except ValueError:
                data = None
                for frame in response.text.split("\n\n"):
                    lines = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
                    if lines:
                        candidate = json.loads("\n".join(lines))
                        if candidate.get("id") == self.sequence:
                            data = candidate
            if not isinstance(data, dict) or data.get("id") != self.sequence or "error" in data:
                raise BridgeError("bridge_protocol")
            return data["result"]
        except requests.Timeout:
            raise BridgeError("bridge_timeout") from None
        except BridgeError:
            raise
        except Exception:
            raise BridgeError() from None
        finally:
            if response is not None:
                response.close()

    def call(self, name, arguments):
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        try:
            if result.get("isError"):
                raise BridgeError("bridge_task_failed")
            value = result.get("structuredContent")
            if value is None:
                value = json.loads(next(c["text"] for c in result["content"] if c["type"] == "text"))
            if not isinstance(value, dict):
                raise ValueError()
            if value.get("error") or value.get("ok") is False:
                raise BridgeError("bridge_task_failed")
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            def scrub(item):
                if isinstance(item, str):
                    return safe_text(item, (token, self.auth_file))
                if isinstance(item, list):
                    return [scrub(v) for v in item]
                if isinstance(item, dict):
                    return {k: scrub(v) for k, v in item.items()}
                return item
            return scrub(value)
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("bridge_protocol") from None

    def start_agent_task(self, agent, prompt, timeout):
        if agent not in ("claude", "codex"):
            raise BridgeError("invalid_agent")
        result = self.call("start_agent_task", {"agent": agent, "prompt": prompt,
            "mode": "read_only", "timeout": int(timeout)})
        if not result.get("task_id") or result.get("agent") != agent:
            raise BridgeError("bridge_protocol")
        return result

    def get_agent_task(self, task_id):
        return self.call("get_agent_task", {"task_id": task_id})

    def cancel_agent_task(self, task_id):
        return self.call("cancel_agent_task", {"task_id": task_id})

def read_provider_key_header(path):
    """Same protected-file checks, but supports spaces in a header value."""
    import stat
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError()
        text = os.read(fd, 8193).decode("ascii").strip()
        if text.lower().startswith("authorization:"):
            text = text.split(":", 1)[1].strip()
        if len(text) > 8192 or not text.startswith("Bearer ") or len(text.split()) != 2:
            raise ValueError()
        return text
    except (OSError, ValueError, TypeError, UnicodeError):
        raise BridgeError("bridge_configuration") from None
    finally:
        if fd is not None:
            os.close(fd)
