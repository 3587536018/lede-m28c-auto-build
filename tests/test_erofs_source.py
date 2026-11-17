"""Keep the fallback source cache exact and preserve existing cache entries."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("erofs_source", ROOT / "scripts/seed-erofs-source.py")
EROFS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EROFS)
FIXTURE = ROOT / "tests/fixtures/lede-8e247d214/tools/erofs-utils/Makefile"


class ErofsSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="m28c-erofs-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "sources").mkdir()
        shutil.copyfile(ROOT / "sources" / EROFS.ARCHIVE, self.root / "sources" / EROFS.ARCHIVE)
        shutil.copyfile(ROOT / "sources.lock.json", self.root / "sources.lock.json")
        self.definition = self.root / "lede" / EROFS.MAKEFILE
        self.definition.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURE, self.definition)
        self.cache = self.root / "lede/dl" / EROFS.ARCHIVE

    def test_original_archive_and_upstream_fixture_match_record(self):
        record = json.loads((ROOT / "sources/erofs-utils-1.8.10.json").read_text())
        data = EROFS.archive_bytes(ROOT, ROOT / "sources" / EROFS.ARCHIVE)
        self.assertEqual((record["size"], record["sha256"]), (len(data), hashlib.sha256(data).hexdigest()))
        self.assertEqual(record["upstream_definition"]["sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertEqual(record["upstream_definition"]["revision"], EROFS.LEDE_REVISION)
        self.assertEqual(record["archive"], EROFS.ARCHIVE)
        self.assertEqual(record["version"], EROFS.VERSION)

    def test_missing_cache_is_seeded_and_temporary_removed(self):
        self.assertIn("Seeded", EROFS.seed(self.root))
        self.assertEqual(self.cache.read_bytes(), (self.root / "sources" / EROFS.ARCHIVE).read_bytes())
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])

    def test_valid_cache_is_preserved(self):
        EROFS.seed(self.root)
        before = self.cache.stat()
        self.assertIn("existing", EROFS.seed(self.root))
        after = self.cache.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_corrupt_existing_cache_is_not_overwritten(self):
        self.cache.parent.mkdir()
        self.cache.write_bytes(b"incomplete download")
        with self.assertRaisesRegex(ValueError, "size/SHA256 mismatch"):
            EROFS.seed(self.root)
        self.assertEqual(self.cache.read_bytes(), b"incomplete download")
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])

    def test_corrupt_vendor_is_rejected_even_with_good_cache(self):
        EROFS.seed(self.root)
        before = self.cache.read_bytes()
        (self.root / "sources" / EROFS.ARCHIVE).write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "size/SHA256 mismatch"):
            EROFS.seed(self.root)
        self.assertEqual(self.cache.read_bytes(), before)

    def test_upstream_definition_drift_stops_before_cache_creation(self):
        self.definition.write_bytes(self.definition.read_bytes().replace(b"1.8.10", b"1.8.11"))
        with self.assertRaisesRegex(ValueError, "Makefile changed"):
            EROFS.seed(self.root)
        self.assertFalse(self.cache.parent.exists())

    def test_source_lock_drift_stops_before_cache_creation(self):
        path = self.root / "sources.lock.json"
        lock = json.loads(path.read_text())
        lock["lede"]["revision"] = "0" * 40
        path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "LEDE revision changed"):
            EROFS.seed(self.root)
        self.assertFalse(self.cache.parent.exists())

    def test_racing_corrupt_cache_is_preserved_and_rejected(self):
        def competing_writer(temporary, destination):
            destination.write_bytes(b"other incomplete download")
            raise FileExistsError()
        with mock.patch.object(EROFS.os, "link", side_effect=competing_writer):
            with self.assertRaisesRegex(ValueError, "size/SHA256 mismatch"):
                EROFS.seed(self.root)
        self.assertEqual(self.cache.read_bytes(), b"other incomplete download")
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])

    def test_linked_cache_directory_is_rejected(self):
        other = self.root / "other"
        other.mkdir()
        try:
            self.cache.parent.symlink_to(other, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege unavailable")
            raise
        with self.assertRaisesRegex(ValueError, "linked source/cache path"):
            EROFS.seed(self.root)
        self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
