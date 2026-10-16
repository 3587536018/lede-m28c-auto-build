"""Pinned-source and real shell behavior tests, without touching live devices."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("customize_automount", ROOT / "scripts/customize-automount.py")
automount = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(automount)
FIXTURES = ROOT / "tests/fixtures/automount"
SHELL = shutil.which("bash") or next((str(p) for p in (Path("C:/Program Files/Git/bin/bash.exe"),) if p.exists()), None)
PATHS = {
    "Makefile": "package/lean/automount/Makefile",
    "15-automount": "package/lean/automount/files/15-automount",
    "zzz-move-automount": "package/lean/automount/files/zzz-move-automount",
    "mount.hotplug": "package/system/fstools/files/mount.hotplug",
    "fstab.default": "package/system/fstools/files/fstab.default",
    "fstools.Makefile": "package/system/fstools/Makefile",
    "smb.auto": "feeds/packages/net/samba4/files/smb.auto",
    "samba.Makefile": "feeds/packages/net/samba4/Makefile",
}


def populate(root):
    for name, relative in PATHS.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name, target)


class AutomountSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        populate(self.root)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_pinned_fixtures_and_complete_idempotent_package_replacement(self):
        for name, expected in automount.HASHES.items():
            self.assertEqual(automount.digest((FIXTURES / name).read_text(encoding="utf-8")), expected)
        automount.customize(self.root)
        before = self.snapshot()
        automount.customize(self.root)
        self.assertEqual(before, self.snapshot())
        makefile = (self.root / PATHS["Makefile"]).read_text()
        self.assertIn("PKG_RELEASE:=41.m28c1", makefile)
        self.assertNotIn("15-automount", makefile)
        self.assertFalse((self.root / PATHS["15-automount"]).exists())
        self.assertFalse((self.root / PATHS["zzz-move-automount"]).exists())
        for name in ("m28c-automount", "05-m28c-automount", "m28c-automount.init"):
            self.assertEqual((self.root / "package/lean/automount/files" / name).read_bytes(), (ROOT / "scripts/automount" / name).read_bytes())

    def test_changed_upstream_rejected_before_any_write(self):
        path = self.root / PATHS["smb.auto"]
        path.write_text(path.read_text() + "# upstream changed\n", newline="\n")
        before = self.snapshot()
        with self.assertRaises(automount.PatchError):
            automount.customize(self.root)
        self.assertEqual(before, self.snapshot())

    def test_changed_managed_runtime_is_preserved(self):
        automount.customize(self.root)
        path = self.root / "package/lean/automount/files/m28c-automount"
        path.write_text(path.read_text() + "# local edit\n", newline="\n")
        before = self.snapshot()
        with self.assertRaisesRegex(automount.PatchError, "runtime source changed"):
            automount.customize(self.root)
        self.assertEqual(before, self.snapshot())

    def test_changed_obsolete_hook_is_not_deleted(self):
        path = self.root / PATHS["15-automount"]
        path.write_text("# user hook\n", newline="\n")
        before = self.snapshot()
        with self.assertRaisesRegex(automount.PatchError, "refusing to remove"):
            automount.customize(self.root)
        self.assertEqual(before, self.snapshot())

    @unittest.skipUnless(SHELL, "Bash not available")
    def test_first_boot_does_not_reserve_inserted_disks_or_replace_existing_fstab(self):
        automount.customize(self.root)
        config = self.root / "fstab"
        source = (self.root / PATHS["fstab.default"]).read_text().replace("/etc/config/fstab", config.as_posix())
        script = self.root / "firstboot.sh"
        script.write_text(source, newline="\n")
        subprocess.run([SHELL, str(script)], check=True, capture_output=True)
        self.assertIn("option auto_mount '1'", config.read_text())
        self.assertIn("option anon_mount '0'", config.read_text())
        self.assertNotIn("config mount", config.read_text())
        config.write_text("# user's disabled and explicit UUID mounts\n", newline="\n")
        subprocess.run([SHELL, str(script)], check=True, capture_output=True)
        self.assertEqual(config.read_text(), "# user's disabled and explicit UUID mounts\n")

    @unittest.skipUnless(SHELL, "Bash not available")
    def test_busy_owned_remove_skips_native_detach_and_unowned_remove_still_works(self):
        automount.customize(self.root)
        helper = self.root / "helper"
        helper.write_text('#!/bin/sh\n[ "$OWNED" = 1 ]\n', newline="\n")
        helper.chmod(0o755)
        native = self.root / "block"
        native.write_text('#!/bin/sh\nprintf native >> "$TRACE"\n', newline="\n")
        native.chmod(0o755)
        source = (self.root / PATHS["mount.hotplug"]).read_text().replace("/usr/libexec/m28c-automount", helper.as_posix()).replace("/sbin/block", native.as_posix())
        script = self.root / "hotplug.sh"
        script.write_text(source, newline="\n")
        trace = self.root / "trace"
        for owned, expected in (("1", ""), ("0", "native")):
            trace.write_text("")
            subprocess.run([SHELL, str(script)], check=True, capture_output=True, env={**os.environ, "ACTION": "remove", "DEVNAME": "sda3", "OWNED": owned, "TRACE": trace.as_posix()})
            self.assertEqual(trace.read_text(), expected)

    @unittest.skipUnless(SHELL, "Bash not available")
    def test_samba_autoshare_remove_preserves_busy_mount_data(self):
        automount.customize(self.root)
        data = self.root / "mnt/removable/sda3"
        data.mkdir(parents=True)
        important = data / "important.txt"
        important.write_text("preserve")
        source = (self.root / PATHS["smb.auto"]).read_text()
        source = source.replace(". /lib/functions.sh", "").replace(". /lib/functions/service.sh", "")
        source = source.replace("/etc/init.d/samba4 reload", "true").replace("/mnt", (self.root / "mnt").as_posix())
        prefix = '''config_load() { :; }
config_get_bool() { autoshare=1; }
config_foreach() { "$1" settings; }
uci() {
 case "$*" in
  'get samba4.@sambashare[0].device') echo sda3 ;;
  'get samba4.@sambashare[0].auto') echo 1 ;;
  'get samba4.@sambashare[0].name') echo removable/sda3 ;;
  delete*|commit) : ;;
  *) return 1 ;;
 esac
}
'''
        script = self.root / "samba.sh"
        # Hotplug files are sourced, so top-level return is legal.
        script.write_text(prefix + "\n" + source, newline="\n")
        subprocess.run([SHELL, "-c", '. "$1"', "fixture", str(script)], check=True, capture_output=True, env={**os.environ, "ACTION": "remove", "DEVPATH": "/devices/block/sda/sda3"})
        self.assertEqual(important.read_text(), "preserve")
        self.assertNotIn("rm -rf", source)


if __name__ == "__main__":
    unittest.main()
