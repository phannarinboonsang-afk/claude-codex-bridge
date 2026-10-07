# Release and versioning

Use semantic versions for tagged releases. A minor release may add backward-compatible tools or config settings; a major release may remove or change public tool/config contracts. Patch releases contain compatible fixes.

Every release candidate must pass the full test suite, portable/security tests, secret scan on source and history, and fresh-install simulation. Revalidate Codex CLI compatibility before changing its pinned version. Publish only the sanitized independent export, never the working branch history or local audit/evidence reports.
