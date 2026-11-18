"""Catch missing, altered or unmanaged fan payloads before shipping an image."""

import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fan_package", ROOT / "scripts/verify-fan-package.py")
FAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FAN)


class FanPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="m28c-fan-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "package"
        shutil.copytree(FAN.PACKAGE, self.package)
        self.source = json.loads((self.package / "SOURCE.json").read_text(encoding="utf-8"))
        self.verified = FAN.verify_source(self.package)
        self.image = self.root / "lede/build_dir/target-test/root-rockchip"
        shutil.copytree(self.package / "files", self.image)
        for name, record in self.source["files"].items():
            (self.image / name).chmod(int(record["mode"], 8))
        self.info = self.image / "usr/lib/opkg/info"
        self.info.mkdir(parents=True)
        self.control = self.verified["installed_control"].copy()
        self.control["Source"] = "package/custom/" + FAN.NAME
        self.write_control()
        (self.info / (FAN.NAME + ".conffiles")).write_text("/etc/config/m28c_fan\n", encoding="utf-8")
        (self.info / (FAN.NAME + ".list")).write_text(
            "".join("/" + name + "\n" for name in self.source["files"]), encoding="utf-8")

    def write_control(self):
        (self.info / (FAN.NAME + ".control")).write_text(
            "".join(key + ": " + value + "\n" for key, value in self.control.items()), encoding="utf-8")

    def enable_service(self):
        link = self.image / "etc/rc.d/S99m28c-fan"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to("../init.d/m28c-fan")
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Image service symlink requires Windows symlink privilege")
            raise
        return link

    def test_actual_supplied_ipk_and_all_payload_files_match(self):
        source = FAN.verify_source(self.package)
        self.assertEqual(source["sha256"], "4f43ab3d4e8b190beed27915fc4ab8ed26bfb29c46e6f11f82bcd557e0840b00")
        self.assertEqual(source["control"]["Version"], "2026.11.16-r3")
        self.assertEqual(source["installed_control"]["Version"], "2026.11.16-r3.m28c1")
        self.assertEqual(len(source["files"]), 7)
        changed = {name for name in source["files"]
                   if source["files"][name] != source["installed_files"][name]}
        self.assertEqual(changed, {FAN.UI_PATH})
        menu = json.loads((self.package / "files/usr/share/luci/menu.d/luci-app-m28c-fan.json").read_bytes())
        self.assertEqual(menu["admin/system/m28c-fan"]["title"], "风扇控制")

    def test_changed_original_archive_is_rejected(self):
        archive = self.package / "original.ipk"
        archive.write_bytes(archive.read_bytes() + b"unexpected trailer")
        with self.assertRaisesRegex(ValueError, "IPK hash or size"):
            FAN.verify_source(self.package)

    def test_changed_expanded_payload_is_rejected(self):
        worker = self.package / "files/usr/sbin/m28c-fan-control"
        worker.write_bytes(worker.read_bytes() + b"\n# changed\n")
        with self.assertRaisesRegex(ValueError, "payload differs"):
            FAN.verify_source(self.package)

    def test_missing_and_extra_source_files_are_rejected(self):
        worker = self.package / "files/usr/sbin/m28c-fan-control"
        original = worker.read_bytes()
        worker.unlink()
        with self.assertRaisesRegex(ValueError, "file set"):
            FAN.verify_source(self.package)
        worker.write_bytes(original)
        (self.package / "files/private-settings").write_bytes(b"not part of the supplied IPK")
        with self.assertRaisesRegex(ValueError, "file set"):
            FAN.verify_source(self.package)

    def test_undeclared_ui_edit_is_rejected(self):
        ui = self.package / "files" / FAN.UI_PATH
        ui.write_bytes(ui.read_bytes() + b"\nwindow.unreviewed = true;\n")
        with self.assertRaisesRegex(ValueError, "payload differs"):
            FAN.verify_source(self.package)

    def test_ui_derivative_requires_exact_original_identity_and_output(self):
        path = self.package / "UI-CHANGES.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (("source_archive_sha256", "0" * 64),
                           ("source_version", "2026.11.16-r2"),
                           ("version", "2026.11.16-r3"),
                           ("path", "usr/sbin/m28c-fan-control"),
                           ("input_sha256", "0" * 64),
                           ("output_sha256", "0" * 64)):
            with self.subTest(key=key):
                record = {**original, key: value}
                path.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "UI derivative"):
                    FAN.verify_source(self.package)

    def test_ui_record_cannot_inject_code_or_change_nontranslated_values(self):
        path = self.package / "UI-CHANGES.json"
        original = path.read_bytes()
        mutations = [
            {"after": "'); alert(1); _('injected"},
            {"after": "unsafe\\x27 escape"},
            {"before": "m28c_fan"},
            {"occurrences": 1000},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                record = json.loads(original)
                record["replacements"][0].update(mutation)
                path.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "UI.*replacement"):
                    FAN.verify_source(self.package)
        record = json.loads(original)
        record["replacements"].append(record["replacements"][0])
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            FAN.verify_source(self.package)

    def test_missing_derivative_record_is_rejected(self):
        (self.package / "UI-CHANGES.json").unlink()
        with self.assertRaisesRegex(ValueError, "regular package file"):
            FAN.verify_source(self.package)

    def test_forged_control_or_script_record_is_rejected(self):
        original = (self.package / "SOURCE.json").read_bytes()
        for section, key, value in (("control", "Version", "0.0.0"),
                                    ("control_scripts", "postinst", {"size": 1, "sha256": "0" * 64})):
            with self.subTest(section=section):
                source = json.loads(original)
                source[section][key] = value
                (self.package / "SOURCE.json").write_text(json.dumps(source), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "control"):
                    FAN.verify_source(self.package)

    def test_unsafe_or_link_archive_member_is_rejected_without_extraction(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("valid", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w:") as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    if kind == tarfile.SYMTYPE:
                        member.linkname = "/etc/passwd"
                    archive.addfile(member)
                with self.assertRaises(ValueError):
                    FAN.tar_files(gzip.compress(buffer.getvalue()))
        self.assertFalse((self.root / "outside").exists())

    def test_actual_file_bytes_are_checked_in_image(self):
        worker = self.image / "usr/sbin/m28c-fan-control"
        worker.write_bytes(b"stale controller\n")
        with self.assertRaisesRegex(ValueError, "Image fan payload differs"):
            FAN.verify_image(self.image, self.package)

    def test_original_ui_cannot_silently_replace_declared_derivative_in_image(self):
        outer = FAN.tar_files((self.package / "original.ipk").read_bytes())
        original_ui = FAN.tar_files(outer["data.tar.gz"][0])[FAN.UI_PATH][0]
        (self.image / FAN.UI_PATH).write_bytes(original_ui)
        with self.assertRaisesRegex(ValueError, "Image fan payload differs"):
            FAN.verify_image(self.image, self.package)

    def test_missing_file_is_rejected_in_image(self):
        (self.image / "etc/uci-defaults/90-m28c-fan").unlink()
        with self.assertRaisesRegex(ValueError, "Missing regular package file"):
            FAN.verify_image(self.image, self.package)

    def test_overlay_files_without_opkg_package_are_rejected(self):
        (self.info / (FAN.NAME + ".control")).unlink()
        with self.assertRaisesRegex(ValueError, "Missing regular package file"):
            FAN.verify_image(self.image, self.package)

    def test_wrong_installed_identity_or_dependencies_are_rejected(self):
        for field, value in (("Version", "2026.11.16-r2"), ("Architecture", "x86_64"),
                             ("Version", "2026.11.16-r3"),
                             ("Source", "feeds/other/luci-app-m28c-fan"),
                             ("Provides", ""), ("Depends", "libc")):
            with self.subTest(field=field):
                previous = self.control[field]
                self.control[field] = value
                self.write_control()
                with self.assertRaises(ValueError):
                    FAN.verify_image(self.image, self.package)
                self.control[field] = previous

    def test_configuration_and_package_ownership_are_required(self):
        path = self.info / (FAN.NAME + ".conffiles")
        path.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conffiles"):
            FAN.verify_image(self.image, self.package)
        path.write_text("/etc/config/m28c_fan\n", encoding="utf-8")
        (self.info / (FAN.NAME + ".list")).write_text("/etc/config/m28c_fan\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not own"):
            FAN.verify_image(self.image, self.package)

    def test_complete_files_without_enabled_boot_service_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "boot service"):
            FAN.verify_image(self.image, self.package)

    def test_complete_image_passes(self):
        self.enable_service()
        self.assertEqual(FAN.verify_image(self.image, self.package), 7)

    def test_wrong_boot_service_target_is_rejected(self):
        link = self.enable_service()
        link.unlink()
        link.symlink_to("../init.d/other-service")
        with self.assertRaisesRegex(ValueError, "boot service"):
            FAN.verify_image(self.image, self.package)

    @unittest.skipIf(os.name == "nt", "Unix modes are checked on the Linux build runner")
    def test_nonexecutable_fan_worker_is_rejected(self):
        (self.image / "usr/sbin/m28c-fan-control").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "file mode"):
            FAN.verify_image(self.image, self.package)

    def test_cli_checks_source_and_rejects_ambiguous_or_missing_image_tree(self):
        def run(*args):
            return subprocess.run([sys.executable, str(ROOT / "scripts/verify-fan-package.py"),
                                   "--package", str(self.package), *map(str, args)],
                                  capture_output=True, text=True, timeout=20)
        self.assertEqual(run().returncode, 0)
        missing = run("--lede", self.root / "absent")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("found 0", missing.stderr)
        (self.root / "lede/build_dir/target-another/root-rockchip").mkdir(parents=True)
        multiple = run("--lede", self.root / "lede")
        self.assertNotEqual(multiple.returncode, 0)
        self.assertIn("found 2", multiple.stderr)

    def test_actual_makefile_install_preserves_supplied_payload_and_metadata(self):
        make = os.environ.get("MAKE") or shutil.which("make")
        if not make:
            self.skipTest("GNU make is required for the real package install regression")
        (self.package / "rules.mk").write_text(
            "INCLUDE_DIR:=.\nPKG_BUILD_DIR:=build\n"
            "INSTALL_DIR:=install -d -m0755\nINSTALL_BIN:=install -m0755\n"
            "INSTALL_DATA:=install -m0644\n", encoding="utf-8")
        (self.package / "package.mk").write_text(
            "define BuildPackage\n$(eval $(call Package/$(1)))\n"
            ".PHONY: package-test\npackage-test:\n"
            "\t$(Build/Prepare)\n\t$(call Package/$(1)/install,image)\n"
            "\t@printf '%s\\n' '$(VERSION)' > tested-version\n"
            "\t@printf '%s\\n' '$(PKGARCH)' > tested-architecture\n"
            "\t@printf '%s\\n' '$(DEPENDS)' > tested-depends\n"
            "\t@printf '%s\\n' '$(PROVIDES)' > tested-provides\n"
            "\t@printf '%s\\n' '$(Package/$(1)/conffiles)' > tested-conffiles\n"
            "endef\n", encoding="utf-8")
        result = subprocess.run([make, "--no-print-directory", "TOPDIR=.", "package-test"],
                                cwd=self.package, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name, record in self.verified["installed_files"].items():
            path = self.package / "image" / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"], name)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o7777, int(record["mode"], 8), name)
        self.assertEqual((self.package / "tested-version").read_text().strip(),
                         self.verified["installed_control"]["Version"])
        self.assertEqual((self.package / "tested-architecture").read_text().strip(), "all")
        self.assertEqual(set((self.package / "tested-depends").read_text().split()), {"+libc", "+luci-base"})
        self.assertEqual((self.package / "tested-provides").read_text().strip(), "luci-app-m28c-fan-any")
        self.assertEqual((self.package / "tested-conffiles").read_text().splitlines(), self.source["conffiles"])


if __name__ == "__main__":
    unittest.main()
