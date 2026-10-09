#!/usr/bin/python3
"""Fixed synthetic runner, executed only as agentbridge inside a run service."""
import json
import os
import pathlib
import subprocess
import sys
import time

def membership():
    lines=pathlib.Path('/proc/self/cgroup').read_text().splitlines()
    if len(lines)!=1 or not lines[0].startswith('0::/system.slice/agentbridge-run-'):raise RuntimeError('not_contained')
    return lines[0][3:]

def proof():
    cg=membership()
    return dict(pid=os.getpid(),uid=os.getuid(),cgroup=cg,non_root=os.getuid()!=0)

def control(name):
    return (pathlib.Path('/sys/fs/cgroup'+membership())/name).read_text().strip()

def counters(name):
    return {k:int(v) for k,v in (line.split() for line in control(name).splitlines())}

def write_result(result):
    p=pathlib.Path('/runtime/result.json');temp=p.with_suffix('.tmp')
    result=bounded_result(result)
    temp.write_text(json.dumps(result));os.replace(temp,p)

def bounded_result(result):
    # Budget the actual serialized JSON, including escaping, not each stream alone.
    result=json.loads(json.dumps(result))
    def trim(value,limit):
        if isinstance(value,dict):
            for k,v in list(value.items()):
                if k in {'stdout','stderr','text'} and isinstance(v,str) and len(v)>limit:
                    value[k]=v[:limit];value['truncated']=True
                else:trim(v,limit)
        elif isinstance(value,list):
            for v in value:trim(v,limit)
    limit=16384
    while len(json.dumps(result).encode())>131072:
        limit//=2
        if limit<16:raise RuntimeError('result_metadata_limit')
        trim(result,limit)
    return result

def synthetic(profile):
    result={'parent':proof(),'profile':profile,'limits':{n:control(n) for n in ['memory.max','memory.swap.max','pids.max','cpu.max']}}
    children=[]
    if profile=='tree':
        child=subprocess.Popen(['/usr/bin/python3','/worker/runner.py','child-tree'])
        children.append(child)
        for _ in range(100):
            if pathlib.Path('/runtime/grandchild.json').exists():break
            time.sleep(.05)
        for kind in ['child','grandchild']:
            result[kind]=json.loads(pathlib.Path('/runtime/'+kind+'.json').read_text())
        write_result(result)
        time.sleep(300)
    elif profile=='memory':
        before=counters('memory.events');maximum=int(control('memory.max'))
        for _ in range(2):children.append(subprocess.Popen(['/usr/bin/python3','/worker/runner.py','child-memory',str(maximum)]))
        codes=[]
        for child in children:
            try:codes.append(child.wait(timeout=15))
            except subprocess.TimeoutExpired:child.kill();codes.append(child.wait());result['workload_timed_out']=True
        after=counters('memory.events')
        result.update(events_before=before,events_after=after,requested_bytes=2*maximum,
            peak=int(control('memory.peak')),maximum=maximum,child_exit_codes=codes,
            child_proofs=[json.loads(p.read_text()) for p in pathlib.Path('/runtime').glob('memory-child-*.json')])
        write_result(result)
    elif profile=='pids':
        before=counters('pids.events');maximum=int(control('pids.max'));failure=False
        try:
            for _ in range(maximum+8):children.append(subprocess.Popen(['/usr/bin/python3','/worker/runner.py','sleep']))
        except (BlockingIOError,OSError):failure=True
        current=int(control('pids.current'));after=counters('pids.events')
        for child in children:child.terminate()
        for child in children:child.wait(timeout=5)
        result.update(events_before=before,events_after=after,allocation_failed=failure,current=current,maximum=maximum)
        write_result(result)
    elif profile=='cpu':
        before=counters('cpu.stat');wall=time.monotonic();used=time.process_time()
        while time.monotonic()-wall<5:sum(i*i for i in range(1000))
        result.update(wall_seconds=time.monotonic()-wall,cpu_seconds=time.process_time()-used,
                      stats_before=before,stats_after=counters('cpu.stat'))
        write_result(result)
    elif profile=='timeout':
        synthetic('tree')
    else:raise RuntimeError('profile_denied')

def bounded_command(argv,input_bytes=None):
    # Drain output concurrently so neither pipe blocks or grows without bound.
    import threading
    process=subprocess.Popen(argv,stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    buffers=[bytearray(),bytearray()];truncated=[False,False]
    def drain(stream,index):
        while data:=stream.read(65536):
            remaining=262144-len(buffers[index]);buffers[index].extend(data[:max(0,remaining)])
            if len(data)>remaining:truncated[index]=True
    threads=[threading.Thread(target=drain,args=(stream,i)) for i,stream in enumerate([process.stdout,process.stderr])]
    for t in threads:t.start()
    if input_bytes is not None:
        try:process.stdin.write(input_bytes);process.stdin.close()
        except BrokenPipeError:pass
    code=process.wait()
    for t in threads:t.join()
    process.stdout.close();process.stderr.close()
    return dict(exit_code=code,stdout=bytes(buffers[0]).decode('utf8','replace'),stderr=bytes(buffers[1]).decode('utf8','replace'),truncated=any(truncated))

def checkout_source(registration,git,workspace='/workspace',previous_source='file:///previous'):
    source=previous_source if 'previous_run_id' in registration else registration['repository_url']
    clone=bounded_command(git+['clone','--no-local','--no-checkout','--',source,workspace])
    if clone['exit_code']!=0:raise RuntimeError('source_clone_failed')
    local=git+['-C',workspace]
    target=registration.get('previous_commit',registration['base_commit'])
    checkout=bounded_command(local+['checkout','--detach',target])
    actual=bounded_command(local+['rev-parse','HEAD'])
    ancestry=bounded_command(local+['merge-base','--is-ancestor',registration['base_commit'],'HEAD'])
    if checkout['exit_code']!=0 or ancestry['exit_code']!=0 or actual['stdout'].strip()!=target:raise RuntimeError('source_commit_mismatch')


def agent_task(agent):
    registration=json.loads(pathlib.Path('/runtime/registration.json').read_text())
    # Clone/check out independently within both filesystem and resource boundary.
    os.environ.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_TERMINAL_PROMPT='0',
        HOME='/tmp/home',HTTPS_PROXY='http://127.0.0.1:3128',HTTP_PROXY='http://127.0.0.1:3128',ALL_PROXY='http://127.0.0.1:3128')
    pathlib.Path('/tmp/home').mkdir(mode=0o700)
    git=['/usr/bin/git','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-c','core.pager=cat']
    checkout_source(registration,git)
    creds=json.loads(pathlib.Path('/credentials/model').read_text())
    key='OPENAI_API_KEY' if agent=='codex' else 'ANTHROPIC_API_KEY'
    if set(creds)!={key} or not isinstance(creds[key],str) or not creds[key]:raise RuntimeError('invalid_scoped_model_auth')
    os.environ[key]=creds[key]
    os.environ.update(HOME='/tmp/home',HTTPS_PROXY='http://127.0.0.1:3128',HTTP_PROXY='http://127.0.0.1:3128',ALL_PROXY='http://127.0.0.1:3128',DISABLE_AUTOUPDATER='1',CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    task=pathlib.Path('/runtime/task.txt').read_text()
    argv=['/opt/agentbridge-tools/bin/codex','exec','--json','--sandbox','workspace-write','-'] if agent=='codex' else ['/opt/agentbridge-tools/bin/claude','-p','--output-format','stream-json','--verbose','--permission-mode','acceptEdits']
    result=bounded_command(argv,task.encode())
    os.environ.pop(key,None)
    tests=[]
    if any(pathlib.Path(p).exists() for p in ['pytest.ini','pyproject.toml','tests']):
        tests.append(dict(profile='python-pytest',result=bounded_command(['/usr/bin/python3','-m','pytest','-q','--maxfail=1'])))
    package=pathlib.Path('package.json')
    if package.is_file() and package.stat().st_size<=262144:
        scripts=json.loads(package.read_text()).get('scripts',{})
        for name in ['test','build']:
            if name in scripts:tests.append(dict(profile='npm-'+name,result=bounded_command(['/opt/agentbridge-tools/bin/npm','run',name])))
    artifacts=[];used=0
    for p in pathlib.Path('/artifacts').rglob('*'):
        if p.is_symlink():continue
        if not p.is_file() or p.suffix not in {'.json','.txt','.log','.patch'} or p.stat().st_size>32768:continue
        if len(artifacts)>=8 or used+p.stat().st_size>32768:break
        text=p.read_text(encoding='utf8',errors='replace');used+=p.stat().st_size
        artifacts.append(dict(path=p.relative_to('/artifacts').as_posix(),text=text))
    # Snapshot only in this isolated worktree; never push or apply to production.
    if bounded_command(git+['add','--all'])['exit_code']!=0:raise RuntimeError('checkpoint_failed')
    dirty=bounded_command(git+['diff','--cached','--quiet'])
    if dirty['exit_code']==1:
        committed=bounded_command(git+['-c','user.name=AgentBridge Worker','-c','user.email=worker@example.invalid',
            'commit','--no-gpg-sign','-m','AgentBridge isolated task checkpoint'])
        if committed['exit_code']!=0:raise RuntimeError('checkpoint_failed')
    elif dirty['exit_code']!=0:raise RuntimeError('checkpoint_failed')
    result.update(parent=proof(),agent=agent,tests=tests,
        commit=bounded_command(git+['rev-parse','HEAD']),
        changed_files=bounded_command(git+['diff','--name-only',registration['base_commit'],'HEAD']),
        diff=bounded_command(git+['diff','--no-ext-diff','--no-textconv',registration['base_commit']]),
        artifacts=artifacts,trusted=False)
    # stdout/stderr may contain sensitive or hostile content; collection is owner-scoped.
    # Scrub exact credential values before persisting or returning agent output.
    result=json.loads(json.dumps(result).replace(creds[key],'[REDACTED_MODEL_CREDENTIAL]'))
    write_result(result)
    raise SystemExit(0 if result['exit_code']==0 and all(x['result']['exit_code']==0 for x in tests) else 1)

def main():
    if os.getuid()==0:raise RuntimeError('privileged_agent_denied')
    if sys.argv[1:] == ['child-tree']:
        pathlib.Path('/runtime/child.json').write_text(json.dumps(proof()))
        p=subprocess.Popen(['/usr/bin/python3','/worker/runner.py','grandchild'])
        p.wait();return
    if sys.argv[1:] == ['grandchild']:
        pathlib.Path('/runtime/grandchild.json').write_text(json.dumps(proof()));time.sleep(300);return
    if sys.argv[1:] == ['sleep']:time.sleep(300);return
    if sys.argv[1:2] == ['child-memory']:
        pathlib.Path('/runtime/memory-child-'+str(os.getpid())+'.json').write_text(json.dumps(proof()))
        pathlib.Path('/proc/self/oom_score_adj').write_text('1000')
        blocks=[]
        try:
            for _ in range(int(sys.argv[2])//1048576):blocks.append(bytearray(1048576))
            time.sleep(1)
        except MemoryError:pass
        return
    if sys.argv[1:2] == ['inside']:
        synthetic(sys.argv[2]);return
    if sys.argv[1:2] == ['inside-agent']:
        agent_task(sys.argv[2]);return
    if len(sys.argv)!=5:raise RuntimeError('invalid_runner_arguments')
    rid,agent,profile,expected_digest=sys.argv[1:]
    import worker
    worker.check_id(rid)
    if agent not in worker.PROFILES or profile not in worker.PROFILES[agent]:raise RuntimeError('invalid_profile')
    cg=membership()
    if cg!='/system.slice/'+worker.unit_name(rid):raise RuntimeError('wrong_run_membership')
    base=worker.safe_path(rid)
    registration=json.loads((base/'runtime'/'registration.json').read_text())
    claimed=registration.pop('spec_digest',None)
    if worker.digest(registration)!=claimed or claimed!=expected_digest or registration['agent']!=agent or registration['profile']!=profile:raise RuntimeError('stale_runner_registration')
    if os.getuid()!=__import__('pwd').getpwnam('agentbridge').pw_uid:raise RuntimeError('wrong_agent_identity')
    # Whitelisted filesystem only; no home, root state, sibling runs or credentials.
    argv=['/usr/bin/bwrap','--unshare-user','--unshare-pid','--die-with-parent',
          '--new-session','--cap-drop','ALL','--clearenv','--setenv','PATH','/usr/bin:/bin',
          '--setenv','HOME','/tmp','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin',
          '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
          '--ro-bind','/opt/agentbridge-worker','/worker','--ro-bind','/sys/fs/cgroup','/sys/fs/cgroup',
          '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
          '--ro-bind','/etc/ld.so.cache','/etc/ld.so.cache',
          '--bind',str(base/'worktree'),'/workspace','--bind',str(base/'runtime'),'/runtime',
          '--bind',str(base/'artifacts'),'/artifacts','--chdir','/workspace']
    if agent=='synthetic':argv+=['--unshare-net','/usr/bin/python3','/worker/runner.py','inside',profile]
    else:
        if 'previous_run_id' in registration:
            previous=worker.safe_path(registration['previous_run_id'])/'worktree'
            if previous.is_symlink() or not previous.is_dir():raise RuntimeError('invalid_predecessor_workspace')
            argv+=['--ro-bind',str(previous),'/previous']
        credentials=pathlib.Path(os.environ.get('CREDENTIALS_DIRECTORY','/nonexistent'))/'model'
        if not credentials.is_file():raise RuntimeError('scoped_model_auth_missing')
        argv+=['--ro-bind','/opt/agentbridge-tools','/opt/agentbridge-tools','--setenv','PATH','/opt/agentbridge-tools/bin:/usr/bin:/bin',
            '--dir','/credentials','--ro-bind',str(credentials),'/credentials/model',
            '--ro-bind','/etc/ssl/certs','/etc/ssl/certs','--ro-bind','/etc/resolv.conf','/etc/resolv.conf',
            '/usr/bin/python3','/worker/runner.py','inside-agent',agent]
    os.execv(argv[0],argv)

if __name__=='__main__':
    try:main()
    except Exception:print('RUNNER_FAILED_CLOSED',file=sys.stderr);sys.exit(1)
