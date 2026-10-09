#!/usr/bin/env python3
"""Fail-closed scan of staged text files for credential material and host paths."""
import pathlib
import ipaddress
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PRIVATE_HEADER = "-----BEGIN " + r"(?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----"
PRODUCTION_ROOT = "/" + "mnt/ssd/"
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
SYNTHETIC_ADDRESSES = {'tests/test_regressions.py': {'10.0.0.1', '169.254.169.254'},
                       'tests/test_security.py': {'192.168.1.0'}}
PATTERNS = [
    re.compile(PRIVATE_HEADER),
    re.compile(r"\bdop" + r"_v1_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-(?:proj-|ant-)[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)authorization[\"']?\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9_-]{24,}"),
    re.compile(r"(?i)bridge[_-]bearer[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_-]{16,}"),
]
FORBIDDEN_NAMES = {"id_rsa", "id_ed25519", "owner.json", ".env", "credentials.json"}
failures = []
for path in ROOT.rglob("*"):
    if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
        continue
    if path.is_symlink():
        failures.append(str(path.relative_to(ROOT)) + ":symlink")
        continue
    if path.name.lower() in FORBIDDEN_NAMES:
        failures.append(str(path.relative_to(ROOT)) + ":secret_filename")
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    if "\ufffd" in text:
        failures.append(str(path.relative_to(ROOT)) + ":non_utf8_content")
    if PRODUCTION_ROOT in text:
        failures.append(str(path.relative_to(ROOT)) + ":host_specific_value")
    for value in IPV4.findall(text):
        try: address = ipaddress.ip_address(value)
        except ValueError: continue
        documentation = any(address in ipaddress.ip_network(net) for net in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24'))
        if address.is_private and not address.is_loopback and not address.is_unspecified and not documentation:
            if value not in SYNTHETIC_ADDRESSES.get(path.relative_to(ROOT).as_posix(), set()) and path.name != 'secret-scan.py':
                failures.append(str(path.relative_to(ROOT)) + ':private_host_address')
    for pattern in PATTERNS:
        if pattern.search(text):
            failures.append(str(path.relative_to(ROOT)) + ":credential_pattern")
if failures:
    print("SECRET_SCAN_FAIL:" + ",".join(failures), file=sys.stderr)
    raise SystemExit(1)
print("SECRET_SCAN_PASS")
