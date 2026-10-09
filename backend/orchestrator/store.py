"""Durable runs and controls, reusing the Agent Chat message table."""
import hashlib
import json
import time
import uuid
from ..agent_chat.store import StoreError
from ..agent_chat.safety import safe_text
from .types import RunState, TERMINAL, _TRANSITIONS


class OrchestratorStore:
    def __init__(self,agent_store):
        self.shared=agent_store
        with self.shared.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS orchestrator_runs(run_id TEXT PRIMARY KEY,owner TEXT NOT NULL,session_id TEXT NOT NULL REFERENCES agent_chat_sessions(session_id),idempotency_key TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,UNIQUE(owner,idempotency_key));
                CREATE TABLE IF NOT EXISTS orchestrator_session_leases(session_id TEXT PRIMARY KEY REFERENCES agent_chat_sessions(session_id),run_id TEXT UNIQUE NOT NULL REFERENCES orchestrator_runs(run_id));
                CREATE TABLE IF NOT EXISTS orchestrator_messages(run_id TEXT NOT NULL REFERENCES orchestrator_runs(run_id),step_id TEXT NOT NULL,stage TEXT NOT NULL,message_id TEXT UNIQUE NOT NULL REFERENCES agent_chat_messages(message_id),digest TEXT NOT NULL,PRIMARY KEY(run_id,step_id,stage));
                CREATE TABLE IF NOT EXISTS orchestrator_controls(run_id TEXT NOT NULL,request_key TEXT NOT NULL,digest TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(run_id,request_key));
                CREATE TABLE IF NOT EXISTS orchestrator_worker_leases(run_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,process_id INTEGER NOT NULL,process_identity TEXT NOT NULL);
            ''')

    def create_run(self,owner,session_id,goal,default_agent,writer_enabled,source_id,source_commit,idempotency_key):
        self.shared.get_session(owner,session_id)
        if default_agent not in ('codex','claude') or not isinstance(writer_enabled,bool) or not isinstance(goal,str) or not goal.strip() or len(goal)>18000 or not all(isinstance(v,str) and 0<len(v)<=100 for v in (source_id,source_commit,idempotency_key)):
            raise StoreError('Invalid orchestrator request.',400)
        goal=safe_text(goal)
        digest=hashlib.sha256(json.dumps([session_id,goal,default_agent,writer_enabled,source_id,source_commit]).encode()).hexdigest()
        rid=uuid.uuid4().hex; now=time.time()
        checkpoint={'goal':goal[:4000],'completed_work':[],'current_work':'','current_agent':default_agent,
            'files_changed':[],'latest_test_state':[],'pending_questions':[],'pending_actions':[],
            'owner_constraints':[],'round_number':0,'recent_transcript_reference':[], 'git_head':None,'git_status':''}
        run=dict(run_id=rid,session_id=session_id,goal=goal,state='IDLE',coordinator_model='gpt-6-luna',
            default_agent=default_agent,current_agent=default_agent,current_bridge_task_id=None,
            round_number=0,created_at=now,updated_at=now,started_at=None,paused_at=None,completed_at=None,
            owner_gate=None,last_error=None,checkpoint_version=0,checkpoint=checkpoint,scope_id=None,
            writer_enabled=writer_enabled,source_id=source_id,source_commit=source_commit,version=1,
            input_revision=0,current_step=None,snapshot=None,stop_requested=False,
            coordinator_failures=0,agent_failures=0,max_rounds=20,max_duration=3600,completion=None,
            max_coordinator_failures=2,max_agent_failures=2,usage=None,budget_measured=False)
        with self.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT run_id,digest FROM orchestrator_runs WHERE owner=? AND idempotency_key=?',(owner,idempotency_key)).fetchone()
            if old:
                if old['digest']!=digest: raise StoreError('Idempotency key conflicts.')
                return self._get(db,owner,old['run_id'])
            if db.execute("SELECT 1 FROM agent_chat_runs WHERE session_id=? AND status NOT IN ('completed','cancelled','failed','provider_timeout','bridge_timeout')",(session_id,)).fetchone() or db.execute('SELECT 1 FROM orchestrator_session_leases WHERE session_id=?',(session_id,)).fetchone():
                raise StoreError('A run owns this session.')
            db.execute('INSERT INTO orchestrator_runs VALUES(?,?,?,?,?,?,?,?)',(rid,owner,session_id,idempotency_key,digest,'IDLE',1,json.dumps(run)))
            db.execute('INSERT INTO orchestrator_session_leases VALUES(?,?)',(session_id,rid))
            self._append(db,run,'owner-goal','goal','user','user',goal)
        return run

    def _get(self,db,owner,rid):
        row=db.execute('SELECT data FROM orchestrator_runs WHERE run_id=? AND owner=?',(rid,owner)).fetchone()
        if not row: raise StoreError('Orchestrator run not found.',404)
        return json.loads(row['data'])

    def get_run(self,owner,rid):
        with self.shared.connection() as db: return self._get(db,owner,rid)

    def internal_run(self,rid):
        with self.shared.connection() as db:
            row=db.execute('SELECT owner FROM orchestrator_runs WHERE run_id=?',(rid,)).fetchone()
            if not row: raise StoreError('Orchestrator run not found.',404)
            return row['owner'],self._get(db,row['owner'],rid)

    def _write(self,db,run,expected_version):
        previous=db.execute('SELECT state,version FROM orchestrator_runs WHERE run_id=?',(run['run_id'],)).fetchone()
        if previous['version']!=expected_version: raise StoreError('Run version is stale.')
        RunState(run['state'])
        if previous['state']!=run['state'] and not any(old.value==previous['state'] and new.value==run['state'] for (old,event),new in _TRANSITIONS.items()):
            raise StoreError('Run transition is invalid.')
        run['version']=expected_version+1; run['updated_at']=time.time()
        db.execute('UPDATE orchestrator_runs SET state=?,version=?,data=? WHERE run_id=? AND version=?',(run['state'],run['version'],json.dumps(run),run['run_id'],expected_version))
        if run['state'] in TERMINAL and run['current_step'] is None:
            db.execute('DELETE FROM orchestrator_session_leases WHERE run_id=?',(run['run_id'],))
        return run

    def compare_update(self,rid,expected_version,changes):
        with self.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT owner FROM orchestrator_runs WHERE run_id=?',(rid,)).fetchone()
            if not row: raise StoreError('Orchestrator run not found.',404)
            run=self._get(db,row['owner'],rid)
            if not set(changes)<=set(run) or set(changes)&{'run_id','session_id','version','created_at','source_id','source_commit','writer_enabled'}:
                raise StoreError('Invalid run update.')
            run.update(changes)
            return self._write(db,run,expected_version)

    def _append(self,db,run,step,stage,speaker_type,speaker_name,content,bridge_task_id=None):
        self.shared._validate_speaker(speaker_type,speaker_name)
        content=safe_text(content)[:18000]
        if not content.strip(): raise StoreError('Message is empty.',400)
        digest=hashlib.sha256(json.dumps([speaker_type,speaker_name,content,bridge_task_id]).encode()).hexdigest()
        old=db.execute('SELECT message_id,digest FROM orchestrator_messages WHERE run_id=? AND step_id=? AND stage=?',(run['run_id'],step,stage)).fetchone()
        if old:
            if old['digest']!=digest: raise StoreError('Message idempotency conflict.')
            return dict(db.execute('SELECT * FROM agent_chat_messages WHERE message_id=?',(old['message_id'],)).fetchone())
        mid=uuid.uuid4().hex; now=time.time()
        db.execute('''INSERT INTO agent_chat_messages(message_id,session_id,role,content,status,bridge_task_id,created_at,speaker_type,speaker_name,chat_model,agent,stage) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (mid,run['session_id'],'user' if speaker_type=='user' else 'assistant',content,'completed',bridge_task_id,now,speaker_type,speaker_name,'gpt',run['current_agent'],stage))
        db.execute('INSERT INTO orchestrator_messages VALUES(?,?,?,?,?)',(run['run_id'],step,stage,mid,digest))
        db.execute('UPDATE agent_chat_sessions SET updated_at=? WHERE session_id=?',(now,run['session_id']))
        return dict(db.execute('SELECT * FROM agent_chat_messages WHERE message_id=?',(mid,)).fetchone())

    def append_message_once(self,rid,step,stage,speaker_type,speaker_name,content,bridge_task_id=None):
        owner,run=self.internal_run(rid)
        with self.shared.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._append(db,run,step,stage,speaker_type,speaker_name,content,bridge_task_id)
