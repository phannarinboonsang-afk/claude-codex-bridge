"""Private stdio MCP server; launched only as a child of one Codex task."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# -I ignores caller PYTHONPATH and current-directory imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from claude_bridge.read_broker import ReadBroker

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--protected-file", action="append", default=[])
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args()
    broker = ReadBroker(Path(args.project), Path(args.scratch), tuple(Path(p) for p in args.root),
                        protected_files=tuple(Path(p) for p in args.protected_file))
    mcp = FastMCP("bridge-read-broker", instructions="Read-only source inspection. Only these vetted operations are available.")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False, idempotentHint=True)
    operations = (broker.read_file, broker.list_files, broker.search)
    if not args.no_git:
        operations += (broker.git_state,)
    for operation in operations:
        mcp.tool(annotations=annotations)(operation)
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
