#!/usr/bin/env python3
"""Compute a deterministic SHA256 checksum for deployable repository files.

Usage:
  python3 checksum.py [--root PATH]
  python3 checksum.py --files-only
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import List
import fnmatch
import unicodedata

# Single source of truth for files that are deployed and checksummed.
# Patterns are matched against normalized POSIX paths relative to the repo root.
INCLUDE_FILE_PATTERNS = (
    "**/*.py",
    "**/*.sh",
    "**/*.html",
    "requirements.txt",
)

EXCLUDE_PATTERNS = (
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    "deploy.sh",
    "test_*.py",
)


def _matches_any_pattern(path: Path, patterns: tuple[str, ...], repo_root: Path) -> bool:
    """Return True if `path` matches any of the glob-style `patterns`.

    Matching is attempted against the file's relative path (with forward
    slashes). Patterns without a slash only match top-level files.
    """
    rel = unicodedata.normalize("NFC", path.relative_to(repo_root).as_posix())
    rel_path = Path(rel)
    for pat in patterns:
        pat_n = unicodedata.normalize("NFC", pat)
        root_pat = pat_n[3:] if pat_n.startswith("**/") else None
        if (
            fnmatch.fnmatch(rel, pat_n)
            or rel_path.match(pat_n)
            or (root_pat is not None and fnmatch.fnmatch(rel, root_pat))
        ):
            return True
        if any(fnmatch.fnmatch(part, pat_n) for part in rel_path.parts):
            return True
    return False


def list_included_files(root: str | os.PathLike | None = None) -> List[Path]:
    """Return deployable files included in the checksum, sorted deterministically."""
    if root is None:
        root = os.path.dirname(os.path.abspath(__file__))
    p = Path(root)
    files = [
        f
        for f in p.rglob("*")
        if (
            f.is_file()
            and _matches_any_pattern(f, INCLUDE_FILE_PATTERNS, p)
            and not _matches_any_pattern(f, EXCLUDE_PATTERNS, p)
        )
    ]
    # Sort by normalized POSIX relative path so ordering is consistent across
    # platforms (path separators and Unicode normalization).
    files.sort(key=lambda x: unicodedata.normalize("NFC", x.relative_to(p).as_posix()))
    return files


# Backward-compatible name used by callers that imported the previous helper.
list_repo_files = list_included_files


def compute_repo_checksum(root: str | os.PathLike | None = None) -> str:
    """Return SHA256 hex digest computed deterministically over repository files."""
    h = hashlib.sha256()
    p = Path(root or os.path.dirname(os.path.abspath(__file__)))
    files = list_repo_files(root)
    for f in files:
        rel = unicodedata.normalize("NFC", f.relative_to(p).as_posix())
        # Update aggregate hasher with path separator + file contents. This
        # is equivalent to hashing the concatenation of each file's
        # relative path, a NUL byte, the file bytes, and a newline. This
        # ensures renames and content changes both affect the final digest.
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(8192), b""):
                    h.update(chunk)
        except Exception:
            h.update(b"<unreadable:" + rel.encode("utf-8") + b">")
        h.update(b"\n")
    return h.hexdigest()


def compute_file_hash(f: Path) -> str:
    h = hashlib.sha256()
    try:
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                h.update(chunk)
    except Exception:
        # Return an unstable marker if unreadable
        return "<unreadable>"
    return h.hexdigest()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute repository checksum and list hashed files."
    )
    parser.add_argument(
        "--root",
        "-r",
        default=None,
        help="Repository root (defaults to script directory)",
    )
    parser.add_argument(
        "--no-list",
        action="store_true",
        help="Do not print the file list, only checksum",
    )
    parser.add_argument(
        "--files-only",
        action="store_true",
        help="Print included file paths only, one per line, for tools like rsync --files-from",
    )
    parser.add_argument(
        "--long",
        action="store_true",
        help="Print full 64-char SHA256 hashes instead of short 8-char (default)",
    )
    args = parser.parse_args(argv)

    root = args.root
    p = Path(root or os.path.dirname(os.path.abspath(__file__)))
    files = list_included_files(root)

    if args.files_only:
        for f in files:
            print(unicodedata.normalize("NFC", f.relative_to(p).as_posix()))
        return 0

    checksum = compute_repo_checksum(root)

    # By default print short 8-char hashes; `--long` requests full 64-char
    short = not getattr(args, "long", False)
    # Choose display width for the hash column so header aligns with content
    hash_col_width = 8 if short else 64
    if not args.no_list:
        hash_label = "SHA256"
        file_label = "File"
        print(f"{hash_label.ljust(hash_col_width)}  {file_label}")
        for f in files:
            rel = unicodedata.normalize("NFC", f.relative_to(p).as_posix())
            fh = compute_file_hash(f)
            fh_disp = fh[:8] if short and fh != "<unreadable>" else fh
            print(f"{fh_disp.ljust(hash_col_width)}  {rel}")
        print()

    if short:
        print(f"Repository SHA256 (short 8): {checksum[:8]}")
    else:
        print(f"Repository SHA256: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
