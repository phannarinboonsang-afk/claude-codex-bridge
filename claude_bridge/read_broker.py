"""Task-scoped read boundary. No model-controlled shell command is accepted."""
from __future__ import annotations
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from .security import is_secret_name, sanitize

EXCLUDED = {".git", ".codex", ".ssh", ".venv", "venv", "data", ".worktrees",
            "__pycache__", ".pytest_cache", "node_modules"}
EXTRA_SECRET = re.compile(r"(?i)(private.?key|(^|[._-])auth([._-]|$)|keystore|secret|token|credential|password|passwd|api[._-]?key|(^|[._-])key([._-]|$)|\.env($|\.))")
MAX_FILE = 65536
MAX_FILES = 2000

def denied(name: str) -> bool:
    return name.lower() in EXCLUDED or is_secret_name(name) or bool(EXTRA_SECRET.search(name))

class ReadBroker:
    def __init__(self, project: Path, scratch: Path, roots: tuple[Path, ...]):
        self.project = project.resolve(strict=True)
        self.scratch = scratch.resolve(strict=True)
        self.roots = tuple(p.resolve() for p in roots)
        if any(is_secret_name(p) or p.lower() == ".codex" or EXTRA_SECRET.search(p) for p in self.project.parts):
            raise PermissionError("secret project root denied")
        st = self.project.stat()
        self._identity = (st.st_dev, st.st_ino)
        if not any(self.project == r or self.project.is_relative_to(r) for r in self.roots):
            raise PermissionError("project outside allowed roots")

    def _root_fd(self) -> int:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.project.parts[1:]:
                nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = nextfd
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != self._identity:
                raise PermissionError("project root identity changed")
            return fd
        except (OSError, PermissionError):
            os.close(fd)
            raise PermissionError("project root unavailable or replaced") from None

    def _parts(self, raw: str) -> tuple[str, ...]:
        if not isinstance(raw, str) or not raw or len(raw) > 4096 or "\x00" in raw or raw.startswith("/") or "\\" in raw:
            raise PermissionError("invalid relative path")
        parts = PurePosixPath(raw).parts
        if not parts or any(p in (".", "..") or denied(p) for p in parts):
            raise PermissionError("path denied by read policy")
        return parts

    def _bytes(self, raw: str) -> bytes:
        parts = self._parts(raw)
        dfd = self._root_fd()
        try:
            for part in parts[:-1]:
                nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
                os.close(dfd); dfd = nextfd
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > MAX_FILE:
                    raise PermissionError("not an eligible regular source file")
                data = os.read(fd, MAX_FILE + 1)
                if len(data) > MAX_FILE or b"\x00" in data:
                    raise PermissionError("oversized or binary file")
                return data
            finally:
                os.close(fd)
        except OSError:
            raise PermissionError("path unavailable or symlink denied") from None
        finally:
            os.close(dfd)

    def read_file(self, path: str) -> dict:
        return {"path": path, "text": sanitize(self._bytes(path), MAX_FILE)}

    def list_files(self) -> dict:
        result = []; seen = 0; truncated = False
        def walk(fd: int, prefix: str, depth: int):
            nonlocal seen, truncated
            if depth > 20:
                truncated = True; return
            for name in sorted(os.listdir(fd)):
                seen += 1
                if seen > 10000 or len(result) >= MAX_FILES:
                    truncated = True; return
                if denied(name): continue
                rel = prefix + name
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try: walk(child, rel + "/", depth + 1)
                        finally: os.close(child)
                    elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_size <= MAX_FILE:
                        result.append(rel)
                except OSError:
                    continue
        fd = self._root_fd()
        try: walk(fd, "", 0)
        finally: os.close(fd)
        return {"files": result, "truncated": truncated}

    def search(self, text: str) -> dict:
        if not isinstance(text, str) or not text or len(text) > 256:
            raise ValueError("search must be 1..256 literal characters")
        matches = []
        inventory = self.list_files()
        for name in inventory["files"]:
            try: content = self.read_file(name)["text"]
            except PermissionError: continue
            for number, line in enumerate(content.splitlines(), 1):
                if text in line:
                    matches.append({"path": name, "line": number, "text": line[:500]})
                    if len(matches) >= 50:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": inventory["truncated"]}

    @contextmanager
    def _git_directory(self):
        rootfd = self._root_fd()
        try:
            try:
                fd = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
            except OSError:
                raise PermissionError("Git inspection requires an internal non-symlink .git directory") from None
        finally:
            os.close(rootfd)
        try:
            gd = Path(f"/proc/self/fd/{fd}")
            if (gd / "commondir").exists():
                raise PermissionError("linked git common directory is not supported")
            yield gd, fd
        finally:
            os.close(fd)

    def git_state(self, operation: str) -> dict:
        if operation not in ("status", "diff", "log"):
            raise PermissionError("git operation not allowlisted")
        inventory = self.list_files()
        with self._git_directory() as (gd, gitfd), tempfile.TemporaryDirectory(prefix="git-view-", dir=self.scratch) as tmp:
            snapshot = Path(tmp)
            (snapshot / ".git").mkdir()
            empty = snapshot / ".bridge-empty-config"; empty.write_text("")
            names = []; total = 0
            for name in inventory["files"]:
                try: data = self.read_file(name)["text"].encode()
                except PermissionError: continue
                total += len(data)
                if total > 16000000:
                    raise PermissionError("git view exceeds size limit")
                dest = snapshot / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
                names.append(name)
            # Expose only vetted source copies plus Git metadata to a trusted,
            # fixed Git command. Hide local config/hooks/attributes before Git runs.
            cmd = ["/usr/bin/bwrap", "--die-with-parent", "--unshare-net", "--unshare-pid",
                   "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
                   "--ro-bind", "/bin", "/bin", "--proc", "/proc", "--dev", "/dev",
                   "--ro-bind", str(snapshot), "/project",
                   "--ro-bind", str(gd), "/project/.git",
                   "--ro-bind", str(empty), "/project/.git/config"]
            if (gd / "hooks").is_dir(): cmd += ["--tmpfs", "/project/.git/hooks"]
            if (gd / "info" / "attributes").is_file():
                cmd += ["--ro-bind", str(empty), "/project/.git/info/attributes"]
            cmd += ["--chdir", "/project", "/usr/bin/git", "--no-pager", "--no-optional-locks",
                    "-c", "safe.directory=/project", "-c", "core.fsmonitor=false",
                    "-c", "core.hooksPath=/dev/null", "-c", "core.attributesfile=/dev/null",
                    "-c", "diff.external=", "-c", "core.quotePath=true"]
            if operation == "status": cmd += ["status", "--porcelain=v1", "--untracked-files=normal", "--ignore-submodules=all"]
            elif operation == "log": cmd += ["log", "-10", "--format=%h %cI %s", "--no-show-signature"]
            elif names: cmd += ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--ignore-submodules=all", "--", *names]
            else: return {"status": "ok", "text": "", "warnings": ["no eligible source paths"]}
            env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1"}
            try:
                p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, pass_fds=(gitfd,))
            except (OSError, subprocess.TimeoutExpired):
                raise PermissionError("isolated git inspection unavailable") from None
            if p.returncode:
                return {"status": "unavailable", "text": "", "warnings": ["isolated git command failed"]}
            output = sanitize(p.stdout, 20000)
            if operation == "status":
                output = "\n".join(line for line in output.splitlines()
                                   if not any(denied(part.strip('"')) for part in line[3:].split("/")))
            return {"status": "ok", "text": output,
                    "warnings": ["Git compares a sanitized source snapshot; excluded, oversized and deleted paths are omitted from content diffs."]}
