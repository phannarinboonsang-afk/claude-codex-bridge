#!/usr/bin/python3
import os
import pathlib
import subprocess

def main():
    if os.geteuid()!=0:raise RuntimeError('guest_admin_only')
    rules=pathlib.Path('/etc/agentbridge-worker/egress.nft').read_text()
    exists=subprocess.run(['/usr/sbin/nft','list','table','inet','agentbridge_egress'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    batch=('delete table inet agentbridge_egress\n' if exists else '')+rules
    subprocess.run(['/usr/sbin/nft','-f','-'],input=batch.encode(),check=True)

if __name__=='__main__':main()
