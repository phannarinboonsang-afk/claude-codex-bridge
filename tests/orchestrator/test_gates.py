import pytest
from .test_store import setup
from .test_controls import running
from backend.orchestrator.controls import Controls
from backend.agent_chat.store import StoreError


def test_protected_action_waits_and_cannot_expand_root(setup):
    store,shared,run=running(setup); control=Controls(store)
    gate=control.open_gate(run['run_id'],'production_restart','Restart proposed','systemctl restart protected-service.service','Production restart')
    waiting=store.get_run('owner',run['run_id'])
    assert waiting['state']=='WAITING_FOR_OWNER' and gate['command']=='systemctl restart protected-service.service'
    approved=control.approve('owner',run['run_id'],gate['gate_id'],gate['version'],gate['digest'],'approve')
    assert approved['state']=='BLOCKED' and approved['last_error']=='protected_capability_unavailable'


def test_approval_releases_one_action(setup):
    store,shared,run=running(setup); control=Controls(store)
    gate=control.open_gate(run['run_id'],'extend_rounds','Round budget reached','extend rounds by 5','Five extra rounds')
    resumed=control.approve('owner',run['run_id'],gate['gate_id'],gate['version'],gate['digest'],'approve')
    assert resumed['state']=='RUNNING' and resumed['max_rounds']==25
    assert resumed['owner_gate']['consumed']
    with pytest.raises(StoreError): control.approve('owner',run['run_id'],gate['gate_id'],gate['version'],gate['digest'],'replay')


def test_approval_replay_wrong_owner_and_stale_rejected(setup):
    store,shared,run=running(setup); control=Controls(store)
    gate=control.open_gate(run['run_id'],'deploy','Proposed deploy','deploy candidate','Production')
    with pytest.raises(StoreError): control.approve('other',run['run_id'],gate['gate_id'],gate['version'],gate['digest'],'other')
    with pytest.raises(StoreError): control.approve('owner',run['run_id'],gate['gate_id'],gate['version'],'0'*64,'stale')
