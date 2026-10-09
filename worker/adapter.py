"""Orchestrator execution interface backed exclusively by the remote Worker API."""
import json
import re
from .client import RemoteWorkerClient, WorkerError, canonical, digest, run_id


class RemoteWorkerAdapter:
    def __init__(self, url, auth_file, journal, *, limits=None):
        self.client = RemoteWorkerClient(url, auth_file, journal)
        self.db = self.client.db
        self.limits = dict(limits or {'memory_mb':2048,'tasks':128,'cpu_percent':200,'timeout':180})
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                snapshot TEXT NOT NULL, previous TEXT, stopped INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS steps(scope TEXT NOT NULL, step TEXT NOT NULL,
                task TEXT UNIQUE NOT NULL, spec TEXT NOT NULL, PRIMARY KEY(scope,step));
        ''')

    def __enter__(self): return self
    def __exit__(self, *args): self.client.close()

    def create_scope(self, run):
        if run.get('writer_enabled') is not True: raise WorkerError('remote_writer_disabled')
        rid = run_id(run['run_id'])
        binding = {key:run[key] for key in ('run_id','source_id','source_commit','writer_enabled')}
        if not re.fullmatch('[a-f0-9]{40}', binding['source_commit']): raise WorkerError('worker_invalid_commit')
        initial = {'head':binding['source_commit'],'status':'','changed_files':[],'diff_summary':'','tests':[]}
        with self.db:
            row = self.db.execute('SELECT binding FROM scopes WHERE id=?',(rid,)).fetchone()
            if row and row[0] != canonical(binding): raise WorkerError('worker_scope_conflict')
            if not row: self.db.execute('INSERT INTO scopes(id,binding,snapshot) VALUES(?,?,?)',(rid,canonical(binding),canonical(initial)))
        return {'scope_id':rid}

    def _scope(self, scope):
        run_id(scope)
        row = self.db.execute('SELECT binding,snapshot,previous,stopped FROM scopes WHERE id=?',(scope,)).fetchone()
        if not row: raise WorkerError('worker_unknown_scope')
        return row

    def snapshot(self, scope): return json.loads(self._scope(scope)[1])

    def find(self, scope, step):
        self._scope(scope)
        row = self.db.execute('SELECT task,spec FROM steps WHERE scope=? AND step=?',(scope,step)).fetchone()
        if row:
            # create is idempotent and does not launch; recover a lost registration
            # response before inspecting. Never repeat start from find/reconnect.
            self.client.create(json.loads(row[1]),'step-'+row[0])
            return self.get(row[0])
        return None

    def start(self, agent, scope, step, instruction, timeout):
        run_id(step)
        current = self._scope(scope)
        if current[3]: raise WorkerError('worker_scope_stopped')
        if agent not in {'codex','claude'}: raise WorkerError('worker_profile_denied')
        if type(timeout) is not int or not 5 <= timeout <= self.limits['timeout']: raise WorkerError('worker_invalid_timeout')
        binding = json.loads(current[0])
        task = digest({'scope':scope,'step':step})[:32]
        limits = dict(self.limits, timeout=timeout)
        spec = {'run_id':task,'repository':binding['source_id'],'base_commit':binding['source_commit'],
            'agent':agent,'profile':agent+'-task','task':instruction,'limits':limits}
        if current[2]: spec['previous_run_id'] = current[2]
        with self.db:
            row = self.db.execute('SELECT task,spec FROM steps WHERE scope=? AND step=?',(scope,step)).fetchone()
            if row:
                # A retry after completion must compare the original immutable inputs,
                # not recompute its predecessor from the now advanced scope.
                original = json.loads(row[1])
                if any(original[k] != spec[k] for k in ('agent','profile','task','limits','repository','base_commit')):
                    raise WorkerError('worker_step_conflict')
                spec = original
            else: self.db.execute('INSERT INTO steps VALUES(?,?,?,?)',(scope,step,task,canonical(spec)))
        self.client.create(spec, 'step-'+task)
        self.client.start(task)
        return self.get(task)

    def get(self, task):
        row = self.db.execute('SELECT scope,spec FROM steps WHERE task=?',(run_id(task),)).fetchone()
        if not row: raise WorkerError('worker_unknown_task')
        record = self.client.inspect(task)
        state = record['state']
        status = {'CREATED':'reconciling','STARTING':'running','RUNNING':'running','UNKNOWN':'reconciling',
            'COMPLETED':'completed','FAILED':'failed','STOPPED':'cancelled','CLEANED':'reconciling'}[state]
        answer = {'task_id':task,'status':status}
        if state == 'COMPLETED':
            result = self.client.collect(task)['result']
            def output(name):
                item = result.get(name)
                if not isinstance(item,dict) or item.get('exit_code') != 0 or not isinstance(item.get('stdout'),str):
                    raise WorkerError('worker_invalid_result')
                return item['stdout']
            head = output('commit').strip()
            if not re.fullmatch('[a-f0-9]{40}',head): raise WorkerError('worker_invalid_result')
            files = output('changed_files')
            diff = output('diff')
            tests = result.get('tests')
            if not isinstance(tests,list): raise WorkerError('worker_invalid_result')
            snapshot = {'head':head,'status':'','changed_files':files.splitlines(),
                'diff_summary':diff,'tests':tests}
            # Agent output remains explicitly untrusted. Existing transcript safety
            # sanitation is applied by OrchestratorStore when it appends the result.
            answer.update(result=canonical(result),snapshot=snapshot)
            with self.db:
                latest = self._scope(row[0])[2]
                position = self.db.execute('SELECT rowid FROM steps WHERE task=?',(task,)).fetchone()[0]
                latest_position = self.db.execute('SELECT rowid FROM steps WHERE task=?',(latest,)).fetchone() if latest else None
                if latest_position is None or position >= latest_position[0]:
                    self.db.execute('UPDATE scopes SET snapshot=?,previous=? WHERE id=?',(canonical(snapshot),task,row[0]))
        return answer

    def cancel(self, task):
        self.client.cancel(task)
        return self.get(task)

    def stop(self, scope):
        self._scope(scope)
        with self.db: self.db.execute('UPDATE scopes SET stopped=1 WHERE id=?',(scope,))
        for row in self.db.execute('SELECT task FROM steps WHERE scope=?',(scope,)).fetchall(): self.client.stop(row[0])

    def stop_task(self, task):
        row = self.db.execute('SELECT scope FROM steps WHERE task=?',(run_id(task),)).fetchone()
        if not row: raise WorkerError('worker_unknown_task')
        self.stop(row[0])
        return self.get(task)
