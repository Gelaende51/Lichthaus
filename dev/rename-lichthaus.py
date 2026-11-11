#!/usr/bin/env python3
"""Rename Haus.Licht to Lichthaus in one repository (dev/rename-lichthaus.md).

    dev/rename-lichthaus.py [--dry-run] [REPO]      REPO: default the current directory

Applies the name map to every file git tracks in REPO: the content of text
files, and the paths (moved with `git mv`). Deterministic and re-runnable:
a second run changes nothing. Prints each changed file with its number of
replacements, each moved path, and a total. Made for every repository of
the project (Haus.Licht, -plasma, -gnome, -cinnamon, -xfce, -tray, -bar)
and every branch, so all of them get the identical rename.

Untouched on purpose:
- `Licht.Haus`, the upstream this was forked from (attribution);
- the Python package `haus/` and identifiers such as `HausCtl` (the map
  only knows the project's own spellings, case-sensitively; `Hauslicht`,
  the name Ledvance gives its mesh bulbs, is not one of them);
- binary files (a NUL byte, or not UTF-8) and symbolic links;
- this script and the spec (dev/rename-lichthaus.{py,md});
- lines that must keep the old names (migration code, the "formerly"
  notes): a line containing `rename-lichthaus: keep`, the lines between
  `rename-lichthaus: keep-begin` and `rename-lichthaus: keep-end`, and a
  whole file that contains `rename-lichthaus: keep-file`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROTECT = "Licht.Haus"
PLACEHOLDER = "\x00UPSTREAM\x00"  # cannot occur in a text file (NUL bytes mark binaries)

# Order matters: the repository-name forms and the display name before the
# lower-case forms, so no step sees another one's output half-done.
NAME_MAP: list[tuple[str, str]] = [
    ("Haus.Licht-", "Lichthaus-"),   # repo names: Haus.Licht-plasma, -gnome, ...
    ("Haus.Licht", "Lichthaus"),     # display name, repo name, URLs
    ("HausLicht", "Lichthaus"),      # ids: io.github.gelaende51.HausLicht, X-HausLicht-Page
    ("HAUSLICHT", "LICHTHAUS"),      # environment variables
    ("haus.licht", "lichthaus"),     # plasmoid id haus.licht.tray
    ("haus-licht", "lichthaus"),     # system file names (modules-load.d, udev rules)
    ("hauslicht", "lichthaus"),      # commands, service, state file, dirs, sockets, KCMs
]

SKIP_PATHS = {"dev/rename-lichthaus.py", "dev/rename-lichthaus.md"}
KEEP, KEEP_BEGIN, KEEP_END, KEEP_FILE = ("rename-lichthaus: keep", "rename-lichthaus: keep-begin",
                                         "rename-lichthaus: keep-end", "rename-lichthaus: keep-file")


def rename_text(text: str) -> tuple[str, int]:
    """The text with the name map applied (Licht.Haus protected); the number of replacements."""
    text = text.replace(PROTECT, PLACEHOLDER)
    count = 0
    for old, new in NAME_MAP:
        n = text.count(old)
        if n:
            text = text.replace(old, new)
            count += n
    return text.replace(PLACEHOLDER, PROTECT), count


def rename_content(text: str) -> tuple[str, int]:
    """rename_text line by line, leaving the keep-marked lines as they are."""
    if KEEP_FILE in text:
        return text, 0
    out, count, keeping = [], 0, False
    for line in text.splitlines(keepends=True):
        if KEEP_BEGIN in line:
            keeping = True
        if keeping or KEEP in line:  # KEEP also matches the begin/end markers
            out.append(line)
        else:
            line, n = rename_text(line)
            out.append(line)
            count += n
        if KEEP_END in line:
            keeping = False
    return "".join(out), count


def tracked(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True,
                         capture_output=True).stdout.decode()
    return sorted(p for p in out.split("\0") if p)


def read_text(path: Path) -> str | None:
    """The file's text, or None for a binary file or a symbolic link."""
    if path.is_symlink() or not path.is_file():
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?", default=".", help="the repository (default: the current directory)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="print what would change, change nothing")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    files = tracked(repo)
    changed = replacements = moved = 0
    for rel in files:
        if rel in SKIP_PATHS:
            continue
        path = repo / rel
        text = read_text(path)
        if text is not None:
            new, n = rename_content(text)
            if n:
                changed += 1
                replacements += n
                print(f"{rel}: {n}")
                if not args.dry_run:
                    path.write_bytes(new.encode("utf-8"))  # bytes: line endings stay as they were
        new_rel, n = rename_text(rel)
        if n and new_rel != rel:
            moved += 1
            print(f"{rel} -> {new_rel}")
            if not args.dry_run:
                (repo / new_rel).parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "-C", str(repo), "mv", "--", rel, new_rel], check=True)
    verb = "would change" if args.dry_run else "changed"
    print(f"{verb}: {replacements} replacements in {changed} files, {moved} paths moved ({len(files)} tracked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
