#!/usr/bin/env python3
"""Guest admin: export only structured synthetic evidence, never raw logs/secrets."""
import json
import os
import pathlib
import sys
if os.geteuid()!=0:raise SystemExit('guest_admin_only')
p=pathlib.Path('/var/lib/agentbridge-worker/containment-evidence.json')
if p.is_symlink() or p.stat().st_size>1048576:raise SystemExit('invalid_evidence')
e=json.loads(p.read_text());json.dump({k:e[k] for k in ['checks','evidence','cleanup_errors','passed','writers_enabled']},sys.stdout,indent=2)
