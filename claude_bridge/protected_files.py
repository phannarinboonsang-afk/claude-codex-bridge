
"""Shared protected-file policy. Paths and inode identities, never credential contents."""
from __future__ import annotations
import re
from pathlib import Path

PROTECTED_DIRS={".ssh",".gnupg",".aws",".kube",".docker",".claude",".azure",".codex"}
_PATTERN=re.compile(r"(?i)(private.?key|(^|[._-])auth([._-]|$)|keystore|secret|token|credential|password|passwd|api[._-]?key|(^|[._-])key([._-]|$)|\.env($|\.)|\.(pem|key|p12|pfx|jks|kdbx|ovpn)$|^id_(rsa|dsa|ecdsa|ed25519)|^\.(netrc|pgpass|htpasswd|npmrc|pypirc)$)")
def protected_name(name: str) -> bool:
    return name.lower() in PROTECTED_DIRS or bool(_PATTERN.search(name))
class ProtectedFiles:
    def __init__(self,paths=()):
        self.declared=tuple(Path(p).absolute() for p in paths)
        self.canonical=tuple(p.resolve() for p in self.declared)
        self.identities=set()
        for p in self.declared:
            try: st=p.stat();self.identities.add((st.st_dev,st.st_ino))
            except OSError: pass
    def denies(self,path: Path,identity=None) -> bool:
        # Re-resolve configured paths on each check as well as preserving their
        # initial targets, so replacing a symlink cannot unprotect either target.
        candidate=path.resolve()
        targets=self.declared+self.canonical
        if any(candidate==target.resolve() or path.absolute()==target for target in targets):return True
        if identity is not None:
            if identity in self.identities:return True
            for target in targets:
                try:st=target.stat()
                except OSError:continue
                if (st.st_dev,st.st_ino)==identity:return True
        return False
