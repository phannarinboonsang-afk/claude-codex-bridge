"""Task-scoped read boundary. No model-controlled shell command is accepted."""
from __future__ import annotations
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from .security import sanitize
from .protected_files import ProtectedFiles, protected_name

EXCLUDED = {".git", ".codex", ".ssh", ".venv", "venv", "data", ".worktrees",
            "__pycache__", ".pytest_cache", "node_modules"}

MAX_FILE = 65536
MAX_FILES = 2000

def denied(name: str) -> bool:
    return name.lower() in EXCLUDED or protected_name(name)

class SandboxUnavailable(PermissionError):
    """Sanitized public error with a safe internal diagnostic classification."""
    def __init__(self, diagnostic_code: str):
        super().__init__("isolated git inspection unavailable")
        self.diagnostic_code = diagnostic_code


class ReadBroker:
    def __init__(self, project: Path, scratch: Path, roots: tuple[Path, ...], protected_files: tuple[Path, ...] = ()):
        self.protected = ProtectedFiles(protected_files)
        self.project = project.resolve(strict=True)
        self.scratch = scratch.resolve(strict=True)
        self.roots = tuple(p.resolve() for p in roots)
        if any(protected_name(p) for p in self.project.parts):
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

    def _validate_opened_file(self, fd: int, expected: Path, st) -> None:
        actual = Path(f"/proc/self/fd/{fd}").resolve(strict=True)
        if actual != expected or not actual.is_relative_to(self.project):
            raise PermissionError("opened file moved outside requested project path")
        if any(denied(part) for part in actual.relative_to(self.project).parts):
            raise PermissionError("opened file moved into protected location")
        if self.protected.denies(actual, (st.st_dev, st.st_ino)):
            raise PermissionError("configured protected file denied")

    def _bytes(self, raw: str) -> bytes:
        parts = self._parts(raw)
        candidate = self.project.joinpath(*parts)
        if self.protected.denies(candidate):
            raise PermissionError("configured protected file denied")
        dfd = self._root_fd()
        try:
            for part in parts[:-1]:
                nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
                os.close(dfd); dfd = nextfd
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            try:
                st = os.fstat(fd)
                # Compare the opened inode/path before the first content read.
                self._validate_opened_file(fd, candidate, st)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > MAX_FILE:
                    raise PermissionError("not an eligible regular source file")
                data = os.read(fd, MAX_FILE + 1)
                # A rename during the read must not release bytes to the model.
                self._validate_opened_file(fd, candidate, os.fstat(fd))
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
            expected = self.project / prefix.rstrip("/")
            checkpoint = len(result)
            try:
                self._validate_opened_file(fd, expected, os.fstat(fd))
                entries = sorted(os.listdir(fd))
                self._validate_opened_file(fd, expected, os.fstat(fd))
                for name in entries:
                    seen += 1
                    if seen > 10000 or len(result) >= MAX_FILES:
                        self._validate_opened_file(fd, expected, os.fstat(fd))
                        truncated = True; return
                    if denied(name) or self.protected.denies(self.project / (prefix + name)): continue
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
                self._validate_opened_file(fd, expected, os.fstat(fd))
            except OSError:
                del result[checkpoint:]
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
            # x86-64 uses /lib64 for the trusted ELF interpreter. ARM64
            # commonly has no such directory; never expose any caller path.
            if Path("/lib64").is_dir():
                cmd += ["--ro-bind", "/lib64", "/lib64"]
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
            except subprocess.TimeoutExpired:
                raise SandboxUnavailable("sandbox_timeout") from None
            except FileNotFoundError:
                raise SandboxUnavailable("sandbox_executable_missing") from None
            except OSError:
                raise SandboxUnavailable("sandbox_launch_denied") from None
            if p.returncode:
                return {"status": "unavailable", "text": "", "warnings": ["isolated git command failed"]}
            output = sanitize(p.stdout, 20000)
            if operation == "status":
                output = "\n".join(line for line in output.splitlines()
                                   if not any(denied(part.strip('"')) for part in line[3:].split("/")))
            return {"status": "ok", "text": output,
                    "warnings": ["Git compares a sanitized source snapshot; excluded, oversized and deleted paths are omitted from content diffs."]}
