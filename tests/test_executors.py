import asyncio
import importlib.util
import json
import os
import stat
import sys
from dataclasses import replace
import pytest
from claude_bridge.runner import Bridge
from test_async import wait_for, TERMINAL
from conftest import alive,pids

FAKE = r'''#!{py}
import json,os,sys,time,subprocess
from pathlib import Path
if "--version" in sys.argv: print("codex-cli 0.160.0");sys.exit(0)
if "debug" in sys.argv:
 print(json.dumps({{"models":[{{"slug":"codex-test-model","apply_patch_tool_type":"freeform","shell_type":"unified_exec"}}]}}));sys.exit(0)
here=Path(__file__).parent
prompt=sys.stdin.read()
with (here/"codex-pids.log").open("a") as f:f.write(str(os.getpid())+"\n")
(here/"codex-call.json").write_text(json.dumps({{"argv":sys.argv[1:],"env":list(os.environ),"cwd":os.getcwd()}}))
if "SPAWN" in prompt:
 p=subprocess.Popen(["sleep","60"])
 with (here/"codex-children.log").open("a") as f:f.write(str(p.pid)+"\n")
if "HANG" in prompt or "SPAWN" in prompt:time.sleep(60)
if "SLOW" in prompt:time.sleep(2)
if "BADJSON" in prompt: print("bad");sys.exit(0)
if "FAIL" in prompt: print(json.dumps({{"type":"turn.failed","error":{{"message":"password=hunter2"}}}}));sys.exit(0)
print(json.dumps({{"type":"thread.started","thread_id":"test"}}))
print(json.dumps({{"type":"item.completed","item":{{"type":"agent_message","text":"codex result password=hunter2\n3 passed in 0.1s"}}}}))
if "INCOMPLETE" not in prompt:print(json.dumps({{"type":"turn.completed","usage":{{}}}}))
'''

def make_bridge(env,**kw):
    assert importlib.util.find_spec("claude_bridge.executors"),"Agent executors module missing"
    f=env["fake"].parent/"codex";f.write_text(FAKE.format(py=sys.executable));f.chmod(0o700)
    return Bridge(replace(env["cfg"],codex_bin=str(f),codex_enabled=True,**kw))

async def test_codex_async_identity_sanitization_inbox(env,monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","DUMMY_SHOULD_NOT_INHERIT")
    b=make_bridge(env)
    t=await b.start_task("UNIQUE_PROMPT_DO_NOT_STORE",None,"read_only",None,agent="codex")
    assert t["agent"]=="codex"
    v=await wait_for(b,t["task_id"],TERMINAL)
    assert v["agent"]=="codex" and v["status"]=="completed" and v["model"]=="codex-test-model"
    assert "hunter2" not in str(v) and v["tests"] and v["completed_at"]
    assert b.store.get_task(only_completed=True)["agent"]=="codex"
    assert b.store.get_messages()[0]["task_id"]==t["task_id"]
    call=json.loads((env["fake"].parent/"codex-call.json").read_text())
    assert "OPENAI_API_KEY" not in call["env"]
    assert set(call["env"]) <= {"PATH","HOME","LANG","TERM","USER","LOGNAME"}
    assert call["cwd"]!=str(env["root"])
    assert "UNIQUE_PROMPT_DO_NOT_STORE" not in b.cfg.db_path.read_bytes().decode(errors="replace")

@pytest.mark.parametrize("prompt",["BADJSON","FAIL","INCOMPLETE"])
async def test_codex_stream_failures_not_completed(env,prompt):
    b=make_bridge(env)
    t=await b.start_task(prompt,None,"read_only",None,agent="codex")
    v=await wait_for(b,t["task_id"],TERMINAL)
    assert v["status"]=="failed" and v["agent"]=="codex"
    assert "hunter2" not in str(v)

async def test_codex_writer_refused_even_if_claude_writer_enabled(env):
    b=make_bridge(env,writer_enabled=True,writer_roots=(env["wroot"],))
    t=await b.start_task("write",str(env["wroot"]),"writer",None,agent="codex")
    assert t["status"]=="blocked" and t["agent"]=="codex"
    assert pids(env,"codex-pids.log")==[]

async def test_agent_selector_and_disabled_codex(env):
    b=make_bridge(env)
    with pytest.raises(ValueError): await b.start_task("x",None,"read_only",None,agent="other")
    b=Bridge(replace(b.cfg,codex_enabled=False))
    t=await b.start_task("x",None,"read_only",None,agent="codex")
    assert t["status"]=="blocked"

async def test_mixed_readers_share_limit_and_cancel_tree(env):
    b=make_bridge(env,max_concurrent_readers=2)
    a=await b.start_task("SPAWN",None,"read_only",30,agent="codex")
    c=await b.start_task("HANG",None,"read_only",30)
    third=await b.start_task("queued",None,"read_only",None,agent="codex")
    await wait_for(b,a["task_id"],{"running"});await wait_for(b,c["task_id"],{"running"})
    await asyncio.sleep(.4)
    assert b.task_view(third["task_id"])["status"]=="queued"
    bystander=__import__("subprocess").Popen(["sleep","30"])
    try:
        v=await b.cancel_task(a["task_id"])
        assert v["status"]=="cancelled" and v["agent"]=="codex"
        assert not any(alive(pid) for pid in pids(env,"codex-pids.log")+pids(env,"codex-children.log"))
        assert bystander.poll() is None and b.task_view(c["task_id"])["status"]=="running"
        await b.cancel_task(c["task_id"])
        assert (await wait_for(b,third["task_id"],TERMINAL))["status"]=="completed"
    finally:
        bystander.kill();bystander.wait();await b.shutdown()

async def test_codex_timeout_tree(env):
    b=make_bridge(env)
    t=await b.start_task("SPAWN",None,"read_only",1,agent="codex")
    v=await wait_for(b,t["task_id"],TERMINAL)
    assert v["status"]=="timed_out" and v["agent"]=="codex"
    assert not any(alive(pid) for pid in pids(env,"codex-pids.log")+pids(env,"codex-children.log"))

async def test_legacy_task_normalizes_claude(env):
    b=make_bridge(env)
    t=await b.start_task("hi",None,"read_only",None)
    await wait_for(b,t["task_id"],TERMINAL)
    r=b.store.get_task(t["task_id"]);r.pop("agent");b.store.save_task(r,"r_legacy")
    assert b.task_view(t["task_id"])["agent"]=="claude"

async def test_new_mcp_surface_preserves_old_aliases(env):
    from mcp.shared.memory import create_connected_server_and_client_session
    from claude_bridge.server import build_server
    b=make_bridge(env,tool_profile="agents")
    m,_=build_server(b.cfg,b)
    async with create_connected_server_and_client_session(m._mcp_server) as c:
        tools={t.name:t for t in (await c.list_tools()).tools}
        assert {"start_agent_task","get_agent_task","cancel_agent_task","get_agent_status",
                "get_last_result","get_agent_messages","get_project_state",
                "start_claude_task","get_claude_task","cancel_claude_task","get_claude_status"} <= set(tools)
        assert "ask_claude" not in tools
        schema=tools["start_agent_task"].inputSchema
        assert schema["properties"]["agent"]["enum"]==["claude","codex"]
        assert (await c.call_tool("start_agent_task",{"agent":"other","prompt":"x"})).isError
        result=await c.call_tool("start_agent_task",{"agent":"codex","prompt":"hi"})
        data=json.loads(result.content[0].text)
        assert data["agent"]=="codex"
        await wait_for(b,data["task_id"],TERMINAL)

def test_broker_tools_have_explicit_auto_policy(env):
    from claude_bridge.executors import codex_command
    b=make_bridge(env)
    cmd=codex_command(b.cfg,env["root"],env["root"],"codex-test-model")
    config=next(x for x in cmd if x.startswith("mcp_servers="))
    assert '"default_tools_approval_mode"="auto"' in config
    assert '"enabled_tools"=["read_file","list_files","search","git_state"]' in config

def test_catalog_disables_deferred_discovery_and_collaboration():
    from claude_bridge.executors import restricted_catalog
    raw=json.dumps({"models":[{"slug":"test","supports_search_tool":True,"multi_agent_version":"v2","apply_patch_tool_type":"freeform"}]}).encode()
    m=restricted_catalog(raw)["models"][0]
    assert m["supports_search_tool"] is False
    assert m["multi_agent_version"]=="disabled"
    assert m["apply_patch_tool_type"] is None

async def test_unvalidated_codex_version_fails_closed(env):
    b=make_bridge(env)
    f=env["fake"].parent/"codex"
    f.write_text(f.read_text().replace("codex-cli 0.160.0","codex-cli 0.161.0"))
    t=await b.start_task("hi",None,"read_only",None,agent="codex")
    v=await wait_for(b,t["task_id"],TERMINAL)
    assert v["status"]=="blocked" and "codex_version_denied" in str(v["warnings"])
    assert pids(env,"codex-pids.log")==[]

async def test_codex_restart_preserves_agent_and_kills_only_owned_orphan(env):
    import subprocess
    from claude_bridge.runner import proc_starttime, proc_command_hash
    from claude_bridge.store import utcnow
    b=make_bridge(env)
    tid="t_123456789abc"
    taskdir=b.cfg.data_dir/"codex_tasks"/(tid+"-proof");taskdir.mkdir(parents=True)
    orphan=subprocess.Popen([b.cfg.codex_bin,"--no-daemon","exec","--ephemeral","-C",str(taskdir)],
                            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    orphan.stdin.write(b"HANG");orphan.stdin.close()
    bystander=subprocess.Popen(["sleep","30"])
    try:
        b.store.save_task({"task_id":tid,"agent":"codex","status":"running","mode":"read_only","project":str(env["root"]),
                          "started_at":utcnow(),"warnings":[],"summary":"","result":"","duration_ms":0},"r_restart")
        b.store.set_proc(tid,orphan.pid,proc_starttime(orphan.pid),proc_command_hash(orphan.pid))
        recovered=Bridge(b.cfg)
        v=recovered.task_view(tid)
        assert v["agent"]=="codex" and v["status"]=="failed"
        assert "leftover Codex" in str(v["warnings"])
        orphan.wait(3)
        assert bystander.poll() is None
    finally:
        if orphan.poll() is None:os.killpg(orphan.pid,9);orphan.wait()
        bystander.kill();bystander.wait()

async def test_relative_codex_path_cannot_execute_project_binary(env):
    marker=env["root"]/"EXECUTED"
    evil=env["root"]/"codex"
    evil.write_text("#!/bin/sh\ntouch "+str(marker)+"\nprintf 'codex-cli 0.160.0\\n'\n")
    evil.chmod(0o700)
    b=make_bridge(env);b=Bridge(replace(b.cfg,codex_bin="codex"))
    t=await b.start_task("hi",None,"read_only",None,agent="codex")
    v=await wait_for(b,t["task_id"],TERMINAL)
    assert v["status"]=="blocked" and not marker.exists()

def test_codex_recovery_requires_recorded_command_fingerprint(env):
    import subprocess
    from claude_bridge.runner import proc_starttime
    b=make_bridge(env)
    unrelated=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)",b.cfg.codex_bin,"--no-daemon","--ephemeral"],start_new_session=True)
    try:
        assert b._kill_orphan(unrelated.pid,proc_starttime(unrelated.pid),agent="codex") is False
        assert unrelated.poll() is None
    finally:
        if unrelated.poll() is None:unrelated.kill()
        unrelated.wait()

def test_default_codex_model_uses_current_account_catalog():
    import claude_bridge.executors as ex
    assert hasattr(ex,"select_codex_model"),"account-aware model selection missing"
    bundled={"models":[{"slug":"unavailable"},{"slug":"available"}]}
    account={"models":[{"slug":"reserve","visibility":"hide","priority":0},
                       {"slug":"available","visibility":"list","priority":4}]}
    assert ex.select_codex_model(None,bundled,account)=="available"
    with pytest.raises(ValueError): ex.select_codex_model("unavailable",bundled,account)
