#!/usr/bin/env python3
"""Prepare a hash-pinned official Mihomo binary for the M28C core package.

The ARM64 binary is inspected, never executed on the build host. Downloads are
cached in LEDE's dl directory. The target package owns the core so its installed
version and public source metadata remain visible to opkg.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import urllib.request
import zlib


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "m28c-openclash-core"
BLOCK_SIZE = 1024 * 1024
MAX_CORE_SIZE = 128 * 1024 * 1024


def load_source(lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    source = lock.get("binaries", {}).get("openclash_core", {})
    version = source.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Invalid pinned Mihomo release version")
    asset = f"mihomo-linux-arm64-v{version}.gz"
    url = f"https://github.com/MetaCubeX/mihomo/releases/download/v{version}/{asset}"
    if (source.get("project") != "MetaCubeX/mihomo"
            or source.get("architecture") != "linux-arm64"
            or source.get("asset") != asset or source.get("url") != url):
        raise ValueError("Mihomo lock must name the official Linux ARM64 release asset")
    if not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
        raise ValueError("Mihomo release must have an exact SHA256 digest")
    if type(source.get("size")) is not int or not 0 < source["size"] <= MAX_CORE_SIZE:
        raise ValueError("Invalid pinned Mihomo archive size")
    return source


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path: Path, source: dict) -> None:
    if path.stat().st_size != source["size"]:
        raise ValueError("Mihomo archive size does not match the locked release")
    if sha256(path) != source["sha256"]:
        raise ValueError("Mihomo archive SHA256 does not match the locked release")


def download_archive(cache: Path, source: dict) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / source["asset"]
    if archive.is_file():
        try:
            verify_archive(archive, source)
            return archive
        except ValueError:
            pass
    # A partial or bad download never replaces an existing cache entry.
    for attempt in range(3):
        temporary = None
        try:
            request = urllib.request.Request(source["url"], headers={"User-Agent": "M28C-firmware-builder"})
            with tempfile.NamedTemporaryFile(dir=cache, prefix=".mihomo-download-", delete=False) as output:
                temporary = Path(output.name)
                with urllib.request.urlopen(request, timeout=60) as response:
                    size = 0
                    for block in iter(lambda: response.read(BLOCK_SIZE), b""):
                        size += len(block)
                        if size > source["size"]:
                            raise ValueError("Mihomo download exceeds the locked archive size")
                        output.write(block)
            verify_archive(temporary, source)
            temporary.replace(archive)
            return archive
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise AssertionError("unreachable")


def verify_elf(path: Path) -> None:
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError("Mihomo must be a little-endian ELF64 executable")
        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        _, kind, machine, version, entry, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = fields
        if kind not in (2, 3) or machine != 183 or version != 1 or not entry or ehsize != 64:
            raise ValueError("Mihomo must be an ARM64 executable with a valid ELF header")
        if phentsize != 56 or not 0 < phnum <= 128 or phoff < 64 or phoff + phnum * 56 > size:
            raise ValueError("Invalid Mihomo ELF program header table")
        executable = False
        stream.seek(phoff)
        for _ in range(phnum):
            ptype, flags, offset, _, _, filesz, memsz, _ = struct.unpack("<IIQQQQQQ", stream.read(56))
            if offset + filesz > size:
                raise ValueError("Truncated Mihomo ELF segment")
            if ptype == 3:
                raise ValueError("Mihomo core must be static: unexpected dynamic interpreter")
            if ptype == 1:
                if memsz < filesz:
                    raise ValueError("Invalid Mihomo ELF load segment")
                executable |= bool(flags & 1 and filesz)
        if not executable:
            raise ValueError("Mihomo ELF has no executable load segment")


def unpack_core(archive: Path, destination: Path, source: dict) -> dict:
    verify_archive(archive, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".clash-meta-", delete=False) as output:
            temporary = Path(output.name)
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            size = 0
            with archive.open("rb") as stream:
                for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
                    pending = block
                    while pending:
                        data = decoder.decompress(pending, BLOCK_SIZE)
                        size += len(data)
                        if size > MAX_CORE_SIZE:
                            raise ValueError("Mihomo core exceeds the uncompressed size limit")
                        output.write(data)
                        pending = decoder.unconsumed_tail
                        if decoder.unused_data:
                            raise ValueError("Unexpected extra gzip member or data after the Mihomo core")
            if not decoder.eof:
                raise ValueError("Truncated Mihomo gzip stream")
        verify_elf(temporary)
        info = {"size": size, "sha256": sha256(temporary), "format": "ELF64 little-endian AArch64 static", "mode": "0755"}
        temporary.chmod(0o755)
        temporary.replace(destination)
        return info
    except zlib.error as error:
        raise ValueError(f"Invalid Mihomo gzip CRC or stream: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare(lede_root: Path, lock_path: Path) -> dict:
    source = load_source(lock_path)
    package = lede_root / "package/custom" / PACKAGE
    makefile = package / "Makefile"
    if not makefile.is_file():
        raise ValueError("Copy the project packages into LEDE before preparing the OpenClash core")
    versions = re.findall(r"^PKG_VERSION:=(\S+)\s*$", makefile.read_text(encoding="utf-8"), re.MULTILINE)
    if versions != [source["version"]]:
        raise ValueError("Mihomo package version differs from sources.lock.json")
    archive = download_archive(lede_root / "dl", source)
    info = unpack_core(archive, package / "files/clash_meta", source)
    report = {
        "schema": 1, "package": PACKAGE, "version": source["version"],
        "source": source,
        "installed_path": "/etc/openclash/core/clash_meta", "binary": info,
        "notice": "Official release binary; no subscription, configuration, key or runtime data included.",
    }
    metadata = package / "files/source.json"
    metadata.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lede_root", type=Path, help="Prepared LEDE tree with project packages already copied")
    parser.add_argument("--lock", type=Path, default=ROOT / "sources.lock.json")
    args = parser.parse_args()
    try:
        report = prepare(args.lede_root.resolve(), args.lock.resolve())
    except (OSError, ValueError, KeyError) as error:
        print(f"OpenClash core preparation failed: {error}", file=sys.stderr)
        return 1
    print(f"Verified Mihomo {report['version']} ARM64 core: "
          f"{report['binary']['sha256']} ({report['binary']['size']} bytes); "
          f"prepared package {PACKAGE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
