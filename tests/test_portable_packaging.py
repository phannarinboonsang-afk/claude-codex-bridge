import subprocess
from pathlib import Path


def test_install_dry_run_is_non_mutating(tmp_path):
    root = Path(__file__).resolve().parents[1]
    prefix = tmp_path / "install-root"
    result = subprocess.run([str(root / "scripts/install.sh"), "--dry-run", "--prefix", str(prefix)],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout.lower()
    assert not prefix.exists()


def test_install_preserves_existing_config_in_dry_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / "bridge.env"
    config.write_text("OPERATOR_VALUE=preserve\n")
    result = subprocess.run([str(root / "scripts/install.sh"), "--dry-run", "--config", str(config)],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert config.read_text() == "OPERATOR_VALUE=preserve\n"


def test_verify_reports_missing_tools_and_auth_without_showing_secret(tmp_path):
    root = Path(__file__).resolve().parents[1]
    secret = "Bearer local-test-secret-must-not-appear"
    auth = tmp_path / "auth-header"
    auth.write_text(secret)
    config = tmp_path / "bridge.env"
    config.write_text(f"BRIDGE_WORKSPACE_ROOT={tmp_path}\nAUTH_HEADER_FILE={auth}\nCLAUDE_EXECUTABLE={tmp_path}/missing-claude\nCODEX_EXECUTABLE={tmp_path}/missing-codex\n")
    result = subprocess.run([str(root / "scripts/verify.sh"), "--config", str(config), "--prerequisites-only"],
                            cwd=root, capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "auth file: present" in output.lower()
    assert "missing-claude" in output and "missing-codex" in output
    assert secret not in output


def test_config_example_has_no_machine_or_secret_values():
    root = Path(__file__).resolve().parents[1]
    content = (root / "config.example").read_text()
    assert "AUTH_HEADER_FILE=" in content
    assert "BRIDGE_WORKSPACE_ROOT=" in content
    assert "192.168." not in content
    assert "agx-" + "press1" not in content
    assert "Bearer " not in content


def test_verify_missing_auth_file_fails_without_showing_content(tmp_path):
    root = Path(__file__).resolve().parents[1]
    missing = tmp_path / "missing-auth-header"
    config = tmp_path / "bridge.env"
    config.write_text(f"BRIDGE_WORKSPACE_ROOT={tmp_path}\nAUTH_HEADER_FILE={missing}\n")
    result = subprocess.run([str(root / "scripts/verify.sh"), "--config", str(config), "--prerequisites-only"],
                            cwd=root, capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "FAIL auth file: present" in output
    assert str(missing) not in output


def test_systemd_template_uses_path_values_without_shell_quotes():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy/claude-chatgpt-bridge.service").read_text()
    assert "WorkingDirectory=@BRIDGE_HOME@" in unit
    assert "EnvironmentFile=-@CONFIG_FILE@" in unit
    assert "ExecStart=@PYTHON@ -m claude_bridge" in unit


def test_installer_rejects_systemd_paths_with_whitespace(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / "scripts/install.sh"), "--dry-run", "--prefix", str(tmp_path / "space root")],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert "must not contain whitespace" in result.stderr
