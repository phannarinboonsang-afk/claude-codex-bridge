#!/usr/bin/env python3
import ast
import json
import pathlib
import os
import re
import subprocess
import sys
from provision import verify_bundle

verify_bundle()
root=pathlib.Path(__file__).resolve().parent
for p in root.rglob('*.py'):ast.parse(p.read_text(encoding='utf8'))
for p in root.rglob('*.json'):json.loads(p.read_text(encoding='utf8'))
test_run=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(root/'tests'),'-v'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)
print(test_run.stdout,end='')
if test_run.returncode:raise SystemExit(test_run.returncode)
match=re.search(r'Ran (\d+) tests?',test_run.stdout)
if not match:raise SystemExit('FAIL:unittest_count_unavailable')
report=pathlib.Path(os.environ.get('AGENTBRIDGE_TEST_RESULTS','/tmp/agentbridge-offline-tests.json'))
report.write_text(json.dumps({'python_unit_test_count':int(match.group(1))})+'\n',encoding='utf8')
print('OFFLINE_HASH_SYNTAX_TESTS_PASS; no Linux/runtime/cloud acceptance claimed')
