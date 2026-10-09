import hashlib
import json
import time
import uuid
from ..agent_chat.store import StoreError
from ..agent_chat.safety import safe_text
from .types import transition, DecisionError, TERMINAL


class Controls:
    def __init__(self,store): self.store=store

    def apply(self,owner,rid,action,expected_version,idempotency_key,payload):
        if action not in ('pause','continue','interrupt','stop','input') or not isinstance(idempotency_key,str) or not 0<len(idempotency_key)<=100 or not isinstance(expected_version,int) or not isinstance(payload,dict):
            raise StoreError('Invalid control request.',400)
        digest=hashlib.sha256(json.dumps([action,expected_version,payload],sort_keys=True).encode()).hexdigest()
        with self.store.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run=self.store._get(db,owner,rid)
            old=db.execute('SELECT digest,response FROM orchestrator_controls WHERE run_id=? AND request_key=?',(rid,idempotency_key)).fetchone()
            if old:
                if old['digest']!=digest: raise StoreError('Control idempotency conflicts.')
                return json.loads(old['response'])
            if run['state']=='DONE' and action=='input':
                raise StoreError('RUN_ALREADY_DONE: start a new run explicitly.')
            if run['version']!=expected_version or run['state'] in TERMINAL:
                raise StoreError('Run version or state rejects this control.')
            try:
                if action=='input':
                    if set(payload)!={'message'} or not isinstance(payload['message'],str) or not 0<len(payload['message'].strip())<=18000:
                        raise StoreError('Owner message is invalid.',400)
                    message=safe_text(payload['message'])
                    self.store._append(db,run,idempotency_key,'input','user','user',message)
                    run['input_revision']+=1
                    constraints=run['checkpoint']['owner_constraints']+[message]
                    run['checkpoint']['owner_constraints']=constraints[-10:]
                    run['checkpoint']['owner_constraints'][-1]=message[:16000]
                    while sum(map(len,run['checkpoint']['owner_constraints']))>18000:
                        run['checkpoint']['owner_constraints'].pop(0)
                    if run['state']=='WAITING_FOR_OWNER':
                        run['state']='PAUSED'; run['owner_gate']=None
                        run['checkpoint']['pending_actions']=[]
                else:
                    if payload: raise StoreError('Control payload must be empty.',400)
                    event=action
                    if action=='continue' and run['state'] in ('BLOCKED','FAILED'):
                        checkpoint=run.get('checkpoint')
                        if not isinstance(checkpoint,dict) or not checkpoint.get('goal') or run['current_step'] is not None or run['current_bridge_task_id'] is not None:
                            raise StoreError('RECOVERY_REQUIRES_RECONCILIATION: no active task and a persisted checkpoint are required.')
                        # Only failures whose producer guarantees child cleanup
                        # are recoverable. Unknown/unsafe execution is never
                        # revived merely because a client requests Continue.
                        if run['state']=='FAILED' and run['last_error']!='coordinator_unavailable':
                            raise StoreError('FAILURE_NOT_RECOVERABLE.')
                        event='recover'
                    run['state']=transition(run['state'],event).value
                    if action in ('pause','interrupt') and run['current_step'] is None:
                        run['state']=transition(run['state'],'boundary').value
                    if action=='stop':
                        run['stop_requested']=True; run['completed_at']=time.time()
                    if run['state']=='PAUSED': run['paused_at']=time.time()
                if action!='input':
                    self.store._append(db,run,idempotency_key,'control','system','orchestrator',f'Owner {action} requested.')
                response=self.store._write(db,run,expected_version)
            except DecisionError:
                raise StoreError('Control transition is invalid.') from None
            db.execute('INSERT INTO orchestrator_controls VALUES(?,?,?,?)',(rid,idempotency_key,digest,json.dumps(response)))
            return response

    def open_gate(self,rid,action,reason,exact_command,impact):
        owner,initial=self.store.internal_run(rid)
        values=[action,reason,exact_command,impact]
        if any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in values):
            raise StoreError('Owner gate is invalid.',400)
        action,reason,exact_command,impact=map(safe_text,values)
        digest=hashlib.sha256(json.dumps([rid,action,reason,exact_command,impact]).encode()).hexdigest()
        with self.store.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run=self.store._get(db,owner,rid)
            if run['state'] in TERMINAL: raise StoreError('Terminal run rejects owner gates.')
            if run['owner_gate'] and not run['owner_gate']['consumed']:
                if run['owner_gate']['digest']==digest: return run['owner_gate']
                raise StoreError('Another owner gate is pending.')
            version=run['version']
            run['state']='WAITING_FOR_OWNER'
            gate={'gate_id':uuid.uuid4().hex,'version':version+1,'action':action,'reason':reason,
                  'command':exact_command,'impact':impact,'digest':digest,'consumed':False,'approved_at':None}
            run['owner_gate']=gate; run['checkpoint']['pending_actions']=[gate]
            self.store._append(db,run,gate['gate_id'],'gate','system','orchestrator',f'Owner approval required: {action}. {reason}. Proposed action: {exact_command}. Impact: {impact}.')
            self.store._write(db,run,version)
            return gate

    def prepare_gate(self,db,run,action,reason,exact_command,impact):
        """Build a gate within the caller's fenced decision transaction."""
        values=[action,reason,exact_command,impact]
        if any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in values):
            raise StoreError('Owner gate is invalid.',400)
        action,reason,exact_command,impact=map(safe_text,values)
        digest=hashlib.sha256(json.dumps([run['run_id'],action,reason,exact_command,impact]).encode()).hexdigest()
        gate={'gate_id':uuid.uuid4().hex,'version':run['version']+1,'action':action,'reason':reason,
              'command':exact_command,'impact':impact,'digest':digest,'consumed':False,'approved_at':None}
        run['state']='WAITING_FOR_OWNER';run['owner_gate']=gate;run['checkpoint']['pending_actions']=[gate]
        self.store._append(db,run,gate['gate_id'],'gate','system','orchestrator',f'Owner approval required: {action}. {reason}. Proposed action: {exact_command}. Impact: {impact}.')
        return gate

    def approve(self,owner,rid,gate_id,gate_version,action_digest,idempotency_key):
        with self.store.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run=self.store._get(db,owner,rid); gate=run['owner_gate']
            if run['state']!='WAITING_FOR_OWNER' or not gate or gate['consumed'] or (gate['gate_id'],gate['version'],gate['digest'])!=(gate_id,gate_version,action_digest) or run['version']!=gate_version:
                raise StoreError('Owner approval is stale or already used.')
            gate['consumed']=True; gate['approved_at']=time.time(); gate['owner']=owner
            run['checkpoint']['pending_actions']=[]
            if gate['action']=='extend_rounds' and gate['command']=='extend rounds by 5':
                run['max_rounds']+=5; run['state']='RUNNING'
            elif gate['action']=='extend_duration' and gate['command']=='extend duration by 3600 seconds':
                run['max_duration']+=3600; run['state']='RUNNING'
            else:
                run['state']='BLOCKED'; run['last_error']='protected_capability_unavailable'
            self.store._append(db,run,gate_id,'approval','system','orchestrator',f'Owner approved exactly: {gate["action"]}.')
            return self.store._write(db,run,run['version'])
