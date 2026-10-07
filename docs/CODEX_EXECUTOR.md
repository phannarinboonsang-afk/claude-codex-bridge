# Codex read-only executor

Codex is a separate executor selected through generic agent task API. It shares Bridge queue/store, workspace policy, process timeout/cancellation, persistence, and sanitization with Claude. Legacy Claude tools remain.

Codex v1 is disabled by default and accepts only validated CLI version 0.160.0. It uses local CLI login and never receives OPENAI_API_KEY from Bridge. Bridge read broker is the only project file interface and enforces path, secret-file, symlink, size, and content rules. Arbitrary shell, patch, network, plugin, and collaboration tools are not granted.

Set CODEX_EXECUTABLE to trusted absolute executable; enable BRIDGE_CODEX_ENABLED=1 only after review/security tests. Do not add roots to work around denied files.
