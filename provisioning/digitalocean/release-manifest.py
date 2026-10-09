#!/usr/bin/env python3
"""Emit a non-secret manifest only after Linux checks have completed."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

from provision import BUNDLE, PACKAGE_VERSION, verify_bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    verify_bundle()
    results = json.loads(pathlib.Path(args.results).read_text(encoding="utf-8"))
    if results.get("verdict") != "PASS":
        raise SystemExit("FAIL:linux_validation_not_passed")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True,
                            stdout=subprocess.PIPE).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("FAIL:git_commit_unavailable")
    manifest = {
        "package_version": PACKAGE_VERSION,
        "package_sha256s_fingerprint": hashlib.sha256((BUNDLE / "SHA256SUMS").read_bytes()).hexdigest(),
        "git_commit": commit,
        "github_workflow_run_id": int(os.environ['GITHUB_RUN_ID']),
        "shellcheck_version": subprocess.check_output(['shellcheck','--version'],text=True).strip(),
        "runner_os": "ubuntu-24.04",
        "systemd_version": results["systemd_version"],
        "cloud_init_version": results["cloud_init_version"],
        "validation_counts": {
            "python_unit_tests": results["python_unit_test_count"],
            "systemd_units": results["systemd_unit_count"],
            "cloud_init_documents": results["cloud_init_document_count"],
            "yaml_documents": results["yaml_document_count"],
            "shell_files": results["shell_file_count"],
            "provisioning_safety_cases": results["provisioning_safety_case_count"],
        },
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "verdict": "PASS",
    }
    pathlib.Path(args.output).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("RELEASE_VALIDATION_MANIFEST_PASS")


if __name__ == "__main__":
    main()
