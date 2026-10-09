"""Real HTTP contract tests; only privileged execution plumbing is synthetic."""
import importlib.util
import http.server
import json
from pathlib import Path
import socket
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from worker.client import RemoteWorkerClient, WorkerError

PACKAGE = Path(__file__).resolve().parents[2] / 'provisioning/digitalocean'
loader = importlib.util.spec_from_file_location('contract_worker', PACKAGE / 'worker.py')
api = importlib.util.module_from_spec(loader)
loader.loader.exec_module(api)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auth = self.root / 'worker-auth'
        self.auth.write_text('synthetic-worker-authentication-only-123456789')
        self.auth.chmod(0o600)
        self.launches = []
        self.active = {}
        self.results = {}
        self.auto_finish = False
        self.launch_hook = None
        ready = {'gates':{key:True for key in ('containment','egress','source','toolchain','authenticated_channel','orchestrator_recovery')},'boot_id':'synthetic-boot'}
        (self.root/'readiness.json').write_text(json.dumps(ready))
        (self.root/'boot-id').write_text('synthetic-boot')
        self.server = http.server.HTTPServer(('127.0.0.1', 0), api.handler_type(self, self.auth.read_bytes()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = RemoteWorkerClient(self.url, self.auth, self.root / 'journal.db')

    def dispatch(self, op, body):
        # Store is opened on the HTTP server thread, like the shipped service.
        store = api.Store(self.root / 'server-state')
        controller = api.Controller(api.Policy({'repositories': {'fixture': 'https://example.invalid/project.git'}, 'writers_enabled': True}), store)
        def prepare(spec):
            self.launches.append(spec['run_id'])
            if self.launch_hook: self.launch_hook()
            runtime = self.root/'runs'/spec['run_id']/'runtime'
            runtime.mkdir(parents=True)
            (runtime/'result.json').write_text(json.dumps({'exit_code':0, 'stdout':'SYNTHETIC_AGENT_RESULT',
                'commit':{'exit_code':0,'stdout':'b'*40+'\n'},
                'changed_files':{'exit_code':0,'stdout':''},
                'diff':{'exit_code':0,'stdout':''},'tests':[]}))
        controller.prepare = prepare
        controller.assert_empty = lambda rid: None
        controller.status = lambda rid: {'LoadState': 'loaded', 'ExecMainStartTimestampMonotonic': '1',
            'ActiveState': 'active' if self.active.get(rid) else 'inactive', 'Result': 'success', 'ExecMainStatus': '0'}
        def plumbing(argv, **kwargs):
            if len(argv) > 1 and argv[1] == 'start': self.active[body['run_id']] = True
            if len(argv) > 1 and argv[1] == 'stop': self.active[body['run_id']] = False
            return b''
        try:
            if op=='inspect' and self.auto_finish and self.active.get(body['run_id']): self.active[body['run_id']] = False
            with patch.object(api, 'precheck'), patch.object(api, 'run', plumbing), patch.object(api, 'ROOT', self.root), patch.object(api, 'pathlib', types.SimpleNamespace(Path=self.system_path)):
                return controller.dispatch(op, body)
        finally:
            store.db.close()

    def system_path(self, value):
        if str(value) == '/run/systemd/system':
            p = self.root / 'units'; p.mkdir(exist_ok=True); return p
        if str(value)=='/var/lib/agentbridge-worker/writer-readiness.json': return self.root/'readiness.json'
        if str(value)=='/proc/sys/kernel/random/boot_id': return self.root/'boot-id'
        return Path(value)

    def tearDown(self):
        self.client.close()
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.tmp.cleanup()

    def register(self, rid='a'*32):
        return self.client.create({'run_id': rid, 'repository': 'fixture', 'base_commit': 'b'*40,
            'agent': 'synthetic', 'profile': 'cpu', 'task': 'synthetic task',
            'limits': {'memory_mb': 128, 'tasks': 16, 'cpu_percent': 50, 'timeout': 10}}, 'task-'+rid)

    def test_create_start_inspect_and_duplicate_start(self):
        self.register(); self.client.start('a'*32); self.client.start('a'*32)
        self.assertEqual(self.launches, ['a'*32])
        self.assertEqual(self.client.inspect('a'*32)['state'], 'RUNNING')

    def test_cancel_active(self):
        self.register(); self.client.start('a'*32)
        self.assertEqual(self.client.cancel('a'*32)['state'], 'STOPPED')

    def test_stop_before_launch_prohibits_start(self):
        self.register(); self.client.stop('a'*32)
        with self.assertRaises(WorkerError): self.client.start('a'*32)
        self.assertEqual(self.launches, [])

    def test_auth_failure_is_sanitized(self):
        self.auth.write_text('wrong-synthetic-authentication-123456789')
        with self.assertRaises(WorkerError) as caught: self.register()
        self.assertEqual(caught.exception.code, 'worker_authentication_failed')
        self.assertNotIn('wrong-synthetic', str(caught.exception))

    def test_unknown_task_fails_closed(self):
        with self.assertRaises(WorkerError): self.client.inspect('f'*32)

    def test_stale_registration_rejected(self):
        self.register()
        spec = {'run_id': 'a'*32, 'repository': 'fixture', 'base_commit': 'c'*40,
            'agent': 'synthetic', 'profile': 'cpu', 'task': 'different',
            'limits': {'memory_mb': 128, 'tasks': 16, 'cpu_percent': 50, 'timeout': 10}}
        with self.assertRaises(WorkerError): self.client.create(spec, 'task-'+'a'*32)

    def test_network_loss_not_completion(self):
        self.register(); self.client.start('a'*32)
        self.server.shutdown(); self.server.server_close()
        with self.assertRaises(WorkerError) as caught: self.client.inspect('a'*32)
        self.assertEqual(caught.exception.code, 'worker_disconnected')

    def test_reconnect_inspects_completed_without_start(self):
        self.register(); self.client.start('a'*32); self.active['a'*32] = False
        self.client.close()
        self.client = RemoteWorkerClient(self.url, self.auth, self.root / 'journal.db')
        self.assertEqual(self.client.inspect('a'*32)['state'], 'COMPLETED')
        self.assertEqual(self.launches, ['a'*32])

    def test_collect_completed_actual_http(self):
        self.register(); self.client.start('a'*32); self.active['a'*32] = False
        self.client.inspect('a'*32)
        self.assertEqual(self.client.collect('a'*32)['result']['stdout'], 'SYNTHETIC_AGENT_RESULT')

    def test_malformed_response_rejected(self):
        original = self.dispatch
        self.dispatch = lambda op, body: {'unexpected': True}
        with self.assertRaises(WorkerError) as caught: self.register()
        self.assertEqual(caught.exception.code, 'worker_invalid_response')
        self.dispatch = original

    def test_parent_registration_binding(self):
        self.register(); self.client.start('a'*32); self.active['a'*32] = False
        self.client.inspect('a'*32)
        spec = {'run_id':'c'*32,'repository':'fixture','base_commit':'b'*40,
            'agent':'synthetic','profile':'cpu','task':'next round',
            'limits':{'memory_mb':128,'tasks':16,'cpu_percent':50,'timeout':10},'previous_run_id':'a'*32}
        record = self.client.create(spec, 'task-'+'c'*32)
        self.assertEqual(record['spec']['previous_run_id'],'a'*32)

    def test_unknown_parent_rejected(self):
        spec = {'run_id':'c'*32,'repository':'fixture','base_commit':'b'*40,
            'agent':'synthetic','profile':'cpu','task':'next round',
            'limits':{'memory_mb':128,'tasks':16,'cpu_percent':50,'timeout':10},'previous_run_id':'f'*32}
        with self.assertRaises(WorkerError): self.client.create(spec,'task-'+'c'*32)

    def test_public_endpoint_configuration_rejected(self):
        with self.assertRaises(WorkerError): RemoteWorkerClient('http://example.invalid:8765', self.auth, self.root/'other.db')

    def test_no_arbitrary_operation(self):
        self.assertFalse(hasattr(self.client, 'shell'))
        with self.assertRaises(WorkerError): self.client.inspect('../escape')

    def test_codex_and_claude_adapter_profiles_and_handoff(self):
        from worker.adapter import RemoteWorkerAdapter
        adapter = RemoteWorkerAdapter(self.url,self.auth,self.root/'adapter.db')
        try:
            run={'run_id':'d'*32,'source_id':'fixture','source_commit':'b'*40,'writer_enabled':True}
            scope=adapter.create_scope(run)['scope_id']
            self.auto_finish=True
            first=adapter.start('codex',scope,'e'*32,'first synthetic instruction',180)
            second=adapter.start('claude',scope,'f'*32,'second synthetic instruction',180)
            self.assertEqual(first['status'],'completed'); self.assertEqual(second['status'],'completed')
            records=[json.loads(row[0]) for row in adapter.db.execute('SELECT spec FROM registrations ORDER BY rowid')]
            self.assertEqual([r['profile'] for r in records],['codex-task','claude-task'])
            self.assertEqual(records[1]['previous_run_id'],first['task_id'])
            self.assertEqual(adapter.start('codex',scope,'e'*32,'first synthetic instruction',180)['task_id'],first['task_id'])
            self.assertEqual(len(self.launches),2)
            self.assertEqual(adapter._scope(scope)[2],second['task_id'])
            adapter.stop(scope)
            with self.assertRaises(WorkerError):adapter.start('codex',scope,'1'*32,'later',180)
        finally:adapter.client.close()

    def test_autonomous_loop_actual_transport(self):
        from backend.agent_chat.store import AgentChatStore
        from backend.orchestrator.store import OrchestratorStore
        from backend.orchestrator.worker import Worker
        from backend.orchestrator.decisions import Decision
        from tests.orchestrator.test_decisions import payload
        from worker.adapter import RemoteWorkerAdapter
        shared=AgentChatStore(self.root/'chat.db')
        sid=shared.create_session('owner','gpt','codex')['session_id']
        store=OrchestratorStore(shared)
        run=store.create_run('owner',sid,'Synthetic goal','codex',True,'fixture','b'*40,'request1')
        actions=iter([payload('continue_agent',agent='codex'),payload('switch_agent',agent='claude'),payload('done')])
        class Coordinator:
            def decide(self,*args):return Decision.parse(next(actions))
        self.auto_finish=True
        worker=Worker(store,Coordinator(),lambda:RemoteWorkerAdapter(self.url,self.auth,self.root/'adapter.db'),poll_interval=0)
        result=worker.run_until_boundary(run['run_id'])
        self.assertEqual(result['state'],'DONE'); self.assertEqual(result['round_number'],2)
        self.assertEqual(len(self.launches),2)
        worker.run_until_boundary(run['run_id']);self.assertEqual(len(self.launches),2)

    def test_owner_stop_prevents_later_autonomous_round(self):
        from backend.agent_chat.store import AgentChatStore
        from backend.orchestrator.store import OrchestratorStore
        from backend.orchestrator.worker import Worker
        from backend.orchestrator.controls import Controls
        from backend.orchestrator.decisions import Decision
        from tests.orchestrator.test_decisions import payload
        from worker.adapter import RemoteWorkerAdapter
        shared=AgentChatStore(self.root/'chat.db');sid=shared.create_session('owner','gpt','codex')['session_id']
        store=OrchestratorStore(shared)
        run=store.create_run('owner',sid,'Synthetic goal','codex',True,'fixture','b'*40,'request1')
        def owner_stop():
            current=store.get_run('owner',run['run_id'])
            Controls(store).apply('owner',run['run_id'],'stop',current['version'],'owner-stop',{})
        self.launch_hook=owner_stop
        class Coordinator:
            def decide(self,*args):return Decision.parse(payload())
        worker=Worker(store,Coordinator(),lambda:RemoteWorkerAdapter(self.url,self.auth,self.root/'adapter.db'),poll_interval=0)
        result=worker.run_until_boundary(run['run_id'])
        self.assertEqual(result['state'],'STOPPED');self.assertEqual(len(self.launches),1)
        worker.run_until_boundary(run['run_id']);self.assertEqual(len(self.launches),1)
        self.assertFalse(any(self.active.values()))

    def test_lost_start_response_reconciles_without_duplicate(self):
        self.register()
        original=self.dispatch
        def interrupted(op,body):
            result=original(op,body)
            if op=='start':raise OSError('synthetic loss after execution accepted')
            return result
        self.dispatch=interrupted
        with self.assertRaises(WorkerError):self.client.start('a'*32)
        self.dispatch=original
        self.assertEqual(self.client.start('a'*32)['state'],'RUNNING')
        self.assertEqual(len(self.launches),1)

    def test_stale_event_replay_fails_closed(self):
        old=self.register();self.client.start('a'*32)
        self.dispatch=lambda op,body:old
        with self.assertRaises(WorkerError) as caught:self.client.inspect('a'*32)
        self.assertEqual(caught.exception.code,'worker_stale_state')


if __name__ == '__main__': unittest.main()
