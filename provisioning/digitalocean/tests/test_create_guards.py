import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import provision


ROOT = pathlib.Path(__file__).resolve().parents[1]


def resign(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name in {"SHA256SUMS", "release-validation-manifest.json"}:
            continue
        rows.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(root).as_posix())
    (root / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


class CreateGuardTests(unittest.TestCase):
    def test_git_checkout_metadata_is_outside_bundle_hash_inventory(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name) / "bundle"
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        metadata = root / ".git"
        metadata.mkdir()
        (metadata / "config").write_text("[core]\n", encoding="utf-8")
        with patch.object(provision, "BUNDLE", root):
            provision.verify_bundle()

    def fixture(self, owner):
        temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(temp.name) / "bundle"
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        resign(root)
        (root / "release-validation-manifest.json").write_text(json.dumps({
            "package_version": "1.0.2",
            "package_sha256s_fingerprint": hashlib.sha256((root / "SHA256SUMS").read_bytes()).hexdigest(),
            "git_commit": "a" * 40,
            "github_workflow_run_id": 123,
            "shellcheck_version": "ShellCheck version 0.9.0",
            "runner_os": "ubuntu-24.04",
            "systemd_version": "systemd 255",
            "cloud_init_version": "cloud-init 24.4",
            "validation_counts": {"python_unit_tests": 1, "systemd_units": 1, "cloud_init_documents": 1,
                                  "yaml_documents": 1, "shell_files": 0, "provisioning_safety_cases": 4},
            "timestamp_utc": "2026-10-09T00:00:00Z",
            "verdict": "PASS",
        }), encoding="utf-8")
        owner_path = pathlib.Path(temp.name) / "owner.json"
        owner_path.write_text(json.dumps(owner), encoding="utf-8")
        return temp, root, owner_path

    def run_create(self, owner, *, marker=True):
        temp, root, owner_path = self.fixture(owner)
        self.addCleanup(temp.cleanup)
        if not marker:
            (root / "release-validation-manifest.json").unlink()
            resign(root)
        with patch.object(provision, "BUNDLE", root), patch("sys.argv", ["provision.py", "create", "--approve-create", "--owner-config", str(owner_path)]):
            return provision.main()

    def test_missing_linux_release_marker_stops_before_token_or_api(self):
        with patch("provision.getpass.getpass", side_effect=AssertionError("must not prompt")), patch.object(provision.API, "call", side_effect=AssertionError("must not call provider")):
            with self.assertRaisesRegex(ValueError, "release_validation_manifest_missing"):
                self.run_create({"ssh_key": "123", "admin_cidr": "203.0.113.1/32"}, marker=False)

    def test_mismatched_package_fingerprint_stops_before_token_or_api(self):
        temp, root, owner_path = self.fixture({"ssh_key": "123", "admin_cidr": "203.0.113.1/32"})
        self.addCleanup(temp.cleanup)
        marker = json.loads((root / "release-validation-manifest.json").read_text())
        marker["package_sha256s_fingerprint"] = "0" * 64
        (root / "release-validation-manifest.json").write_text(json.dumps(marker), encoding="utf-8")
        with patch.object(provision, "BUNDLE", root), patch("sys.argv", ["provision.py", "create", "--approve-create", "--owner-config", str(owner_path)]), \
             patch("provision.getpass.getpass", side_effect=AssertionError("must not prompt")), \
             patch.object(provision.API, "call", side_effect=AssertionError("must not call provider")):
            with self.assertRaisesRegex(ValueError, "release_validation_manifest_mismatch"):
                provision.main()

    def test_missing_ssh_key_stops_before_token_or_api(self):
        with patch("provision.getpass.getpass", side_effect=AssertionError("must not prompt")), patch.object(provision.API, "call", side_effect=AssertionError("must not call provider")):
            with self.assertRaisesRegex(ValueError, "owner_config_fields"):
                self.run_create({"admin_cidr": "203.0.113.1/32"})

    def test_missing_admin_ipv4_stops_before_token_or_api(self):
        with patch("provision.getpass.getpass", side_effect=AssertionError("must not prompt")), patch.object(provision.API, "call", side_effect=AssertionError("must not call provider")):
            with self.assertRaisesRegex(ValueError, "owner_config_fields"):
                self.run_create({"ssh_key": "123"})

    @unittest.skipUnless(os.name == "posix", "token prompt path is exercised on the Ubuntu Actions runner")
    def test_empty_token_stops_before_provider_api_call(self):
        with patch("provision.getpass.getpass", return_value=""), patch.object(provision.API, "call", side_effect=AssertionError("must not call provider")):
            with self.assertRaisesRegex(ValueError, "token_required"):
                self.run_create({"ssh_key": "123", "admin_cidr": "203.0.113.1/32"})


if __name__ == "__main__":
    unittest.main()
