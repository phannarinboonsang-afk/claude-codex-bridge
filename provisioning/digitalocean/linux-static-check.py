#!/usr/bin/env python3
"""Native Linux static validation only; never installs, enables, or starts units."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from provision import BUNDLE, cloud_init, verify_bundle
from worker import Policy, unit_text


def run_checked(argv, *, env=None):
    result = subprocess.run(argv, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode:
        raise SystemExit("FAIL:" + pathlib.Path(argv[0]).name + ":exit_" + str(result.returncode))
    if re.search(r"(?im)\b(?:warning|deprecated|unsupported|unknown lvalue|unknown key|invalid|error|failed)\b", result.stdout):
        raise SystemExit("FAIL:" + pathlib.Path(argv[0]).name + ":diagnostic_requires_review")
    return result.stdout


def main():
    if os.name != "posix":
        raise SystemExit("BLOCKED:Linux_required")
    verify_bundle()
    if not shutil.which("systemd-analyze"):
        raise SystemExit("FAIL:systemd-analyze_missing")
    if not shutil.which("cloud-init"):
        raise SystemExit("FAIL:cloud-init_missing")
    if not shutil.which("/usr/bin/python3"):
        raise SystemExit("FAIL:system_python_missing")

    version = run_checked(["systemd-analyze", "--version"]).splitlines()[0].strip()
    cloud_version = run_checked(["cloud-init", "--version"]).strip()
    units = []
    for source in sorted((BUNDLE / "units").iterdir()):
        if source.is_file() and (source.name.endswith((".service", ".slice", ".socket", ".timer", ".target", ".path"))
                                 or source.name.endswith((".service.example", ".slice.example", ".socket.example", ".timer.example"))):
            target_name = source.name.removesuffix(".example")
            units.append((source, target_name))

    with tempfile.TemporaryDirectory(prefix="agentbridge-systemd-verify-") as tmp:
        unit_dir = pathlib.Path(tmp)
        for source, target_name in units:
            shutil.copyfile(source, unit_dir / target_name)
        policy = Policy({"repositories": {"synthetic": "synthetic:none"}, "writers_enabled": False})
        run = policy.validate({
            "run_id": "a" * 32, "repository": "synthetic", "base_commit": "0" * 40,
            "agent": "synthetic", "profile": "tree", "task": "validation only",
            "limits": {"memory_mb": 64, "tasks": 32, "cpu_percent": 10, "timeout": 30},
        })
        generated = unit_dir / "agentbridge-run-validation.service"
        generated.write_text(unit_text(run), encoding="utf-8")
        search = [str(unit_dir), "/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"]
        env = os.environ.copy()
        env["SYSTEMD_UNIT_PATH"] = os.pathsep.join(search)
        verify = ["systemd-analyze", "verify"] + [str(unit_dir / name) for _, name in units] + [str(generated)]
        run_checked(verify, env=env)

    cloud_documents = []
    for source in sorted(BUNDLE.rglob("*")):
        if source.is_file() and source.suffix.lower() in (".yaml", ".yml"):
            cloud_documents.append((source, source.read_text(encoding="utf-8")))
    cloud_documents.append((pathlib.Path("generated-user-data.yaml"), cloud_init()))

    try:
        import yaml
    except ImportError:
        raise SystemExit("FAIL:python_yaml_unavailable")
    for source, text in cloud_documents:
        yaml.safe_load(text)
        if text.lstrip().startswith("#cloud-config"):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as f:
                f.write(text)
                config_path = f.name
            try:
                run_checked(["cloud-init", "schema", "--config-file", config_path])
            finally:
                pathlib.Path(config_path).unlink(missing_ok=True)

    test_report = json.loads(pathlib.Path(os.environ.get("AGENTBRIDGE_TEST_RESULTS", "/tmp/agentbridge-offline-tests.json")).read_text(encoding="utf-8"))
    report = {
        "systemd_version": version,
        "cloud_init_version": cloud_version,
        "systemd_unit_count": len(units) + 1,
        "cloud_init_document_count": sum(1 for _, text in cloud_documents if text.lstrip().startswith("#cloud-config")),
        "yaml_document_count": len(cloud_documents),
        "python_unit_test_count": test_report["python_unit_test_count"],
        "shell_file_count": len(list(BUNDLE.rglob("*.sh"))),
        "provisioning_safety_case_count": 5,
        "verdict": "PASS",
    }
    results_path = pathlib.Path(os.environ.get("AGENTBRIDGE_VALIDATION_RESULTS", "/tmp/agentbridge-linux-validation-results.json"))
    results_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("LINUX_STATIC_VALIDATION_PASS")


if __name__ == "__main__":
    main()
