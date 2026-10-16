#!/usr/bin/env python3
"""Reject an image staging tree that omitted the checked runtime package fixes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat


# Compare installed bytes with the actual patched package inputs, after make.
# These paths contain public code/defaults, never a running router's config.
FILES = {
    "etc/init.d/dockerd": "feeds/packages/utils/dockerd/files/dockerd.init",
    "etc/config/dockerd": "feeds/packages/utils/dockerd/files/etc/config/dockerd",
    "usr/libexec/m28c-docker.sh": "feeds/packages/utils/dockerd/files/m28c-docker.sh",
    "etc/docker/fw4-lan-published.nft": "feeds/packages/utils/dockerd/files/fw4-lan-published.nft",
    "etc/uci-defaults/90-m28c-docker-nftables": "feeds/packages/utils/dockerd/files/90-m28c-docker-nftables",
    "usr/libexec/m28c-automount": "package/lean/automount/files/m28c-automount",
    "etc/hotplug.d/block/05-m28c-automount": "package/lean/automount/files/05-m28c-automount",
    "etc/init.d/m28c-automount": "package/lean/automount/files/m28c-automount.init",
    "etc/hotplug.d/block/10-mount": "package/system/fstools/files/mount.hotplug",
    "etc/uci-defaults/10-fstab": "package/system/fstools/files/fstab.default",
    "etc/hotplug.d/block/20-smb": "feeds/packages/net/samba4/files/smb.auto",
}
EXECUTABLES = {
    "etc/init.d/dockerd", "etc/init.d/m28c-automount", "usr/libexec/m28c-automount",
}
OBSOLETE = (
    "etc/15-automount", "etc/hotplug.d/block/15-automount",
    "etc/uci-defaults/zzz-move-automount",
)


def verify(lede: Path, image_root: Path | None = None) -> int:
    if image_root is None:
        candidates = sorted(lede.glob("build_dir/target-*/root-rockchip"))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one compiled root-rockchip tree, found {len(candidates)}")
        image_root = candidates[0]
    for installed, source in FILES.items():
        target = image_root / installed
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"Missing regular runtime file in image: {installed}")
        if target.read_bytes() != (lede / source).read_bytes():
            raise ValueError(f"Image runtime differs from patched package: {installed}")
        if os.name != "nt" and installed in EXECUTABLES and not target.stat().st_mode & stat.S_IXUSR:
            raise ValueError(f"Image runtime is not executable: {installed}")
    for installed in OBSOLETE:
        path = image_root / installed
        if path.exists() or path.is_symlink():
            raise ValueError(f"Obsolete automount script remains in image: {installed}")
    service = image_root / "etc/rc.d/S95m28c-automount"
    if not service.is_symlink() or os.readlink(service) != "../init.d/m28c-automount":
        raise ValueError("Automount boot scan is not enabled in the image")
    return len(FILES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lede", type=Path)
    parser.add_argument("--image-root", type=Path)
    args = parser.parse_args()
    count = verify(args.lede.resolve(), args.image_root)
    print(f"Verified {count} installed runtime files, automount boot service and absence of obsolete hooks")
