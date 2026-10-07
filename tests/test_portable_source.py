from pathlib import Path
import re
import subprocess


def tracked_files(root: Path):
    out = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    return [root / name.decode() for name in out.split(b"\0") if name]


def test_source_tree_has_no_machine_identity_or_fixed_workstation_path():
    root = Path(__file__).resolve().parents[1]
    patterns = {
        "fixed workstation path": re.compile(re.escape("/mnt/" + "ssd/" + "Workstation"), re.I),
        "machine username": re.compile("agx-" + "press1", re.I),
        "private LAN address": re.compile(re.escape("192" + ".168" + ".1" + ".127")),
        "production project label": re.compile(r"\b(?:" + "na" + "ck|" + "go" + "lf|" + "aq" + "1" + "a" + r")\b", re.I),
    }
    ignored = {"PORTABLE_AUDIT.md", "PORTABLE_VERIFICATION.md", "tests/test_portable_source.py"}
    findings = []
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in ignored or relative.startswith("evidence/") or not path.is_file():
            continue
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        for label, pattern in patterns.items():
            if pattern.search(content):
                findings.append(f"{relative}: {label}")
    assert findings == []


def test_source_history_tree_does_not_track_live_evidence_or_runtime_state():
    root = Path(__file__).resolve().parents[1]
    tracked = [p.relative_to(root).as_posix() for p in tracked_files(root)]
    forbidden = [p for p in tracked if p.startswith("evidence/")]
    forbidden += [p for p in tracked if p.startswith("data/")]
    forbidden += [p for p in tracked if p.endswith((".db", ".sqlite", ".sqlite3", ".log"))]
    forbidden += [p for p in tracked if Path(p).name == ".env"]
    assert forbidden == []