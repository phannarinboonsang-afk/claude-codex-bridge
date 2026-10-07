# Uninstall and rollback

Preview scripts/uninstall.sh --dry-run. Running scripts/uninstall.sh disables the user unit and removes its unit file, preserving checkout, config, credentials, and runtime database.

State deletion is manual: inspect BRIDGE_DATA_DIR, stop service, retain backup, remove only confirmed Bridge state. For rollback restore previous reviewed checkout/dependencies and state backup if needed. Do not reset or clean operator changes.
