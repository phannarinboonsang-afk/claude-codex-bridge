import threading
import pytest
from .test_store import setup
from .test_loop import build
from .test_decisions import payload
from .test_controls import running
from backend.orchestrator.controls import Controls
from backend.agent_chat.store import StoreError


@pytest.mark.parametrize('repeat',range(5))
def test_owner_commits_before_done_barrier(setup,monkeypatch,repeat):
    store,shared,run,c,b,w=build(setup,[payload('done'),payload('done')])
    ready=threading.Barrier(2);committed=threading.Barrier(2);errors=[];fired=False
    from backend.orchestrator import worker
    original=worker.protected_request
    def fence(text):
        nonlocal fired
        if not fired:
            fired=True;ready.wait(timeout=5);committed.wait(timeout=5)
        return original(text)
    monkeypatch.setattr(worker,'protected_request',fence)
    def owner():
        try:
            ready.wait(timeout=5)
            current=store.get_run('owner',run['run_id'])
            Controls(store).apply('owner',run['run_id'],'input',current['version'],'new-input',{'message':'Consider new backend constraint'})
        except Exception as error:errors.append(error)
        finally:committed.wait(timeout=5)
    thread=threading.Thread(target=owner);thread.start()
    result=w.run_until_boundary(run['run_id']);thread.join(timeout=5)
    assert not errors and not thread.is_alive()
    assert result['state']=='DONE' and len(c.contexts)==2
    assert any(m['content']=='Consider new backend constraint' for m in c.contexts[-1])
    assert len(b.calls)==0
    assert sum(m['content']=='Consider new backend constraint' for m in shared.get_messages('owner',run['session_id']))==1


@pytest.mark.parametrize('repeat',range(5))
def test_done_committed_first_rejects_owner_input_and_launch(setup,monkeypatch,repeat):
    store,shared,run,c,b,w=build(setup,[payload('done')])
    ready=threading.Barrier(2);finished=threading.Barrier(2);responses=[]
    commit=w._commit_decision
    def fence(*args):
        accepted=commit(*args)
        ready.wait(timeout=5);finished.wait(timeout=5)
        return accepted
    monkeypatch.setattr(w,'_commit_decision',fence)
    def owner():
        try:
            ready.wait(timeout=5)
            current=store.get_run('owner',run['run_id'])
            Controls(store).apply('owner',run['run_id'],'input',current['version'],'late',{'message':'New work'})
            responses.append('UNEXPECTED_SUCCESS')
        except StoreError as error:responses.append(str(error))
        finally:finished.wait(timeout=5)
    thread=threading.Thread(target=owner);thread.start()
    result=w.run_until_boundary(run['run_id'])
    thread.join(timeout=5)
    assert not thread.is_alive() and len(responses)==1 and 'RUN_ALREADY_DONE' in responses[0]
    w.run_until_boundary(run['run_id'])
    assert not b.calls and all(m['content']!='New work' for m in shared.get_messages('owner',run['session_id']))


@pytest.mark.parametrize('action,expected',[('pause','PAUSED'),('stop','STOPPED')])
def test_control_before_done_preserves_owner_state(setup,monkeypatch,action,expected):
    store,shared,run,c,b,w=build(setup,[payload('done')])
    from backend.orchestrator import worker
    def fence(text):
        current=store.get_run('owner',run['run_id'])
        Controls(store).apply('owner',run['run_id'],action,current['version'],'owner-control',{})
        return None
    monkeypatch.setattr(worker,'protected_request',fence)
    result=w.run_until_boundary(run['run_id'])
    assert result['state']==expected and not b.calls and result['completion'] is None
    assert not any(m['content']=='Complete' for m in shared.get_messages('owner',run['session_id']))


def test_blocked_input_continue_idempotent_stale_preserves_checkpoint(setup):
    store,shared,run=running(setup)
    blocked=store.compare_update(run['run_id'],run['version'],{'state':'BLOCKED'})
    controls=Controls(store)
    updated=controls.apply('owner',run['run_id'],'input',blocked['version'],'input',{'message':'Backend only'})
    continued=controls.apply('owner',run['run_id'],'continue',updated['version'],'continue',{})
    assert continued['state']=='RUNNING' and continued['checkpoint']==updated['checkpoint']
    assert controls.apply('owner',run['run_id'],'continue',updated['version'],'continue',{})==continued
    with pytest.raises(StoreError):controls.apply('owner',run['run_id'],'continue',updated['version'],'stale',{})


@pytest.mark.parametrize('classification,active,checkpoint,allowed',[
    ('coordinator_unavailable',False,True,True),
    ('unsafe_execution_state',False,True,False),
    ('coordinator_unavailable',True,True,False),
    ('coordinator_unavailable',False,False,False),
])
def test_failed_recovery_explicit_classification(setup,classification,active,checkpoint,allowed):
    store,shared,run=running(setup)
    changes={'state':'FAILED','last_error':classification,'checkpoint':run['checkpoint'] if checkpoint else None}
    if active:changes.update(current_step={'phase':'agent'},current_bridge_task_id='unknown-active')
    failed=store.compare_update(run['run_id'],run['version'],changes)
    controls=Controls(store)
    if not allowed:
        with pytest.raises(StoreError):controls.apply('owner',run['run_id'],'continue',failed['version'],'continue',{})
    else:
        result=controls.apply('owner',run['run_id'],'continue',failed['version'],'continue',{})
        assert result['state']=='RUNNING' and result['checkpoint']==failed['checkpoint']
