#!/usr/bin/env python3
"""Fail when U+2014 appears in shipped source, tests, scripts, packaging, or the README."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EM_DASH = chr(0x2014)
SCAN_DIRS = ("src", "tests", "scripts", "packaging", ".github")
SCAN_FILES = ("README.md", "pyproject.toml", "LICENSE")
TEXT_SUFFIXES = {
    ".py",
    ".css",
    ".sh",
    ".md",
    ".toml",
    ".in",
    ".spec",
    ".yml",
    ".yaml",
    ".svg",
    ".desktop",
    "",
}


def iter_files():
    for name in SCAN_FILES:
        path = ROOT / name
        if path.is_file():
            yield path
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix in TEXT_SUFFIXES:
                yield path


def main() -> int:
    hits = []
    scanned = 0
    for path in iter_files():
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if EM_DASH in line:
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}")
    if hits:
        print("Em dash (U+2014) found:")
        for hit in hits:
            print("  " + hit)
        return 1
    print(f"check-emdash: OK ({scanned} files scanned, zero U+2014)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
