#!/usr/bin/python3
"""Guest owner setup only. Accepts a dedicated tunnel PUBLIC key, never private key."""
import os
import pathlib
import re
import subprocess
import sys

def main():
    if os.geteuid()!=0 or len(sys.argv)!=2:raise SystemExit('usage: guest root configure-tunnel.py PUBLIC_KEY_FILE')
    key=pathlib.Path(sys.argv[1]).read_text().strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [^\r\n]*)?',key):raise SystemExit('dedicated_ed25519_public_key_required')
    import pwd
    try:pwd.getpwnam('agentbridge-tunnel')
    except KeyError:subprocess.run(['useradd','--system','--user-group','--home-dir','/var/lib/agentbridge-tunnel','--shell','/usr/sbin/nologin','agentbridge-tunnel'],check=True)
    home=pathlib.Path('/var/lib/agentbridge-tunnel');ssh=home/'.ssh'
    if home.is_symlink() or ssh.is_symlink():raise SystemExit('symlink_boundary')
    ssh.mkdir(mode=0o700,parents=True,exist_ok=True)
    auth=ssh/'authorized_keys'
    if auth.is_symlink():raise SystemExit('symlink_boundary')
    content='restrict,port-forwarding,permitopen="127.0.0.1:8765",command="/usr/bin/false" '+key+'\n'
    if auth.exists() and auth.read_text()!=content:raise SystemExit('existing_tunnel_key_requires_explicit_rotation')
    auth.write_text(content);auth.chmod(0o600)
    user=pwd.getpwnam('agentbridge-tunnel');os.chown(home,user.pw_uid,user.pw_gid);os.chown(ssh,user.pw_uid,user.pw_gid);os.chown(auth,user.pw_uid,user.pw_gid)
    config=pathlib.Path('/etc/ssh/sshd_config.d/60-agentbridge-tunnel.conf')
    text='Match User agentbridge-tunnel\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    AllowTcpForwarding local\n    PermitOpen 127.0.0.1:8765\n    AllowAgentForwarding no\n    X11Forwarding no\n    PermitTTY no\n    MaxSessions 0\nMatch all\n'
    if config.is_symlink() or (config.exists() and config.read_text()!=text):raise SystemExit('unexpected_existing_ssh_config')
    config.write_text(text);config.chmod(0o644)
    subprocess.run(['/usr/sbin/sshd','-t'],check=True)
    subprocess.run(['/usr/bin/systemctl','reload','ssh.service'],check=True)
    print('GUEST_TUNNEL_CONFIGURED_API_REMAINS_LOOPBACK_ONLY')

if __name__=='__main__':main()
