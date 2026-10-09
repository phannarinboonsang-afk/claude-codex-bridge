from .test_store import setup
from .test_loop import build
from .test_decisions import payload
from backend.orchestrator.recovery import Recovery
from backend.orchestrator.worker import Worker


def test_restart_after_admission_no_duplicate_task(setup):
    store,shared,run,c,b,w=build(setup,[payload('done')])
    step={'step_id':'persisted-step','phase':'launching','input_revision':0,'agent':'codex','instruction':'Inspect'}
    store.compare_update(run['run_id'],run['version'],{'state':'RUNNING','started_at':1,'scope_id':'synthetic-scope','current_step':step,'snapshot':b.snapshot('synthetic-scope'),'max_duration':10**12})
    b.start('codex','synthetic-scope','persisted-step','Inspect',180)
    recovered=Recovery(store,c,lambda:b).reconcile(run['run_id'])
    assert recovered['state']=='DONE' and len(b.calls)==1
    assert sum(m['speaker_type']=='agent' for m in shared.get_messages('owner',run['session_id']))==1


def test_reload_retains_transcript_checkpoint_and_worker_lease(setup):
    store,shared,run,c,b,w=build(setup,[payload(),payload('done')])
    assert w._claim(run['run_id'])
    other=Worker(store,c,lambda:b)
    assert not other._claim(run['run_id'])
    with store.shared.connection() as db: db.execute('DELETE FROM orchestrator_worker_leases')
    result=other.run_until_boundary(run['run_id'])
    assert result['state']=='DONE'
    assert result['checkpoint_version']==1
