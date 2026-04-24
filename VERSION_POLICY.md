Checksum-based version policy

Goal
----
Provide a simple, deterministic checksum that reflects the deployed code so users and automated checks can detect whether the running server matches the repository files.

How it works
-----------
- The `/api/version` endpoint now returns JSON with two fields: `version` (keeps existing content from `version.txt`) and `checksum` (a SHA256 hex digest).
- The checksum is computed deterministically across repository files (sorted by relative path) and includes each file's relative path and raw contents.
- Files and directories excluded from the checksum: `.git`, `venv`, `.venv`, `__pycache__`, `node_modules`, `.pytest_cache`, and the `version.txt` file itself.

Usage
-----
- The UI displays `v <version> · <short-checksum>` (first 8 chars) and will show a banner when the checksum differs from the value first seen during the session.
- For automated checks, compare the `/api/version` `checksum` value against an expected checksum (e.g., produced at build/deploy time) to confirm the deployed code matches the source.

Notes
-----
- This checksum is independent of Git; it intentionally does not rely on commit IDs so uncommitted changes are reflected.
- Because `version.txt` is excluded, writing a deployment marker to that file won't change the checksum itself.
- Keep the excluded list small; add other build artifacts if your deployment creates them on the server.
