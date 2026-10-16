"""Exercise real package bytes, index integrity, inventory and export boundaries."""
import base64
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_export", ROOT / "scripts/export-package-repository.py")
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)
KERNEL = "6.12.104-1-3fc3553b323ebeaf8484fc8c1710e7e8"
ARCH = "aarch64_generic"


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def make_ipk(name, version, provides=""):
    control = f"Package: {name}\nVersion: {version}\nArchitecture: {ARCH}\n"
    if provides:
        control += f"Provides: {provides}\n"
    control += "Description: Test package\n continuation line\n"
    return tar_bytes({"./debian-binary": b"2.0\n", "./control.tar.gz": tar_bytes({"./control": control.encode()}),
                      "./data.tar.gz": tar_bytes({"./etc/example": b"payload\n"})})


class PackageExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lede = self.root / "lede"
        self.target = self.lede / "bin/targets/rockchip/armv8"
        self.core = self.target / "packages"
        self.base = self.lede / "bin/packages" / ARCH / "base"
        self.output = self.root / "export"
        self.core.mkdir(parents=True)
        self.base.mkdir(parents=True)
        host = self.lede / "staging_dir/host/bin"
        host.mkdir(parents=True)
        (host / "usign").write_text("test executable boundary\n")
        key = b"Ed" + bytes.fromhex("0123456789abcdef") + bytes(range(32))
        (self.lede / "key-build.pub").write_bytes(b"untrusted comment: Local build key\n" + base64.b64encode(key) + b"\n")
        (self.lede / "key-build").write_text("PRIVATE KEY MUST NEVER BE EXPORTED")
        (self.lede / "private-key.pem").write_text("PRIVATE PEM MUST NEVER BE EXPORTED")
        (self.target / "profiles.json").write_text(json.dumps({"arch_packages": ARCH}))
        self.manifest = self.target / "openwrt-rockchip-armv8-widora_mangopi-m28c.manifest"
        self.manifest.write_text(f"kernel - {KERNEL}\nlibc - 1.2.5-4\ndemo - 1.0-1\nlibexample2024 - 2.0-1\n")
        (self.lede / ".config").write_text("CONFIG_PACKAGE_kernel=y\nCONFIG_PACKAGE_libc=y\nCONFIG_PACKAGE_demo=y\nCONFIG_PACKAGE_libexample=m\n")
        (self.lede / "tmp").mkdir()
        self.packageinfo = self.lede / "tmp/.packageinfo"
        self.packageinfo.write_text("".join(f"Package: {name}\nType: ipkg\nDescription: test\n@@\n\n"
                                            for name in ["kernel", "libc", "demo", "libexample"]))
        self.add_package(self.core, "kernel", KERNEL, indexed=False)
        self.add_package(self.core, "libc", "1.2.5-4", indexed=False)
        self.demo = self.add_package(self.core, "demo", "1.0-1")
        self.library = self.add_package(self.base, "libexample2024", "2.0-1", provides="libexample (= 2.0-1)")
        self.write_index(self.core, [self.demo])
        self.write_index(self.base, [self.library])
        # This boundary is a host usign executable in production. Unit tests
        # assert every invocation and fail closed on verifier errors; package
        # hash, gzip, archive metadata and filesystem checks are not mocked.
        self.run = mock.patch.object(EXPORT.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")).start()
        self.addCleanup(mock.patch.stopall)

    def add_package(self, directory, name, version, indexed=True, provides=""):
        filename = f"{name}_{version}_{ARCH}.ipk"
        data = make_ipk(name, version, provides)
        (directory / filename).write_bytes(data)
        return {"Package": name, "Version": version, "Architecture": ARCH,
                "Filename": filename, "Size": str(len(data)), "SHA256sum": hashlib.sha256(data).hexdigest(),
                "Provides": provides, "Description": "Test package"}

    def write_index(self, directory, records):
        text = "\n\n".join("\n".join(f"{key}: {value}" for key, value in record.items()) for record in records) + "\n\n"
        data = text.encode()
        (directory / "Packages").write_bytes(data)
        (directory / "Packages.gz").write_bytes(gzip.compress(data, mtime=0))
        (directory / "Packages.sig").write_text("fixture signature checked at mocked usign boundary\n")

    def export(self):
        return EXPORT.export_repository(self.lede, self.output)

    def reject(self, pattern):
        with self.assertRaisesRegex((ValueError, FileNotFoundError), pattern):
            self.export()
        self.assertFalse(self.output.exists())

    def test_complete_export_keeps_signed_bytes_and_never_copies_private_keys(self):
        report = self.export()
        self.assertEqual(report["package_files"], 4)
        self.assertEqual(report["firmware_manifest_packages"], 4)
        self.assertEqual(report["selected_packages_verified"], 4)
        self.assertEqual(report["kernel"], KERNEL)
        self.assertEqual(report["public_key_id"], "0123456789abcdef")
        self.assertEqual(len(self.run.call_args_list), 2)
        for call in self.run.call_args_list:
            args = call.args[0]
            self.assertEqual(args[1], "-V")
            self.assertEqual(args[args.index("-p") + 1], str(self.lede / "key-build.pub"))
            self.assertTrue(call.kwargs["check"])
            self.assertNotIn("shell", call.kwargs)
        for feed, source in [("core", self.core), ("base", self.base)]:
            for filename in ("Packages", "Packages.gz", "Packages.sig"):
                self.assertEqual((self.output / feed / filename).read_bytes(), (source / filename).read_bytes())
        names = {p.name for p in self.output.rglob("*")}
        self.assertNotIn("key-build", names)
        self.assertNotIn("private-key.pem", names)
        self.assertEqual(len(list(self.output.rglob("*.ipk"))), 4)
        self.assertIn("file:///opt/m28c-packages/core", (self.output / "offline-feeds.conf").read_text())
        self.assertIn("option check_signature", (self.output / "README.md").read_text())
        for line in (self.output / "sha256sums").read_text().splitlines():
            expected, path = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / path).read_bytes()).hexdigest(), expected)
        exceptions = [p for f in report["feeds"] for p in f["packages"] if not p["index_signed"]]
        self.assertEqual({p["name"] for p in exceptions}, {"kernel", "libc"})

    def test_changed_ipk_bytes_are_rejected_by_real_sha256(self):
        path = self.core / self.demo["Filename"]
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        self.reject("hash or size mismatch")

    def test_wrong_index_size_is_rejected(self):
        self.demo["Size"] = str(int(self.demo["Size"]) + 1)
        self.write_index(self.core, [self.demo])
        self.reject("hash or size mismatch")

    def test_gzip_and_signed_plaintext_must_be_identical(self):
        (self.core / "Packages.gz").write_bytes(gzip.compress(b"different index"))
        self.reject("Compressed index differs")

    def test_invalid_utf8_index_reports_feed_and_byte_offset_after_signature_check(self):
        data = (self.core / "Packages").read_bytes().replace(
            b"Description: Test package", b"Description: legacy \xa1 text", 1)
        offset = data.index(b"\xa1")
        (self.core / "Packages").write_bytes(data)
        (self.core / "Packages.gz").write_bytes(gzip.compress(data))
        self.reject(f"Invalid UTF-8 in signed core/Packages at byte {offset}")
        self.run.assert_called_once()
        self.assertIn(str(self.core / "Packages"), self.run.call_args.args[0])
        self.assertEqual((self.core / "Packages").read_bytes(), data)

    def test_signature_failure_stops_before_output_creation(self):
        self.run.side_effect = subprocess.CalledProcessError(1, ["usign"], stderr="verification failed")
        self.reject("usign verification failed")

    def test_missing_signature_is_not_silently_skipped(self):
        (self.base / "Packages.sig").unlink()
        self.reject("Packages.sig")

    def test_missing_ipk_is_not_silently_skipped(self):
        (self.core / self.demo["Filename"]).unlink()
        self.reject(self.demo["Filename"])

    def test_traversal_and_absolute_index_filenames_are_rejected(self):
        for filename in ("../key-build.ipk", "/tmp/demo.ipk", "C:\\secret.ipk", "subdir/demo.ipk"):
            with self.subTest(filename=filename):
                self.demo["Filename"] = filename
                self.write_index(self.core, [self.demo])
                self.reject("Unsafe or duplicate IPK filename")

    def test_dot_slash_basename_is_supported_without_rewriting_signed_index(self):
        self.demo["Filename"] = "./" + self.demo["Filename"]
        self.write_index(self.core, [self.demo])
        self.export()
        self.assertEqual((self.core / "Packages").read_bytes(), (self.output / "core/Packages").read_bytes())

    def test_external_ipk_symlink_is_rejected(self):
        package = self.core / self.demo["Filename"]
        outside = self.root / "external.ipk"
        outside.write_bytes(package.read_bytes())
        package.unlink()
        try:
            package.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Native symlinks unavailable: {error}")
        self.reject("escapes its build boundary")

    def test_existing_output_is_preserved(self):
        self.output.mkdir()
        (self.output / "keep").write_text("user data")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export()
        self.assertEqual((self.output / "keep").read_text(), "user data")

    def test_output_inside_build_tree_is_rejected(self):
        self.output = self.lede / "export"
        self.reject("outside the LEDE")

    def test_unindexed_nonexception_package_is_rejected(self):
        self.add_package(self.base, "extra", "1-1", indexed=False)
        self.reject("Unexpected unindexed package")

    def test_unindexed_kernel_must_match_full_firmware_abi(self):
        for path in self.core.glob("kernel_*.ipk"):
            path.unlink()
        self.add_package(self.core, "kernel", "6.12.104-1-other-abi", indexed=False)
        self.reject("Unexpected unindexed package")

    def test_unindexed_exception_metadata_cannot_trick_path_extraction(self):
        path = next(self.core.glob("libc_*.ipk"))
        path.write_bytes(tar_bytes({"../control.tar.gz": tar_bytes({"./control": b"Package: libc\n"})}))
        self.reject("Invalid IPK control archive")
        self.assertFalse((self.root / "control.tar.gz").exists())

    def test_all_firmware_manifest_versions_must_be_exported(self):
        with self.manifest.open("a") as stream:
            stream.write("not-exported - 1.0-1\n")
        self.reject("Firmware packages missing.*not-exported")

    def test_selected_module_packages_must_not_be_omitted(self):
        with (self.lede / ".config").open("a") as stream:
            stream.write("CONFIG_PACKAGE_missing-module=m\n")
        with self.packageinfo.open("a") as stream:
            stream.write("Package: missing-module\nType: ipkg\nDescription: test\n@@\n")
        self.reject("Selected packages missing.*missing-module")

    def test_real_package_metadata_excludes_suboptions_and_build_only_images(self):
        with (self.lede / ".config").open("a") as stream:
            stream.write("CONFIG_PACKAGE_TAR_GZIP=y\nCONFIG_PACKAGE_demo_INCLUDE_feature=y\nCONFIG_PACKAGE_u-boot-demo=y\n")
        with self.packageinfo.open("a") as stream:
            stream.write("Package: u-boot-demo\nType: bin\nBuild-Only: 1\nDescription: bootloader\n@@\n")
        report = self.export()
        self.assertEqual(report["selected_packages_verified"], 4)
        self.assertEqual(report["selected_non_ipk_build_packages"], ["u-boot-demo"])

    def test_legacy_metadata_text_and_embedded_headers_preserve_real_inventory(self):
        with (self.lede / ".config").open("a") as stream:
            stream.write("CONFIG_PACKAGE_fake-description=y\nCONFIG_PACKAGE_fake-config=m\n")
        metadata = (
            b"Package: kernel\nType: ipkg\nTitle: legacy \xa1 text\nMaintainer: legacy \xa1 name\n"
            b"Description: legacy \xa1 description\nType: bin\nBuild-Only: 1\n"
            b"Package: fake-description\nType: ipkg\nBuild-Only: 0\n@@\n"
            b"Type: bin\nBuild-Only: 1\n"
            b"Package: libc\nType: ipkg\nDescription: real package after description\n@@\n"
            b"Config:\nconfig TEST\n\thelp\n\t  legacy \xa1 config\n"
            b"Package: fake-config\nType: ipkg\nBuild-Only: 0\n@@\n"
            b"Type: bin\nBuild-Only: 1\n"
            b"Package: demo\nType: ipkg\nDescription: real package after config\n@@\n"
            b"Package: libexample\nType: ipkg\nDescription: ABI alias\n@@\n"
        )
        for name, newline in (("lf", b"\n"), ("crlf", b"\r\n")):
            with self.subTest(newline=name):
                self.packageinfo.write_bytes(metadata.replace(b"\n", newline))
                self.output = self.root / ("export-" + name)
                report = self.export()
                self.assertEqual(report["selected_packages_verified"], 4)
                self.assertEqual(report["selected_non_ipk_build_packages"], [])
                self.assertEqual(report["package_files"], 4)
                for feed, source in (("core", self.core), ("base", self.base)):
                    for filename in EXPORT.INDEX_FILES:
                        self.assertEqual((self.output / feed / filename).read_bytes(),
                                         (source / filename).read_bytes())

    def test_metadata_structural_fields_reject_non_ascii_bytes(self):
        original = self.packageinfo.read_bytes()
        record = b"Package: strict-fields\nType: ipkg\nBuild-Only: 0\nDescription: test\n@@\n"
        for field, valid, invalid in (
                ("Package", b"Package: strict-fields", b"Package: strict-\xa1fields"),
                ("Type", b"Type: ipkg", b"Type: ip\xa1kg"),
                ("Build-Only", b"Build-Only: 0", b"Build-Only: \xa10")):
            with self.subTest(field=field):
                self.packageinfo.write_bytes(original + record.replace(valid, invalid))
                self.reject("Non-ASCII " + field)

    def test_real_duplicate_metadata_package_is_still_rejected(self):
        with self.packageinfo.open("ab") as stream:
            stream.write(b"Package: demo\nType: ipkg\nDescription: duplicate package\n@@\n")
        self.reject("duplicate build package metadata.*demo")

    def test_unterminated_metadata_text_cannot_hide_selected_packages(self):
        original = self.packageinfo.read_bytes()
        with (self.lede / ".config").open("a") as stream:
            stream.write("CONFIG_PACKAGE_missing-module=m\n")
        for block in (b"Description: legacy \xa1 description\n", b"Config:\nlegacy \xa1 config\n"):
            with self.subTest(block=block.split(b":", 1)[0]):
                self.packageinfo.write_bytes(
                    original + block + b"Package: missing-module\nType: ipkg\n")
                self.reject("Unterminated Description/Config")

    def test_private_key_cannot_be_exported_under_public_filename(self):
        secret = b"Ed" + bytes(102)
        (self.lede / "key-build.pub").write_bytes(b"untrusted comment: private key\n" + base64.b64encode(secret) + b"\n")
        self.reject("42-byte.*public key")

    def test_duplicate_or_foreign_architecture_entries_are_rejected(self):
        self.write_index(self.core, [self.demo, self.demo])
        self.reject("duplicate IPK filename")
        self.demo["Architecture"] = "x86_64"
        self.write_index(self.core, [self.demo])
        self.reject("Wrong package architecture")

    def test_partial_copy_failure_does_not_publish_or_leave_temporary_export(self):
        with mock.patch.object(EXPORT.shutil, "copyfile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.export()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".m28c-package-export-*")))


if __name__ == "__main__":
    unittest.main()
