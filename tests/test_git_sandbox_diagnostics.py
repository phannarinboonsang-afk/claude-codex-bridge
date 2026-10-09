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
