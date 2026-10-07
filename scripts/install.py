#!/usr/bin/env python3
import argparse, os, platform, shutil, subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
default_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config"))/"claude-chatgpt-bridge/bridge.env"
default_unit_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config"))/"systemd/user"
p=argparse.ArgumentParser()
p.add_argument("--prefix",type=Path,default=root); p.add_argument("--config",type=Path,default=default_config)
p.add_argument("--dry-run",action="store_true"); p.add_argument("--start",action="store_true")
a=p.parse_args()
if not a.prefix.is_absolute() or not a.config.is_absolute(): p.error("prefix and config paths must be absolute")
if any(c.isspace() for c in str(a.prefix)+str(a.config)+str(default_unit_dir)): p.error("systemd paths must not contain whitespace")
if platform.system()!="Linux": p.error("user systemd installation requires Linux")
if sys.version_info<(3,11): p.error("Python 3.11 or newer is required")
if not (root/"requirements.lock").is_file(): p.error("incomplete checkout")
unit=default_unit_dir/"claude-chatgpt-bridge.service"
if a.dry_run:
 print(f"dry-run checkout: {a.prefix}"); print(f"dry-run config: {a.config} (preserve if present)")
 print(f"dry-run unit: {unit}"); print("dry-run dependencies: requirements.lock")
 print("dry-run service: enable/start requested" if a.start else "dry-run service: will remain stopped"); raise SystemExit(0)
if a.prefix.resolve()!=root: p.error("--prefix must point to this source checkout")
venv=root/".venv"
subprocess.run([sys.executable,"-m","venv",str(venv)],check=True)
subprocess.run([str(venv/"bin/python"),"-m","pip","install","--disable-pip-version-check","-r",str(root/"requirements.lock")],check=True)
if a.config.exists(): print(f"preserving existing config: {a.config}")
else:
 a.config.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 shutil.copyfile(root/"config.example",a.config); a.config.chmod(0o600)
 print(f"created config template: {a.config}; edit paths before starting")
default_unit_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
template=(root/"deploy/claude-chatgpt-bridge.service").read_text()
unit_text=template.replace("@BRIDGE_HOME@",str(root)).replace("@CONFIG_FILE@",str(a.config)).replace("@PYTHON@",str(venv/"bin/python"))
unit.write_text(unit_text); unit.chmod(0o600)
subprocess.run(["systemctl","--user","daemon-reload"],check=True)
if a.start: subprocess.run(["systemctl","--user","enable","--now","claude-chatgpt-bridge.service"],check=True)
else: print("unit installed, not started; verify config before starting")
