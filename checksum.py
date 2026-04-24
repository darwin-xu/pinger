#!/usr/bin/env python3
"""Compute a deterministic SHA256 checksum for the repository and list hashed files.

Usage:
  python3 checksum.py [--root PATH]

By default excludes: .git, venv, .venv, __pycache__, node_modules, and .pytest_cache
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import List
import fnmatch
import unicodedata

# Patterns may include shell-style wildcards (eg. "*.db", "build/*", "venv")
EXCLUDE_DIRS = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".github",
}
EXCLUDE_FILES = {
    ".gitignore",
    "pinger.db*",
    "*.md",
    "config.yaml*",
    "*.txt",
    "*.log",
}
# Note: `version.txt` is no longer used and therefore not excluded.


def _matches_any_pattern(path: Path, patterns: set, repo_root: Path) -> bool:
    """Return True if `path` matches any of the glob-style `patterns`.

    Matching is attempted against the file's relative path (with forward
    slashes) and against each path segment so patterns like `venv` or
    `__pycache__` match regardless of their depth.
    """
    rel = unicodedata.normalize("NFC", path.relative_to(repo_root).as_posix())
    parts = rel.split("/")
    for pat in patterns:
        pat_n = unicodedata.normalize("NFC", pat)
        if fnmatch.fnmatch(rel, pat_n):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat_n):
                return True
    return False


def list_repo_files(root: str | os.PathLike | None = None) -> List[Path]:
    if root is None:
        root = os.path.dirname(os.path.abspath(__file__))
    p = Path(root)
    files = [f for f in p.rglob("*") if f.is_file()]
    # Sort by normalized POSIX relative path so ordering is consistent across
    # platforms (path separators and Unicode normalization).
    files.sort(key=lambda x: unicodedata.normalize("NFC", x.relative_to(p).as_posix()))
    out: List[Path] = []
    for f in files:
        # Exclude by directory patterns (matches any path segment)
        if _matches_any_pattern(f, EXCLUDE_DIRS, p):
            continue
        # Exclude by file patterns (matches relative path or filename)
        if _matches_any_pattern(f, EXCLUDE_FILES, p):
            continue
        out.append(f)
    return out


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
        "--long",
        action="store_true",
        help="Print full 64-char SHA256 hashes instead of short 8-char (default)",
    )
    args = parser.parse_args(argv)

    root = args.root
    p = Path(root or os.path.dirname(os.path.abspath(__file__)))
    files = list_repo_files(root)

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
