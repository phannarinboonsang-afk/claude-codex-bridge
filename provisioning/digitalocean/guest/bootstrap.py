#!/usr/bin/python3
"""Guest only; idempotent synthetic baseline. No model credentials or live agents."""
import json
import os
import pathlib
import pwd
import secrets
import subprocess

def call(args):subprocess.run(args,check=True,stdin=subprocess.DEVNULL)

def main():
    if os.geteuid()!=0:raise RuntimeError('guest_root_required')
    release=dict(line.split('=',1) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('VERSION_ID','').strip('"')!='24.04':raise RuntimeError('guest_release_mismatch')
    if subprocess.check_output(['dpkg','--print-architecture']).strip()!=b'amd64':raise RuntimeError('guest_arch_mismatch')
    call(['apt-get','update'])
    call(['apt-get','install','-y','--no-install-recommends','git','python3.12','python3.12-venv','python3-pip','python3-pytest','build-essential','curl','ca-certificates','xz-utils','bubblewrap','nftables'])
    try:pwd.getpwnam('agentbridge')
    except KeyError:call(['useradd','--system','--user-group','--home-dir','/srv/agentbridge','--shell','/usr/sbin/nologin','agentbridge'])
    user=pwd.getpwnam('agentbridge')
    if user.pw_uid==0 or user.pw_shell!='/usr/sbin/nologin':raise RuntimeError('unsafe_agent_identity')
    try:pwd.getpwnam('agentbridge-proxy')
    except KeyError:call(['useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','agentbridge-proxy'])
    for suffix in ['','runs','repos','artifacts','state']:
        p=pathlib.Path('/srv/agentbridge')/suffix
        if p.is_symlink():raise RuntimeError('symlink_boundary')
        p.mkdir(mode=0o755,parents=True,exist_ok=True);os.chown(p,0,0);p.chmod(0o755)
    # Controller state/auth never belong to the agent identity.
    state=pathlib.Path('/var/lib/agentbridge-worker');state.mkdir(mode=0o700,exist_ok=True);state.chmod(0o700)
    auth=pathlib.Path('/etc/agentbridge-worker/api-token')
    if auth.is_symlink():raise RuntimeError('auth_symlink')
    if not auth.exists():
        fd=os.open(auth,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32)+'\n')
    else:
        if auth.stat().st_uid!=0 or auth.stat().st_mode&0o077:raise RuntimeError('auth_permissions')
    import sys
    sys.path.insert(0,'/opt/agentbridge-worker')
    import worker
    worker.precheck()
    import toolchain
    toolchain.main()
    # No broad nft flush. Replace only this dedicated table on reruns.
    import egress_policy
    egress_policy.main()
    call(['systemctl','daemon-reload']);call(['systemctl','enable','--now','agentbridge-egress-policy.service','agentbridge-egress.service','agentbridge-worker.service'])
    pathlib.Path('/var/lib/agentbridge-worker/bootstrap-status.json').write_text(json.dumps({'status':'SYNTHETIC_ONLY','writers_enabled':False,'toolchain':'PINNED_INSTALLED_RUNTIME_VALIDATION_PENDING'}))
    print('WORKER_BOOTSTRAPPED_SYNTHETIC_ONLY_WRITER_DISABLED')

if __name__=='__main__':main()
