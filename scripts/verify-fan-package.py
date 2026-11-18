#!/usr/bin/env python3
"""Verify the original fan IPK, declared UI text derivative and image contents."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
NAME = "luci-app-m28c-fan"
PACKAGE = ROOT / "package" / NAME
UI_PATH = "www/luci-static/resources/view/m28c-fan/settings.js"
DERIVED_VERSION = "2026.11.16-r3.m28c1"


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


def derive_ui(package: Path, source: dict, original: bytes) -> tuple[bytes, dict]:
    """Reconstruct the sole permitted derivative from original translated text.

    Replacements cannot contain JavaScript delimiters or escapes. No executable
    expression, form field, RPC call or control logic can be changed by this
    format, even if someone updates the declared output digest.
    """
    record = json.loads(regular_file(package, "UI-CHANGES.json").read_text(encoding="utf-8"))
    if (set(record) != {"schema", "kind", "source_archive_sha256", "source_version",
                        "version", "path", "reason", "input_sha256", "output_sha256",
                        "replacements"}
            or record["schema"] != 1 or record["kind"] != "luci-translated-text-replacements"
            or record["path"] != UI_PATH or record["version"] != DERIVED_VERSION
            or record["source_archive_sha256"] != source["sha256"]
            or record["source_version"] != source["control"]["Version"]
            or record["input_sha256"] != digest(original)):
        raise ValueError("Invalid fan UI derivative identity or input digest")
    replacements = record["replacements"]
    if not isinstance(replacements, list) or not replacements:
        raise ValueError("Fan UI derivative requires translated-text replacements")
    text = original.decode("utf-8")
    updated = text
    seen = set()
    for item in replacements:
        if not isinstance(item, dict) or set(item) != {"before", "after", "occurrences"}:
            raise ValueError("Invalid fan UI replacement record")
        before, after, count = item["before"], item["after"], item["occurrences"]
        if (any(not isinstance(value, str) or not value
                or any(char in value for char in "'\\\r\n\u2028\u2029")
                for value in (before, after))
                or before == after or before in seen
                or type(count) is not int or count < 1):
            raise ValueError("Unsafe or duplicate fan UI translated-text replacement")
        seen.add(before)
        old, new = "_('" + before + "')", "_('" + after + "')"
        if text.count(old) != count or updated.count(old) != count:
            raise ValueError("Fan UI replacement count differs from original")
        updated = updated.replace(old, new)
    translated = re.compile(r"_\('(?:[^'\\]|\\.)*'\)")
    if translated.sub("_('<text>')", text) != translated.sub("_('<text>')", updated):
        raise ValueError("Fan UI derivative changes executable code")
    derived = updated.encode("utf-8")
    if digest(derived) != record["output_sha256"]:
        raise ValueError("Fan UI derivative output digest differs")
    return derived, record


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
    derived_ui, derivative = derive_ui(package, source, payload[UI_PATH][0])
    expected_payload = dict(payload)
    expected_payload[UI_PATH] = (derived_ui, payload[UI_PATH][1])
    files_root = package / "files"
    present = {path.relative_to(files_root).as_posix()
               for path in files_root.rglob("*") if path.is_symlink() or not path.is_dir()}
    if present != set(payload):
        raise ValueError("Fan package payload file set differs from original IPK")
    for name, (data, _) in expected_payload.items():
        if regular_file(files_root, name).read_bytes() != data:
            raise ValueError("Fan package payload differs from verified original/UI derivative: " + name)
    return {**source, "derivative": derivative,
            "installed_control": {**source["control"], "Version": DERIVED_VERSION},
            "installed_files": {
                name: {"sha256": digest(data), "size": len(data), "mode": f"{mode:04o}"}
                for name, (data, mode) in expected_payload.items()}}


def verify_image(image_root: Path, package: Path = PACKAGE) -> int:
    source = verify_source(package)
    for name, expected in source["installed_files"].items():
        path = regular_file(image_root, name)
        data = path.read_bytes()
        if len(data) != expected["size"] or digest(data) != expected["sha256"]:
            raise ValueError("Image fan payload differs from verified original/UI derivative: " + name)
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != int(expected["mode"], 8):
            raise ValueError("Image fan file mode differs: " + name)
    info = "usr/lib/opkg/info/" + NAME
    installed = fields(regular_file(image_root, info + ".control").read_bytes())
    for key in ("Package", "Version", "Architecture", "SourceName", "License"):
        if installed.get(key) != source["installed_control"][key]:
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
        print(f"Verified original {NAME} {source['control']['Version']}, declared UI derivative "
              f"{source['installed_control']['Version']} and {len(source['installed_files'])} package files")
    else:
        count = verify_image(image, args.package)
        print(f"Verified {count} fan files against original/declared UI derivative, "
              "installed package identity and fan boot service")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError, EOFError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(1)
