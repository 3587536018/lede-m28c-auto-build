#!/usr/bin/env python3
"""Prepare immutable LEDE sources without discarding an existing user's edits."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def git(directory, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=check,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def read_lock(path):
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("schema") != 1 or not lock.get("feeds"):
        raise ValueError("Invalid source lock schema or empty feed list")
    names = set()
    for source in [lock["lede"], *lock["feeds"]]:
        if not re.fullmatch(r"[0-9a-f]{40}", source.get("revision", "")):
            raise ValueError("Every source must use a full 40-character commit SHA")
        if not source.get("url") or re.search(r"[\s^;]", source["url"]):
            raise ValueError("Source URLs must not contain whitespace, ^ or ;")
    for feed in lock["feeds"]:
        name = feed.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or name in names:
            raise ValueError("Invalid or duplicate feed name: " + name)
        names.add(name)
    version = lock.get("baseline", {}).get("version", "")
    if not re.fullmatch(r"r[0-9]+-[0-9a-f]{7,40}", version):
        raise ValueError("The baseline must record the original LEDE revision string")
    if not lock["lede"]["revision"].startswith(version.rsplit("-", 1)[1]):
        raise ValueError("The baseline version does not match the pinned LEDE commit")
    return lock


def feeds_config(lock):
    return "".join(
        "src-git {name} {url}^{revision}\n".format(**feed)
        for feed in lock["feeds"]
    ).encode("utf-8")


def checkout_source(directory, source):
    revision = source["revision"]
    if directory.exists():
        if not (directory / ".git").exists():
            raise ValueError(f"{directory} already exists and is not a Git checkout; preserved")
        dirty = git(directory, "status", "--porcelain", "--untracked-files=no").stdout
        if dirty:
            raise ValueError(
                f"{directory} has tracked modifications; preserved without reset or clean. "
                "Commit or back up those edits before preparing the pinned baseline.\n" + dirty
            )
        remote = git(directory, "remote", "get-url", "origin").stdout.strip()
        if remote.removesuffix(".git") != source["url"].removesuffix(".git"):
            raise ValueError(f"Existing origin differs from source lock: {remote}; preserved")
    else:
        directory.mkdir(parents=True)
        git(directory, "init", "--quiet")
        git(directory, "remote", "add", "origin", source["url"])

    if git(directory, "cat-file", "-e", revision + "^{commit}", check=False).returncode:
        print(f"Fetching LEDE {revision}", flush=True)
        git(directory, "fetch", "--depth=1", "origin", revision)
    # No --force: Git itself also refuses to overwrite conflicting untracked files.
    git(directory, "checkout", "--detach", revision)
    actual = git(directory, "rev-parse", "HEAD").stdout.strip()
    if actual != revision:
        raise ValueError(f"Source revision mismatch: {actual} != {revision}")
    print("LEDE pinned to " + actual)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sync_overlay(root, directory, lock):
    state_path = directory / ".m28c-prepared.json"
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    managed = previous.get("managed", {})
    # getver.sh cannot count the historical REBOOT..HEAD range in a shallow
    # checkout. Its supported version-file override preserves the verified label.
    desired = {
        "feeds.conf": feeds_config(lock),
        "version": (lock["baseline"]["version"] + "\n").encode("utf-8"),
    }
    source_files = root / "files"
    if not source_files.is_dir():
        raise ValueError(f"Firmware overlay is missing: {source_files}")
    for source in sorted(source_files.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"Overlay symlinks are not supported: {source}")
        if source.is_file():
            desired["files/" + source.relative_to(source_files).as_posix()] = source.read_bytes()

    # Validate the complete update before writing or removing any managed file.
    for name in set(managed) | set(desired):
        if name not in {"feeds.conf", "version"} and not name.startswith("files/"):
            raise ValueError("Invalid managed overlay path: " + name)
        destination = directory / name
        if ".." in Path(name).parts or any(p.is_symlink() for p in [destination, *destination.parents] if p != directory.parent):
            raise ValueError("Unsafe overlay path: " + name)
        if destination.exists():
            if not destination.is_file():
                raise ValueError(f"Overlay destination is not a file: {destination}")
            current = digest(destination.read_bytes())
            expected = digest(desired[name]) if name in desired else None
            if current not in (managed.get(name), expected):
                raise ValueError(f"User-edited overlay file preserved: {destination}")

    for name in managed.keys() - desired.keys():
        (directory / name).unlink(missing_ok=True)
    for name, content in desired.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        source = source_files / name.removeprefix("files/")
        if name.startswith("files/"):
            destination.chmod(source.stat().st_mode)
    state = {"revision": lock["lede"]["revision"], "managed": {k: digest(v) for k, v in desired.items()}}
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(desired) - 2} overlay files and {len(lock['feeds'])} pinned feeds")


def verify_feeds(directory, lock):
    for feed in lock["feeds"]:
        actual = git(directory / "feeds" / feed["name"], "rev-parse", "HEAD").stdout.strip()
        if actual != feed["revision"]:
            raise ValueError(f"Feed {feed['name']} is {actual}, expected {feed['revision']}")
        print(f"Verified feed {feed['name']}: {actual}")


def verify_baseline(directory, lock):
    actual = git(directory, "rev-parse", "HEAD").stdout.strip()
    if actual != lock["lede"]["revision"]:
        raise ValueError(f"LEDE is {actual}, expected {lock['lede']['revision']}; run prepare.sh")
    if (directory / "feeds.conf").read_bytes() != feeds_config(lock):
        raise ValueError("feeds.conf differs from sources.lock.json; run prepare.sh")
    if (directory / "version").read_text(encoding="utf-8") != lock["baseline"]["version"] + "\n":
        raise ValueError("LEDE version differs from sources.lock.json; run prepare.sh")
    print("Verified pinned LEDE revision, version label and feed configuration")


def required_packages(path):
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_+.-]+", name):
            raise ValueError("Invalid required package name: " + name)
        packages.append(name)
    if not packages:
        raise ValueError("Required package list is empty")
    return packages


def verify_packages(path, required_path, manifest=False):
    lines = set(path.read_text(encoding="utf-8").splitlines())
    names = {line.partition(" - ")[0] for line in lines} if manifest else None
    missing = [name for name in required_packages(required_path)
               if (name not in names if manifest else f"CONFIG_PACKAGE_{name}=y" not in lines)]
    if missing:
        raise ValueError(f"Required packages missing from {path.name}: " + ", ".join(missing))
    print(f"Required packages verified in {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--lede-dir", type=Path)
    parser.add_argument("--lock", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify-baseline", action="store_true")
    mode.add_argument("--verify-feeds", action="store_true")
    mode.add_argument("--verify-config", type=Path)
    mode.add_argument("--verify-manifest", type=Path)
    parser.add_argument("--required-packages", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    directory = (args.lede_dir or root / "lede").resolve()
    lock = read_lock(args.lock or root / "sources.lock.json")
    if args.verify_baseline:
        verify_baseline(directory, lock)
    elif args.verify_feeds:
        verify_feeds(directory, lock)
    elif args.verify_config or args.verify_manifest:
        verify_packages(args.verify_config or args.verify_manifest,
                        args.required_packages or root / "configs/required-packages.txt",
                        manifest=args.verify_manifest is not None)
    else:
        checkout_source(directory, lock["lede"])
        sync_overlay(root, directory, lock)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
