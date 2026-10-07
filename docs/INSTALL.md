# Install and upgrade

From Linux with Python 3.11+, preview scripts/install.sh --dry-run, then run scripts/install.sh. Copy config.example to local config if needed and edit it. Installer preserves existing config and never creates credentials.

Authenticate both CLIs locally as service user. Run scripts/verify.sh --prerequisites-only and full tests. Review unit/config, start service, and run scripts/verify.sh for authenticated localhost MCP health.

For upgrade, back up SQLite state with service stopped, review source, run tests, install without --start, inspect unit/config, then restart after review. Preserve previous checkout/state until health passes.
