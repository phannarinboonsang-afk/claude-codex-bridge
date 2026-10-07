#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, shutil, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
ROOT=Path(__file__).resolve().parents[1]
def read_env(path):
 values={}
 if not path.is_file(): return values
 for number,raw in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
  line=raw.strip()
  if not line or line.startswith("#"): continue
  if "=" not in line: raise ValueError(f"invalid config line {number}")
  key,value=line.split("=",1); key=key.strip(); value=value.strip().strip('"').strip("'")
  if not key.replace("_","").isalnum() or not key[0].isalpha(): raise ValueError(f"invalid config key on line {number}")
  values[key]=value
 return values
def resolve(value):
 path=Path(value).expanduser()
 found=str(path.resolve()) if path.is_absolute() and path.is_file() else shutil.which(value)
 return found if found and os.access(found,os.X_OK) else None
def main():
 default=Path(os.environ.get("XDG_CONFIG_HOME",Path.home()/".config"))/"claude-chatgpt-bridge/bridge.env"
 parser=argparse.ArgumentParser(); parser.add_argument("--config",type=Path,default=default); parser.add_argument("--prerequisites-only",action="store_true")
 args=parser.parse_args(); failures=0
 def check(label,ok,detail=""):
  nonlocal failures
  print(f"{'PASS' if ok else 'FAIL'} {label}"+(f": {detail}" if detail else "")); failures+=not ok
 check("Python runtime >= 3.11",sys.version_info>=(3,11),sys.version.split()[0])
 check("checkout",(ROOT/"claude_bridge"/"__main__.py").is_file())
 check("dependency lock",(ROOT/"requirements.lock").is_file())
 try: config=read_env(args.config)
 except (OSError,ValueError) as exc: print(f"FAIL config: {exc}"); return 1
 print(f"PASS config file: {args.config}" if args.config.is_file() else f"WARN config missing: {args.config}")
 env=os.environ.copy(); env.update(config)
 workspace=Path(env.get("BRIDGE_WORKSPACE_ROOT",str(ROOT))).expanduser()
 check("workspace root is absolute and exists",workspace.is_absolute() and workspace.is_dir())
 original={key:os.environ.get(key) for key in config}
 try:
  os.environ.update(config)
  sys.path.insert(0,str(ROOT))
  from claude_bridge.config import Config
  Config.from_env()
  check("Bridge configuration",True)
 except Exception:
  check("Bridge configuration",False,"invalid or unsafe configuration")
 finally:
  for key,value in original.items():
   if value is None: os.environ.pop(key,None)
   else: os.environ[key]=value
 host=env.get("BRIDGE_LISTEN_HOST",env.get("BRIDGE_HOST","127.0.0.1"))
 check("listener is loopback",host in ("127.0.0.1","::1","localhost"),host)
 try:
  port=int(env.get("BRIDGE_LISTEN_PORT",env.get("BRIDGE_PORT","5077"))); check("listener port",1<=port<=65535,str(port))
 except ValueError: check("listener port",False,"must be integer")
 for label,names,default_value in (("Claude CLI",("CLAUDE_EXECUTABLE","BRIDGE_CLAUDE_BIN"),str(Path.home()/".local/bin/claude")),("Codex CLI",("CODEX_EXECUTABLE","BRIDGE_CODEX_BIN"),"codex")):
  value=next((env[n] for n in names if n in env),default_value); found=resolve(value); check(label,found is not None,found or value)
 auth=env.get("AUTH_HEADER_FILE",env.get("BRIDGE_AUTH_TOKEN_FILE",""))
 if auth:
  ap=Path(auth).expanduser(); check("auth file: present",ap.is_file() and os.access(ap,os.R_OK))
 else: print("WARN auth file: not configured")
 if not args.prerequisites_only:
  url=f"http://{host}:{port}/mcp"
  body=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"bridge-verify","version":"1"}}}'
  headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"}
  if auth and Path(auth).is_file(): headers["Authorization"]=Path(auth).expanduser().read_text(encoding="utf-8").strip()
  try:
   with urlopen(Request(url,data=body,headers=headers,method="POST"),timeout=5) as response: check("localhost MCP health",response.status==200,f"HTTP {response.status}")
  except HTTPError as exc: check("localhost MCP health",False,f"HTTP {exc.code}")
  except (URLError,TimeoutError,OSError): check("localhost MCP health",False,"unreachable")
 else: print("SKIP localhost MCP health (prerequisites-only)")
 return 1 if failures else 0
if __name__=="__main__": raise SystemExit(main())
