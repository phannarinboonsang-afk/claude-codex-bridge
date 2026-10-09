import os
import stat
from .base import ProviderError

def readiness(path):
    try:
        info = os.lstat(path)
        return "READY" if stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid() else "CONFIG REQUIRED"
    except (OSError, TypeError, ValueError):
        return "CONFIG REQUIRED"

def read_provider_key(path):
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError()
        key = os.read(fd, 8193).decode("ascii").strip()
        if not key or len(key) > 8192 or any(c.isspace() for c in key):
            raise ValueError()
        return key
    except (OSError, TypeError, ValueError, UnicodeError):
        raise ProviderError("Chat model configuration is unavailable.") from None
    finally:
        if fd is not None:
            os.close(fd)
