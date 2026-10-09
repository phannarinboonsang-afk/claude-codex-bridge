#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update
sudo apt-get install --yes --no-install-recommends cloud-init python3-jsonschema python3-yaml shellcheck
