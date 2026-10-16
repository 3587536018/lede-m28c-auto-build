#!/usr/bin/env python3
"""Patch the locked dockerd package, including the matching IPK's init files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PATCH_DIR = Path(__file__).resolve().parents[1] / "patches/docker"


class PatchError(RuntimeError):
    pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_text(text: str, entry: dict) -> str:
    current = digest(text)
    if current == entry["patched_sha256"]:
        return text
    if current != entry["sha256"]:
        raise PatchError(f"Docker source drift: {entry['path']}")
    for replacement in entry["replacements"]:
        old, new = replacement["old"], replacement["new"]
        if text.count(old) != 1:
            raise PatchError(f"Docker patch anchor is not unique: {entry['path']}")
        text = text.replace(old, new, 1)
    if digest(text) != entry["patched_sha256"]:
        raise PatchError(f"Docker patch output checksum mismatch: {entry['path']}")
    return text


def customize(root: Path) -> None:
    package = root / "feeds/packages/utils/dockerd"
    manifest = json.loads((PATCH_DIR / "package.json").read_text(encoding="utf-8"))
    pending = []
    for entry in manifest["files"]:
        path = package / entry["path"]
        pending.append((path, patch_text(path.read_text(encoding="utf-8"), entry)))
    for source in sorted((PATCH_DIR / "files").rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(PATCH_DIR)
        path = package / relative
        text = source.read_text(encoding="utf-8")
        if path.exists() and path.read_text(encoding="utf-8") != text:
            raise PatchError(f"Existing Docker helper differs from managed source: {relative}")
        pending.append((path, text))
    # Validate every original and existing helper before changing any input.
    for path, text in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    print("Docker: package-native nftables backend, fw4 default bridge policy and router DNS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="LEDE source tree with locked packages feed")
    customize(parser.parse_args().root.resolve())
