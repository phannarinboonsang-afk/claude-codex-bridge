"""Path validation and output sanitization."""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath



class PathError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_SECRET_NAME = re.compile(
    r"""(^\.env($|\.)) | (\.env$)
      | (\.(pem|key|p12|pfx|jks|keystore|kdbx|ovpn)$)
      | (^id_(rsa|dsa|ecdsa|ed25519))
      | (^\.(netrc|pgpass|htpasswd|npmrc|pypirc)$)
      | ((^|[._-])(secret|secrets|token|tokens|credential|credentials|passwd|password|passwords|apikey|api[_-]key)([._-]|$))
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".kube", ".docker", ".claude", ".azure"}


def is_secret_name(name: str) -> bool:
    return name.lower() in _SECRET_DIRS or bool(_SECRET_NAME.search(name))


def has_secret_component(path: Path | str) -> bool:
    return any(is_secret_name(p) for p in PurePosixPath(str(path)).parts if p not in ("/", ""))


def validate_project_path(raw: str | None, roots: tuple[Path, ...]) -> Path:
    """Return the resolved real directory, or raise PathError. No filesystem mutation."""
    if not roots:
        raise PathError("no_roots", "no project roots are configured")
    if raw is None or raw == "":
        return roots[0].resolve()
    if not isinstance(raw, str) or len(raw) > 4096 or "\x00" in raw:
        raise PathError("invalid", "invalid project_path")
    if ".." in PurePosixPath(raw).parts:
        raise PathError("traversal", "path traversal ('..') is not allowed")
    if not raw.startswith("/"):
        raise PathError("not_absolute", "project_path must be absolute")
    try:
        real = Path(raw).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise PathError("not_found", "project_path does not exist")
    except (OSError, RuntimeError):
        raise PathError("invalid", "project_path cannot be resolved")
    if not real.is_dir():
        raise PathError("not_dir", "project_path is not a directory")
    for root in roots:
        rroot = root.resolve()
        if real == rroot or real.is_relative_to(rroot):
            rel = real.relative_to(rroot)
            if has_secret_component(rel) or has_secret_component(raw):
                raise PathError("secret_path", "path points at a secret-bearing location")
            return real
    raise PathError("outside_roots", "project_path is outside the allowed roots")


def validate_writer_roots(roots: tuple[Path, ...], protected_roots: tuple[Path, ...] = ()) -> None:
    """Refuse writer roots that overlap the workspace or operator-protected paths."""
    resolved_protected = tuple(path.resolve() for path in protected_roots)
    for root in roots:
        resolved_root = root.resolve()
        for protected in resolved_protected:
            if (resolved_root == protected or resolved_root.is_relative_to(protected)
                    or protected.is_relative_to(resolved_root)):
                raise ValueError(f"writer root {resolved_root} overlaps a protected path {protected}")


# ---------- sanitization ----------
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/\-]{12,}=*"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+(@)"),
]
_KV = re.compile(
    r"""(?ix)\b([A-Za-z0-9_.\-]*(?:password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)[A-Za-z0-9_.\-]*)
        (\s*[:=]\s*)(["']?)([^\s"',;]{3,})\3"""
)
_SENSITIVE_ENV = re.compile(r"(?i)(key|token|secret|passw|credential|auth|cookie|session)")
REDACTED = "[REDACTED]"


def _env_secret_values() -> list[str]:
    vals = []
    for k, v in os.environ.items():
        if _SENSITIVE_ENV.search(k) and len(v) >= 6:
            vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


def sanitize(text: str | bytes | None, max_bytes: int | None = None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    truncated = False
    if max_bytes is not None and len(text.encode("utf-8", "ignore")) > max_bytes:
        text = text.encode("utf-8", "ignore")[:max_bytes].decode("utf-8", "ignore")
        truncated = True
    text = _ANSI.sub("", text)
    for val in _env_secret_values():
        text = text.replace(val, REDACTED)
    for pat in _PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(2)}", text)
        else:
            text = pat.sub(REDACTED, text)
    text = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    if truncated:
        text += "\n[output truncated]"
    return text
