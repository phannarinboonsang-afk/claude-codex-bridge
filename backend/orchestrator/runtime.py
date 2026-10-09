"""HTTP-independent execution service. Only operator-registered sources are allowed."""
from concurrent.futures import ThreadPoolExecutor
import threading
from ..agent_chat.store import StoreError
from .controls import Controls


class Service:
    def __init__(self,store,execute,sources):
        self.store=store; self.execute=execute; self.sources=dict(sources)
        self.controls=Controls(store); self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='orchestrator')
        self.create_lock=threading.Lock()
        self.lock=threading.Lock(); self.jobs={}; self.closed=False

    def wake(self,rid):
        with self.lock:
            if self.closed: raise StoreError('Runtime is closing.',503)
            if rid not in self.jobs or self.jobs[rid].done():
                self.jobs[rid]=self.pool.submit(self.execute,rid)

    def create(self,owner,payload):
        required={'goal','default_agent','source_id','source_commit','idempotency_key'}
        optional={'session_id','writer_enabled'}
        if not isinstance(payload,dict) or not required<=set(payload) or set(payload)-required-optional:
            raise StoreError('Invalid start request.',400)
        if self.sources.get(payload['source_id'])!=payload['source_commit']:
            raise StoreError('Source is not registered.',400)
        with self.create_lock:
            sid=payload.get('session_id')
            if sid is None:
                with self.store.shared.connection() as db:
                    old=db.execute('SELECT session_id FROM orchestrator_runs WHERE owner=? AND idempotency_key=?',(owner,payload['idempotency_key'])).fetchone()
                sid=old['session_id'] if old else self.store.shared.create_session(owner,'gpt',payload['default_agent'])['session_id']
            run=self.store.create_run(owner,sid,payload['goal'],payload['default_agent'],payload.get('writer_enabled',False),payload['source_id'],payload['source_commit'],payload['idempotency_key'])
        self.wake(run['run_id']); return self.view(owner,run['run_id'])

    def view(self,owner,rid):
        run=self.store.get_run(owner,rid)
        allowed=('run_id','session_id','goal','state','coordinator_model','default_agent','current_agent','round_number','version','created_at','updated_at','started_at','paused_at','completed_at','owner_gate','last_error','checkpoint_version','writer_enabled','completion','budget_measured')
        messages=self.store.shared.get_messages(owner,run['session_id'])
        # Private process paths, source paths, broker/auth data and raw diagnostics
        # never cross this control surface.
        return {'run':{k:run[k] for k in allowed},'transcript':[{k:m[k] for k in ('message_id','session_id','speaker_type','speaker_name','content','created_at')} for m in messages]}

    def control(self,owner,rid,action,payload):
        if not isinstance(payload,dict): raise StoreError('Invalid control.',400)
        if action=='approve':
            keys={'expected_version','idempotency_key','gate_id','gate_digest'}
            if set(payload)!=keys: raise StoreError('Invalid approval.',400)
            self.controls.approve(owner,rid,payload['gate_id'],payload['expected_version'],payload['gate_digest'],payload['idempotency_key'])
        else:
            required={'expected_version','idempotency_key'}
            optional={'message'} if action=='input' else set()
            if not required<=set(payload) or set(payload)-required-optional: raise StoreError('Invalid control.',400)
            data={'message':payload['message']} if action=='input' and 'message' in payload else {}
            self.controls.apply(owner,rid,action,payload['expected_version'],payload['idempotency_key'],data)
        self.wake(rid); return self.view(owner,rid)

    def recover_active_runs(self):
        with self.store.shared.connection() as db:
            rows=db.execute("SELECT run_id FROM orchestrator_runs WHERE state IN ('IDLE','RUNNING','PAUSE_REQUESTED','INTERRUPTING') OR json_extract(data,'$.current_step') IS NOT NULL").fetchall()
        for row in rows: self.wake(row['run_id'])
        return [row['run_id'] for row in rows]

    def close(self):
        with self.lock: self.closed=True
        # Caller first pauses active runs; never SIGSTOP or detach writers.
        self.pool.shutdown(wait=True,cancel_futures=False)
