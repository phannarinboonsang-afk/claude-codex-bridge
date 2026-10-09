import copy
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from worker import Policy, Store, Denied, command, unit_text
from provision import resource_spec, destruction_ids, validate_owner

def spec():
    return dict(run_id='a'*32, repository='demo', base_commit='b'*40,
                agent='synthetic', profile='tree', task='synthetic probe',
                limits=dict(memory_mb=128, tasks=32, cpu_percent=50, timeout=20))

def policy():
    return Policy({'repositories': {'demo': 'https://github.com/example/demo.git'},
                   'writers_enabled': False})

@contextmanager
def store_fixture():
    with tempfile.TemporaryDirectory() as d:
        s=Store(d)
        try:yield s
        finally:s.db.close()

class SecurityTests(unittest.TestCase):
    def test_registration_derives_path(self):
        r=policy().validate(spec())
        self.assertEqual(r['worktree'], '/srv/agentbridge/runs/'+'a'*32+'/worktree')
        self.assertEqual(len(r['spec_digest']),64)
    def test_bad_ids(self):
        for x in ['../x','/etc','A'*32,'a'*31,'a'*33,'a'*32+'\n']:
            r=spec(); r['run_id']=x
            with self.subTest(x=x),self.assertRaises(Denied): policy().validate(r)
    def test_extra_inputs_denied(self):
        for field in ['command','pid','path','executable','systemctl','credentials','worktree']:
            r=spec();r[field]='arbitrary'
            with self.subTest(field=field),self.assertRaises(Denied):policy().validate(r)
    def test_repository_allowlist(self):
        r=spec();r['repository']='https://evil.test/repo'
        with self.assertRaises(Denied):policy().validate(r)
    def test_commit_exact(self):
        for sha in ['main','HEAD','--upload-pack=x','c'*39,'c'*40+'\n']:
            r=spec();r['base_commit']=sha
            with self.subTest(sha=sha),self.assertRaises(Denied):policy().validate(r)
    def test_profiles(self):
        for p in ['shell','../../bin/sh','codex','tree;id']:
            r=spec();r['profile']=p
            with self.subTest(p=p),self.assertRaises(Denied):policy().validate(r)
    def test_limits(self):
        for key,val in [('tasks',0),('memory_mb',99999),('cpu_percent',999),('timeout',0),('tasks',True)]:
            r=spec();r['limits'][key]=val
            with self.subTest(key=key),self.assertRaises(Denied):policy().validate(r)
    def test_writer_disabled(self):
        r=spec();r.update(agent='codex',profile='codex-task')
        with self.assertRaises(Denied):policy().validate(r)
    def test_command_no_shell(self):
        r=policy().validate(spec()); argv=command(r)
        self.assertIsInstance(argv,list)
        self.assertNotIn('/bin/sh',argv)
    def test_unit_limits(self):
        t=unit_text(policy().validate(spec()))
        for x in ['User=agentbridge','MemoryMax=128M','MemorySwapMax=0','TasksMax=32','CPUQuota=50%','RuntimeMaxSec=20','KillMode=control-group','NoNewPrivileges=yes']:
            self.assertIn(x,t)
    def test_idempotent_create(self):
        with store_fixture() as s:
            r=policy().validate(spec())
            a=s.create(r,'request-123'); b=s.create(r,'request-123')
            self.assertEqual(a,b)
    def test_changed_idempotency_denied(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123')
            r2=spec();r2['limits']['tasks']=33
            with self.assertRaises(Denied):s.create(policy().validate(r2),'request-123')
    def test_duplicate_run_denied(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123')
            with self.assertRaises(Denied):s.create(r,'request-456')
    def test_stale_registration(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123')
            s.db.execute("UPDATE runs SET spec=?",(json.dumps({**r,'task':'changed'}),));s.db.commit()
            with self.assertRaises(Denied):s.inspect(r['run_id'])
    def test_one_active(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123');s.reserve(r['run_id'])
            r2=spec();r2['run_id']='c'*32;s.create(policy().validate(r2),'request-456')
            with self.assertRaises(Denied):s.reserve(r2['run_id'])
    def test_recovery_not_done(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123');s.reserve(r['run_id']);s.recover()
            self.assertEqual(s.inspect(r['run_id'])['state'],'UNKNOWN')
            with self.assertRaises(Denied):s.reserve(r['run_id'])
    def test_resource_spec(self):
        r=resource_spec({'ssh_key':'123','admin_cidr':'203.0.113.1/32'},'deploy-123')
        self.assertEqual(r['size'],'s-4vcpu-8gb');self.assertEqual(r['region'],'sgp1')
        self.assertFalse(r['ipv6']);self.assertFalse(r['backups'])
    def test_owner_wide_network_denied(self):
        for net in ['0.0.0.0/0','::/0','192.168.1.0/24','203.0.113.1/24']:
            with self.subTest(net=net),self.assertRaises(ValueError):validate_owner({'ssh_key':'123','admin_cidr':net})
    def test_destroy_exact_resources(self):
        m={'deployment_tag':'deploy-123','droplet_id':9,'firewall_id':'f'}
        d={'id':9,'name':'agentbridge-writer-v1','tags':['deploy-123'],'region':{'slug':'sgp1'},'size_slug':'s-4vcpu-8gb'}
        f={'id':'f','name':'agentbridge-writer-v1-deploy-123','tags':['deploy-123']}
        self.assertEqual(destruction_ids(m,d,f),(9,'f'))
    def test_destroy_foreign_resource_denied(self):
        m={'deployment_tag':'deploy-123','droplet_id':9,'firewall_id':'f'}
        with self.assertRaises(ValueError):destruction_ids(m,{'id':10},None)

if __name__=='__main__':unittest.main()
