"""Fail closed on credential/host literals and unexpected backend dependencies.

Only exact, intentionally invalid pre-existing security-test specimens are
exempted. Scan Git's tracked set, so ignored runtime files are never candidates.
"""
import ast
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
FAKE_KEYS={
    'tests/conftest.py': ('sk-ant-api03-ABCDEFGHIJKLMNOP1234',),
    'tests/test_bridge.py': ('-----BEGIN RSA PRIVATE KEY-----',),
}
SYNTHETIC_NETWORK_FIXTURES={
    'provisioning/digitalocean/tests/test_regressions.py':{'10.0.0.1','169.254.169.254','192.0.2.8'},
    'provisioning/digitalocean/tests/test_security.py':{'192.168.1.0'},
}
KEY=re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----|\b(?:sk-proj-|sk-ant-api03-|dop_v1_)[A-Za-z0-9_-]{20,}')
PRIVATE_PATH=re.compile(r"/mnt/ssd/|(?:C:|/C:)[/\\]Users[/\\]|/home/(?!synthetic\b|private\b|\*|\.\.|['\"])")
IP=re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
APPROVED_THIRD_PARTY={'flask','requests','mcp','pydantic','uvicorn','werkzeug'}


def check():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    errors=[];network=[];dependencies=[]
    for name in filter(None,files):
        p=ROOT/name
        if not p.is_file() or p.is_symlink():errors.append((name,'invalid_public_file'));continue
        data=p.read_bytes()
        if b'\0' in data:errors.append((name,'unexpected_binary'));continue
        text=data.decode('utf8')
        for line_no,line in enumerate(text.splitlines(),1):
            for match in KEY.finditer(line):
                allowed = FAKE_KEYS.get(name,())
                if name=='scripts/public_safety_check.py':allowed=tuple(value for values in FAKE_KEYS.values() for value in values)
                if match.group() not in allowed:errors.append((name,line_no,'credential_pattern'))
            if PRIVATE_PATH.search(line):
                safe=name=='scripts/public_safety_check.py' and line.startswith('PRIVATE_PATH=')
                if not safe:errors.append((name,line_no,'private_host_path'))
            for match in IP.finditer(line):
                try:address=ipaddress.ip_address(match.group())
                except ValueError:continue
                # Private deny-ranges are intentional egress policy, not host identity.
                if address.is_private and not address.is_loopback and not address.is_unspecified:
                    classification=''
                    if match.group() in SYNTHETIC_NETWORK_FIXTURES.get(name,set()):classification='synthetic rejection fixture'
                    elif name.startswith('provisioning/digitalocean/') and ('/' in line or 'deny' in line.lower() or 'metadata' in line.lower()):classification='documented denied network range'
                    elif name.endswith('package-lock.json'):classification='package version literal'
                    elif name=='scripts/public_safety_check.py':classification='explicit synthetic scan exemption'
                    else:errors.append((name,line_no,'private_address'))
                    network.append({'file':name,'line':line_no,'value':match.group(),'classification':classification})
        if name.startswith(('backend/','worker/')) and name.endswith('.py'):
            for node in ast.walk(ast.parse(text)):
                modules=[item.name for item in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) and node.level==0 else []
                for module in modules:
                    top=module.split('.')[0]
                    if top not in sys.stdlib_module_names|APPROVED_THIRD_PARTY|{'backend','worker'}:
                        errors.append((name,'unapproved_dependency',module))
                    dependencies.append({'file':name,'module':module})
    provenance=json.loads((ROOT/'docs/source-provenance.json').read_text())
    for item in provenance:
        if hashlib.sha256((ROOT/item['TARGET_PATH']).read_bytes()).hexdigest()!=item['target_sha256']:
            errors.append((item['TARGET_PATH'],'provenance_digest_mismatch'))
    print(json.dumps({'files_reviewed':len(list(filter(None,files))),'errors':errors,
        'host_specific_address_matches':network,'imports_checked':len(dependencies)},indent=2))
    return bool(errors)


if __name__=='__main__':raise SystemExit(check())
