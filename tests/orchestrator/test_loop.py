import json
import pytest
from .test_store import setup,create
from .test_decisions import payload
from backend.orchestrator.worker import Worker
from backend.orchestrator.decisions import Decision
from backend.orchestrator.controls import Controls


class FakeBridge:
    def __init__(self): self.tasks={}; self.calls=[]; self.cancelled=[]; self.hook=None
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def create_scope(self,run): return {'scope_id':'synthetic-scope'}
    def snapshot(self,scope): return {'head':'b'*40,'status':' M example.py','changed_files':['example.py'],'diff_summary':'VALUE changed','tests':[{'status':'passed'}]}
    def start(self,agent,scope,step,prompt,timeout):
        if step in self.tasks: return self.tasks[step]
        task={'task_id':step,'status':'running','agent':agent,'result':'AGENT_RESULT','snapshot':self.snapshot(scope)}
        self.tasks[step]=task; self.calls.append((agent,scope,prompt))
        if self.hook: self.hook()
        return task
    def get(self,task_id):
        return {**self.tasks[task_id],'status':'completed'}
    def cancel(self,task_id):
        self.cancelled.append(task_id)
        return {**self.tasks[task_id],'status':'cancelled'}
    def find(self,scope,step): return self.tasks.get(step)


class ScriptCoordinator:
    def __init__(self,actions): self.actions=list(actions); self.contexts=[]; self.hook=None
    def decide(self,run,context,snapshot,cancel):
        self.contexts.append(context)
        if self.hook:
            hook,self.hook=self.hook,None; hook()
        raw=self.actions.pop(0)
        if isinstance(raw,Exception): raise raw
        return Decision.parse(raw)


def build(setup,actions):
    store,shared,sid=setup; run=create(store,sid)
    coordinator=ScriptCoordinator(actions); bridge=FakeBridge()
    worker=Worker(store,coordinator,lambda:bridge,poll_interval=0)
    return store,shared,run,coordinator,bridge,worker


@pytest.mark.parametrize('agents',[['codex','codex'],['claude'],['codex','claude','codex']])
def test_multi_round_and_switching(setup,agents):
    actions=[payload('continue_agent' if i==0 else 'switch_agent',agent=agent) for i,agent in enumerate(agents)]+[payload('done')]
    store,shared,run,coordinator,bridge,worker=build(setup,actions)
    result=worker.run_until_boundary(run['run_id'])
    assert result['state']=='DONE' and result['round_number']==len(agents)
    assert [c[0] for c in bridge.calls]==agents
    assert {c[1] for c in bridge.calls}=={'synthetic-scope'}
    messages=shared.get_messages('owner',run['session_id'])
    names=[m['speaker_name'] for m in messages]
    assert names[0]=='user' and names[-1]=='gpt'
    assert names.count('codex')==agents.count('codex')
    assert names.count('claude-code')==agents.count('claude')
    assert 'AGENT_RESULT' in json.dumps(coordinator.contexts[-1])
    assert result['checkpoint']['files_changed']==['example.py']
    worker.run_until_boundary(run['run_id']); assert len(bridge.calls)==len(agents)


def test_owner_backend_only_input_supersedes_stale_decision(setup):
    store,shared,run,c,b,w=build(setup,[payload(message_to_agent='Edit frontend'),payload(message_to_agent='Inspect backend only'),payload('done')])
    def steer():
        current=store.get_run('owner',run['run_id'])
        Controls(store).apply('owner',run['run_id'],'input',current['version'],'new-input',{'message':'Backend only'})
    c.hook=steer
    assert w.run_until_boundary(run['run_id'])['state']=='DONE'
    assert len(b.calls)==1 and b.calls[0][2]=='Inspect backend only'
    assert 'Backend only' in json.dumps(c.contexts[1])


def test_pause_after_agent_and_continue_preserved_checkpoint(setup):
    store,shared,run,c,b,w=build(setup,[payload(),payload('done')])
    def pause():
        current=store.get_run('owner',run['run_id'])
        Controls(store).apply('owner',run['run_id'],'pause',current['version'],'pause',{})
    b.hook=pause
    paused=w.run_until_boundary(run['run_id'])
    assert paused['state']=='PAUSED' and paused['checkpoint']['files_changed']==['example.py']
    assert len(b.calls)==1
    resumed=Controls(store).apply('owner',run['run_id'],'continue',paused['version'],'continue',{})
    assert w.run_until_boundary(run['run_id'])['state']=='DONE'


def test_stop_and_interrupt_preserve_partial_state(setup):
    store,shared,run,c,b,w=build(setup,[payload()])
    def stop():
        current=store.get_run('owner',run['run_id'])
        Controls(store).apply('owner',run['run_id'],'stop',current['version'],'stop',{})
    b.hook=stop
    result=w.run_until_boundary(run['run_id'])
    assert result['state']=='STOPPED' and b.cancelled
    assert result['snapshot']['changed_files']==['example.py']
    assert not any(m['speaker_type']=='agent' for m in shared.get_messages('owner',run['session_id']))


def test_coordinator_failure_preserves_agent_output(setup):
    store,shared,run,c,b,w=build(setup,[payload(),ValueError('unsafe detail')])
    result=w.run_until_boundary(run['run_id'])
    assert result['state']=='PAUSED'
    assert any(m['speaker_type']=='agent' for m in shared.get_messages('owner',run['session_id']))
    assert 'unsafe detail' not in json.dumps(result)


def test_max_round_owner_gate(setup):
    store,shared,run,c,b,w=build(setup,[payload(),payload()])
    store.compare_update(run['run_id'],run['version'],{'max_rounds':1})
    result=w.run_until_boundary(run['run_id'])
    assert result['state']=='WAITING_FOR_OWNER' and len(b.calls)==1


def test_protected_instruction_gate_no_agent_launch(setup):
    store,shared,run,c,b,w=build(setup,[payload(message_to_agent='git reset --hard')])
    result=w.run_until_boundary(run['run_id'])
    assert result['state']=='WAITING_FOR_OWNER' and not b.calls


def test_agent_failure_recovery_ceiling(setup):
    store,shared,run,c,b,w=build(setup,[payload(),payload(),payload()])
    b.get=lambda task_id:{**b.tasks[task_id],'status':'failed','result':'Agent failure'}
    result=w.run_until_boundary(run['run_id'])
    assert result['state']=='BLOCKED' and result['agent_failures']==2 and len(b.calls)==2
    assert not any(m['speaker_type']=='agent' for m in shared.get_messages('owner',run['session_id']))


def test_snapshot_failure_blocks_handoff(setup):
    store,shared,run,c,b,w=build(setup,[payload(),payload()])
    b.get=lambda task_id:{**b.tasks[task_id],'status':'completed','snapshot':None}
    original=b.snapshot
    calls=[]
    def snapshot(scope):
        calls.append(scope)
        if len(calls)>2: raise OSError('unavailable')
        return original(scope)
    b.snapshot=snapshot
    result=w.run_until_boundary(run['run_id'])
    assert result['state']=='BLOCKED' and len(b.calls)==1
    assert any(m['speaker_type']=='agent' for m in shared.get_messages('owner',run['session_id']))


def test_bounded_all_speaker_context(setup):
    from backend.orchestrator.context import ContextBuilder
    store,shared,sid=setup; run=create(store,sid)
    for i in range(15):
        store.append_message_once(run['run_id'],str(i),'agent','agent','codex',str(i)+'x'*3000)
    context=ContextBuilder(store).build(run['run_id'])
    assert len(context)<=10 and sum(len(m['content']) for m in context)<=18000
    assert context[-1]['speaker_name']=='codex'
