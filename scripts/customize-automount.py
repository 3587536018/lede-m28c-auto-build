#!/usr/bin/env python3
"""Replace the pinned unsafe anonymous automounter in the actual package sources."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

MARKER = "M28C_REMOVABLE_AUTOMOUNT_V1"
HASHES = {
    "Makefile": "7a19f0488c88a7c09a62397a5f924629db27e49c9e7fe7252e4d19dc2564ddcc",
    "15-automount": "eaaaf79335fb9830d816ec4992f86e6bb68ea9d63cbcba88de270b9c737fdf72",
    "zzz-move-automount": "7a41b8279f9a6dca125c9e878c65c6b03844d39ffcf42c15f705ecca0e54bef8",
    "mount.hotplug": "813b089ab1c966c724c9508236e302b81d9e4bcfd6c5572484e1d135db2f2499",
    "fstab.default": "663247e760bbc135ddff2f15e14555d09bc119b931b8ce6eba16259012906885",
    "smb.auto": "4d386243cae95e535922fbfe4cbd7841a36fc1bcaa67424f5d9cdd0556d73bda",
    "fstools.Makefile": "c539426d6494e17e78b241a8040bf468d4ffc74d7e0e1789f01d15c330b2125d",
    "samba.Makefile": "92f7e605cdf13756742bc3c5a3faa6aa7be431402d1a1bdc1b9dd9646ff8d384",
}
INSTALL_OLD = """\t$(INSTALL_DIR) $(1)/etc/uci-defaults
\t$(INSTALL_BIN) ./files/15-automount $(1)/etc/15-automount
\t$(INSTALL_BIN) ./files/zzz-move-automount $(1)/etc/uci-defaults/zzz-move-automount"""
INSTALL_NEW = """\t# M28C_REMOVABLE_AUTOMOUNT_V1: package-owned runtime, no delayed legacy hook.
\t$(INSTALL_DIR) $(1)/usr/libexec $(1)/etc/hotplug.d/block $(1)/etc/init.d
\t$(INSTALL_BIN) ./files/m28c-automount $(1)/usr/libexec/m28c-automount
\t$(INSTALL_BIN) ./files/05-m28c-automount $(1)/etc/hotplug.d/block/05-m28c-automount
\t$(INSTALL_BIN) ./files/m28c-automount.init $(1)/etc/init.d/m28c-automount"""
HOTPLUG_OLD = '[ "$ACTION" = "add" -o "$ACTION" = "remove" ] && /sbin/block hotplug\n'
HOTPLUG_NEW = """# M28C_REMOVABLE_AUTOMOUNT_V1: retain busy mounts owned by the safe helper.
if [ "$ACTION" = remove ] && [ -x /usr/libexec/m28c-automount ] &&
   /usr/libexec/m28c-automount owns "${DEVNAME:-${DEVPATH##*/}}"; then
    :
else
    [ "$ACTION" = "add" -o "$ACTION" = "remove" ] && /sbin/block hotplug
fi
"""
FSTAB_OLD = '[ ! -f /etc/config/fstab ] && ( block detect > /etc/config/fstab )\nexit 0\n'
FSTAB_NEW = """# M28C_REMOVABLE_AUTOMOUNT_V1: do not create disabled entries for first-boot cards.
if [ ! -e /etc/config/fstab ] && [ ! -L /etc/config/fstab ]; then
    cat > /etc/config/fstab <<'M28C_FSTAB'
config global
    option anon_swap '0'
    option anon_mount '0'
    option auto_swap '1'
    option auto_mount '1'
    option delay_root '5'
    option check_fs '0'
M28C_FSTAB
fi
exit 0
"""
SAMBA_OLD = "\t\t\t\t\trm -rf /mnt/$device /mnt/$mount_dir"
SAMBA_NEW = "\t\t\t\t\t# M28C_REMOVABLE_AUTOMOUNT_V1: the mount owner removes only empty directories."


class PatchError(RuntimeError):
    pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_text(text: str, name: str, replacements: list[tuple[str, str]]) -> str:
    original = text
    if digest(original) != HASHES[name]:
        for before, after in reversed(replacements):
            if original.count(after) != 1:
                raise PatchError(f"{name}: unknown upstream or modified M28C patch")
            original = original.replace(after, before, 1)
        if digest(original) != HASHES[name]:
            raise PatchError(f"{name}: pinned source hash mismatch")
    result = original
    for before, after in replacements:
        if result.count(before) != 1:
            raise PatchError(f"{name}: expected exactly one patch anchor")
        result = result.replace(before, after, 1)
    if text != original and text != result:
        raise PatchError(f"{name}: incomplete previous patch")
    return result


def customize(root: Path) -> None:
    package = root / "package/lean/automount"
    fstools = root / "package/system/fstools/files"
    changes: dict[Path, str] = {}
    entries = [
        (package / "Makefile", "Makefile", [("PKG_RELEASE:=40", "PKG_RELEASE:=41.m28c1"), ("+libblkid \\\n", "+libblkid +blkid \\\n"), (INSTALL_OLD, INSTALL_NEW)]),
        (fstools / "mount.hotplug", "mount.hotplug", [(HOTPLUG_OLD, HOTPLUG_NEW)]),
        (fstools / "fstab.default", "fstab.default", [(FSTAB_OLD, FSTAB_NEW)]),
        (root / "feeds/packages/net/samba4/files/smb.auto", "smb.auto", [(SAMBA_OLD, SAMBA_NEW)]),
        (fstools.parent / "Makefile", "fstools.Makefile", [("PKG_RELEASE:=2\n", "PKG_RELEASE:=3.m28c1\n")]),
        (root / "feeds/packages/net/samba4/Makefile", "samba.Makefile", [("PKG_RELEASE:=$(AUTORELEASE)\n", "PKG_RELEASE:=$(AUTORELEASE).m28c1\n")]),
    ]
    for path, name, replacements in entries:
        changes[path] = patch_text(path.read_text(encoding="utf-8"), name, replacements)
    patched = MARKER in (package / "Makefile").read_text(encoding="utf-8")
    obsolete = [package / "files" / name for name in ("15-automount", "zzz-move-automount")]
    for path in obsolete:
        if path.exists():
            if digest(path.read_text(encoding="utf-8")) != HASHES[path.name]:
                raise PatchError(f"{path.name}: refusing to remove changed source")
        elif not patched:
            raise PatchError(f"{path.name}: expected pinned source is missing")
    templates = Path(__file__).resolve().parent / "automount"
    for name in ("m28c-automount", "05-m28c-automount", "m28c-automount.init"):
        text = (templates / name).read_text(encoding="utf-8")
        if MARKER not in text or not text.startswith("#!/bin/sh"):
            raise PatchError(f"{name}: invalid runtime template")
        destination = package / "files" / name
        if destination.exists():
            if destination.is_symlink() or destination.read_text(encoding="utf-8") != text:
                raise PatchError(f"{name}: managed runtime source changed; refusing to overwrite")
        elif patched:
            raise PatchError(f"{name}: managed runtime source is missing")
        changes[destination] = text
    # All input and idempotency checks complete before changing any source.
    for path, text in changes.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    for path in obsolete:
        path.unlink(missing_ok=True)
    print("Automount: checked package replacement, guarded removable mounts, first-boot fstab and Samba cleanup")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    customize(parser.parse_args().root.resolve())
