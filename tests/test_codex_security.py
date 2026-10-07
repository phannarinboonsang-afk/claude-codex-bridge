"""Read broker security: production bypasses must fail before file contents escape."""
import importlib.util
import hashlib
import os
import subprocess
from pathlib import Path
import pytest

def broker_type():
    assert importlib.util.find_spec("claude_bridge.read_broker"), "Bridge read broker is missing"
    from claude_bridge.read_broker import ReadBroker
    return ReadBroker

def make_broker(tmp_path):
    # Pytest names cases after tests; a test containing "secret" must not itself
    # make the approved project root secret-bearing.
    fixture_root=tmp_path.parent/("fixture_"+hashlib.sha256(tmp_path.name.encode()).hexdigest()[:12])
    fixture_root.mkdir()
    project=fixture_root/"project"; project.mkdir()
    scratch=fixture_root/"scratch"; scratch.mkdir()
    return broker_type()(project, scratch, (project,)),project

def test_allowed_source_read(tmp_path):
    b,p=make_broker(tmp_path); (p/"source.py").write_text("print(42)\n")
    assert b.read_file("source.py")["text"]=="print(42)\n"

@pytest.mark.parametrize("name", [".env", "dev.env", ".env.local", "tls.pem", "tls.key", "id_ed25519", "api-token.txt", "private-key.txt", "auth.json", "credentials.json", "myToken.txt", "mysecret.json", "dev.env.backup", "key.txt", "privatekey.txt", "CREDENTIALSTORE.json", ".ssh/id_rsa", ".codex/auth.json", "data/bridge.db", ".git/config"])
def test_secret_file_denied_even_inside_project(tmp_path,name):
    b,p=make_broker(tmp_path); f=p/name; f.parent.mkdir(parents=True,exist_ok=True); f.write_text("DUMMY_SECRET")
    with pytest.raises(PermissionError): b.read_file(name)
    assert "DUMMY_SECRET" not in str(b.search("DUMMY_SECRET"))

@pytest.mark.parametrize("name", ["../outside.txt", "/etc/passwd", "a/../../outside.txt", "bad\x00name"])
def test_path_rejected(tmp_path,name):
    b,p=make_broker(tmp_path)
    with pytest.raises((PermissionError,ValueError)): b.read_file(name)

def test_symlink_and_hardlink_denied(tmp_path):
    b,p=make_broker(tmp_path); outside=tmp_path/"outside"; outside.write_text("DUMMY_SECRET")
    (p/"link").symlink_to(outside); os.link(outside,p/"hard")
    for name in ("link","hard"):
        with pytest.raises(PermissionError): b.read_file(name)

def test_special_files_and_oversized_reads_denied(tmp_path):
    b,p=make_broker(tmp_path); os.mkfifo(p/"pipe"); (p/"big").write_bytes(b"x"*65537)
    for name in ("pipe","big"):
        with pytest.raises(PermissionError): b.read_file(name)

def test_search_sanitizes_and_bounds(tmp_path):
    b,p=make_broker(tmp_path); (p/"source.py").write_text("password=hunter2\n"*100)
    r=b.search("password")
    assert "hunter2" not in str(r) and len(r["matches"])<=50

def test_no_arbitrary_command_operation(tmp_path):
    b,p=make_broker(tmp_path)
    for operation in ("commit","checkout","reset","clean","rm","curl","psql","systemctl","status; rm -rf /"):
        with pytest.raises(PermissionError): b.git_state(operation)

def test_git_inspection_cannot_run_configured_external_commands(tmp_path):
    b,p=make_broker(tmp_path)
    subprocess.run(["git","init","-q",str(p)],check=True)
    subprocess.run(["git","-C",str(p),"config","user.email","test@example.invalid"],check=True)
    subprocess.run(["git","-C",str(p),"config","user.name","Test"],check=True)
    (p/"source.py").write_text("old\n"); (p/".env").write_text("DUMMY_SECRET")
    subprocess.run(["git","-C",str(p),"add","source.py",".env"],check=True)
    subprocess.run(["git","-C",str(p),"commit","-qm","baseline"],check=True)
    marker=tmp_path/"EXECUTED"
    subprocess.run(["git","-C",str(p),"config","diff.external",f"touch {marker}"],check=True)
    subprocess.run(["git","-C",str(p),"config","core.fsmonitor",f"touch {marker}"],check=True)
    (p/"source.py").write_text("new\n")
    for op in ("status","log","diff"):
        r=b.git_state(op)
        assert r["status"]=="ok",r
        assert "DUMMY_SECRET" not in str(r)
    assert "+new" in b.git_state("diff")["text"]
    assert not marker.exists()

def test_ancestor_symlink_replacement_cannot_escape(tmp_path):
    T=broker_type()
    base=tmp_path/"base";p=base/"nested"/"project";p.mkdir(parents=True)
    (p/"source.py").write_text("SAFE")
    scratch=tmp_path/"scratch";scratch.mkdir()
    b=T(p,scratch,(base,))
    outside=tmp_path/"outside";evil=outside/"nested"/"project";evil.mkdir(parents=True)
    (evil/"source.py").write_text("DUMMY_SECRET")
    base.rename(tmp_path/"saved");base.symlink_to(outside,target_is_directory=True)
    with pytest.raises(PermissionError): b.read_file("source.py")

def test_secret_project_root_is_denied_even_as_default(tmp_path):
    p=tmp_path/".ssh";p.mkdir();scratch=tmp_path/"scratch";scratch.mkdir()
    with pytest.raises(PermissionError):broker_type()(p,scratch,(p,))

def test_root_inode_replacement_cannot_read_new_tree(tmp_path):
    b,p=make_broker(tmp_path)
    p.rename(p.with_name("original"));p.mkdir();(p/"source.py").write_text("DUMMY_SECRET")
    with pytest.raises(PermissionError):b.read_file("source.py")

@pytest.mark.parametrize("name", ["*", ":(glob)**"])
def test_git_filename_pathspec_is_literal(tmp_path,name):
    b,p=make_broker(tmp_path)
    subprocess.run(["git","init","-q",str(p)],check=True)
    (p/".env").write_text("DUMMY_UNCLASSIFIED_VALUE")
    subprocess.run(["git","-C",str(p),"add",".env"],check=True)
    subprocess.run(["git","-C",str(p),"-c","user.name=Test","-c","user.email=test@example.invalid","commit","-qm","baseline"],check=True)
    (p/name).write_text("allowed")
    r=b.git_state("diff")
    assert r["status"]=="ok",r
    assert "DUMMY_UNCLASSIFIED_VALUE" not in r["text"]
