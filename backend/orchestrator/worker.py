import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from .context import ContextBuilder
from .controls import Controls
from .types import TERMINAL
from .protected import protected_request
from ..agent_chat.store import StoreError


def process_identity(pid):
    try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError): return ''


class Worker:
    def __init__(self,store,coordinator,bridge_factory,*,poll_interval=.2):
        self.store,self.coordinator,self.bridge_factory=store,coordinator,bridge_factory
        self.poll_interval=poll_interval; self.worker_id=uuid.uuid4().hex
        self.context=ContextBuilder(store); self.controls=Controls(store)

    def _current(self,rid): return self.store.internal_run(rid)[1]

    def _update(self,rid,changes):
        for _ in range(20):
            run=self._current(rid)
            fields=changes(run) if callable(changes) else dict(changes)
            if run['state'] in TERMINAL: fields.pop('state',None)
            try: return self.store.compare_update(rid,run['version'],fields)
            except StoreError:
                if self._current(rid)['version']==run['version']: raise
        raise StoreError('Concurrent run updates require reconciliation.')

    def _claim(self,rid):
        with self.store.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM orchestrator_worker_leases WHERE run_id=?',(rid,)).fetchone()
            if existing:
                if process_identity(existing['process_id'])==existing['process_identity']: return False
                db.execute('DELETE FROM orchestrator_worker_leases WHERE run_id=?',(rid,))
            db.execute('INSERT INTO orchestrator_worker_leases VALUES(?,?,?,?)',(rid,self.worker_id,os.getpid(),process_identity(os.getpid())))
            return True

    def run_until_boundary(self,rid):
        if not self._claim(rid): return self._current(rid)
        try:
            run=self._current(rid)
            if run['state']=='IDLE': self._update(rid,{'state':'RUNNING','started_at':time.time()})
            with self.bridge_factory() as bridge:
                run=self._current(rid)
                if run['state'] in TERMINAL and run['current_step'] is None: return run
                if not run['scope_id']:
                    scope=bridge.create_scope(run)
                    self._update(rid,{'scope_id':scope['scope_id'],'snapshot':bridge.snapshot(scope['scope_id'])})
                while True:
                    run=self._current(rid)
                    if run['state'] not in ('BLOCKED','FAILED','WAITING_FOR_OWNER','PAUSED') and run['current_step'] and run['current_step']['phase'] in ('launching','agent'):
                        self._agent_step(rid,bridge); continue
                    if run['state'] in ('PAUSE_REQUESTED','INTERRUPTING'):
                        self._update(rid,{'state':'PAUSED','current_step':None,'paused_at':time.time()}); break
                    if run['state']!='RUNNING': break
                    if run['round_number']>=run['max_rounds']:
                        self.controls.open_gate(rid,'extend_rounds','Autonomous round limit reached','extend rounds by 5','Five additional autonomous rounds'); break
                    if time.time()-run['started_at']>=run['max_duration']:
                        self.controls.open_gate(rid,'extend_duration','Run duration limit reached','extend duration by 3600 seconds','One additional hour'); break
                    self.execute_step(rid,bridge)
                return self._current(rid)
        except Exception:
            self._update(rid,{'state':'BLOCKED','last_error':'execution_reconciliation_required'})
            return self._current(rid)
        finally:
            with self.store.shared.connection() as db:
                db.execute('DELETE FROM orchestrator_worker_leases WHERE run_id=? AND worker_id=?',(rid,self.worker_id))

    def execute_step(self,rid,bridge):
        run=self._current(rid)
        step={'step_id':uuid.uuid4().hex,'phase':'coordinator','input_revision':run['input_revision']}
        run=self._update(rid,{'current_step':step})
        try:
            decision=self.coordinator.decide(run,self.context.build(rid),run['snapshot'],lambda:self._current(rid)['state'] in ('STOPPED','INTERRUPTING'))
        except Exception:
            def failure(current):
                count=current['coordinator_failures']+1
                state='BLOCKED' if count>=current['max_coordinator_failures'] else 'PAUSED'
                if current['state']=='PAUSE_REQUESTED': state='PAUSED'
                return {'coordinator_failures':count,'state':state,'current_step':None,'last_error':'coordinator_unavailable'}
            self._update(rid,failure)
            self.store.append_message_once(rid,step['step_id'],'error','system','orchestrator','Coordinator unavailable. Completed Agent work is preserved; owner may continue.')
            return
        current=self._current(rid)
        if current['state']!='RUNNING' or current['input_revision']!=step['input_revision']:
            self._update(rid,{'current_step':None}); return
        # Structured control is authoritative. Instruction scanning adds an early owner gate;
        # the broker/sandbox remain the hard boundary against requests missed by this check.
        protected=protected_request(decision.message_to_agent or '')
        accepted=self._commit_decision(run,step,decision,protected)
        if accepted and decision.action in ('continue_agent','switch_agent') and not protected:
            self._agent_step(rid,bridge)

    def _commit_decision(self,expected,step,decision,protected):
        # Owner input and decision acceptance serialize on the SAME SQLite write
        # transaction. Completion message, checkpoint and state commit together.
        rid=expected['run_id'];owner,_=self.store.internal_run(rid)
        with self.store.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.store._get(db,owner,rid)
            active=current['current_step']
            same_step=active and active.get('step_id')==step['step_id']
            if current['version']!=expected['version'] or current['state']!='RUNNING' or current['input_revision']!=step['input_revision'] or not same_step:
                if same_step:
                    current['current_step']=None
                    self.store._write(db,current,current['version'])
                return False
            if decision.message_to_user:
                self.store._append(db,current,step['step_id'],'planning','model','gpt',decision.message_to_user)
            cp=dict(current['checkpoint'])
            allowed={'completed_work','current_work','pending_questions','pending_actions'}
            cp.update({k:v for k,v in decision.checkpoint_update.items() if k in allowed})
            if len(json.dumps(cp).encode())>32768:raise StoreError('Checkpoint exceeds bound.')
            current.update(checkpoint=cp,coordinator_failures=0,current_step=None)
            if decision.action=='wait_for_owner' or protected:
                gate=decision.protected_action_requested or {'action':protected,'command':decision.message_to_agent[:2000],'impact':'Protected operation; outside autonomous authority.'}
                self.controls.prepare_gate(db,current,gate['action'],decision.reason,gate['command'],gate['impact'])
            elif decision.action=='done':
                self.store._append(db,current,step['step_id'],'completion','model','gpt',decision.completion['final_summary'])
                current.update(state='DONE',completed_at=time.time(),completion=decision.completion)
            elif decision.action in ('ask_user','blocked'):
                current.update(state='PAUSED' if decision.action=='ask_user' else 'BLOCKED',last_error='NEED_USER' if decision.action=='ask_user' else 'coordinator_blocked')
            else:
                current.update(current_step=dict(step,phase='launching',agent=decision.agent,instruction=decision.message_to_agent),current_agent=decision.agent)
            self.store._write(db,current,expected['version'])
            return True

    def _agent_step(self,rid,bridge):
        run=self._current(rid); step=run['current_step']
        task=bridge.find(run['scope_id'],step['step_id'])
        if not task or task.get('found') is False:
            if run['state']!='RUNNING':
                self._update(rid,{'current_step':None}); return
            task=bridge.start(step['agent'],run['scope_id'],step['step_id'],step['instruction'],180)
        task_id=task['task_id']; step=dict(step,phase='agent',task_id=task_id)
        self._update(rid,{'current_step':step,'current_bridge_task_id':task_id})
        while task['status'] in ('queued','running'):
            state=self._current(rid)['state']
            if state=='STOPPED' and hasattr(bridge,'stop_task'): task=bridge.stop_task(task_id)
            elif state in ('STOPPED','INTERRUPTING'): task=bridge.cancel(task_id)
            else: task=bridge.get(task_id)
            if task['status'] in ('queued','running'): time.sleep(self.poll_interval)
        if task['status']=='reconciling':
            self._update(rid,{'state':'BLOCKED','last_error':'agent_reconciliation_required'}); return
        if task['status']=='completed' and task.get('result'):
            self.store.append_message_once(rid,step['step_id'],'agent','agent','codex' if step['agent']=='codex' else 'claude-code',task['result'],task_id)
        elif task['status']!='completed':
            self.store.append_message_once(rid,step['step_id'],'agent_status','system','orchestrator',f'{step["agent"]} task {task["status"]}. Partial work is preserved.')
        snapshot=task.get('snapshot') or bridge.snapshot(run['scope_id'])
        def finish(current):
            count=current['round_number']+1
            cp=dict(current['checkpoint']); cp.update(files_changed=snapshot['changed_files'],latest_test_state=snapshot['tests'],
                git_head=snapshot['head'],git_status=snapshot['status'],round_number=count,current_agent=step['agent'])
            messages=self.context.build(rid); cp['recent_transcript_reference']=[m['message_id'] for m in messages]
            failure_count=0 if task['status']=='completed' else current['agent_failures']+1
            fields={'snapshot':snapshot,'checkpoint':cp,'checkpoint_version':current['checkpoint_version']+1,
                'round_number':count,'current_step':None,'current_bridge_task_id':None,'agent_failures':failure_count}
            if current['state'] in ('PAUSE_REQUESTED','INTERRUPTING'): fields.update(state='PAUSED',paused_at=time.time())
            elif failure_count>=current['max_agent_failures']: fields.update(state='BLOCKED',last_error='agent_failure_limit')
            return fields
        self._update(rid,finish)
