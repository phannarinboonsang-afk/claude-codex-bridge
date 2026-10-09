import pytest
from .test_store import setup,create
from backend.orchestrator.controls import Controls
from backend.agent_chat.store import StoreError


def running(setup):
    store,shared,sid=setup
    run=create(store,sid)
    return store,shared,store.compare_update(run['run_id'],run['version'],{'state':'RUNNING'})


def test_pause_safe_boundary(setup):
    store,shared,run=running(setup)
    run=store.compare_update(run['run_id'],run['version'],{'current_step':{'phase':'agent'}})
    paused=Controls(store).apply('owner',run['run_id'],'pause',run['version'],'pause',{})
    assert paused['state']=='PAUSE_REQUESTED'


def test_continue_checkpoint_idempotent(setup):
    store,shared,run=running(setup); control=Controls(store)
    paused=control.apply('owner',run['run_id'],'pause',run['version'],'pause',{})
    assert paused['state']=='PAUSED'
    resumed=control.apply('owner',run['run_id'],'continue',paused['version'],'continue',{})
    again=control.apply('owner',run['run_id'],'continue',paused['version'],'continue',{})
    assert resumed==again and resumed['checkpoint']==paused['checkpoint']
    with pytest.raises(StoreError): control.apply('owner',run['run_id'],'pause',paused['version'],'old',{})


def test_interrupt_active_task_and_stop_fences_launch(setup):
    store,shared,run=running(setup)
    run=store.compare_update(run['run_id'],run['version'],{'current_step':{'phase':'agent'},'current_bridge_task_id':'task'})
    control=Controls(store)
    interrupted=control.apply('owner',run['run_id'],'interrupt',run['version'],'interrupt',{})
    assert interrupted['state']=='INTERRUPTING'
    stopped=control.apply('owner',run['run_id'],'stop',interrupted['version'],'stop',{})
    assert stopped['state']=='STOPPED' and stopped['stop_requested']
    with pytest.raises(StoreError): control.apply('owner',run['run_id'],'continue',stopped['version'],'continue',{})


def test_owner_input_revision(setup):
    store,shared,run=running(setup)
    updated=Controls(store).apply('owner',run['run_id'],'input',run['version'],'input',{'message':'Backend only'})
    assert updated['input_revision']==1
    assert updated['checkpoint']['owner_constraints']==['Backend only']
    assert shared.get_messages('owner',run['session_id'])[-1]['content']=='Backend only'
