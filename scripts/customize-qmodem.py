#!/usr/bin/env python3
"""Apply checked USB rebinding fixes to the locked QModem feed.

All inputs are checked before writing; an unexpected upstream or partially
modified file aborts the build. Reapplying this customization is harmless.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PATCH_DIR = Path(__file__).resolve().parents[1] / "patches" / "qmodem"


class PatchError(RuntimeError):
    pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_text(text: str, entry: dict) -> str:
    """Verify normalized upstream bytes and each unique replacement anchor."""
    current = digest(text)
    if current == entry["patched_sha256"]:
        return text
    if current != entry["sha256"]:
        raise PatchError(f"QModem source drift: {entry['path']}")
    result = text
    for replacement in entry["replacements"]:
        old, new = replacement["old"], replacement["new"]
        if result.count(old) != 1:
            raise PatchError(f"QModem patch anchor is not unique: {entry['path']}")
        result = result.replace(old, new, 1)
    if digest(result) != entry["patched_sha256"]:
        raise PatchError(f"QModem patch output checksum mismatch: {entry['path']}")
    return result


def customize(root: Path) -> None:
    feed = root / "feeds" / "qmodem"
    pending = []
    helpers = (PATCH_DIR / "scanner-helpers.c").read_text(encoding="utf-8")
    for manifest_name in ("scanner.json", "runtime.json"):
        manifest = json.loads((PATCH_DIR / manifest_name).read_text(encoding="utf-8"))
        if manifest_name == "scanner.json":
            occurrences = sum(
                replacement["new"].count(helpers)
                for entry in manifest["files"]
                for replacement in entry["replacements"]
            )
            if occurrences != 1:
                raise PatchError("Scanner manifest differs from the reviewed C helper source")
        for entry in manifest["files"]:
            path = feed / entry["path"]
            text = path.read_text(encoding="utf-8")
            pending.append((path, patch_text(text, entry)))
    header = PATCH_DIR / "m28c_usb_identity.h"
    header_target = feed / "application/modem_scan/src/m28c_usb_identity.h"
    header_text = header.read_text(encoding="utf-8")
    if header_target.exists() and header_target.read_text(encoding="utf-8") != header_text:
        raise PatchError("Existing QModem USB identity header differs from the managed source")
    pending.append((header_target, header_text))
    for path, text in pending:
        path.write_text(text, encoding="utf-8", newline="\n")
    print("QModem: stable USB controller/port binding; refreshed dial and monitoring configuration")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="LEDE source tree with the locked QModem feed installed")
    customize(parser.parse_args().root.resolve())
