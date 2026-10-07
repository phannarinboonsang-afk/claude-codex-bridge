"""Executor adapters. Scheduling, persistence and subprocess ownership remain in Bridge."""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from abc import ABC, abstractmethod
from .security import sanitize

CODEX_VERSION = "codex-cli 0.160.0"
DISABLED_FEATURES = (
    "goals", "shell_tool", "view_image", "apps", "plugins", "hooks", "multi_agent", "multi_agent_v2",
    "browser_use", "browser_use_external", "computer_use", "image_generation", "code_mode",
    "code_mode_host", "memories", "shell_snapshot", "skill_search", "skill_mcp_dependency_install",
    "tool_suggest", "request_permissions_tool", "remote_plugin", "workspace_dependencies",
)
BROKER_TOOLS = ("read_file", "list_files", "search", "git_state")

class BaseAgentRunner(ABC):
    @abstractmethod
    async def execute(self, bridge, task_id, prompt, project, mode, timeout_s, started, timeout_status):
        ...

class ClaudeRunner(BaseAgentRunner):
    async def execute(self, bridge, *args):
        return await bridge._execute_claude(*args)

def codex_env(cfg) -> dict[str, str]:
    # HOME supports existing CLI ChatGPT login. No key/token/config env inheritance.
    env = {"PATH": str(Path(cfg.codex_bin).parent) + ":/usr/bin:/bin", "HOME": str(Path.home()),
           "LANG": "C.UTF-8", "TERM": "dumb"}
    for name in ("USER", "LOGNAME"):
        if name in os.environ: env[name] = os.environ[name]
    return env

def restricted_catalog(raw: bytes) -> dict:
    data = json.loads(raw)
    models = data.get("models")
    if not isinstance(models, list) or not models or len(models) > 100:
        raise ValueError("invalid bundled model catalog")
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str):
            raise ValueError("invalid model catalog entry")
        model.update(apply_patch_tool_type=None, shell_type="disabled", tool_mode="direct",
                     node_repl_disabled=True, supports_search_tool=False, multi_agent_version="disabled", experimental_supported_tools=[],
                     include_apps_usage_instructions=False, include_plugin_usage_instructions=False,
                     include_skills_usage_instructions=False)
    return data

def select_codex_model(configured: str | None, bundled: dict, account: dict) -> str:
    validated = {m["slug"] for m in bundled["models"]}
    available = account["models"]
    candidates = [m for m in available if m["slug"] in validated]
    if configured:
        if configured not in {m["slug"] for m in candidates}:
            raise ValueError("model unavailable for this account or not in bundled catalog")
        return configured
    visible = [m for m in candidates if m.get("visibility", "list") == "list"]
    if not visible:
        raise ValueError("no visible account model in validated bundled catalog")
    def priority(m):
        value = m.get("priority", 0)
        if not isinstance(value, int): raise ValueError("invalid model priority")
        return value
    return min(visible, key=priority)["slug"]


def broker_arguments(cfg, task_dir: Path, project: Path) -> list[str]:
    args = [str(Path(__file__).with_name("broker_server.py")), "--project", str(project),
            "--scratch", str(task_dir)]
    for root in cfg.read_roots: args += ["--root", str(root.resolve())]
    if cfg.auth_token_file is not None:
        args += ["--protected-file", str(cfg.auth_token_file.absolute())]
    return args

def codex_command(cfg, task_dir: Path, project: Path, model: str) -> list[str]:
    args = broker_arguments(cfg, task_dir, project)
    mcp = {"bridge_read": {"command": sys.executable, "args": ["-I", *args],
                          "enabled_tools": list(BROKER_TOOLS), "default_tools_approval_mode": "auto", "required": True,
                          "startup_timeout_sec": 15, "tool_timeout_sec": 15}}
    cmd = [cfg.codex_bin, "--no-daemon", "-a", "never", "exec", "--strict-config",
           "--ignore-user-config", "--ignore-rules", "--ephemeral", "--sandbox", "read-only",
           "--json", "--color", "never", "--skip-git-repo-check", "-C", str(task_dir),
           "--model", model]
    for feature in DISABLED_FEATURES: cmd += ["--disable", feature]
    cmd += ["--enable", "skip_host_skill_discovery"]
    overrides = {
        "web_search": "disabled", "project_doc_max_bytes": 0,
        "model_catalog_json": str(task_dir / "catalog.json"),
        "mcp_servers": mcp, "shell_environment_policy.inherit": "none",
        "suppress_unstable_features_warning": True,
    }
    # -c values are TOML; MCP config uses a TOML inline table.
    def toml(v):
        if isinstance(v, dict): return "{" + ",".join(json.dumps(k) + "=" + toml(x) for k, x in v.items()) + "}"
        if isinstance(v, list): return "[" + ",".join(toml(x) for x in v) + "]"
        return json.dumps(v)
    for key, value in overrides.items(): cmd += ["-c", key + "=" + toml(value)]
    return cmd + ["-"]

class CodexRunner(BaseAgentRunner):
    async def execute(self, bridge, task_id, prompt, project, mode, timeout_s, started, timeout_status):
        from .runner import run_process, proc_starttime, proc_command_hash
        cfg = bridge.cfg
        def result(status, **kw):
            return bridge._result(task_id, status, mode, str(project), started, agent="codex", **kw)
        if mode != "read_only":
            return result("blocked", summary="Codex supports read_only only", warnings=["codex_writer_denied"])
        if not Path(cfg.codex_bin).is_absolute():
            return result("blocked", summary="Codex executable must be an absolute trusted path",
                          warnings=["codex_executable_denied"])
        env = codex_env(cfg)
        on_start = lambda pid: bridge.store.set_proc(task_id, pid, proc_starttime(pid), proc_command_hash(pid))
        version = await run_process([cfg.codex_bin, "--version"], Path("/"), b"", env, 15, on_start=on_start)
        if version.returncode != 0 or version.stdout.decode(errors="replace").strip() != CODEX_VERSION:
            return result("blocked", summary="Codex version is not security-validated",
                          warnings=["codex_version_denied: requires " + CODEX_VERSION])
        catalog = await run_process([cfg.codex_bin, "debug", "models", "--bundled"], Path("/"), b"", env, 15, on_start=on_start)
        if catalog.returncode or catalog.timed_out or len(catalog.stdout) > 2000000:
            return result("failed", summary="Could not load bundled Codex catalog", warnings=["codex_catalog_failed"])
        try: data = restricted_catalog(catalog.stdout)
        except (ValueError, TypeError, KeyError):
            return result("blocked", summary="Invalid Codex catalog", warnings=["codex_catalog_denied"])
        # Bundled catalog is capability authority; authenticated discovery is
        # availability authority. Never assume its first bundled model is entitled.
        available = await run_process([cfg.codex_bin, "debug", "models"], Path("/"), b"", env, 15, on_start=on_start)
        if available.returncode or available.timed_out or len(available.stdout) > 2000000:
            return result("failed", summary="Could not discover account models", warnings=["codex_model_discovery_failed"])
        try:
            account = restricted_catalog(available.stdout)
            model = select_codex_model(cfg.codex_model, data, account)
        except (ValueError, TypeError, KeyError):
            return result("blocked", summary="No validated model available for this account", warnings=["codex_model_denied"])
        # Never run in user project: project config/instructions cannot add authority.
        task_parent = cfg.data_dir / "codex_tasks"; task_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=task_id + "-", dir=task_parent) as tmp:
            task_dir = Path(tmp).resolve()
            (task_dir / "catalog.json").write_text(json.dumps(data))
            cmd = codex_command(cfg, task_dir, project, model)
            out = await run_process(cmd, task_dir, prompt.encode(), env, timeout_s, on_start=on_start)
            return self.interpret(bridge, task_id, project, mode, started, out, timeout_s, timeout_status, model)

    def interpret(self, bridge, task_id, project, mode, started, out, timeout_s, timeout_status, model):
        from .runner import _summarize, _TEST_LINE
        def result(status, **kw):
            return bridge._result(task_id, status, mode, str(project), started, agent="codex", model=model, **kw)
        if out.timed_out:
            return result(timeout_status, summary=f"timed out after {timeout_s}s", warnings=["timeout: Codex task group killed"])
        if out.returncode:
            # Avoid raw events/arguments/prompts in diagnostics.
            return result("failed", exit_code=out.returncode, summary="Codex CLI failed", warnings=["codex_command_failed"])
        text = []; completed = False; failed = False; warnings = []
        try:
            for line in out.stdout.splitlines():
                event = json.loads(line)
                if not isinstance(event, dict): raise ValueError("event is not an object")
                kind = event.get("type")
                if kind == "turn.completed": completed = True
                elif kind in ("turn.failed", "error"): failed = True
                elif kind == "item.completed":
                    item = event.get("item", {})
                    if not isinstance(item, dict): raise ValueError("invalid item")
                    if item.get("type") == "agent_message":
                        value = item.get("text")
                        if not isinstance(value, str): raise ValueError("invalid final text")
                        text.append(value)
                    elif item.get("type") == "error": failed = True
        except (ValueError, TypeError):
            return result("failed", summary="Invalid Codex JSONL", warnings=["codex_invalid_jsonl"])
        if failed or not completed or not text:
            return result("failed", summary="Codex turn failed or incomplete", warnings=["codex_incomplete_turn"])
        clean = sanitize(text[-1], bridge.cfg.max_output_bytes)
        bridge.last_models["codex"] = model
        return result("completed", exit_code=0, summary=_summarize(clean), result=clean,
                      tests=[m.group(0).strip() for m in _TEST_LINE.finditer(clean)][:5], warnings=warnings)
