#!/usr/bin/env python3
"""Export verified packages from the same LEDE build as the M28C firmware.

Layout follows rules.mk and include/feeds.mk at LEDE 8e247d214f071ef0cb3a31c879d4daf224849d3e.
The pinned scripts/ipkg-make-index.sh intentionally excludes kernel and libc.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


TARGET = Path("bin/targets/rockchip/armv8")
MANIFEST = "openwrt-rockchip-armv8-widora_mangopi-m28c.manifest"
INDEX_FILES = ("Packages", "Packages.gz", "Packages.sig")
UNINDEXED = {"kernel", "libc"}
SAFE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]*\Z")
SAFE_IPK = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.+~%=-]*\.ipk\Z")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def contained_file(path: Path, boundary: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(boundary) or not resolved.is_file():
        raise ValueError(f"File escapes its build boundary or is not regular: {path}")
    return resolved


def control_records(data: bytes) -> list[dict[str, str]]:
    records, record, previous = [], {}, None
    for line in data.decode("utf-8").splitlines() + [""]:
        if not line:
            if record:
                records.append(record)
            record, previous = {}, None
        elif line[0].isspace():
            if previous is None:
                raise ValueError("Control continuation without a field")
            record[previous] += "\n" + line
        else:
            name, separator, value = line.partition(":")
            if not separator or not name or name in record:
                raise ValueError(f"Invalid or duplicate control field: {name}")
            record[name], previous = value.strip(), name
    return records


def public_key_id(path: Path) -> str:
    lines = path.read_bytes().decode("ascii").splitlines()
    if len(lines) != 2 or not lines[0].startswith("untrusted comment:"):
        raise ValueError("Expected a usign public key, not a private key")
    raw = base64.b64decode(lines[1], validate=True)
    if len(raw) != 42 or raw[:2] != b"Ed":
        raise ValueError("Expected a 42-byte Ed25519 usign public key")
    return raw[2:10].hex()


def verify_signature(usign: Path, public_key: Path, directory: Path) -> None:
    command = [str(usign), "-V", "-m", str(directory / "Packages"),
               "-p", str(public_key), "-x", str(directory / "Packages.sig")]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ValueError(f"usign verification failed for {directory}: {detail[:500]}") from error


def ipk_control(path: Path) -> dict[str, str]:
    # This LEDE branch produces gzip tar IPKs, as read by ipkg-make-index.sh.
    # Read control data in memory, never extract archive paths to the host.
    with tarfile.open(path, "r:gz") as package:
        members = [m for m in package.getmembers() if m.name in {"control.tar.gz", "./control.tar.gz"}]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
            raise ValueError(f"Invalid IPK control archive: {path.name}")
        content = package.extractfile(members[0]).read()
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as controls:
        members = [m for m in controls.getmembers() if m.name in {"control", "./control"}]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
            raise ValueError(f"Invalid IPK control record: {path.name}")
        records = control_records(controls.extractfile(members[0]).read())
    if len(records) != 1:
        raise ValueError(f"Expected one IPK control record: {path.name}")
    return records[0]


def identity(record: dict[str, str], architecture: str) -> tuple[str, str]:
    name, version = record.get("Package", ""), record.get("Version", "")
    if not SAFE_NAME.fullmatch(name) or not version or any(c.isspace() for c in version):
        raise ValueError("Missing or invalid package name/version")
    if record.get("Architecture") not in {architecture, "all"}:
        raise ValueError(f"Wrong package architecture: {name}")
    return name, version


def read_manifest(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, version = line.partition(" - ")
        if not separator or not SAFE_NAME.fullmatch(name) or not version or name in result:
            raise ValueError(f"Invalid or duplicate firmware manifest entry: {line}")
        result[name] = version
    if "kernel" not in result or "libc" not in result:
        raise ValueError("Firmware manifest must identify kernel and libc")
    return result


def selected_ipk_packages(lede: Path, config: str) -> tuple[set[str], list[str]]:
    selected = set(re.findall(r"^CONFIG_PACKAGE_([^=\n]+)=[ym]$", config, re.M))
    data = contained_file(lede / "tmp/.packageinfo", lede).read_bytes()
    catalog, current, in_fields, free_text = {}, None, False, False
    # .packageinfo contains free-form Description/Config sections terminated by
    # @@ and legacy non-UTF-8 descriptions. Only the structural fields used for
    # package selection need decoding; no signed index bytes are transformed.
    for number, raw in enumerate(data.split(b"\n"), 1):
        line = raw.removesuffix(b"\r")
        if free_text:
            if line == b"@@":
                free_text = False
            continue
        if line.startswith((b"Description:", b"Config:")):
            free_text, in_fields = True, False
            continue
        if line.startswith(b"Package: "):
            try:
                current = line.removeprefix(b"Package: ").decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError(f"Non-ASCII Package in .packageinfo line {number}") from error
            in_fields = True
            if current in catalog or not SAFE_NAME.fullmatch(current):
                raise ValueError(f"Invalid or duplicate build package metadata: {current}")
            catalog[current] = {}
        elif in_fields and line.startswith((b"Type:", b"Build-Only:")):
            field, value = line.split(b":", 1)
            try:
                catalog[current][field.decode("ascii")] = value.strip().decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError(f"Non-ASCII {field.decode('ascii')} for {current} "
                                 f"in .packageinfo line {number}") from error
    if free_text:
        raise ValueError("Unterminated Description/Config in .packageinfo")
    # The pinned package/kernel/linux/Makefile skips BuildPackage,kernel during
    # DUMP and selects it locally during compilation. It is intentionally absent
    # from .packageinfo/.config; its actual IPK and full ABI are checked against
    # the mandatory firmware manifest independently of this selected inventory.
    if "libc" not in catalog:
        raise ValueError("Incomplete build package metadata")
    chosen = selected & catalog.keys()
    packages = {name for name in chosen if "ipkg" in catalog[name].get("Type", "").split()
                and catalog[name].get("Build-Only", "0") in {"", "0"}}
    if not packages:
        raise ValueError("Generated configuration contains no selected IPK packages")
    return packages, sorted(chosen - packages)


def inspect_feed(directory: Path, name: str, bin_root: Path, architecture: str,
                 key: Path, usign: Path, manifest: dict[str, str]) -> dict:
    if not SAFE_NAME.fullmatch(name) or name.startswith("."):
        raise ValueError(f"Unsafe feed name: {name}")
    directory = directory.resolve(strict=True)
    if not directory.is_relative_to(bin_root) or not directory.is_dir():
        raise ValueError(f"Feed directory escapes build outputs: {directory}")
    indices = {filename: contained_file(directory / filename, bin_root) for filename in INDEX_FILES}
    data = indices["Packages"].read_bytes()
    if gzip.decompress(indices["Packages.gz"].read_bytes()) != data:
        raise ValueError(f"Compressed index differs from Packages: {name}")
    verify_signature(usign, key, directory)
    try:
        records = control_records(data)
    except UnicodeDecodeError as error:
        raise ValueError(f"Invalid UTF-8 in signed {name}/Packages at byte {error.start}") from error
    files, packages, seen = {}, [], set()
    for record in records:
        package_name, version = identity(record, architecture)
        filename = record.get("Filename", "")
        if filename.startswith("./"):
            filename = filename[2:]
        if not SAFE_IPK.fullmatch(filename) or filename in seen:
            raise ValueError(f"Unsafe or duplicate IPK filename in {name}: {filename}")
        seen.add(filename)
        expected_hash, size_text = record.get("SHA256sum", ""), record.get("Size", "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or not re.fullmatch(r"[0-9]+", size_text):
            raise ValueError(f"Missing IPK hash or size: {filename}")
        path = contained_file(directory / filename, bin_root)
        actual_hash, actual_size = digest(path), path.stat().st_size
        if actual_hash != expected_hash or actual_size != int(size_text):
            raise ValueError(f"IPK hash or size mismatch: {filename}")
        files[filename] = path
        packages.append({"name": package_name, "version": version, "filename": filename,
                         "sha256": actual_hash, "size": actual_size, "index_signed": True,
                         "provides": record.get("Provides", "")})
    # Upstream excludes only kernel and libc; all other missing index entries
    # are fatal instead of silently discarding packages selected by this build.
    for path in sorted(directory.glob("*.ipk")):
        if path.name in files:
            continue
        if not SAFE_IPK.fullmatch(path.name):
            raise ValueError(f"Unsafe IPK filename: {path.name}")
        filename = path.name
        path = contained_file(path, bin_root)
        record = ipk_control(path)
        package_name, version = identity(record, architecture)
        if package_name not in UNINDEXED or manifest.get(package_name) != version:
            raise ValueError(f"Unexpected unindexed package: {package_name} {version}")
        files[filename] = path
        packages.append({"name": package_name, "version": version, "filename": filename,
                         "sha256": digest(path), "size": path.stat().st_size,
                         "index_signed": False, "provides": record.get("Provides", ""),
                         "reason": "Pinned upstream index generator excludes kernel and libc"})
    if len(list(directory.rglob("*.ipk"))) != len(files):
        raise ValueError(f"Nested or unaccounted IPKs in {name}")
    return {"name": name, "directory": directory, "indices": indices, "files": files,
            "packages": packages, "index_hashes": {k: digest(v) for k, v in indices.items()}}


def export_repository(lede: Path, output: Path) -> dict:
    lede, output = lede.resolve(strict=True), output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists; preserved: {output}")
    output = output.resolve()
    if output.is_relative_to(lede) or lede.is_relative_to(output):
        raise ValueError("Output must be outside the LEDE source tree")
    bin_root = (lede / "bin").resolve(strict=True)
    if not bin_root.is_relative_to(lede):
        raise ValueError("Build output directory escapes LEDE")
    target = lede / TARGET
    manifest_path = contained_file(target / MANIFEST, bin_root)
    manifest = read_manifest(manifest_path)
    profiles = json.loads(contained_file(target / "profiles.json", bin_root).read_text(encoding="utf-8"))
    architecture = profiles.get("arch_packages", "")
    if not SAFE_NAME.fullmatch(architecture):
        raise ValueError("Missing or unsafe target package architecture")
    key = contained_file(lede / "key-build.pub", lede)
    key_id = public_key_id(key)
    usign = contained_file(lede / "staging_dir/host/bin/usign", lede)
    candidates = [("core", target / "packages")]
    architecture_dir = lede / "bin/packages" / architecture
    if not architecture_dir.is_dir():
        raise ValueError("Missing per-feed package directory")
    for directory in sorted(architecture_dir.iterdir()):
        if not directory.is_dir():
            raise ValueError(f"Unexpected entry in per-feed directory: {directory}")
        if directory.name == "core":
            raise ValueError("Feed name collides with target core repository")
        if any(directory.rglob("*.ipk")) or any((directory / n).exists() for n in INDEX_FILES):
            candidates.append((directory.name, directory))
    for other in (lede / "bin/packages").iterdir():
        if other.name != architecture and any(other.rglob("*.ipk")):
            raise ValueError(f"Unexpected additional package architecture: {other.name}")
    feeds = [inspect_feed(path, name, bin_root, architecture, key, usign, manifest) for name, path in candidates]
    identities, provided = {}, set()
    for feed in feeds:
        for package in feed["packages"]:
            ident = (package["name"], package["version"])
            if ident in identities and identities[ident] != package["sha256"]:
                raise ValueError(f"Ambiguous package contents across feeds: {ident}")
            identities[ident] = package["sha256"]
            provided.add(package["name"])
            for alias in package["provides"].split(","):
                if alias.strip():
                    provided.add(alias.strip().split()[0])
    missing = [f"{name} - {version}" for name, version in manifest.items() if (name, version) not in identities]
    if missing:
        raise ValueError("Firmware packages missing from export: " + ", ".join(missing))
    config = contained_file(lede / ".config", lede).read_text(encoding="utf-8")
    selected, build_only = selected_ipk_packages(lede, config)
    if selected - provided:
        raise ValueError("Selected packages missing from export: " + ", ".join(sorted(selected - provided)))
    report = {"schema": 1, "target": "rockchip/armv8", "architecture": architecture,
              "kernel": manifest["kernel"], "public_key_id": key_id,
              "public_key_sha256": digest(key), "firmware_manifest_packages": len(manifest),
              "selected_packages_verified": len(selected),
              "selected_non_ipk_build_packages": build_only,
              "package_files": sum(len(f["packages"]) for f in feeds),
              "feeds": [{k: f[k] for k in ("name", "packages", "index_hashes")} for f in feeds],
              "unindexed_exception": "Only kernel/libc are excluded by pinned upstream; their IPKs are retained with control/manifest checks and recorded hashes, not claimed to have Packages signature coverage."}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".m28c-package-export-", dir=output.parent)).resolve()
    try:
        for feed in feeds:
            destination = staging / feed["name"]
            destination.mkdir()
            for filename, path in {**feed["indices"], **feed["files"]}.items():
                shutil.copyfile(path, destination / filename)
            for filename, expected_hash in feed["index_hashes"].items():
                if digest(destination / filename) != expected_hash:
                    raise ValueError("Index changed during export")
            for package in feed["packages"]:
                copied = destination / package["filename"]
                if digest(copied) != package["sha256"] or copied.stat().st_size != package["size"]:
                    raise ValueError("IPK changed during export")
        shutil.copyfile(key, staging / "key-build.pub")
        if digest(staging / "key-build.pub") != report["public_key_sha256"]:
            raise ValueError("Public key changed during export")
        shutil.copyfile(manifest_path, staging / MANIFEST)
        (staging / "repository.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        offline_config = "".join(f"src/gz m28c_{f['name']} file:///opt/m28c-packages/{f['name']}\n" for f in feeds)
        (staging / "offline-feeds.conf").write_text(offline_config, encoding="utf-8")
        (staging / "README.md").write_text(readme(report), encoding="utf-8")
        checksums = "".join(f"{digest(path)}  {path.relative_to(staging).as_posix()}\n"
                            for path in sorted(staging.rglob("*")) if path.is_file())
        (staging / "sha256sums").write_text(checksums, encoding="ascii")
        staging.rename(output)
    finally:
        # Only remove our checked temporary child, never a requested output or
        # a computed directory outside the export parent (including on Windows).
        if staging.exists():
            if staging.parent != output.parent.resolve() or not staging.name.startswith(".m28c-package-export-"):
                raise ValueError("Unsafe temporary export cleanup path")
            shutil.rmtree(staging)
    return report


def readme(report: dict) -> str:
    return f"""# M28C packages from the firmware build

Target: rockchip/armv8 ({report['architecture']}).
Required installed kernel: `{report['kernel']}`.
Build public key ID: `{report['public_key_id']}`.
Exported IPKs: {report['package_files']}; firmware manifest entries verified: {report['firmware_manifest_packages']}.

Every copied Packages index was verified with the build host's usign and
key-build.pub. Packages.gz decompresses to the exact signed Packages bytes.
Every indexed IPK's size and SHA256 match that signed index. No private key is
included. repository.json records the checks and hashes; sha256sums covers the
export files. Checksums alone do not establish trust in an untrusted download.

## Use offline with the matching firmware

Extract this directory to /opt/m28c-packages on the matching device, after
confirming /opt is mounted on its data partition. Preserve the feed directories.
Keep `option check_signature` in /etc/opkg.conf. Confirm the installed kernel's
complete version equals the value above and that the public key is already
trusted by the same firmware. Do not blindly trust a key from another build.

The supplied offline-feeds.conf uses file:///opt/m28c-packages/<feed> URLs.
For a separate offline transaction, copy /etc/opkg.conf to a temporary config,
remove any src/src-gz/src/gz declarations from that temporary copy, and append
offline-feeds.conf. Use `opkg -f /tmp/m28c-offline-opkg.conf update` followed by
`opkg -f /tmp/m28c-offline-opkg.conf install <chosen-package>`. The temporary
config retains root/overlay destinations and signature checking while selecting
only these repositories. Back up and inspect configuration before making changes.

Do not force dependencies or bulk-upgrade a running installation. kmod packages
are only for this exact kernel ABI, not another build with the same version name.

## Upstream kernel/libc exception

The locked LEDE scripts/ipkg-make-index.sh deliberately omits kernel and libc
from Packages. Their actual IPKs are retained, their control metadata must match
the firmware manifest, and their hashes/sizes are recorded in repository.json.
They are NOT covered by the Packages signature. They will not be offered by
opkg's feed index and should not be installed to replace a running kernel/libc.
No other unindexed IPK is accepted. All firmware manifest entries and all y/m
selected IPK package names (including Provides aliases) must be present in the
export. tmp/.packageinfo distinguishes actual IPK packages from package suboptions
and build-only bootloader images; excluded build-only names are recorded.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lede", type=Path)
    parser.add_argument("output", type=Path, help="new output directory outside LEDE")
    args = parser.parse_args()
    try:
        report = export_repository(args.lede, args.output)
    except (OSError, ValueError, EOFError, tarfile.TarError) as error:
        print(f"Package export failed: {error}", file=sys.stderr)
        return 1
    print(f"Exported {report['package_files']} IPKs from {len(report['feeds'])} verified feeds; kernel {report['kernel']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
