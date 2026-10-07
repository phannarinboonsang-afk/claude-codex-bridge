# Protected file access

Read-only Claude Code and Codex tasks use the shared Bridge read broker for source access. Claude receives no native file, shell, edit, or write tools in read-only mode; it uses an explicit broker configuration in a disposable task directory with automatic user and project instruction discovery disabled.

The configured authentication header file is denied by its exact declared and canonical path and by its opened inode, even when its filename looks ordinary. Protected names, private-key file types, and credential directories are denied before file contents are read. Descriptor-relative no-follow opens, canonical descriptor checks, and checks before and after reads reject traversal, symlink, hardlink, alias, and rename races. Listing and search use the same protected-file policy.

These controls preserve the configured read roots and keep writer mode disabled by default. No credential contents are needed by the broker.
