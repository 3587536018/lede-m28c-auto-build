"""Verify the binary boundary without running an ARM64 program or using network."""

import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_openclash_core", ROOT / "scripts/prepare-openclash-core.py")
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def elf64(machine=183, kind=2, interpreter=False, flags=5):
    """A minimal ELF with independently encoded executable program headers."""
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, kind, machine, 1,
                         0x400080, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    size = 64 + 56 + 32
    segment = struct.pack("<IIQQQQQQ", 3 if interpreter else 1, flags, 0,
                          0x400000, 0x400000, size, size, 4096)
    return header + segment + b"\0" * 32


def source_for(data):
    source = json.loads((ROOT / "sources.lock.json").read_text())["binaries"]["openclash_core"].copy()
    source.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    return source


class OpenClashCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "core.gz"
        self.destination = self.root / "package/files/clash_meta"

    def unpack(self, data):
        self.archive.write_bytes(data)
        return core.unpack_core(self.archive, self.destination, source_for(data))

    def test_pinned_lock_names_official_release(self):
        source = core.load_source(ROOT / "sources.lock.json")
        self.assertEqual(source["version"], "1.19.32")
        self.assertEqual(source["sha256"], "9dd862e28b46ff7d775f169cceebc28deccaa0a9e804237d421cd2571e0caba0")
        lock = json.loads((ROOT / "sources.lock.json").read_text())
        feed = next(f for f in lock["feeds"] if f["name"] == "openclash")
        self.assertEqual(feed["revision"], "e553e82e01c697aefbe1555261a503f425e00ccd")

    def test_invalid_binary_lock_is_rejected(self):
        for field, value in (("version", "../../latest"), ("architecture", "linux-amd64"),
                             ("asset", "other.gz"), ("url", "https://example.com/core.gz"),
                             ("sha256", "skip"), ("size", -1), ("size", True)):
            with self.subTest(field=field):
                source = source_for(gzip.compress(elf64()))
                source[field] = value
                path = self.root / "lock.json"
                path.write_text(json.dumps({"binaries": {"openclash_core": source}}))
                with self.assertRaises(ValueError):
                    core.load_source(path)

    def test_verified_arm64_core_is_written_with_hash_and_execution_permission(self):
        binary = elf64()
        report = self.unpack(gzip.compress(binary))
        self.assertEqual(self.destination.read_bytes(), binary)
        self.assertEqual(report["sha256"], hashlib.sha256(binary).hexdigest())
        self.assertEqual(report["size"], len(binary))
        self.assertEqual(report["mode"], "0755")
        if os.name != "nt":
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o755)

    def test_archive_hash_and_size_must_match_before_output_is_created(self):
        data = gzip.compress(elf64())
        self.archive.write_bytes(data)
        for field, value in (("sha256", "0" * 64), ("size", len(data) + 1)):
            with self.subTest(field=field):
                source = source_for(data)
                source[field] = value
                with self.assertRaises(ValueError):
                    core.unpack_core(self.archive, self.destination, source)
                self.assertFalse(self.destination.exists())

    def test_wrong_architecture_or_non_executable_elf_is_rejected(self):
        for binary in (elf64(machine=62), elf64(kind=1), elf64(interpreter=True),
                       elf64(flags=6), b"not an executable", elf64()[:70]):
            with self.subTest(binary=binary[:24]):
                with self.assertRaises(ValueError):
                    self.unpack(gzip.compress(binary))
                self.assertFalse(self.destination.exists())

    def test_invalid_elf_headers_and_segments_are_rejected(self):
        # Independent offsets from the ELF64 specification, including a load
        # segment which points outside the file and impossible memory size.
        for offset, fmt, value in ((5, "B", 2), (24, "Q", 0), (32, "Q", 999999),
                                   (54, "H", 1), (56, "H", 0), (72, "Q", 999999),
                                   (104, "Q", 1)):
            with self.subTest(offset=offset):
                binary = bytearray(elf64())
                struct.pack_into("<" + fmt, binary, offset, value)
                with self.assertRaises(ValueError):
                    self.unpack(gzip.compress(binary))

    def test_gzip_crc_and_truncation_are_checked_even_with_a_matching_archive_hash(self):
        data = gzip.compress(elf64())
        corrupt_crc = bytearray(data)
        corrupt_crc[-8] ^= 1
        for archive in (data[:-1], data[:-8], bytes(corrupt_crc)):
            with self.subTest(archive=archive[-12:]), self.assertRaises(ValueError):
                self.unpack(archive)

    def test_extra_gzip_members_and_trailing_bytes_are_rejected(self):
        data = gzip.compress(elf64())
        for suffix in (b"\0", b"junk", gzip.compress(b"second member")):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.unpack(data + suffix)

    def test_uncompressed_size_limit_and_bad_core_preserve_previous_output(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"previous verified core")
        with mock.patch.object(core, "MAX_CORE_SIZE", 100):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.unpack(gzip.compress(elf64()))
        with self.assertRaises(ValueError):
            self.unpack(gzip.compress(elf64(machine=62)))
        self.assertEqual(self.destination.read_bytes(), b"previous verified core")
        self.assertEqual([p.name for p in self.destination.parent.iterdir()], ["clash_meta"])

    def test_verified_cache_is_reused_without_network(self):
        data = gzip.compress(elf64())
        source = source_for(data)
        archive = self.root / source["asset"]
        archive.write_bytes(data)
        with mock.patch.object(core.urllib.request, "urlopen", side_effect=AssertionError("network not expected")):
            self.assertEqual(core.download_archive(self.root, source), archive)

    def test_download_replaces_cache_only_after_successful_verification(self):
        data = gzip.compress(elf64())
        source = source_for(data)
        archive = self.root / source["asset"]
        archive.write_bytes(b"bad old cache")
        with mock.patch.object(core.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            self.assertEqual(core.download_archive(self.root, source), archive)
        self.assertEqual(archive.read_bytes(), data)
        self.assertFalse(list(self.root.glob(".mihomo-download-*")))

    def test_failed_download_preserves_cache_and_removes_temporary_files(self):
        data = gzip.compress(elf64())
        source = source_for(data)
        archive = self.root / source["asset"]
        archive.write_bytes(b"preserve for diagnosis")
        with mock.patch.object(core.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"corrupt")), \
                mock.patch.object(core.time, "sleep"):
            with self.assertRaises(ValueError):
                core.download_archive(self.root, source)
        self.assertEqual(archive.read_bytes(), b"preserve for diagnosis")
        self.assertFalse(list(self.root.glob(".mihomo-download-*")))

    def test_package_is_prepared_reproducibly_without_private_configuration(self):
        data = gzip.compress(elf64())
        source = source_for(data)
        lock = self.root / "lock.json"
        lock.write_text(json.dumps({"binaries": {"openclash_core": source}}))
        package = self.root / "package/custom/m28c-openclash-core"
        shutil.copytree(ROOT / "package/m28c-openclash-core", package)
        (self.root / "dl").mkdir()
        (self.root / "dl" / source["asset"]).write_bytes(data)
        with mock.patch.object(core.urllib.request, "urlopen", side_effect=AssertionError("network not expected")):
            first = core.prepare(self.root, lock)
            first_bytes = (package / "files/source.json").read_bytes()
            self.assertEqual(core.prepare(self.root, lock), first)
            self.assertEqual((package / "files/source.json").read_bytes(), first_bytes)
        self.assertEqual({p.name for p in (package / "files").iterdir()}, {"clash_meta", "source.json"})
        self.assertEqual(first["installed_path"], "/etc/openclash/core/clash_meta")

    def test_package_version_drift_fails_before_download(self):
        source = source_for(gzip.compress(elf64()))
        lock = self.root / "lock.json"
        lock.write_text(json.dumps({"binaries": {"openclash_core": source}}))
        package = self.root / "package/custom/m28c-openclash-core"
        package.mkdir(parents=True)
        (package / "Makefile").write_text("PKG_VERSION:=0.0.0\n")
        with mock.patch.object(core.urllib.request, "urlopen", side_effect=AssertionError("network not expected")):
            with self.assertRaisesRegex(ValueError, "package version"):
                core.prepare(self.root, lock)


if __name__ == "__main__":
    unittest.main()
