import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_sources", ROOT / "scripts/prepare-sources.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class PrepareSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "upstream"
        self.remote.mkdir()
        PREPARE.git(self.remote, "init", "--quiet")
        PREPARE.git(self.remote, "config", "user.name", "Source lock test")
        PREPARE.git(self.remote, "config", "user.email", "test@example.invalid")
        (self.remote / "tracked.txt").write_text("baseline\n")
        PREPARE.git(self.remote, "add", "tracked.txt")
        PREPARE.git(self.remote, "commit", "--quiet", "-m", "baseline")
        self.revision = PREPARE.git(self.remote, "rev-parse", "HEAD").stdout.strip()
        self.lock = {
            "schema": 1,
            "baseline": {"version": "r1-" + self.revision[:9]},
            "lede": {"url": str(self.remote), "revision": self.revision},
            "feeds": [{"name": "luci", "url": "https://example.invalid/luci.git", "revision": self.revision}],
        }
        self.destination = self.root / "lede"
        (self.root / "files/etc").mkdir(parents=True)
        (self.root / "files/etc/example").write_text("first\n")

    def test_real_lock_uses_full_shas_and_seven_feeds(self):
        lock = PREPARE.read_lock(ROOT / "sources.lock.json")
        self.assertEqual(7, len(lock["feeds"]))
        config = PREPARE.feeds_config(lock).decode()
        self.assertEqual(7, config.count("^"))
        self.assertNotIn(";master", config)

    def test_checkout_is_detached_idempotent_and_preserves_untracked(self):
        PREPARE.checkout_source(self.destination, self.lock["lede"])
        (self.destination / "my-notes.txt").write_text("keep me\n")
        PREPARE.checkout_source(self.destination, self.lock["lede"])
        self.assertNotEqual(0, PREPARE.git(self.destination, "symbolic-ref", "-q", "HEAD", check=False).returncode)
        self.assertEqual(self.revision, PREPARE.git(self.destination, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual("keep me\n", (self.destination / "my-notes.txt").read_text())

    def test_dirty_checkout_is_rejected_without_losing_edits(self):
        PREPARE.checkout_source(self.destination, self.lock["lede"])
        changed = self.destination / "tracked.txt"
        changed.write_text("user edit\n")
        with self.assertRaisesRegex(ValueError, "tracked modifications"):
            PREPARE.checkout_source(self.destination, self.lock["lede"])
        self.assertEqual("user edit\n", changed.read_text())

    def test_build_rejects_drift_in_baseline_or_feed_config(self):
        PREPARE.checkout_source(self.destination, self.lock["lede"])
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        PREPARE.verify_baseline(self.destination, self.lock)
        self.lock["lede"]["revision"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "run prepare.sh"):
            PREPARE.verify_baseline(self.destination, self.lock)
        self.lock["lede"]["revision"] = self.revision
        (self.destination / "feeds.conf").write_text("src-git luci https://example.invalid/luci.git;master\n")
        with self.assertRaisesRegex(ValueError, "feeds.conf differs"):
            PREPARE.verify_baseline(self.destination, self.lock)

    def test_version_override_is_managed_and_user_edits_are_preserved(self):
        PREPARE.checkout_source(self.destination, self.lock["lede"])
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        version = self.destination / "version"
        self.assertEqual(self.lock["baseline"]["version"] + "\n", version.read_text())
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        version.write_text("user version\n")
        with self.assertRaisesRegex(ValueError, "LEDE version differs"):
            PREPARE.verify_baseline(self.destination, self.lock)
        with self.assertRaisesRegex(ValueError, "User-edited"):
            PREPARE.sync_overlay(self.root, self.destination, self.lock)
        self.assertEqual("user version\n", version.read_text())

    def test_overlay_is_idempotent_and_updates_only_managed_files(self):
        self.destination.mkdir()
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        (self.destination / "files/etc/user-file").write_text("preserve\n")
        (self.root / "files/etc/example").unlink()
        (self.root / "files/etc/replacement").write_text("second\n")
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        self.assertFalse((self.destination / "files/etc/example").exists())
        self.assertEqual("second\n", (self.destination / "files/etc/replacement").read_text())
        self.assertEqual("preserve\n", (self.destination / "files/etc/user-file").read_text())

    def test_user_overlay_changes_abort_before_any_overwrite(self):
        self.destination.mkdir()
        PREPARE.sync_overlay(self.root, self.destination, self.lock)
        changed = self.destination / "files/etc/example"
        changed.write_text("user edit\n")
        (self.root / "files/etc/replacement").write_text("new\n")
        with self.assertRaisesRegex(ValueError, "User-edited"):
            PREPARE.sync_overlay(self.root, self.destination, self.lock)
        self.assertEqual("user edit\n", changed.read_text())
        self.assertFalse((self.destination / "files/etc/replacement").exists())

    def test_feed_revision_mismatch_is_rejected(self):
        self.destination.mkdir()
        feed = self.destination / "feeds/luci"
        PREPARE.checkout_source(feed, self.lock["lede"])
        PREPARE.verify_feeds(self.destination, self.lock)
        self.lock["feeds"][0]["revision"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "expected"):
            PREPARE.verify_feeds(self.destination, self.lock)

    def test_missing_or_module_only_required_package_is_rejected(self):
        required = self.root / "required.txt"
        required.write_text("# public image\nluci-app-m28c\ndockerd\n")
        config = self.root / ".config"
        config.write_text("CONFIG_PACKAGE_luci-app-m28c=y\nCONFIG_PACKAGE_dockerd=m\n")
        with self.assertRaisesRegex(ValueError, "dockerd"):
            PREPARE.verify_packages(config, required)
        config.write_text("CONFIG_PACKAGE_luci-app-m28c=y\nCONFIG_PACKAGE_dockerd=y\n")
        PREPARE.verify_packages(config, required)
        manifest = self.root / "image.manifest"
        manifest.write_text("luci-app-m28c - 1\n")
        with self.assertRaisesRegex(ValueError, "dockerd"):
            PREPARE.verify_packages(manifest, required, manifest=True)
        manifest.write_text("luci-app-m28c - 1\ndockerd - 28\n")
        PREPARE.verify_packages(manifest, required, manifest=True)


if __name__ == "__main__":
    unittest.main()
