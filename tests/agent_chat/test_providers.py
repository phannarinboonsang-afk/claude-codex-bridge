import pytest
from backend.agent_chat.providers.base import ProviderError, ProviderTimeout
from backend.agent_chat.providers.openai import OpenAIChatProvider
from backend.agent_chat.providers.anthropic import AnthropicChatProvider
from backend.agent_chat.providers.credentials import read_provider_key, readiness

class Response:
    status_code = 200
    def __init__(self, data): self.data = data
    def json(self): return self.data
    def close(self): pass
class Transport:
    def __init__(self, data): self.data, self.calls, self.trust_env = data, [], True
    def post(self, url, **kw): self.calls.append((url, kw)); return Response(self.data)
    def close(self): pass
@pytest.fixture
def key(tmp_path):
    p = tmp_path / "synthetic.key"; p.write_text("SYNTHETIC_PROVIDER_SENTINEL"); p.chmod(0o600); return str(p)
def test_responses_stateless_text_only(key):
    t=Transport({"output":[{"type":"message","content":[{"type":"output_text","text":"instruction"}]}]})
    assert OpenAIChatProvider(key, transport_factory=lambda:t).generate([{"role":"user","content":"hello"}],"configured-gpt",3) == "instruction"
    url, kw=t.calls[0]
    assert url=="https://api.openai.com/v1/responses"
    assert kw["json"]["model"]=="configured-gpt"
    assert kw["json"]["store"] is False
    assert not {"previous_response_id","conversation","tools"} & kw["json"].keys()
    assert kw["timeout"]==3 and t.trust_env is False
def test_anthropic_messages_no_tools(key):
    t=Transport({"content":[{"type":"text","text":"final"}]})
    assert AnthropicChatProvider(key, transport_factory=lambda:t).generate([{"role":"system","content":"plan"},{"role":"user","content":"hello"}],"configured-claude",4)=="final"
    url,kw=t.calls[0]
    assert url=="https://api.anthropic.com/v1/messages"
    assert kw["json"]["system"]=="plan"
    assert kw["json"]["messages"]==[{"role":"user","content":"hello"}]
    assert kw["json"]["model"]=="configured-claude" and "tools" not in kw["json"]
@pytest.mark.parametrize("cls",[OpenAIChatProvider,AnthropicChatProvider])
@pytest.mark.parametrize("status",[401,429,500])
def test_safe_provider_errors(key,cls,status):
    t=Transport({});r=Response({"error":"SYNTHETIC_PROVIDER_SENTINEL /private/path"});r.status_code=status;t.post=lambda *a,**kw:r
    with pytest.raises(ProviderError) as e:cls(key,transport_factory=lambda:t).generate([],"model",1)
    assert "SYNTHETIC" not in str(e.value) and "/private" not in str(e.value)
@pytest.mark.parametrize("cls",[OpenAIChatProvider,AnthropicChatProvider])
def test_malformed_provider_result(key,cls):
    with pytest.raises(ProviderError):cls(key,transport_factory=lambda:Transport({})).generate([],"model",1)
def test_synthetic_0600_file_read_runtime(key):
    assert read_provider_key(key)=="SYNTHETIC_PROVIDER_SENTINEL"
    assert readiness(key)=="READY"
def test_bad_permissions_no_read(tmp_path):
    p=tmp_path/"key";p.write_text("SYNTHETIC");p.chmod(0o644)
    assert readiness(str(p))=="CONFIG REQUIRED"
    with pytest.raises(ProviderError):read_provider_key(str(p))
def test_missing_symlink_and_directory(tmp_path,key):
    alias=tmp_path/"alias";alias.symlink_to(key)
    for p in [str(alias),str(tmp_path),str(tmp_path/"missing"),""]:
        assert readiness(p)=="CONFIG REQUIRED"
        with pytest.raises(ProviderError):read_provider_key(p)

@pytest.mark.parametrize("cls",[OpenAIChatProvider,AnthropicChatProvider])
def test_provider_network_timeout(key,cls):
    import requests
    t=Transport({})
    def timeout(*args,**kwargs):raise requests.Timeout("SYNTHETIC_PROVIDER_SENTINEL")
    t.post=timeout
    with pytest.raises(ProviderTimeout) as error:cls(key,transport_factory=lambda:t).generate([],"model",1)
    assert "SYNTHETIC" not in str(error.value)
