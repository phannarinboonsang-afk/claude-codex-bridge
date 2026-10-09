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
