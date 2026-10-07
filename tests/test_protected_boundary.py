
"""Protected reads must fail before os.read, independently of any model refusal."""
import hashlib
from dataclasses import replace
from pathlib import Path
import pytest
from claude_bridge.read_broker import ReadBroker
from claude_bridge.config import Config
from claude_bridge.runner import Bridge
from conftest import last_call

@pytest.fixture
def boundary(tmp_path):
    base=tmp_path.parent/("boundary_"+hashlib.sha256(tmp_path.name.encode()).hexdigest()[:12])
    base.mkdir()
    project=base/"project";project.mkdir()
    scratch=base/"scratch";scratch.mkdir()
    return project,scratch

@pytest.mark.parametrize("name",["auth-header","mcp-auth-header",".env",".env.local","sample.key","sample.pem","sample.p12","sample.pfx","api-token.txt","credential-store","password.txt",".netrc",".pgpass",".ssh/id_ed25519","id_rsa","id_dsa","id_ecdsa",".aws/profile",".gnupg/profile",".claude/settings.json",".codex/config.toml","private-key-location.txt"])
def test_protected_names_denied_before_content(boundary,name,monkeypatch):
    project,scratch=boundary
    f=project/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text("SYNTHETIC_CANARY")
    broker=ReadBroker(project,scratch,(project,))
    reads=[]
    import os
    original=os.read
    def record(*a): reads.append(a);return original(*a)
    monkeypatch.setattr(os,"read",record)
    with pytest.raises(PermissionError):broker.read_file(name)
    assert reads==[]

@pytest.mark.parametrize("alias",["ordinary.txt","./ordinary.txt","sub/../ordinary.txt","alias.txt",".//ordinary.txt","sub/./../ordinary.txt"])
def test_configured_auth_exact_canonical_path_denied(boundary,alias,monkeypatch):
    project,scratch=boundary
    f=project/"ordinary.txt";f.write_text("SYNTHETIC_CANARY")
    (project/"sub").mkdir();(project/"alias.txt").symlink_to(f)
    # Existing broker lacks a configured-protected-path input; reproduce its leak.
    import inspect
    kwargs={"protected_files":(f,)} if "protected_files" in inspect.signature(ReadBroker).parameters else {}
    broker=ReadBroker(project,scratch,(project,),**kwargs)
    reads=[]
    import os
    original=os.read
    def record(*a):reads.append(a);return original(*a)
    monkeypatch.setattr(os,"read",record)
    with pytest.raises(PermissionError):broker.read_file(alias)
    assert reads==[]

def test_configured_auth_omitted_from_inventory_search_and_git(boundary):
    project,scratch=boundary
    f=project/"ordinary.txt";f.write_text("SYNTHETIC_CANARY")
    (project/"README.md").write_text("normal allowed source")
    import inspect
    kwargs={"protected_files":(f,)} if "protected_files" in inspect.signature(ReadBroker).parameters else {}
    b=ReadBroker(project,scratch,(project,),**kwargs)
    assert b.list_files()["files"]==["README.md"]
    assert b.search("SYNTHETIC_CANARY")["matches"]==[]
    assert b.read_file("./README.md")["text"]=="normal allowed source"

async def test_claude_has_no_native_file_bypass_and_only_shared_broker(bridge,env):
    result=await bridge.ask_claude("normal fixture inspection",str(env["root"]),"read_only",None)
    assert result["status"]=="completed"
    call=last_call(env);args=call["argv"]
    assert args[args.index("--tools")+1]==""
    assert "--mcp-config" in args
    assert "mcp__bridge_read__read_file" in args[args.index("--allowedTools")+1]
    assert "git_state" not in args[args.index("--allowedTools")+1]
    assert "--no-git" in args[args.index("--mcp-config")+1]
    assert Path(call["cwd"])!=env["root"]
    assert result["project"]==str(env["root"])

def test_codex_passes_configured_auth_path_to_shared_broker(boundary):
    from claude_bridge.executors import codex_command
    project,scratch=boundary
    f=project/"ordinary.txt"
    cfg=Config(codex_bin="/absolute/codex",read_roots=(project,),auth_token_file=f)
    args=codex_command(cfg,scratch,project,"fixture-model")
    wire=" ".join(args)
    assert "--protected-file" in wire and str(f) in wire


async def test_shared_stdio_boundary_for_both_executors(boundary):
    import sys,json
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from claude_bridge.executors import broker_arguments
    from claude_bridge.runner import build_command
    project,scratch=boundary
    (project/"ordinary.txt").write_text("SYNTHETIC_CANARY")
    (project/"README.md").write_text("NORMAL_SOURCE")
    (project/"alias.txt").symlink_to(project/"ordinary.txt")
    for name in ["auth-header","mcp-auth-header",".env","sample.key","credentials.txt"]:
        (project/name).write_text("SYNTHETIC_CANARY")
    cfg=Config(read_roots=(project,),auth_token_file=project/"ordinary.txt")
    for agent in ["claude","codex"]:
        args=["-I",*broker_arguments(cfg,scratch,project)]
        if agent=="claude":
            command=build_command(cfg,"read_only",{"mcpServers":{"bridge_read":{"command":sys.executable,"args":args}}})
            wired=json.loads(command[command.index("--mcp-config")+1])
            args=wired["mcpServers"]["bridge_read"]["args"]
        async with stdio_client(StdioServerParameters(command=sys.executable,args=args)) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                for name in ["auth-header","mcp-auth-header","ordinary.txt","./ordinary.txt","a/../ordinary.txt","alias.txt",".env","sample.key","credentials.txt"]:
                    response=await session.call_tool("read_file",{"path":name})
                    assert response.isError is True,(agent,name)
                    assert "SYNTHETIC_CANARY" not in str(response)
                normal=await session.call_tool("read_file",{"path":"README.md"})
                assert not normal.isError and "NORMAL_SOURCE" in str(normal)


@pytest.mark.parametrize("move",["outside","protected"])
def test_directory_rename_race_denied_before_content(boundary,monkeypatch,move):
    import os
    project,scratch=boundary
    src=project/"src";src.mkdir();(src/"normal.txt").write_text("SYNTHETIC_CANARY")
    destination=project/".aws" if move=="protected" else project.parent/"outside"
    broker=ReadBroker(project,scratch,(project,))
    original=os.open
    def racing_open(path,*args,**kwargs):
        fd=original(path,*args,**kwargs)
        if path=="src":src.rename(destination)
        return fd
    monkeypatch.setattr(os,"open",racing_open)
    with pytest.raises(PermissionError):broker.read_file("src/normal.txt")

def test_claude_disables_automatic_instruction_discovery():
    from claude_bridge.runner import build_command
    cmd=build_command(Config(),"read_only")
    assert "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--tools")+1]==""
    assert cmd[cmd.index("--setting-sources")+1]==""


@pytest.mark.parametrize("move",["outside","protected"])
def test_inventory_rename_race_does_not_expose_names(boundary,monkeypatch,move):
    import os
    project,scratch=boundary
    src=project/"src";src.mkdir();(src/"normal.txt").write_text("SYNTHETIC_CANARY")
    destination=project/".aws" if move=="protected" else project.parent/"outside"
    broker=ReadBroker(project,scratch,(project,))
    original=os.listdir
    def racing_listdir(fd):
        names=original(fd)
        if isinstance(fd,int) and Path(f"/proc/self/fd/{fd}").resolve()==src:src.rename(destination)
        return names
    monkeypatch.setattr(os,"listdir",racing_listdir)
    assert broker.list_files()["files"]==[]


@pytest.mark.parametrize("alias",["ordinary.txt","alias.txt"])
def test_configured_auth_is_itself_a_symlink(boundary,alias):
    import inspect
    project,scratch=boundary
    f=project/"ordinary.txt";f.write_text("SYNTHETIC_CANARY")
    link=project/"alias.txt";link.symlink_to(f)
    kwargs={"protected_files":(link,)} if "protected_files" in inspect.signature(ReadBroker).parameters else {}
    b=ReadBroker(project,scratch,(project,),**kwargs)
    with pytest.raises(PermissionError):b.read_file(alias)

@pytest.mark.parametrize("kind",["rename","hardlink"])
def test_configured_auth_inode_alias_denied(boundary,kind):
    import inspect,os
    project,scratch=boundary
    f=project/"ordinary.txt";f.write_text("SYNTHETIC_CANARY")
    kwargs={"protected_files":(f,)} if "protected_files" in inspect.signature(ReadBroker).parameters else {}
    b=ReadBroker(project,scratch,(project,),**kwargs)
    alias=project/"archive.txt"
    if kind=="rename":f.rename(alias);f.write_text("NEW_SYNTHETIC_CANARY")
    else:os.link(f,alias)
    with pytest.raises(PermissionError):b.read_file("archive.txt")
