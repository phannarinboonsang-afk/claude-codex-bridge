import json
import os
import stat
import sys
from pathlib import Path

import pytest

from claude_bridge.config import Config
from claude_bridge.runner import Bridge
from claude_bridge.server import build_server

FAKE = r'''#!{py}
import json, os, sys, time
from pathlib import Path
here = Path(__file__).parent
if "--version" in sys.argv:
    print("9.9.9 (Fake Claude)"); sys.exit(0)
prompt = sys.stdin.read()
with open(here / "pids.log", "a") as _f:
    _f.write(str(os.getpid()) + "\n")
if "SPAWN" in prompt:
    import subprocess
    _ch = subprocess.Popen(["sleep", "60"])
    with open(here / "children.log", "a") as _f:
        _f.write(str(_ch.pid) + "\n")
(here / "argv.json").write_text(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env_keys": sorted(os.environ), "prompt": prompt}}))
if "FAIL" in prompt:
    sys.stderr.write("boom: password=hunter2 something broke\n"); sys.exit(3)
if "HANG" in prompt or "SPAWN" in prompt:
    time.sleep(60)
if "SLOW" in prompt:
    time.sleep(2)
if "NOTJSON" in prompt:
    print("plain text answer"); sys.exit(0)
res = "cwd=" + os.getcwd() + " bridge working"
if "LEAK" in prompt:
    res = ("key sk-ant-api03-ABCDEFGHIJKLMNOP1234 password=hunter2 Authorization: Bearer abcdefghijklmnop12345 "
           "token " + os.environ.get("LEAK_ME", "") + " \x1b[31mred\x1b[0m")
if "PYTEST" in prompt:
    res += "\n\n===== 3 passed, 1 failed in 0.5s ====="
out = {{"type": "result", "is_error": "ISERR" in prompt, "result": res, "total_cost_usd": 0.01,
       "modelUsage": {{"claude-fake-1": {{}}}}, "permission_denials": [{{"tool_name": "Read"}}] if "DENIED" in prompt else []}}
print(json.dumps(out))
'''


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_text(FAKE.format(py=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / ".ssh").mkdir()
    (root / "secrets").mkdir()
    wroot = tmp_path / "wroot"
    wroot.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    cfg = Config(claude_bin=str(fake), data_dir=tmp_path / "data", read_roots=(root,),
                 writer_roots=(), writer_enabled=False, default_timeout=20, max_timeout=30, tool_profile="full")
    return {"cfg": cfg, "root": root, "wroot": wroot, "outside": outside, "fake": fake, "tmp": tmp_path}


@pytest.fixture
def bridge(env):
    return Bridge(env["cfg"])


@pytest.fixture
def writer_bridge(env):
    from dataclasses import replace
    cfg = replace(env["cfg"], writer_roots=(env["wroot"],), writer_enabled=True)
    return Bridge(cfg)


def last_call(env):
    return json.loads((env["fake"].parent / "argv.json").read_text())


def pids(env, name="pids.log"):
    f = env["fake"].parent / name
    return [int(x) for x in f.read_text().split()] if f.exists() else []


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # zombies count as dead
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False
