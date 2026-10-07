import argparse, os, subprocess
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--dry-run",action="store_true"); a=p.parse_args()
config_home=Path(os.environ.get("XDG_CONFIG_HOME",Path.home()/".config"))
unit=config_home/"systemd/user/claude-chatgpt-bridge.service"
if a.dry_run:
 print("dry-run: would stop/disable claude-chatgpt-bridge.service and remove its unit")
 print("dry-run: checkout, config, credentials and runtime state are preserved"); raise SystemExit(0)
subprocess.run(["systemctl","--user","disable","--now","claude-chatgpt-bridge.service"],check=False)
unit.unlink(missing_ok=True); subprocess.run(["systemctl","--user","daemon-reload"],check=True)
print("service removed; checkout, config, credentials and state are preserved")
print("State deletion is manual: inspect BRIDGE_DATA_DIR before removing it.")
