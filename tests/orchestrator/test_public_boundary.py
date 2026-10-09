import pytest
from backend.orchestrator.bridge import ScopedBridgeClient
from backend.agent_chat.bridge_client import BridgeError
from backend.orchestrator.protected import protected_request

def test_retired_local_writer_transport_fails_closed():
    with pytest.raises(BridgeError) as error:
        ScopedBridgeClient(url='http://127.0.0.1:15087/mcp', auth_file='unused')
    assert error.value.code == 'remote_worker_adapter_required'

def test_generic_service_restart_still_requires_owner_gate():
    assert protected_request('systemctl restart protected-service.service')
