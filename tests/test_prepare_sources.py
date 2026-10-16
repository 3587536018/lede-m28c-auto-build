import importlib.util
import json
import os
import subprocess
import sys
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


class OpenClashGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.lede = self.root / "lede"
        self.lock = PREPARE.read_lock(ROOT / "sources.lock.json")
        self.app_version = next(feed["release"][1:] for feed in self.lock["feeds"]
                                if feed["name"] == "openclash")
        self.core_version = self.lock["binaries"]["openclash_core"]["version"]
        self.required = ROOT / "configs/required-packages.txt"
        self.manifest = self.root / "image.manifest"
        self.lock_path = self.root / "sources.lock.json"
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")
        self.source = self.lede / "feeds/openclash/luci-app-openclash"
        self.source.mkdir(parents=True)
        self.write_source_version(self.source, self.app_version)

    def write_source_version(self, directory, version):
        (directory / "Makefile").write_text(
            f"PKG_NAME:=luci-app-openclash\nPKG_VERSION:={version}\n", encoding="utf-8")

    def write_manifest(self, app_version=None, core_version=None, extra=""):
        versions = {name: "1" for name in PREPARE.required_packages(self.required)}
        versions["luci-app-openclash"] = app_version or self.app_version
        versions["m28c-openclash-core"] = core_version or self.core_version + "-r1"
        self.manifest.write_text(
            "".join(f"{name} - {version}\n" for name, version in versions.items()) + extra,
            encoding="utf-8")

    def install_link(self, feed, target):
        link = self.lede / "package/feeds" / feed / "luci-app-openclash"
        link.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(link.parent.resolve().is_relative_to(self.root))
        self.assertTrue(target.resolve(strict=True).is_relative_to(self.root))
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 1314:
                raise
            # Directory junctions exercise real path resolution without requiring
            # Windows symlink privileges. Remove only the junction during cleanup.
            subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                           check=True, capture_output=True, text=True)
            self.addCleanup(link.rmdir)
        else:
            self.addCleanup(link.unlink)
        return link

    def run_cli(self, *mode):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/prepare-sources.py"),
             "--root", str(self.root), "--lede-dir", str(self.lede),
             "--lock", str(self.lock_path), "--required-packages", str(self.required),
             *map(str, mode)], capture_output=True, text=True)

    def test_manifest_rejects_old_app_and_accepts_exact_locked_version(self):
        for wrong_version in ("0.47.075", self.app_version + ".1"):
            with self.subTest(version=wrong_version):
                self.write_manifest(app_version=wrong_version)
                with self.assertRaises(ValueError) as raised:
                    PREPARE.verify_packages(self.manifest, self.required, manifest=True, lock=self.lock)
                self.assertIn("luci-app-openclash", str(raised.exception))
                self.assertIn(wrong_version, str(raised.exception))
                self.assertIn(self.app_version, str(raised.exception))
        self.write_manifest()
        PREPARE.verify_packages(self.manifest, self.required, manifest=True, lock=self.lock)

    def test_manifest_checks_core_version_and_package_release(self):
        for release in ("-1", "-r1"):
            with self.subTest(release=release):
                self.write_manifest(core_version=self.core_version + release)
                PREPARE.verify_packages(self.manifest, self.required, manifest=True, lock=self.lock)
        for wrong_version in (self.core_version, self.core_version + "-r2", "0.0.0-r1"):
            with self.subTest(version=wrong_version):
                self.write_manifest(core_version=wrong_version)
                with self.assertRaisesRegex(ValueError, "m28c-openclash-core"):
                    PREPARE.verify_packages(self.manifest, self.required, manifest=True, lock=self.lock)

    def test_manifest_rejects_repeated_and_conflicting_package_entries(self):
        for duplicate_version in (self.app_version, "0.47.075"):
            with self.subTest(version=duplicate_version):
                self.write_manifest(extra=f"luci-app-openclash - {duplicate_version}\n")
                with self.assertRaisesRegex(ValueError, "duplicate firmware manifest entry"):
                    PREPARE.verify_packages(self.manifest, self.required, manifest=True, lock=self.lock)

    def test_source_accepts_only_the_selected_locked_feed(self):
        installed = self.install_link("openclash", self.source)
        self.assertEqual(installed.resolve(strict=True), self.source.resolve(strict=True))
        PREPARE.verify_openclash_source(self.lede, self.lock)
        PREPARE.verify_openclash_source(self.lede, self.lock)

    def test_source_rejects_other_feed_even_with_matching_version(self):
        old_source = self.lede / "feeds/luci/applications/luci-app-openclash"
        old_source.mkdir(parents=True)
        self.write_source_version(old_source, self.app_version)
        self.install_link("luci", old_source)
        with self.assertRaisesRegex(ValueError, "source must resolve only"):
            PREPARE.verify_openclash_source(self.lede, self.lock)

    def test_source_rejects_multiple_installed_candidates(self):
        self.install_link("openclash", self.source)
        self.install_link("luci", self.source)
        with self.assertRaisesRegex(ValueError, "source must resolve only"):
            PREPARE.verify_openclash_source(self.lede, self.lock)

    def test_source_rejects_missing_selection_and_wrong_source_version(self):
        with self.assertRaisesRegex(ValueError, "source must resolve only"):
            PREPARE.verify_openclash_source(self.lede, self.lock)
        self.install_link("openclash", self.source)
        self.write_source_version(self.source, "0.47.075")
        with self.assertRaisesRegex(ValueError, "source version"):
            PREPARE.verify_openclash_source(self.lede, self.lock)

    def test_manifest_cli_enforces_the_supplied_version_lock(self):
        self.write_manifest(app_version="0.47.075")
        failed = self.run_cli("--verify-manifest", self.manifest)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn("luci-app-openclash", failed.stderr)
        self.assertIn("0.47.075", failed.stderr)
        self.assertIn(self.app_version, failed.stderr)
        self.write_manifest()
        passed = self.run_cli("--verify-manifest", self.manifest)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_source_cli_checks_selected_source_version(self):
        self.install_link("openclash", self.source)
        passed = self.run_cli("--verify-openclash-source")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.write_source_version(self.source, "0.47.075")
        failed = self.run_cli("--verify-openclash-source")
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn("source version", failed.stderr)

    def test_source_and_manifest_cli_follow_changed_lock_versions(self):
        self.install_link("openclash", self.source)
        self.write_manifest()
        next(feed for feed in self.lock["feeds"] if feed["name"] == "openclash")["release"] = "v0.49.003"
        self.lock["binaries"]["openclash_core"]["version"] = "1.20.007"
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")
        for mode in (("--verify-openclash-source",), ("--verify-manifest", self.manifest)):
            with self.subTest(mode=mode):
                result = self.run_cli(*mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("0.49.003", result.stderr)
        self.app_version, self.core_version = "0.49.003", "1.20.007"
        self.write_source_version(self.source, self.app_version)
        self.write_manifest()
        for mode in (("--verify-openclash-source",), ("--verify-manifest", self.manifest)):
            with self.subTest(mode=mode):
                result = self.run_cli(*mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
