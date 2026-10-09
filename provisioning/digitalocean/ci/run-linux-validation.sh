#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sha256sum --check SHA256SUMS
python3 secret-scan.py
export PYTHONPYCACHEPREFIX="$RUNNER_TEMP/pycache"
python3 -B -m compileall -q -d "$RUNNER_TEMP/pyc" .
python3 offline-check.py 2>&1 | tee "$RUNNER_TEMP/offline-check.log"
python3 linux-static-check.py 2>&1 | tee "$RUNNER_TEMP/linux-static-check.log"
if compgen -G '*.sh' >/dev/null || compgen -G 'ci/*.sh' >/dev/null; then
  mapfile -t shell_files < <(find . -type f -name '*.sh' -not -path './.git/*' -print)
  if ((${#shell_files[@]})); then
    bash -n "${shell_files[@]}"
    shellcheck "${shell_files[@]}"
  fi
fi
python3 release-manifest.py --results "$RUNNER_TEMP/agentbridge-linux-validation-results.json" --output "$RUNNER_TEMP/release-validation-manifest.json"
