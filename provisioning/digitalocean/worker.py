#!/usr/bin/python3
"""Root-owned, loopback-only worker controller. No generic command interface."""
import hashlib
import hmac
import http.server
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = pathlib.Path('/srv/agentbridge')
LIB = '/opt/agentbridge-worker'
OPS = {'create','start','inspect','cancel','stop','collect','cleanup'}
ID = re.compile(r'[a-f0-9]{32}\Z')
PROFILES = {'synthetic': {'tree','memory','pids','cpu','timeout'},
            'codex': {'codex-task'}, 'claude': {'claude-task'}}
ENV = {'PATH':'/opt/agentbridge-tools/bin:/usr/bin:/bin','HOME':'/nonexistent',
       'LANG':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
       'GIT_TERMINAL_PROMPT':'0','HTTPS_PROXY':'http://127.0.0.1:3128',
       'HTTP_PROXY':'http://127.0.0.1:3128','ALL_PROXY':'http://127.0.0.1:3128'}

class Denied(Exception): pass

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)

def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def check_id(value):
    if not isinstance(value,str) or not ID.fullmatch(value): raise Denied('invalid_run_id')
    return value

def safe_path(run_id):
    p=ROOT/'runs'/check_id(run_id)
    for part in [ROOT,ROOT/'runs',p]:
        if part.is_symlink(): raise Denied('symlink_boundary')
    return p

class Policy:
    def __init__(self,data): self.data=data
    def validate(self,r):
        keys={'run_id','repository','base_commit','agent','profile','task','limits'}
        if not isinstance(r,dict) or set(r) not in (keys,keys|{'previous_run_id'}):raise Denied('invalid_fields')
        r=json.loads(canonical(r));check_id(r['run_id'])
        if 'previous_run_id' in r:
            check_id(r['previous_run_id'])
            if r['previous_run_id']==r['run_id']:raise Denied('invalid_predecessor')
        if not isinstance(r['repository'],str) or r['repository'] not in self.data['repositories']:raise Denied('repository_denied')
        if not isinstance(r['base_commit'],str) or not re.fullmatch('[a-f0-9]{40}',r['base_commit']):raise Denied('invalid_commit')
        if not isinstance(r['agent'],str) or r['agent'] not in PROFILES or r['profile'] not in PROFILES[r['agent']]:raise Denied('profile_denied')
        if r['agent']!='synthetic' and not self.data.get('writers_enabled',False):raise Denied('WRITER_DISABLED')
        if not isinstance(r['task'],str) or not 1<=len(r['task'].encode())<=16384 or '\0' in r['task']:raise Denied('invalid_task')
        limits=r['limits'];bounds={'memory_mb':(64,4096),'tasks':(8,256),'cpu_percent':(10,200),'timeout':(5,3600)}
        if not isinstance(limits,dict) or set(limits)!=set(bounds):raise Denied('invalid_limits')
        for key,(low,high) in bounds.items():
            if type(limits[key]) is not int or not low<=limits[key]<=high:raise Denied('invalid_limit_'+key)
        r['worktree']='/srv/agentbridge/runs/'+r['run_id']+'/worktree'
        r['repository_url']=self.data['repositories'][r['repository']]
        tools=pathlib.Path(__file__).with_name('config')/'toolchain.json'
        if not tools.exists():tools=pathlib.Path('/etc/agentbridge-worker/toolchain.json')
        r['toolchain_digest']=digest(json.loads(tools.read_text()))
        r['spec_digest']=digest(r)
        return r
    def source(self,r):
        url=self.data['repositories'][r['repository']]
        if url!=r.get('repository_url'):raise Denied('stale_repository_policy')
        if url=='synthetic:none' and r['agent']=='synthetic':return url
        if not isinstance(url,str) or not re.fullmatch(r'https://[a-zA-Z0-9.-]+/[a-zA-Z0-9_./-]+\.git',url) or '..' in url:raise Denied('invalid_repository_policy')
        return url

class Store:
    def __init__(self,root):
        pathlib.Path(root).mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(pathlib.Path(root)/'tasks.db'))
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,spec TEXT NOT NULL,digest TEXT NOT NULL,key TEXT UNIQUE NOT NULL,state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL);')
    def event(self,rid,state):self.db.execute('INSERT INTO events(id,state,at) VALUES(?,?,?)',(rid,state,time.time()))
    def create(self,r,key):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',key):raise Denied('invalid_idempotency_key')
        old=self.db.execute('SELECT id,digest FROM runs WHERE key=?',(key,)).fetchone()
        if old:
            if old!=(r['run_id'],r['spec_digest']):raise Denied('idempotency_conflict')
            return self.inspect(old[0])
        if self.db.execute('SELECT 1 FROM runs WHERE id=?',(r['run_id'],)).fetchone():raise Denied('duplicate_run')
        with self.db:
            self.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(r['run_id'],canonical(r),r['spec_digest'],key,'CREATED'));self.event(r['run_id'],'CREATED')
        return self.inspect(r['run_id'])
    def inspect(self,rid):
        check_id(rid);row=self.db.execute('SELECT spec,digest,state FROM runs WHERE id=?',(rid,)).fetchone()
        if not row:raise Denied('unregistered_run')
        r=json.loads(row[0]);claimed=r.pop('spec_digest',None)
        if digest(r)!=claimed or claimed!=row[1]:raise Denied('stale_registration')
        r['spec_digest']=claimed
        events=[dict(seq=x[0],state=x[1],at=x[2]) for x in self.db.execute('SELECT seq,state,at FROM events WHERE id=? ORDER BY seq',(rid,))]
        return dict(spec=r,state=row[2],events=events)
    def state(self,rid,state):
        with self.db:self.db.execute('UPDATE runs SET state=? WHERE id=?',(state,rid));self.event(rid,state)
    def reserve(self,rid):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.inspect(rid)['state']!='CREATED':raise Denied('start_state_denied')
            if self.db.execute("SELECT 1 FROM runs WHERE state IN ('STARTING','RUNNING','UNKNOWN')").fetchone():raise Denied('concurrent_writer_denied')
            self.db.execute("UPDATE runs SET state='STARTING' WHERE id=?",(rid,));self.event(rid,'STARTING');self.db.commit()
        except Exception:self.db.rollback();raise
    def recover(self):
        ids=[r[0] for r in self.db.execute("SELECT id FROM runs WHERE state IN ('STARTING','RUNNING')")]
        for rid in ids:self.state(rid,'UNKNOWN')

def command(r):
    return ['/usr/bin/python3',LIB+'/runner.py',r['run_id'],r['agent'],r['profile'],r['spec_digest']]

def unit_name(rid):return 'agentbridge-run-'+check_id(rid)+'.service'

def unit_text(r):
    l=r['limits'];check_id(r['run_id']);path='/srv/agentbridge/runs/'+r['run_id']
    credential=[] if r['agent']=='synthetic' else ['LoadCredential=model:/etc/agentbridge-worker/model-auth/'+r['agent']+'.json']
    return '\n'.join(['[Unit]','Description=AgentBridge isolated run','[Service]']+credential+[
        'Type=exec','User=agentbridge','Group=agentbridge','UMask=0077',
        'ExecStart='+' '.join(command(r)),'WorkingDirectory='+path+'/worktree',
        'MemoryMax='+str(l['memory_mb'])+'M','MemorySwapMax=0','TasksMax='+str(l['tasks']),
        'CPUQuota='+str(l['cpu_percent'])+'%','RuntimeMaxSec='+str(l['timeout']),
        'TimeoutStopSec=10','KillMode=control-group','SendSIGKILL=yes','Restart=no',
        'NoNewPrivileges=yes','CapabilityBoundingSet=','AmbientCapabilities=',
        'ProtectSystem=strict','ProtectHome=yes','PrivateTmp=yes','PrivateDevices=yes',
        'ProtectKernelTunables=yes','ProtectKernelModules=yes','ProtectControlGroups=yes',
        'RestrictSUIDSGID=yes','LockPersonality=yes','RestrictRealtime=yes',
        'ReadWritePaths='+path,'StandardOutput=null','StandardError=null',''])

def run(argv,timeout=30):
    try:r=subprocess.run(argv,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired):raise Denied('operation_unavailable')
    if r.returncode:raise Denied('operation_failed')
    if len(r.stdout)>1048576:raise Denied('output_limit')
    return r.stdout

def precheck():
    if not pathlib.Path('/sys/fs/cgroup/cgroup.controllers').is_file():raise Denied('WRITER_DISABLED:not_unified_v2')
    mounts=pathlib.Path('/proc/mounts').read_text()
    if any(line.split()[2]=='cgroup' for line in mounts.splitlines()):raise Denied('WRITER_DISABLED:hybrid')
    controllers=set(pathlib.Path('/sys/fs/cgroup/cgroup.controllers').read_text().split())
    if not {'cpu','memory','pids'}<=controllers:raise Denied('WRITER_DISABLED:controllers')
    version=int(run(['/usr/bin/systemctl','--version']).decode().split()[1])
    if version<249:raise Denied('WRITER_DISABLED:systemd')

class Controller:
    def __init__(self,policy,store):self.policy=policy;self.store=store
    def status(self,rid):
        data=run(['/usr/bin/systemctl','show',unit_name(rid),'-p','LoadState','-p','ExecMainStartTimestampMonotonic','-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecMainStatus','-p','Result','-p','ControlGroup']).decode()
        return dict(line.split('=',1) for line in data.splitlines() if '=' in line)
    def assert_empty(self,rid):
        s=self.status(rid)
        if s.get('MainPID','0')!='0' or s.get('ActiveState') in {'active','activating','deactivating'}:raise Denied('run_not_empty')
        cg=s.get('ControlGroup','')
        if cg:
            if not re.fullmatch(r'/system.slice/agentbridge-run-'+rid+r'\.service',cg):raise Denied('unexpected_cgroup')
            p=pathlib.Path('/sys/fs/cgroup'+cg)
            if p.exists() and any(x.read_text().strip() for x in p.rglob('cgroup.procs')):raise Denied('descendants_remain')
    def prepare(self,r):
        p=safe_path(r['run_id'])
        if p.exists():raise Denied('workspace_exists')
        p.mkdir(mode=0o755);p.chmod(0o755)
        (p/'worktree').mkdir();(p/'runtime').mkdir();(p/'artifacts').mkdir()
        import pwd
        uid=pwd.getpwnam('agentbridge').pw_uid
        for child in p.iterdir():os.chown(child,uid,uid)
        task=p/'runtime'/'task.txt';task.write_text(r['task']);os.chown(task,uid,uid);task.chmod(0o400)
        self.policy.source(r)
        registration=p/'runtime'/'registration.json';registration.write_text(canonical(r));registration.chmod(0o444)
        # Source acquisition executes inside the same sandbox/cgroup in runner.py.
    def predecessor(self,r):
        if 'previous_run_id' not in r:return
        previous=self.store.inspect(r['previous_run_id'])
        if previous['state']!='COMPLETED' or any(previous['spec'][key]!=r[key] for key in ('repository','repository_url','base_commit')):
            raise Denied('predecessor_not_approved')
        self.assert_empty(r['previous_run_id'])
        path=safe_path(r['previous_run_id'])/'runtime'/'result.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size>262144:raise Denied('invalid_predecessor_result')
        value=json.loads(path.read_text()).get('commit',{})
        commit=value.get('stdout','').strip()
        if value.get('exit_code')!=0 or not re.fullmatch('[a-f0-9]{40}',commit):raise Denied('invalid_predecessor_commit')
        if 'previous_commit' in r and r['previous_commit']!=commit:raise Denied('stale_predecessor')
        return commit
    def dispatch(self,op,body):
        if op not in OPS:raise Denied('operation_denied')
        if op=='create':
            if set(body)!={'spec','idempotency_key'}:raise Denied('invalid_fields')
            spec=self.policy.validate(body['spec'])
            old=self.store.db.execute('SELECT id FROM runs WHERE key=?',(body['idempotency_key'],)).fetchone()
            previous=(self.store.inspect(old[0])['spec'].get('previous_commit') if old else self.predecessor(spec))
            if previous:
                spec['previous_commit']=previous
                spec.pop('spec_digest');spec['spec_digest']=digest(spec)
            return self.store.create(spec,body['idempotency_key'])
        if set(body)!={'run_id','spec_digest'}:raise Denied('invalid_fields')
        rid=check_id(body['run_id']);record=self.store.inspect(rid);r=record['spec']
        if body['spec_digest']!=r['spec_digest']:raise Denied('stale_request')
        if op=='start':
            if record['state'] in {'STARTING','RUNNING','UNKNOWN','COMPLETED','FAILED'}:
                return record
            self.predecessor(r)
            precheck()
            tools=pathlib.Path('/etc/agentbridge-worker/toolchain.json')
            if tools.is_file() and digest(json.loads(tools.read_text()))!=r['toolchain_digest']:raise Denied('stale_toolchain_registration')
            if r['agent']!='synthetic':
                if not self.policy.data.get('writers_enabled',False):raise Denied('WRITER_DISABLED:policy')
                self.policy.source(r)
                marker=pathlib.Path('/var/lib/agentbridge-worker/writer-readiness.json')
                if not marker.is_file() or marker.is_symlink():raise Denied('WRITER_DISABLED:acceptance_pending')
                ready=json.loads(marker.read_text())
                required={'containment','egress','source','toolchain','authenticated_channel','orchestrator_recovery'}
                if set(ready.get('gates',{}))!=required or any(v is not True for v in ready['gates'].values()) or ready.get('boot_id')!=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise Denied('WRITER_DISABLED:acceptance_pending')
            self.store.reserve(rid)
            try:
                self.prepare(r)
                unit=pathlib.Path('/run/systemd/system')/unit_name(rid)
                if unit.exists() or unit.is_symlink():raise Denied('unit_exists')
                unit.write_text(unit_text(r));unit.chmod(0o644)
                run(['/usr/bin/systemctl','daemon-reload']);run(['/usr/bin/systemctl','start',unit_name(rid)])
                self.store.state(rid,'RUNNING')
            except Exception:
                self.store.state(rid,'UNKNOWN');raise
        elif op in {'stop','cancel'}:
            if record['state']=='CREATED':
                self.store.state(rid,'STOPPED');return self.store.inspect(rid)
            if record['state'] in {'CLEANED','STOPPED','COMPLETED','FAILED'}:return record
            run(['/usr/bin/systemctl','stop',unit_name(rid)]);self.assert_empty(rid);self.store.state(rid,'STOPPED')
        elif op=='cleanup':
            if record['state'] not in {'STOPPED','COMPLETED','FAILED'}:raise Denied('cleanup_state_denied')
            dependents=self.store.db.execute("SELECT spec FROM runs WHERE state IN ('CREATED','STARTING','RUNNING','UNKNOWN')").fetchall()
            if any(json.loads(row[0]).get('previous_run_id')==rid for row in dependents):raise Denied('predecessor_in_use')
            self.assert_empty(rid)
            unit=pathlib.Path('/run/systemd/system')/unit_name(rid)
            if unit.is_symlink():raise Denied('unit_symlink')
            unit.unlink(missing_ok=True);run(['/usr/bin/systemctl','daemon-reload'])
            p=safe_path(rid)
            if p.exists():shutil.rmtree(p)
            self.store.state(rid,'CLEANED')
        elif op=='collect':
            self.assert_empty(rid)
            p=safe_path(rid)/'runtime'/'result.json'
            if p.is_symlink() or not p.is_file() or p.stat().st_size>262144:raise Denied('invalid_result')
            return dict(registration=record,result=json.loads(p.read_text()),trusted=False)
        elif op=='inspect' and record['state'] in {'RUNNING','UNKNOWN'}:
            status=self.status(rid)
            executed=(status.get('LoadState')=='loaded' and
                      int(status.get('ExecMainStartTimestampMonotonic','0'))>0 and
                      any(e['state']=='RUNNING' for e in record['events']))
            if executed and status.get('ActiveState') in {'inactive','failed'} and status.get('Result'):
                self.assert_empty(rid)
                state='COMPLETED' if status['Result']=='success' and status.get('ExecMainStatus')=='0' else 'FAILED'
                self.store.state(rid,state)
        return self.store.inspect(rid)

def handler_type(controller,token):
    if len(token)<32:raise Denied('invalid_authentication_configuration')
    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                auth=self.headers.get('Authorization','').encode()
                if not hmac.compare_digest(auth,b'Bearer '+token):raise Denied('unauthorized')
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=32768 or self.headers.get('Transfer-Encoding'):raise Denied('invalid_body')
                body=json.loads(self.rfile.read(n));op=self.path.removeprefix('/v1/')
                if self.path!='/v1/'+op:raise Denied('operation_denied')
                result=controller.dispatch(op,body);code=200
            except Denied as e:code=403;result={'error':str(e)}
            except Exception:code=400;result={'error':'invalid_request_or_operation_failed'}
            data=canonical(result).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    return Handler

def serve():
    if os.geteuid()!=0:raise Denied('root_controller_required')
    policy=Policy(json.loads(pathlib.Path('/etc/agentbridge-worker/policy.json').read_text()))
    token=pathlib.Path('/etc/agentbridge-worker/api-token').read_bytes().strip()
    store=Store('/var/lib/agentbridge-worker');store.recover();controller=Controller(policy,store)
    server=http.server.HTTPServer(('127.0.0.1',8765),handler_type(controller,token));server.serve_forever()

if __name__=='__main__':
    try:
        if sys.argv[1:] == ['precheck']:precheck();print('CGROUP_V2_PRECHECK_PASS_WRITERS_STILL_DISABLED')
        elif sys.argv[1:] == ['serve']:serve()
        else:raise Denied('operation_denied')
    except Denied as e:print('AGENTBRIDGE_WORKER_ERROR:'+str(e),file=sys.stderr);sys.exit(1)
