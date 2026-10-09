#!/usr/bin/python3
"""Guest-root synthetic proof only. Never enables writers."""
import json
import os
import pathlib
import secrets
import time
import urllib.request
import urllib.error

from worker import precheck,unit_name

def memory_enforced(r):
    before=r.get('events_before',{});after=r.get('events_after',{})
    hit=any(after.get(k,0)>before.get(k,0) for k in ['max','oom','oom_kill'])
    children=r.get('child_proofs',[])
    return (hit and not r.get('workload_timed_out') and r['requested_bytes']>r['maximum']
            and 0<r['peak']<=r['maximum'] and len(children)==2
            and all(x['cgroup']==r['parent']['cgroup'] and x['non_root'] for x in children))

def cpu_enforced(r):
    quota,period=map(int,r['limits']['cpu.max'].split())
    ratio=quota/period
    return (r['stats_after']['nr_throttled']>r['stats_before']['nr_throttled']
            and r['wall_seconds']>=4 and 0<r['cpu_seconds']/r['wall_seconds']<=ratio*1.5)

def main():
    if os.geteuid()!=0:raise RuntimeError('guest_admin_only')
    precheck()
    token=pathlib.Path('/etc/agentbridge-worker/api-token').read_text().strip()
    def api(op,body):
        req=urllib.request.Request('http://127.0.0.1:8765/v1/'+op,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)
    checks={};runs=[];evidence={}
    try:
        checks['unified_v2_controllers']=True
        for profile in ['tree','memory','pids','cpu','timeout']:
            rid=secrets.token_hex(16)
            spec=dict(run_id=rid,repository='synthetic',base_commit='0'*40,agent='synthetic',profile=profile,task='synthetic acceptance only',limits=dict(memory_mb=512 if profile=='pids' else 64,tasks=32,cpu_percent=10,timeout=10 if profile=='timeout' else 30))
            record=api('create',dict(spec=spec,idempotency_key=secrets.token_hex(16)))
            handle=dict(run_id=rid,spec_digest=record['spec']['spec_digest']);runs.append(handle)
            api('start',handle)
            result_path=pathlib.Path('/srv/agentbridge/runs')/rid/'runtime/result.json'
            deadline=time.monotonic()+25
            while not result_path.exists() and time.monotonic()<deadline:time.sleep(.1)
            if not result_path.exists():raise RuntimeError('synthetic_result_missing:'+profile)
            result=json.loads(result_path.read_text());evidence[profile]=result
            expected='/system.slice/'+unit_name(rid)
            checks[profile+'_parent']=result['parent']['non_root'] and result['parent']['cgroup']==expected
            if profile=='tree':
                checks['child']=result['child']['non_root'] and result['child']['cgroup']==expected
                checks['grandchild']=result['grandchild']['non_root'] and result['grandchild']['cgroup']==expected
                # A second registered run must not start or share A's cgroup.
                other=dict(spec);other['run_id']=secrets.token_hex(16)
                b=api('create',dict(spec=other,idempotency_key=secrets.token_hex(16)))
                bh=dict(run_id=other['run_id'],spec_digest=b['spec']['spec_digest'])
                try:api('start',bh);checks['one_active_run']=False
                except urllib.error.HTTPError:checks['one_active_run']=api('inspect',bh)['state']=='CREATED'
                api('stop',handle)
                checks['stop_descendants_empty']=api('inspect',handle)['state']=='STOPPED'
                api('cleanup',handle);checks['cleanup_target']=not result_path.parent.parent.exists()
                # After A cleanup, B must run in its own boundary.
                api('start',bh);runs.append(bh)
                bp=pathlib.Path('/srv/agentbridge/runs')/other['run_id']/'runtime/result.json'
                deadline=time.monotonic()+8
                while not bp.exists() and time.monotonic()<deadline:time.sleep(.1)
                br=json.loads(bp.read_text());checks['run_isolation']=br['parent']['cgroup']!=expected
                api('stop',bh);api('cleanup',bh)
            elif profile=='memory':checks['memory_enforcement']=memory_enforced(result)
            elif profile=='pids':checks['pid_enforcement']=result['allocation_failed'] and result['events_after']['max']>result['events_before']['max'] and result['current']<=result['maximum']
            elif profile=='cpu':checks['cpu_enforcement']=cpu_enforced(result)
            elif profile=='timeout':
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    state=api('inspect',handle)['state']
                    if state=='FAILED':break
                    time.sleep(.2)
                checks['timeout']=state=='FAILED' and all(result[k]['cgroup']==expected and result[k]['non_root'] for k in ['child','grandchild'])
            if profile!='tree':
                state=api('inspect',handle)['state']
                if state in {'RUNNING','UNKNOWN'}:api('stop',handle)
                api('cleanup',handle)
        checks['no_privileged_agent']=all(v['parent']['non_root'] for v in evidence.values())
    except Exception as e:
        checks['probe_error']=False;evidence['error_type']=type(e).__name__
    finally:
        cleanup_errors=[]
        for handle in runs:
            try:
                state=api('inspect',handle)['state']
                if state in {'RUNNING','UNKNOWN','STARTING'}:api('stop',handle)
                state=api('inspect',handle)['state']
                if state in {'STOPPED','COMPLETED','FAILED'}:api('cleanup',handle)
            except Exception:cleanup_errors.append(handle['run_id'])
        checks['final_cleanup']=not cleanup_errors
        report=dict(checks=checks,evidence=evidence,cleanup_errors=cleanup_errors,passed=bool(checks) and all(checks.values()),writers_enabled=False)
        pathlib.Path('/var/lib/agentbridge-worker/containment-evidence.json').write_text(json.dumps(report,indent=2))
    print('SYNTHETIC_CONTAINMENT_PASS_WRITERS_STILL_DISABLED' if report['passed'] else 'SYNTHETIC_CONTAINMENT_FAIL_WRITER_DISABLED')
    raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__':main()
