#!/usr/bin/python3
"""Pinned downloads; no curl-pipe-shell, npm lifecycle scripts, or credentials."""
import hashlib
import json
import os
import pathlib
import shutil
import tarfile
import tempfile
import urllib.request

def main():
    if os.geteuid()!=0:raise RuntimeError('guest_admin_only')
    manifest=json.loads(pathlib.Path('/etc/agentbridge-worker/toolchain.json').read_text())
    root=pathlib.Path('/opt/agentbridge-tools');root.mkdir(mode=0o755,exist_ok=True)
    if root.is_symlink():raise RuntimeError('tools_symlink')
    marker=root/'installed.json'
    if marker.exists():
        if json.loads(marker.read_text())!=manifest:raise RuntimeError('toolchain_version_conflict')
        return
    (root/'bin').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='agentbridge-tools-') as tmp:
        for tool,item in manifest.items():
            target=pathlib.Path(tmp)/tool
            with urllib.request.urlopen(item['url'],timeout=90) as src,open(target,'wb') as dest:
                total=0
                while chunk:=src.read(1048576):
                    total+=len(chunk)
                    if total>400*1048576:raise RuntimeError('download_size_limit')
                    dest.write(chunk)
            with open(target,'rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
            if actual!=item['sha256']:raise RuntimeError('toolchain_hash_mismatch')
            if tool=='node':
                with tarfile.open(target) as archive:archive.extractall(tmp,filter='data')
                extracted=pathlib.Path(tmp)/('node-v'+item['version']+'-linux-x64')
                if (root/'node').exists():raise RuntimeError('partial_node_install_requires_review')
                shutil.move(str(extracted),str(root/'node'))
                for name in ['node','npm','npx']:(root/'bin'/name).symlink_to('../node/bin/'+name)
            elif tool=='codex':
                with tarfile.open(target) as archive:
                    members=[m for m in archive.getmembers() if m.isfile() and pathlib.PurePosixPath(m.name).name=='codex-x86_64-unknown-linux-musl']
                    if len(members)!=1:raise RuntimeError('codex_archive_layout')
                    with archive.extractfile(members[0]) as src,open(root/'bin/codex','xb') as dst:shutil.copyfileobj(src,dst)
            elif tool=='claude':shutil.copyfile(target,root/'bin/claude')
            else:raise RuntimeError('unknown_tool')
        for name in ['codex','claude']:(root/'bin'/name).chmod(0o755)
    marker.write_text(json.dumps(manifest,sort_keys=True));marker.chmod(0o644)

if __name__=='__main__':main()
