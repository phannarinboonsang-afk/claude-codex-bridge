import re

_ANSI_OSC = re.compile(r"\x1B\][^\x07]*(?:\x07|\x1B\\)")
_ANSI_CSI = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_text(text, exact=()):
    text = str(text)
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    text = text.replace("\r", "")
    text = _CONTROL.sub("", text)
    for value in exact:
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    text = re.sub(r"/(?:home|mnt|tmp|etc|root|var)/[^\s\"'<>]+", "[PATH]", text)
    return text
