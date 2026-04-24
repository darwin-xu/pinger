Checksum-based version policy

Goal
----
Provide a simple, deterministic checksum that reflects the deployed code so users and automated checks can detect whether the running server matches the repository files.

How it works
-----------
- The `/api/version` endpoint returns JSON with two fields: `checksum` (a SHA256 hex digest computed across repository files) and `server_start` (the server start timestamp in ISO 8601 UTC).
- The checksum is computed deterministically across repository files (sorted by normalized relative path) and includes each file's relative path and raw contents.
- Files and directories excluded from the checksum: `.git`, `venv`, `.venv`, `__pycache__`, `node_modules`, and `.pytest_cache`.

Usage
-----
- The UI displays `v <short-checksum> · started <local time>` and will show a banner when the checksum differs from the value first seen during the session.
- For automated checks, compare the `/api/version` `checksum` value against an expected checksum (e.g., produced at build/deploy time) to confirm the deployed code matches the source.

Notes
-----
- This checksum is independent of Git; it intentionally does not rely on commit IDs so uncommitted changes are reflected.
- Keep the excluded list small; add other build artifacts if your deployment creates them on the server.
