import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from test_security import spec,policy,store_fixture
from worker import Controller,Denied
from provision import cloud_init,destroy,firewall_spec,save
from acceptance import memory_enforced,cpu_enforced
from proxy import validate_target
from runner import bounded_result

class RegressionTests(unittest.TestCase):
    def test_missing_unit_never_completed(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123');s.reserve(r['run_id']);s.state(r['run_id'],'RUNNING');s.recover();c=Controller(policy(),s)
            with patch.object(c,'status',return_value=dict(LoadState='not-found',ActiveState='inactive',Result='success',ExecMainStatus='0',ExecMainStartTimestampMonotonic='0')):
                self.assertEqual(c.dispatch('inspect',dict(run_id=r['run_id'],spec_digest=r['spec_digest']))['state'],'UNKNOWN')
    def test_never_started_unit_never_completed(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123');s.reserve(r['run_id']);s.recover();c=Controller(policy(),s)
            with patch.object(c,'status',return_value=dict(LoadState='loaded',ActiveState='inactive',Result='success',ExecMainStatus='0',ExecMainStartTimestampMonotonic='123')):
                self.assertEqual(c.dispatch('inspect',dict(run_id=r['run_id'],spec_digest=r['spec_digest']))['state'],'UNKNOWN')
    def test_arbitrary_operation_denied(self):
        with store_fixture() as s:
            c=Controller(policy(),s)
            for op in ['shell','systemctl','attach_pid','credentials','/bin/sh','start?cmd=id']:
                with self.subTest(op=op),self.assertRaises(Denied):c.dispatch(op,{})
    def test_stale_request_denied(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123')
            with self.assertRaises(Denied):Controller(policy(),s).dispatch('inspect',dict(run_id=r['run_id'],spec_digest='0'*64))
    def test_extra_pid_denied(self):
        with store_fixture() as s:
            r=policy().validate(spec());s.create(r,'request-123')
            with self.assertRaises(Denied):Controller(policy(),s).dispatch('stop',dict(run_id=r['run_id'],spec_digest=r['spec_digest'],pid=1))
    def test_production_paths_denied(self):
        for repo in ['/root/.ssh','/srv/production','../repo','file:///etc']:
            r=spec();r['repository']=repo
            with self.subTest(repo=repo),self.assertRaises(Denied):policy().validate(r)
    def test_changed_repository_binding_denied(self):
        p=policy();r=p.validate(spec());p.data['repositories']['demo']='https://github.com/another/repo.git'
        with self.assertRaises(Denied):p.source(r)
    def test_stop_running_group_not_empty(self):
        with store_fixture() as s:
            c=Controller(policy(),s)
            with patch.object(c,'status',return_value={'MainPID':'42','ActiveState':'active'}),self.assertRaises(Denied):c.assert_empty('a'*32)
    def test_destroy_keeps_firewall_until_droplet_absent(self):
        class Fake:
            def __init__(self):self.calls=[]
            def call(self,method,path,**kwargs):
                self.calls.append((method,path))
                if method=='DELETE':return {}
                if path=='droplets/9':return {'droplet':{'id':9,'name':'agentbridge-writer-v1','tags':['abwriter-test'],'region':{'slug':'sgp1'},'size_slug':'s-4vcpu-8gb'}}
                return {'firewall':{'id':'f','name':'agentbridge-writer-v1-abwriter-test','tags':['abwriter-test']}}
        with tempfile.TemporaryDirectory() as d:
            state=pathlib.Path(d)/'state.json';save(state,dict(deployment_tag='abwriter-test',droplet_id=9,firewall_id='f',stage='DROPLET_CREATED_WRITERS_DISABLED'))
            api=Fake()
            with self.assertRaises(ValueError):destroy(api,state,'abwriter-test')
            self.assertNotIn(('DELETE','firewalls/f'),api.calls)
    def test_ambiguous_create_no_blind_destroy(self):
        with tempfile.TemporaryDirectory() as d:
            state=pathlib.Path(d)/'state.json';save(state,dict(deployment_tag='abwriter-test',stage='DROPLET_CREATE_INTENT'))
            with self.assertRaises(ValueError):destroy(None,state,'abwriter-test')
    def test_firewall_no_public_api(self):
        fw=firewall_spec(dict(ssh_key='123',admin_cidr='203.0.113.1/32'),'abc')
        self.assertEqual([x['ports'] for x in fw['inbound_rules']],['22'])
    def test_cloud_init_no_password_model_auth(self):
        c=json.loads(cloud_init().split('\n',1)[1]);self.assertFalse(c['ssh_pwauth'])
        self.assertTrue(all('api-token' not in x['path'] and 'model-auth' not in x['path'] for x in c['write_files']))
    def memory(self):
        return dict(events_before={'max':0},events_after={'max':5},requested_bytes=128,maximum=64,peak=64,parent={'cgroup':'/run'},child_proofs=[{'cgroup':'/run','non_root':True}]*2,child_exit_codes=[0,0])
    def test_memory_enforcement_without_death(self):self.assertTrue(memory_enforced(self.memory()))
    def test_memory_no_counter_fails(self):
        r=self.memory();r['events_after']['max']=0;self.assertFalse(memory_enforced(r))
    def test_memory_escape_fails(self):
        r=self.memory();r['child_proofs']=[{'cgroup':'/','non_root':True}]*2;self.assertFalse(memory_enforced(r))
    def test_memory_unbounded_fails(self):
        r=self.memory();r['peak']=65;self.assertFalse(memory_enforced(r))
    def test_cpu_configuration_alone_fails(self):
        self.assertFalse(cpu_enforced(dict(limits={'cpu.max':'10000 100000'},stats_before={'nr_throttled':0},stats_after={'nr_throttled':0},wall_seconds=5,cpu_seconds=.5)))
    def test_proxy_private_metadata_denied(self):
        for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','192.0.2.8','::1','fc00::1']:
            with self.subTest(ip=ip),self.assertRaises(ValueError):validate_target('github.com:443',{'github.com'},[ip])
    def test_proxy_arbitrary_target_denied(self):
        for host in ['evil.test:443','github.com:22','github.com.evil.test:443','127.0.0.1:443','github.com:443/path']:
            with self.subTest(host=host),self.assertRaises(ValueError):validate_target(host,{'github.com'},['8.8.8.8'])
    def test_proxy_mixed_dns_denied(self):
        with self.assertRaises(ValueError):validate_target('github.com:443',{'github.com'},['8.8.8.8','10.0.0.1'])
    def test_proxy_exact_global_target(self):self.assertEqual(validate_target('github.com:443',{'github.com'},['8.8.8.8']),'github.com')
    def test_total_collection_budget(self):
        r={k:{'stdout':'\u0001'*262144,'stderr':'x'*262144,'exit_code':0} for k in ['agent','commit','files','diff']}
        out=bounded_result(r)
        self.assertLessEqual(len(json.dumps(out).encode()),131072)
        self.assertTrue(out['agent']['truncated'])
    def test_artifact_escaping_budget(self):
        r={'artifacts':[{'path':'x.txt','text':chr(1)*32768}]}
        out=bounded_result(r)
        self.assertLessEqual(len(json.dumps(out).encode()),131072)
        self.assertTrue(out['artifacts'][0]['truncated'])
    def test_policy_disable_rechecked_at_start(self):
        with store_fixture() as s:
            p=policy();p.data['writers_enabled']=True
            r=spec();r.update(agent='codex',profile='codex-task');r=p.validate(r);s.create(r,'request-123')
            p.data['writers_enabled']=False;c=Controller(p,s)
            with patch('worker.precheck'),self.assertRaisesRegex(Denied,'WRITER_DISABLED:policy'):
                c.dispatch('start',dict(run_id=r['run_id'],spec_digest=r['spec_digest']))
            self.assertEqual(s.inspect(r['run_id'])['state'],'CREATED')

if __name__=='__main__':unittest.main()
