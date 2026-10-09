import pytest
from backend.agent_chat.store import AgentChatStore, StoreError
from backend.orchestrator.store import OrchestratorStore


@pytest.fixture
def setup(tmp_path):
    shared=AgentChatStore(tmp_path/'agent.db')
    sid=shared.create_session('owner','gpt','codex')['session_id']
    return OrchestratorStore(shared),shared,sid


def create(store,sid,key='request1'):
    return store.create_run('owner',sid,'Inspect goal','codex',False,'fixture','a'*40,key)


def test_additive_migration_reload(setup):
    store,shared,sid=setup
    run=create(store,sid)
    store.append_message_once(run['run_id'],'step1','agent','agent','codex','Meaningful result')
    reopened=OrchestratorStore(AgentChatStore(shared.path))
    assert reopened.get_run('owner',run['run_id'])['checkpoint']['goal']=='Inspect goal'
    messages=shared.get_messages('owner',sid)
    assert [(m['speaker_type'],m['speaker_name']) for m in messages]==[('user','user'),('agent','codex')]


def test_shared_chat_and_orchestrator_admission(setup):
    store,shared,sid=setup
    create(store,sid)
    with pytest.raises(StoreError): shared.begin_run('owner',sid,'other','mid','model')
    with pytest.raises(StoreError): shared.update_selection('owner',sid,'gpt','claude')
    with pytest.raises(StoreError): create(store,sid,'other')


def test_session_and_run_isolation(setup):
    store,shared,sid=setup
    run=create(store,sid)
    with pytest.raises(StoreError): store.get_run('other',run['run_id'])
    other=shared.create_session('owner','gpt','claude')['session_id']
    second=create(store,other,'second')
    assert second['run_id']!=run['run_id']
    assert 'scope_id' in second and second['state']=='IDLE'


def test_create_idempotency(setup):
    store,shared,sid=setup
    run=create(store,sid)
    assert create(store,sid)['run_id']==run['run_id']
    with pytest.raises(StoreError): store.create_run('owner',sid,'changed','codex',False,'fixture','a'*40,'request1')
    updated=store.compare_update(run['run_id'],run['version'],{'state':'RUNNING'})
    with pytest.raises(StoreError): store.compare_update(run['run_id'],run['version'],{'state':'STOPPED'})
    assert updated['version']==run['version']+1
