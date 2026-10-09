"""Sandbox launch errors are classifiable without revealing subprocess details."""
import errno
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bridge.read_broker import ReadBroker


@pytest.mark.parametrize("failure,code", [
    (FileNotFoundError(errno.ENOENT, "synthetic private detail"), "sandbox_executable_missing"),
    (PermissionError(errno.EACCES, "synthetic private detail"), "sandbox_launch_denied"),
    (OSError(errno.EPERM, "synthetic private detail"), "sandbox_launch_denied"),
    (subprocess.TimeoutExpired("synthetic private detail", 10,
                               stderr=b"synthetic private detail"), "sandbox_timeout"),
])
def test_sandbox_failure_classification(tmp_path, failure, code):
    broker = object.__new__(ReadBroker)
    broker.scratch = tmp_path
    broker.list_files = lambda: {"files": []}
    metadata = tmp_path / "metadata"
    metadata.mkdir()

    @contextmanager
    def git_directory():
        yield metadata, 123

    broker._git_directory = git_directory
    with patch("claude_bridge.read_broker.subprocess.run", side_effect=failure):
        with pytest.raises(PermissionError) as caught:
            broker.git_state("status")
    assert str(caught.value) == "isolated git inspection unavailable"
    assert caught.value.diagnostic_code == code
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert "synthetic private detail" not in repr(caught.value)


@pytest.mark.parametrize("loader_present", [True, False])
def test_fixed_optional_x86_loader_mount(tmp_path, loader_present):
    broker = object.__new__(ReadBroker)
    broker.scratch = tmp_path
    broker.list_files = lambda: {"files": []}

    @contextmanager
    def git_directory():
        yield tmp_path, 123

    broker._git_directory = git_directory
    with patch.object(Path, "is_dir", autospec=True,
                      side_effect=lambda path: str(path) == "/lib64" and loader_present), \
         patch("claude_bridge.read_broker.subprocess.run",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")) as launch:
        assert broker.git_state("status")["status"] == "ok"
    command = launch.call_args.args[0]
    assert ("/lib64" in command) == loader_present
    if loader_present:
        index = command.index("/lib64")
        assert command[index - 1:index + 2] == ["--ro-bind", "/lib64", "/lib64"]
