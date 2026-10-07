import asyncio
import os
import subprocess
from dataclasses import replace

import pytest

from claude_bridge.config import Config
from claude_bridge.runner import Bridge, build_command
from claude_bridge.security import PathError, sanitize, validate_project_path, validate_writer_roots

from conftest import alive, last_call, pids



# ---------- read-only call ----------
async def test_read_only_call_schema_and_argv(bridge, env):
    r = await bridge.ask_claude("hello", str(env["root"]), "read_only", None)
    assert r["status"] == "completed"
    for k in ("task_id", "status", "summary", "result", "files_changed", "tests", "warnings"):
        assert k in r
    assert "bridge working" in r["result"] and r["model"] == "claude-fake-1"
    call = last_call(env)
    assert call["prompt"] == "hello"                      # prompt via stdin, not argv
    assert "hello" not in call["argv"]
    a = call["argv"]
    assert "-p" in a and "--restricted" in a and a[a.index("--permission-mode") + 1] == "dontAsk"
    assert a[a.index("--tools") + 1] == "Read,Grep,Glob"   # no Bash/Edit/Write
    assert "Bash" not in a[a.index("--allowedTools") + 1]
    assert "Read(**/.env)" in a[a.index("--disallowedTools") + 1]
    assert os.path.realpath(call["cwd"]) == os.path.realpath(env["root"])


async def test_default_project_and_defaults(bridge, env):
    r = await bridge.ask_claude("hi", None, "read_only", None)
    assert r["status"] == "completed" and r["project"] == str(env["root"].resolve())


async def test_permission_denials_become_warnings(bridge, env):
    r = await bridge.ask_claude("DENIED", None, "read_only", None)
    assert any("permission_denied" in w for w in r["warnings"])


async def test_tests_extracted(bridge):
    r = await bridge.ask_claude("PYTEST", None, "read_only", None)
    assert any("3 passed" in t for t in r["tests"])


async def test_claude_is_error_flag(bridge):
    r = await bridge.ask_claude("ISERR", None, "read_only", None)
    assert r["status"] == "failed"


async def test_non_json_output_still_returned(bridge):
    r = await bridge.ask_claude("NOTJSON", None, "read_only", None)
    assert r["status"] == "completed" and "plain text" in r["result"]
    assert any("non_json" in w for w in r["warnings"])


# ---------- path allowlist / traversal / secrets ----------
@pytest.mark.parametrize("bad,code", [
    ("/etc", "outside_roots"),
    ("/", "outside_roots"),
    ("{root}/../outside", "traversal"),
    ("{root}/sub/../../outside", "traversal"),
    ("relative/path", "not_absolute"),
    ("{root}/does-not-exist", "not_found"),
    ("{root}/escape", "outside_roots"),            # symlink escaping the root
    ("{root}/.ssh", "secret_path"),
    ("{root}/secrets", "secret_path"),
    ("{root}/sub\x00x", "invalid"),
])
async def test_bad_paths_blocked(bridge, env, bad, code):
    p = bad.format(root=env["root"])
    r = await bridge.ask_claude("x", p, "read_only", None)
    assert r["status"] == "blocked", r
    assert code in r["warnings"][0]
    assert not (env["fake"].parent / "argv.json").exists()   # Claude never launched


async def test_valid_subdir_allowed(bridge, env):
    r = await bridge.ask_claude("x", str(env["root"] / "sub"), "read_only", None)
    assert r["status"] == "completed"


def test_validate_unit(env):
    roots = (env["root"],)
    assert validate_project_path(str(env["root"]), roots) == env["root"].resolve()
    with pytest.raises(PathError) as e:
        validate_project_path("/home/../etc", roots)
    assert e.value.code == "traversal"


def test_protected_writer_roots_refused(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    cfg = Config(protected_write_roots=(protected,))
    with pytest.raises(ValueError):
        validate_writer_roots((protected,), cfg.protected_write_roots)
    with pytest.raises(ValueError):
        validate_writer_roots((protected / "child",), cfg.protected_write_roots)
    with pytest.raises(ValueError):
        Bridge(Config(writer_roots=(protected,), protected_write_roots=(protected,), writer_enabled=True,
                      data_dir=tmp_path / "data"))


# ---------- writer gating + locking ----------
async def test_writer_denied_by_default(bridge, env):
    r = await bridge.ask_claude("edit", str(env["root"]), "writer", None)
    assert r["status"] == "blocked" and "writer_denied" in r["warnings"][0]
    assert not (env["fake"].parent / "argv.json").exists()


async def test_writer_enabled_but_path_not_in_writer_roots(writer_bridge, env):
    r = await writer_bridge.ask_claude("edit", str(env["root"]), "writer", None)
    assert r["status"] == "blocked" and "outside_roots" in r["warnings"][0]


async def test_writer_profile_allowlist(writer_bridge, env):
    r = await writer_bridge.ask_claude("edit", str(env["wroot"]), "writer", None)
    assert r["status"] == "completed"
    a = last_call(env)["argv"]
    allowed, denied = a[a.index("--allowedTools") + 1], a[a.index("--disallowedTools") + 1]
    assert "Edit" in allowed and "Bash(git commit:*)" in allowed
    assert "Bash(git reset:*)" in denied and "Bash(git clean:*)" in denied and "Bash(systemctl:*)" in denied
    assert "git reset" not in allowed and "git clean" not in allowed
    assert "Bash(systemctl:*)" not in allowed


async def test_concurrent_writers_second_blocked(writer_bridge, env):
    t1 = asyncio.create_task(writer_bridge.ask_claude("SLOW", str(env["wroot"]), "writer", None))
    await asyncio.sleep(0.5)
    r2 = await writer_bridge.ask_claude("second", str(env["wroot"]), "writer", None)
    assert r2["status"] == "blocked" and "busy" in r2["warnings"][0]
    assert (await t1)["status"] == "completed"
    # lock released: a new writer is admitted afterwards
    assert (await writer_bridge.ask_claude("third", str(env["wroot"]), "writer", None))["status"] == "completed"


async def test_reader_blocked_while_writer_on_same_project(env):
    wroot = env["wroot"]
    cfg = replace(env["cfg"], read_roots=(env["root"], wroot), writer_roots=(wroot,), writer_enabled=True)
    b = Bridge(cfg)
    t1 = asyncio.create_task(b.ask_claude("SLOW", str(wroot), "writer", None))
    await asyncio.sleep(0.5)
    r = await b.ask_claude("read", str(wroot), "read_only", None)
    assert r["status"] == "blocked"
    await t1


async def test_writer_files_changed(writer_bridge, env):
    w = env["wroot"]
    subprocess.run(["git", "init", "-q"], cwd=w, check=True)
    (w / "a.txt").write_text("x")
    # fake claude does not edit anything -> no files changed
    r = await writer_bridge.ask_claude("noop", str(w), "writer", None)
    assert r["files_changed"] == []


# ---------- timeout / failure ----------
async def test_timeout_kills_process(bridge, env):
    r = await bridge.ask_claude("HANG", None, "read_only", 1)
    assert r["status"] == "failed" and any("timeout" in w for w in r["warnings"])
    pid = pids(env)[-1]
    await asyncio.sleep(0.3)
    assert not alive(pid)
    # registry released
    assert bridge.registry.snapshot() == []


async def test_command_failure(bridge):
    r = await bridge.ask_claude("FAIL", None, "read_only", None)
    assert r["status"] == "failed" and r["exit_code"] == 3
    assert "command_failed" in r["warnings"] and "hunter2" not in r["result"]


async def test_missing_binary(env):
    b = Bridge(replace(env["cfg"], claude_bin="/nonexistent/claude"))
    r = await b.ask_claude("x", None, "read_only", None)
    assert r["status"] == "failed" and "claude_not_found" in r["warnings"][0]


# ---------- sanitization ----------
async def test_output_sanitized_and_env_not_leaked(bridge, env, monkeypatch):
    monkeypatch.setenv("LEAK_ME_TOKEN", "super-secret-value-123456")
    monkeypatch.setenv("LEAK_ME", "super-secret-value-123456")
    r = await bridge.ask_claude("LEAK", None, "read_only", None)
    out = r["result"]
    for s in ("sk-ant-api03", "hunter2", "abcdefghijklmnop12345", "super-secret-value-123456", "\x1b"):
        assert s not in out, s
    assert "[REDACTED]" in out
    keys = last_call(env)["env_keys"]
    assert "LEAK_ME_TOKEN" not in keys and "LEAK_ME" not in keys
    assert not any(k for k in keys if "KEY" in k or "TOKEN" in k or "SECRET" in k)


def test_sanitize_unit():
    assert "AKIA" not in sanitize("id " + "AKIA" + "0" * 16)
    assert "ghp_" not in sanitize("ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert "mypass" not in sanitize("postgres://user:mypass@host/db")
    assert "PRIVATE KEY" not in sanitize("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    assert sanitize("a" * 100, max_bytes=10).endswith("[output truncated]")
    assert sanitize("normal text 123") == "normal text 123"


# ---------- inbox ----------
async def test_inbox_lifecycle(bridge):
    s = bridge.store
    assert s.get_messages() == []
    m = s.post_message("hello from claude", "body", "t_x")
    assert m["status"] == "unread"
    got = s.get_messages()
    assert [g["id"] for g in got] == [m["id"]] and got[0]["task_id"] == "t_x"
    again = s.get_messages(mark_read=False)
    assert len(again) == 1                              # reading alone doesn't consume
    s.get_messages(mark_read=True)
    assert s.get_messages() == []                       # now read
    allm = s.get_messages(unread_only=False)
    assert allm[0]["status"] == "read"


async def test_task_completion_posts_message_and_last_result(bridge):
    r = await bridge.ask_claude("hi", None, "read_only", None)
    msgs = bridge.store.get_messages()
    assert msgs and msgs[0]["task_id"] == r["task_id"] and "completed" in msgs[0]["summary"]
    assert bridge.store.get_task(r["task_id"])["task_id"] == r["task_id"]
    assert bridge.store.get_task(only_completed=True)["task_id"] == r["task_id"]
    assert bridge.store.get_task("nope") is None


def test_env_suffix_files_are_secret_and_denied():
    from claude_bridge.security import is_secret_name
    from claude_bridge.config import READ_DENY
    for n in (".env", ".env.local", "probe.env", "prod.env", "id_rsa", "git-token.txt", "my.pem", "api_key.json"):
        assert is_secret_name(n), n
    for n in ("README.md", "tokenizer.py", "environment.md", "reports"):
        assert not is_secret_name(n), n
    assert "Read(**/*.env)" in READ_DENY and "Read(**/.env)" in READ_DENY
