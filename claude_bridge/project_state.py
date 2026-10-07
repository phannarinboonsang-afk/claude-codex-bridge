"""Read-only project inspection. Runs only non-mutating git commands and never reads file contents
(except pytest's lastfailed cache, which holds test ids)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .security import is_secret_name, sanitize

_GIT_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
_GIT = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false"]
EVIDENCE_DIRS = ("reports", "evidence", "test-results", "artifacts", "docs/evidence", "work", "logs/evidence")
STATUS_FILES = ("STATUS.md", "CURRENT.md", "START_HERE.md", "work/STATUS.md", "CLAUDE.md")


async def _git(cwd: Path, *args: str, timeout: float = 15) -> tuple[int, str]:
    try:
        p = await asyncio.create_subprocess_exec(*_GIT, *args, cwd=str(cwd), env=_GIT_ENV,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode or 0, out.decode("utf-8", "replace")
    except (asyncio.TimeoutError, OSError):
        return 1, ""


async def git_porcelain(cwd: Path) -> dict[str, str] | None:
    """{path: xy-status} or None when cwd is not inside a git work tree."""
    rc, out = await _git(cwd, "status", "--porcelain=v1", "--untracked-files=normal")
    if rc != 0:
        return None
    return {line[3:]: line[:2] for line in out.splitlines() if len(line) > 3}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _recent_files(d: Path, limit: int = 5) -> list[dict]:
    rows = []
    try:
        for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
            depth = Path(dirpath).relative_to(d).parts
            if len(depth) >= 2:
                dirnames[:] = []
            dirnames[:] = [x for x in dirnames if not is_secret_name(x) and not x.startswith(".")]
            for f in filenames:
                if is_secret_name(f):
                    continue
                fp = Path(dirpath) / f
                if fp.is_symlink():
                    continue
                st = fp.stat()
                rows.append((st.st_mtime, str(fp.relative_to(d)), st.st_size))
            if len(rows) > 2000:
                break
    except OSError:
        return []
    rows.sort(reverse=True)
    return [{"path": p, "size": s, "modified": _iso(m)} for m, p, s in rows[:limit]]


async def get_project_state(project: Path) -> dict:
    state: dict = {"project": str(project), "git": None, "tests": {}, "evidence": {}, "status_files": {}}
    rc, inside = await _git(project, "rev-parse", "--is-inside-work-tree")
    if rc == 0 and inside.strip() == "true":
        _, branch = await _git(project, "symbolic-ref", "--short", "-q", "HEAD")
        _, head = await _git(project, "log", "-1", "--format=%H%x09%h%x09%cI%x09%s")
        h = head.strip().split("\t")
        porcelain = await git_porcelain(project) or {}
        counts = {"modified": 0, "added": 0, "deleted": 0, "renamed": 0, "untracked": 0, "conflicted": 0}
        for xy in porcelain.values():
            if xy == "??":
                counts["untracked"] += 1
            elif "U" in xy or xy in ("AA", "DD"):
                counts["conflicted"] += 1
            elif "R" in xy:
                counts["renamed"] += 1
            elif "A" in xy:
                counts["added"] += 1
            elif "D" in xy:
                counts["deleted"] += 1
            else:
                counts["modified"] += 1
        files = [{"path": "[hidden: secret-like name]" if any(is_secret_name(x) for x in p.split("/")) else p, "status": xy.strip() or xy}
                 for p, xy in sorted(porcelain.items())[:30]]
        state["git"] = {
            "branch": branch.strip() or "(detached)",
            "head": h[0] if len(h) > 0 and h[0] else None,
            "head_short": h[1] if len(h) > 1 else None,
            "head_date": h[2] if len(h) > 2 else None,
            "head_subject": sanitize(h[3])[:200] if len(h) > 3 else None,
            "dirty": bool(porcelain),
            "counts": counts,
            "changed_files": files,
            "changed_files_truncated": len(porcelain) > 30,
        }
    lf = project / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if lf.is_file() and not lf.is_symlink():
        try:
            state["tests"] = {"pytest_lastfailed_count": len(json.loads(lf.read_text())), "as_of": _iso(lf.stat().st_mtime)}
        except (OSError, ValueError):
            state["tests"] = {"pytest_lastfailed_count": None}
    else:
        state["tests"] = {"pytest_lastfailed_count": None, "note": "no pytest cache"}
    for rel in EVIDENCE_DIRS:
        d = project / rel
        if d.is_dir() and not d.is_symlink():
            state["evidence"][rel] = _recent_files(d)
    for rel in STATUS_FILES:
        f = project / rel
        if f.is_file() and not f.is_symlink():
            state["status_files"][rel] = {"size": f.stat().st_size, "modified": _iso(f.stat().st_mtime)}
    return state
