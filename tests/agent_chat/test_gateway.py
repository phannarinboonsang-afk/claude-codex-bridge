import os
import sys
import time
import pytest
from backend.agent_chat.provider_gateway import ProcessProviderGateway
from backend.agent_chat.config import ProviderConfig
from backend.agent_chat.providers.base import ProviderCancelled,ProviderTimeout

def test_provider_subprocess_timeout_reaped(tmp_path):
    pidfile=tmp_path/"pid"
    command=[sys.executable,"-c",f"import os,time,pathlib;pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(20)"]
    with pytest.raises(ProviderTimeout):
        ProcessProviderGateway(command).generate("gpt",[],ProviderConfig("fake",""),0.3,lambda:False)
    pid=int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):os.kill(pid,0)

def test_provider_subprocess_user_stop_reaped(tmp_path):
    pidfile=tmp_path/"pid";start=time.monotonic()
    command=[sys.executable,"-c",f"import os,time,pathlib;pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(20)"]
    with pytest.raises(ProviderCancelled):
        ProcessProviderGateway(command).generate("claude",[],ProviderConfig("fake",""),5,lambda:time.monotonic()-start>0.3)
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)

def test_minimal_child_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","SYNTHETIC_SECRET")
    command=[sys.executable,"-c","import os,json;print(json.dumps({'text':'clean' if 'OPENAI_API_KEY' not in os.environ else 'leaked'}))"]
    assert ProcessProviderGateway(command).generate("gpt",[],ProviderConfig("fake",""),2,lambda:False)=="clean"
