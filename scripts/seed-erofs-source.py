#!/usr/bin/env python3
"""Seed the locked erofs-utils source archive without a network request."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.8.10"
ARCHIVE = "erofs-utils-" + VERSION + ".tar.gz"
SIZE = 201240
SHA256 = "05eb4edebe11decce6ecb34e98d2f80c8cd283c2f2967d8ba7efd58418570514"
LEDE_REVISION = "8e247d214f071ef0cb3a31c879d4daf224849d3e"
MAKEFILE = "tools/erofs-utils/Makefile"
MAKEFILE_SHA256 = "e3beb23de32a402c41c66b5d9d7bb797e12f509ac97f78b772e7dd892594fafd"


def safe_path(root: Path, path: Path) -> None:
    """Refuse symlinks/junctions in this project's source/cache path."""
    relative = path.relative_to(root)
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Refusing linked source/cache path: " + str(current))


def regular_bytes(root: Path, path: Path, limit: int) -> bytes:
    safe_path(root, path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Expected bounded regular file: " + str(path))
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File grew beyond expected size: " + str(path))
    return data


def archive_bytes(root: Path, path: Path) -> bytes:
    data = regular_bytes(root, path, SIZE)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("erofs-utils archive size/SHA256 mismatch: " + str(path))
    return data


def seed(root: Path = ROOT) -> str:
    root = root.absolute()
    lock = json.loads(regular_bytes(root, root / "sources.lock.json", 1024**2))
    if lock.get("lede", {}).get("revision") != LEDE_REVISION:
        raise ValueError("Locked LEDE revision changed; review erofs-utils source")
    definition = regular_bytes(root, root / "lede" / MAKEFILE, 65536)
    if hashlib.sha256(definition).hexdigest() != MAKEFILE_SHA256:
        raise ValueError("Pinned erofs-utils Makefile changed")
    lines = definition.decode("utf-8").splitlines()
    required = ["PKG_NAME:=erofs-utils", "PKG_VERSION:=" + VERSION,
                "PKG_SOURCE:=$(PKG_NAME)-$(PKG_VERSION).tar.gz", "PKG_HASH:=" + SHA256]
    if any(lines.count(line) != 1 for line in required):
        raise ValueError("Pinned erofs-utils source definition differs")
    data = archive_bytes(root, root / "sources" / ARCHIVE)
    cache = root / "lede/dl"
    destination = cache / ARCHIVE
    safe_path(root, destination)
    if destination.exists():
        archive_bytes(root, destination)
        return "Verified existing erofs-utils source cache"
    cache.mkdir(exist_ok=True)
    # Publish a complete verified file atomically without replacing a cache
    # entry that another process may have created after the existence check.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".m28c-erofs-", dir=cache)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        archive_bytes(root, temporary)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            archive_bytes(root, destination)
            return "Verified concurrently seeded erofs-utils source cache"
        return "Seeded verified erofs-utils " + VERSION + " source cache"
    finally:
        temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print(seed(args.root))
    except (OSError, ValueError) as error:
        parser.exit(1, "ERROR: " + str(error) + "\n")


if __name__ == "__main__":
    main()
