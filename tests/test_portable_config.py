from pathlib import Path

import pytest

from claude_bridge.config import BRIDGE_HOME, Config


CONFIG_ENV = (
    "BRIDGE_WORKSPACE_ROOT", "BRIDGE_READ_ROOTS", "BRIDGE_PROTECTED_WRITE_ROOTS",
    "CLAUDE_EXECUTABLE", "BRIDGE_CLAUDE_BIN", "CODEX_EXECUTABLE", "BRIDGE_CODEX_BIN",
    "BRIDGE_LISTEN_HOST", "BRIDGE_HOST", "BRIDGE_LISTEN_PORT", "BRIDGE_PORT",
    "BRIDGE_PROFILE", "BRIDGE_TOOL_PROFILE", "AUTH_HEADER_FILE", "BRIDGE_AUTH_TOKEN_FILE",
    "BRIDGE_DATA_DIR", "BRIDGE_CODEX_ENABLED", "BRIDGE_WRITER_ENABLED", "BRIDGE_WRITER_ROOTS",
)


@pytest.fixture
def clean_config_env(monkeypatch):
    for name in CONFIG_ENV + ("XDG_STATE_HOME",):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_workspace_local_and_keep_security_off(clean_config_env):
    cfg = Config.from_env()

    assert cfg.workspace_root == BRIDGE_HOME
    assert cfg.read_roots == (BRIDGE_HOME,)
    assert cfg.protected_write_roots == (BRIDGE_HOME,)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 5077
    assert cfg.tool_profile == "chatgpt"
    assert cfg.codex_enabled is False
    assert cfg.writer_enabled is False
    assert cfg.data_dir == Path.home() / ".local" / "state" / "claude-chatgpt-bridge"
    assert cfg.auth_token_file is None


def test_portable_environment_names_override_runtime_configuration(clean_config_env, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    extra_read = tmp_path / "reference"
    protected = tmp_path / "do-not-write"
    data = tmp_path / "state"
    auth = tmp_path / "auth-header"
    for path in (workspace, extra_read, protected):
        path.mkdir()
    values = {
        "BRIDGE_WORKSPACE_ROOT": str(workspace),
        "BRIDGE_READ_ROOTS": f"{workspace}:{extra_read}",
        "BRIDGE_PROTECTED_WRITE_ROOTS": str(protected),
        "CLAUDE_EXECUTABLE": str(tmp_path / "bin" / "claude"),
        "CODEX_EXECUTABLE": str(tmp_path / "bin" / "codex"),
        "BRIDGE_LISTEN_HOST": "127.0.0.1",
        "BRIDGE_LISTEN_PORT": "6099",
        "BRIDGE_PROFILE": "agents",
        "AUTH_HEADER_FILE": str(auth),
        "BRIDGE_DATA_DIR": str(data),
        "BRIDGE_CODEX_ENABLED": "1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    cfg = Config.from_env()

    assert cfg.workspace_root == workspace
    assert cfg.read_roots == (workspace, extra_read)
    assert cfg.protected_write_roots == (workspace, protected)
    assert cfg.claude_bin == values["CLAUDE_EXECUTABLE"]
    assert cfg.codex_bin == values["CODEX_EXECUTABLE"]
    assert (cfg.host, cfg.port) == ("127.0.0.1", 6099)
    assert cfg.tool_profile == "agents"
    assert cfg.auth_token_file == auth
    assert cfg.data_dir == data
    assert cfg.codex_enabled is True
    assert cfg.writer_enabled is False


def test_legacy_environment_names_remain_supported(clean_config_env, monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    token_file = tmp_path / "bridge-auth"
    monkeypatch.setenv("BRIDGE_READ_ROOTS", str(root))
    monkeypatch.setenv("BRIDGE_CLAUDE_BIN", str(tmp_path / "claude"))
    monkeypatch.setenv("BRIDGE_CODEX_BIN", str(tmp_path / "codex"))
    monkeypatch.setenv("BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGE_PORT", "5088")
    monkeypatch.setenv("BRIDGE_TOOL_PROFILE", "agents")
    monkeypatch.setenv("BRIDGE_AUTH_TOKEN_FILE", str(token_file))

    cfg = Config.from_env()

    assert cfg.read_roots == (root,)
    assert cfg.claude_bin == str(tmp_path / "claude")
    assert cfg.codex_bin == str(tmp_path / "codex")
    assert (cfg.host, cfg.port, cfg.tool_profile) == ("127.0.0.1", 5088, "agents")
    assert cfg.auth_token_file == token_file


def test_relative_workspace_root_is_rejected(clean_config_env, monkeypatch):
    monkeypatch.setenv("BRIDGE_WORKSPACE_ROOT", "../outside")

    with pytest.raises(ValueError, match="absolute"):
        Config.from_env()

def test_operator_protected_roots_reject_writer_overlap(clean_config_env, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    protected = tmp_path / "protected"
    workspace.mkdir()
    protected.mkdir()
    monkeypatch.setenv("BRIDGE_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("BRIDGE_PROTECTED_WRITE_ROOTS", str(protected))
    cfg = Config.from_env()

    from claude_bridge.security import validate_writer_roots

    with pytest.raises(ValueError, match="protected"):
        validate_writer_roots((protected / "child",), cfg.protected_write_roots)
