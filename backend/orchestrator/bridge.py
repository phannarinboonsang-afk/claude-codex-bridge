"""Fail-closed boundary while the remote Worker task adapter is reviewed."""
from ..agent_chat.bridge_client import BridgeError

class ScopedBridgeClient:
    def __init__(self, **kwargs):
        raise BridgeError('remote_worker_adapter_required')
