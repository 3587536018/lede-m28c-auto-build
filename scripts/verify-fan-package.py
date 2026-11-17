#!/usr/bin/env python3
"""Verify the supplied fan IPK, package inputs and installed image contents."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
NAME = "luci-app-m28c-fan"
PACKAGE = ROOT / "package" / NAME


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fields(data: bytes) -> dict[str, str]:
    result = {}
    previous = None
    for line in data.decode("utf-8").splitlines():
        if not line:
            continue
        if line.startswith((" ", "\t")) and previous:
            result[previous] += "\n" + line
            continue
        key, separator, value = line.partition(":")
        if not separator or not key or key in result:
            raise ValueError("Invalid or duplicate package control field")
        result[key] = value.removeprefix(" ")
        previous = key
    return result


def tar_files(data: bytes) -> dict[str, tuple[bytes, int]]:
    """Read the supplied gzip-tar format without extracting or running scripts."""
    result = {}
    seen = set()
    # Decompress completely so gzip CRC/truncation failures cannot hide after
    # tar's end marker. The original archive is hash checked before this call.
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)), mode="r:") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if name in ("", ".") and member.isdir():
                continue
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name
                    or str(path) != name.rstrip("/") or name in seen):
                raise ValueError("Unsafe or duplicate IPK member: " + name)
            seen.add(name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Non-regular IPK member: " + name)
            stream = archive.extractfile(member)
            result[name] = (stream.read(), member.mode)
    return result


def regular_file(root: Path, name: str) -> Path:
    path = root / name
    if (path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError("Missing regular package file: " + name)
    return path


def verify_source(package: Path = PACKAGE) -> dict:
    source = json.loads((package / "SOURCE.json").read_text(encoding="utf-8"))
    if source.get("schema") != 1 or source.get("archive") != "original.ipk":
        raise ValueError("Unsupported fan source record")
    original = regular_file(package, "original.ipk").read_bytes()
    if len(original) != source["size"] or digest(original) != source["sha256"]:
        raise ValueError("Original fan IPK hash or size differs from SOURCE.json")
    outer = tar_files(original)
    if set(outer) != {"debian-binary", "control.tar.gz", "data.tar.gz"} or outer["debian-binary"][0] != b"2.0\n":
        raise ValueError("Unexpected original IPK layout")
    control = tar_files(outer["control.tar.gz"][0])
    if not {"control", "conffiles"} <= set(control):
        raise ValueError("Original fan IPK lacks control metadata")
    metadata = fields(control["control"][0])
    if metadata != source["control"] or metadata.get("Package") != NAME or metadata.get("Architecture") != "all":
        raise ValueError("Original fan control identity differs from SOURCE.json")
    conffiles = control["conffiles"][0].decode("utf-8").splitlines()
    if conffiles != source["conffiles"] or conffiles != ["/etc/config/m28c_fan"]:
        raise ValueError("Original fan conffiles differ from SOURCE.json")
    scripts = {name: {"sha256": digest(data), "size": len(data)}
               for name, (data, _) in control.items() if name not in ("control", "conffiles")}
    if scripts != source["control_scripts"]:
        raise ValueError("Original fan control scripts differ from SOURCE.json")
    payload = tar_files(outer["data.tar.gz"][0])
    records = {name: {"sha256": digest(data), "size": len(data), "mode": f"{mode:04o}"}
               for name, (data, mode) in payload.items()}
    if records != source["files"]:
        raise ValueError("Original fan payload differs from SOURCE.json")
    files_root = package / "files"
    present = {path.relative_to(files_root).as_posix()
               for path in files_root.rglob("*") if path.is_symlink() or not path.is_dir()}
    if present != set(payload):
        raise ValueError("Fan package payload file set differs from original IPK")
    for name, (data, _) in payload.items():
        if regular_file(files_root, name).read_bytes() != data:
            raise ValueError("Fan package payload differs from original IPK: " + name)
    return source


def verify_image(image_root: Path, package: Path = PACKAGE) -> int:
    source = verify_source(package)
    for name, expected in source["files"].items():
        path = regular_file(image_root, name)
        data = path.read_bytes()
        if len(data) != expected["size"] or digest(data) != expected["sha256"]:
            raise ValueError("Image fan payload differs from original IPK: " + name)
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != int(expected["mode"], 8):
            raise ValueError("Image fan file mode differs: " + name)
    info = "usr/lib/opkg/info/" + NAME
    installed = fields(regular_file(image_root, info + ".control").read_bytes())
    for key in ("Package", "Version", "Architecture", "SourceName", "License"):
        if installed.get(key) != source["control"][key]:
            raise ValueError("Installed fan control mismatch: " + key)
    if installed.get("Source") != "package/custom/" + NAME:
        raise ValueError("Installed fan package did not come from the custom package")
    for key in ("Depends", "Provides"):
        actual = {item.strip() for item in installed.get(key, "").split(",")}
        expected = {item.strip() for item in source["control"][key].split(",")}
        if actual != expected:
            raise ValueError("Installed fan control mismatch: " + key)
    conffiles = regular_file(image_root, info + ".conffiles").read_text(encoding="utf-8").splitlines()
    if conffiles != source["conffiles"]:
        raise ValueError("Installed fan conffiles are missing or changed")
    owned = set(regular_file(image_root, info + ".list").read_text(encoding="utf-8").splitlines())
    if not {"/" + name for name in source["files"]} <= owned:
        raise ValueError("Installed fan package does not own every payload file")
    boot = image_root / "etc/rc.d/S99m28c-fan"
    if not boot.is_symlink() or os.readlink(boot) != "../init.d/m28c-fan":
        raise ValueError("Fan boot service is not enabled in the image")
    return len(source["files"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=PACKAGE)
    parser.add_argument("--lede", type=Path)
    parser.add_argument("--image-root", type=Path)
    args = parser.parse_args()
    image = args.image_root
    if args.lede and image is None:
        candidates = sorted(args.lede.glob("build_dir/target-*/root-rockchip"))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one compiled root-rockchip tree, found {len(candidates)}")
        image = candidates[0]
    if image is None:
        source = verify_source(args.package)
        print(f"Verified original {NAME} {source['control']['Version']} and {len(source['files'])} package files")
    else:
        count = verify_image(image, args.package)
        print(f"Verified {count} original fan files, installed package identity and fan boot service")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError, EOFError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(1)
