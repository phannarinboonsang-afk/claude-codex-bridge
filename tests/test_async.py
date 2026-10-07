import asyncio
import logging
import subprocess
import time
from dataclasses import replace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_bridge.runner import Bridge, build_command, child_env, proc_starttime
from claude_bridge.server import build_server
from claude_bridge.store import utcnow

from conftest import alive, pids

TERMINAL = {"completed", "failed", "blocked", "timed_out", "cancelled"}


async def wait_for(bridge, tid, statuses, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = bridge.task_view(tid)
        if v["status"] in statuses:
            return v
        await asyncio.sleep(0.05)
    raise AssertionError(f"{tid} never reached {statuses}; last={bridge.task_view(tid)['status']}")


async def test_enqueue_running_completed(bridge):
    t = await bridge.start_task("SLOW hi", None, "read_only", None, request_id="r_test")
    assert t["task_id"].startswith("t_") and t["request_id"] == "r_test"
    assert t["status"] in ("queued", "running")
    v = await wait_for(bridge, t["task_id"], {"running"})
    assert v["completed_at"] is None and v["started_at"]
    v = await wait_for(bridge, t["task_id"], TERMINAL)
    assert v["status"] == "completed" and "bridge working" in v["result"]
    assert v["completed_at"] and v["files_changed"] == [] and v["warnings"] == []
    for k in ("task_id", "status", "summary", "result", "files_changed", "tests", "warnings", "started_at", "completed_at"):
        assert k in v
    assert bridge.registry.snapshot() == [] and bridge._jobs == {}
    assert bridge.store.get_task(only_completed=True)["task_id"] == t["task_id"]     # get_last_result sees it


async def test_start_returns_immediately(bridge):
    t0 = time.monotonic()
    t = await bridge.start_task("SLOW", None, "read_only", None)
    assert time.monotonic() - t0 < 1.0
    await wait_for(bridge, t["task_id"], TERMINAL)


async def test_blocked_start_is_immediate_and_launches_nothing(bridge, env):
    for kw in ({"project_path": "/etc"}, {"project_path": f"{env['root']}/../x"}, {"project_path": f"{env['root']}/.ssh"},
               {"mode": "writer"}):
        args = {"project_path": None, "mode": "read_only", **kw}
        t = await bridge.start_task("x", args["project_path"], args["mode"], None)
        assert t["status"] == "blocked" and t["warnings"]
        assert bridge.task_view(t["task_id"])["status"] == "blocked"
    assert pids(env) == []


async def test_timeout_marks_timed_out_and_kills_tree(bridge, env):
    t = await bridge.start_task("SPAWN hang", None, "read_only", 1)
    v = await wait_for(bridge, t["task_id"], TERMINAL)
    assert v["status"] == "timed_out" and any("timeout" in w for w in v["warnings"])
    await asyncio.sleep(0.3)
    assert pids(env) and not any(alive(p) for p in pids(env))
    assert pids(env, "children.log") and not any(alive(p) for p in pids(env, "children.log"))   # no orphan child
    assert bridge.registry.snapshot() == []


async def test_cancel_running_kills_only_its_own_tree(bridge, env):
    a = await bridge.start_task("SPAWN a", None, "read_only", 30)
    b = await bridge.start_task("HANG b", None, "read_only", 30)
    await wait_for(bridge, a["task_id"], {"running"})
    await wait_for(bridge, b["task_id"], {"running"})
    await asyncio.sleep(0.5)
    all_pids = pids(env)
    assert len(all_pids) == 2
    bystander = subprocess.Popen(["sleep", "30"])
    try:
        v = await bridge.cancel_task(a["task_id"])
        assert v["status"] == "cancelled" and v["completed_at"]
        await asyncio.sleep(0.3)
        child = pids(env, "children.log")[0]
        assert not alive(child)
        assert bridge.task_view(b["task_id"])["status"] == "running"       # sibling task untouched
        assert bystander.poll() is None                                    # unrelated process untouched
        assert sum(alive(p) for p in all_pids) == 1
        await bridge.cancel_task(b["task_id"])
        assert not any(alive(p) for p in all_pids)
    finally:
        bystander.kill()
        bystander.wait()
    assert bridge.registry.snapshot() == []


async def test_cancel_queued_task_never_runs(env):
    b = Bridge(replace(env["cfg"], max_concurrent_readers=1))
    first = await b.start_task("SLOW one", None, "read_only", None)
    second = await b.start_task("QUEUED two", None, "read_only", None)
    await wait_for(b, first["task_id"], {"running"})
    assert b.task_view(second["task_id"])["status"] == "queued"
    v = await b.cancel_task(second["task_id"])
    assert v["status"] == "cancelled" and "still queued" in v["warnings"][0]
    await wait_for(b, first["task_id"], TERMINAL)
    await asyncio.sleep(0.5)
    assert b.task_view(second["task_id"])["status"] == "cancelled"
    assert len(pids(env)) == 1                                              # second never launched


async def test_cancel_edge_cases(bridge):
    t = await bridge.start_task("hi", None, "read_only", None)
    await wait_for(bridge, t["task_id"], TERMINAL)
    v = await bridge.cancel_task(t["task_id"])
    assert v["status"] == "completed" and any("not_cancellable" in w for w in v["warnings"])
    assert (await bridge.cancel_task("t_000000000000"))["found"] is False


async def test_two_readers_parallel_third_queued(bridge):
    ids = [(await bridge.start_task(f"SLOW {i}", None, "read_only", None))["task_id"] for i in range(3)]
    peak, t0 = 0, time.monotonic()
    while any(bridge.task_view(i)["status"] not in TERMINAL for i in ids):
        peak = max(peak, sum(bridge.task_view(i)["status"] == "running" for i in ids))
        assert sum(bridge.task_view(i)["status"] == "running" for i in ids) <= 2
        await asyncio.sleep(0.05)
    assert peak == 2
    assert all(bridge.task_view(i)["status"] == "completed" for i in ids)
    assert time.monotonic() - t0 < 5.5          # 2 in parallel + 1 after: ~4s, not 6s serial


async def test_writer_lock_serializes(env):
    cfg = replace(env["cfg"], read_roots=(env["root"], env["wroot"]), writer_roots=(env["wroot"],), writer_enabled=True)
    writer_bridge = Bridge(cfg)
    w = str(env["wroot"])
    a = await writer_bridge.start_task("SLOW a", w, "writer", None)
    b = await writer_bridge.start_task("SLOW b", w, "writer", None)
    c = await writer_bridge.start_task("reader", w, "read_only", None)       # same project: waits for writers
    ids = [a["task_id"], b["task_id"], c["task_id"]]
    seen_running_together = False
    while any(writer_bridge.task_view(i)["status"] not in TERMINAL for i in ids):
        views = {i: writer_bridge.task_view(i)["status"] for i in ids}
        if views[ids[0]] == "running" and views[ids[1]] == "running":
            seen_running_together = True
        if "running" in (views[ids[0]], views[ids[1]]):
            assert not (views[ids[2]] == "running" and "running" in (views[ids[0]], views[ids[1]]))
        await asyncio.sleep(0.05)
    assert not seen_running_together
    assert all(writer_bridge.task_view(i)["status"] == "completed" for i in ids)


async def test_queue_full(env):
    b = Bridge(replace(env["cfg"], max_queue=1))
    first = await b.start_task("SLOW", None, "read_only", None)
    second = await b.start_task("x", None, "read_only", None)
    assert second["status"] == "blocked" and "queue_full" in second["warnings"][0]
    await wait_for(b, first["task_id"], TERMINAL)


# ---------- restart / interruption ----------
async def test_restart_marks_interrupted_and_keeps_history(env):
    b1 = Bridge(env["cfg"])
    done = await b1.start_task("hi", None, "read_only", None)
    await wait_for(b1, done["task_id"], TERMINAL)
    store = b1.store
    for tid, st in (("t_aaaaaaaaaaaa", "running"), ("t_bbbbbbbbbbbb", "queued")):
        store.save_task(b1._result(tid, st, "read_only", str(env["root"]), utcnow(), finished_at=None), "r_old")
    store.set_proc("t_aaaaaaaaaaaa", 999999, "123")                          # dead pid
    b2 = Bridge(env["cfg"])                                                  # simulated restart, same DB
    for tid, was in (("t_aaaaaaaaaaaa", "running"), ("t_bbbbbbbbbbbb", "queued")):
        v = b2.task_view(tid)
        assert v["status"] == "failed" and v["completed_at"] and f"was {was}" in v["warnings"][-1]
        assert "interrupted_by_restart" in v["warnings"][-1]
    assert b2.task_view(done["task_id"])["status"] == "completed"           # history intact
    msgs = b2.store.get_messages(task_id="t_aaaaaaaaaaaa")
    assert msgs and "interrupted" in msgs[0]["summary"]
    assert Bridge(env["cfg"]).store.active_tasks() == []                    # idempotent


async def test_restart_kills_provable_orphan_but_not_bystanders(env):
    cfg = env["cfg"]
    b1 = Bridge(cfg)
    cmd = build_command(cfg, "read_only")
    orphan = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=env["root"], env=child_env(cfg),
                              start_new_session=True)
    orphan.stdin.write(b"HANG forever")
    orphan.stdin.close()
    bystander = subprocess.Popen(["sleep", "30"], start_new_session=True)
    reused = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=env["root"], env=child_env(cfg),
                              start_new_session=True)                          # real claude-like process, wrong starttime
    reused.stdin.write(b"HANG forever")
    reused.stdin.close()
    try:
        await asyncio.sleep(0.8)
        for tid, p, st in (("t_cccccccccccc", orphan.pid, proc_starttime(orphan.pid)),
                           ("t_dddddddddddd", bystander.pid, proc_starttime(bystander.pid)),
                           ("t_eeeeeeeeeeee", reused.pid, "1")):
            b1.store.save_task(b1._result(tid, "running", "read_only", str(env["root"]), utcnow(), finished_at=None), "r_old")
            b1.store.set_proc(tid, p, st)
        Bridge(cfg)
        await asyncio.sleep(0.5)
        assert orphan.poll() is not None                                     # provable orphan killed
        assert bystander.poll() is None                                      # not a claude process -> untouched
        assert reused.poll() is None                                         # starttime mismatch (pid reuse) -> untouched
        v = Bridge(cfg).task_view("t_cccccccccccc")
        assert v["status"] == "failed" and "leftover Claude process group was killed" in v["warnings"][-1]
    finally:
        for p in (orphan, bystander, reused):
            p.kill()
            p.wait()


# ---------- MCP-level: invalid ids, disconnect, profile ----------
@pytest.mark.parametrize("tid", ["", "nope", "t_XYZ", "t_000000000000x", "../../etc/passwd", "t_0000000000'; DROP TABLE tasks;--"])
async def test_invalid_task_id_rejected_by_schema(bridge, tid):
    mcp, _ = build_server(bridge.cfg, bridge)
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        for tool in ("get_claude_task", "cancel_claude_task"):
            assert (await c.call_tool(tool, {"task_id": tid})).isError is True


async def test_unknown_but_valid_task_id(bridge):
    mcp, _ = build_server(bridge.cfg, bridge)
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        v = (await c.call_tool("get_claude_task", {"task_id": "t_0123456789ab"})).structuredContent
        assert v["found"] is False


async def test_client_disconnect_does_not_stop_task_in_memory(bridge):
    mcp, _ = build_server(bridge.cfg, bridge)
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        tid = (await c.call_tool("start_claude_task", {"prompt": "SLOW x"})).structuredContent["task_id"]
    # session A is gone
    async with create_connected_server_and_client_session(mcp._mcp_server) as c2:
        for _ in range(60):
            v = (await c2.call_tool("get_claude_task", {"task_id": tid})).structuredContent
            if v["status"] in TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert v["status"] == "completed"
        st = (await c2.call_tool("get_claude_status", {})).structuredContent
        assert st["running"] is False and st["queued_count"] == 0


async def test_chatgpt_profile_exposes_exactly_v1_tools(env):
    cfg = replace(env["cfg"], tool_profile="chatgpt")
    mcp, _ = build_server(cfg, Bridge(cfg))
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == {"start_claude_task", "get_claude_task", "cancel_claude_task", "get_claude_status", "get_last_result",
                     "get_agent_messages", "get_project_state"}
    assert "ask_claude" not in names           # no blocking call on the ChatGPT surface


async def test_status_reports_running_and_queued(env):
    cfg = replace(env["cfg"], max_concurrent_readers=1)
    mcp, b = build_server(cfg, Bridge(cfg))
    async with create_connected_server_and_client_session(mcp._mcp_server) as c:
        t1 = (await c.call_tool("start_claude_task", {"prompt": "SLOW 1"})).structuredContent
        await c.call_tool("start_claude_task", {"prompt": "SLOW 2"})
        await asyncio.sleep(0.5)
        st = (await c.call_tool("get_claude_status", {})).structuredContent
        assert st["running"] and st["current_task_id"] == t1["task_id"] and st["queued_count"] == 1
        await wait_for(b, t1["task_id"], TERMINAL)


# ---------- sanitization / no prompt persisted ----------
async def test_async_results_sanitized_prompt_never_persisted_or_logged(bridge, env, monkeypatch, caplog):
    monkeypatch.setenv("LEAK_ME_TOKEN", "super-secret-value-123456")
    monkeypatch.setenv("LEAK_ME", "super-secret-value-123456")
    caplog.set_level(logging.DEBUG)
    prompt = "LEAK PROMPT_CANARY_8f3a please"
    t = await bridge.start_task(prompt, None, "read_only", None)
    v = await wait_for(bridge, t["task_id"], TERMINAL)
    for s in ("sk-ant-api03", "hunter2", "abcdefghijklmnop12345", "super-secret-value-123456", "\x1b"):
        assert s not in v["result"] and s not in v["summary"], s
    raw = (env["cfg"].db_path).read_bytes()
    for s in (b"sk-ant-api03", b"hunter2", b"super-secret-value-123456", b"PROMPT_CANARY_8f3a"):
        assert s not in raw, s
    assert "PROMPT_CANARY_8f3a" not in caplog.text and "hunter2" not in caplog.text
    assert "prompt_sha=" in caplog.text and t["task_id"] in caplog.text and "r_" in caplog.text
    assert "PROMPT_CANARY_8f3a" not in str(bridge.store.get_messages(unread_only=False))


async def test_graceful_shutdown_marks_interrupted_and_kills_tree(bridge, env):
    t = await bridge.start_task("SPAWN shutdown", None, "read_only", 60)
    await wait_for(bridge, t["task_id"], {"running"})
    await asyncio.sleep(0.5)
    await bridge.shutdown()
    v = bridge.task_view(t["task_id"])
    assert v["status"] == "failed" and v["completed_at"] and any("interrupted_by_restart" in w for w in v["warnings"])
    await asyncio.sleep(0.3)
    assert not any(alive(p) for p in pids(env)) and not any(alive(p) for p in pids(env, "children.log"))
    assert bridge._jobs == {} and bridge.registry.snapshot() == []
