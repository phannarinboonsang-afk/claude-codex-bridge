"""CI-only synthetic namespace probe; never uses repository data or credentials."""
import json
import os
import subprocess
import sys

command = [
    "/usr/bin/bwrap", "--die-with-parent", "--unshare-net", "--unshare-pid",
    "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
    "--ro-bind", "/bin", "/bin", "--proc", "/proc", "--dev", "/dev",
    "/usr/bin/git", "--version",
]
if os.path.isdir("/lib64"):
    command[command.index("/usr/bin/git"):command.index("/usr/bin/git")] = [
        "--ro-bind", "/lib64", "/lib64",
    ]
success = False
try:
    result = subprocess.run(command, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
except OSError as error:
    print(json.dumps({"executable_exists": os.path.isfile(command[0]),
                      "exception": type(error).__name__, "errno": error.errno,
                      "reason": os.strerror(error.errno) if error.errno else "unknown"}))
except subprocess.TimeoutExpired:
    print(json.dumps({"exception": "TimeoutExpired"}))
else:
    success = result.returncode == 0
    # This command has fixed arguments and synthetic input only. Runtime broker
    # responses must never expose arbitrary Git/subprocess stderr.
    print(json.dumps({"returncode": result.returncode,
                      "stdout": result.stdout.decode(errors="replace")[:2000],
                      "stderr": result.stderr.decode(errors="replace")[:2000]}))
if "--require" in sys.argv and not success:
    raise SystemExit(1)
if "--require" in sys.argv:
    # Validate the CI policy drops child capabilities while keeping the actual
    # network/PID namespace boundary. This contains no user-provided command.
    host_net = os.readlink("/proc/self/ns/net")
    host_pid = os.readlink("/proc/self/ns/pid")
    probe = '''
import ctypes, json, os
status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
libc = ctypes.CDLL(None, use_errno=True)
result = libc.unshare(0x40000000)
print(json.dumps({"net": os.readlink("/proc/self/ns/net"),
                  "pid": os.readlink("/proc/self/ns/pid"),
                  "capabilities": int(status["CapEff"].strip(), 16),
                  "new_net_denied": result == -1 and ctypes.get_errno() == 1}))
'''
    child = subprocess.run(command[:-2] + ["/usr/bin/python3", "-c", probe],
                           capture_output=True, timeout=10,
                           env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
    if child.returncode:
        print(json.dumps({"child_probe_returncode": child.returncode,
                          "stderr": child.stderr.decode(errors="replace")[:2000]}))
        raise SystemExit(1)
    evidence = json.loads(child.stdout)
    checks = {"host_user_non_root": os.getuid() != 0,
              "network_isolated": evidence["net"] != host_net,
              "pid_isolated": evidence["pid"] != host_pid,
              "child_capabilities_zero": evidence["capabilities"] == 0,
              "child_new_network_namespace_denied": evidence["new_net_denied"]}
    print(json.dumps(checks, sort_keys=True))
    if not all(checks.values()):
        raise SystemExit(1)
