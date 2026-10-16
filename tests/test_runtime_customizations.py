"""Check the final image gate catches package/overlay and service omissions."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("runtime_gate", ROOT / "scripts/verify-runtime-customizations.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class RuntimeGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="m28c-runtime-gate-")
        self.addCleanup(temporary.cleanup)
        self.lede = Path(temporary.name)
        self.image = self.lede / "build_dir/target-test/root-rockchip"
        for installed, source in GATE.FILES.items():
            content = f"public runtime fixture: {installed}\n".encode()
            for path in (self.lede / source, self.image / installed):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o755)
        self.service = self.image / "etc/rc.d/S95m28c-automount"
        self.service.parent.mkdir(parents=True)

    def enable(self):
        try:
            self.service.symlink_to("../init.d/m28c-automount")
        except OSError:
            self.skipTest("Creating image service symlinks requires Windows symlink permission")

    def test_matching_files_without_boot_service_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "boot scan"):
            GATE.verify(self.lede)

    def test_missing_package_file_is_rejected(self):
        (self.image / "usr/libexec/m28c-automount").unlink()
        with self.assertRaisesRegex(ValueError, "Missing regular"):
            GATE.verify(self.lede)

    def test_stale_overlay_overriding_patched_init_is_rejected(self):
        (self.image / "etc/init.d/dockerd").write_text("old legacy init\n")
        with self.assertRaisesRegex(ValueError, "differs from patched"):
            GATE.verify(self.lede)

    def test_legacy_hook_cannot_coexist_with_replacement(self):
        (self.image / "etc/15-automount").write_text("legacy mount\n")
        with self.assertRaisesRegex(ValueError, "Obsolete automount"):
            GATE.verify(self.lede)

    def test_multiple_or_missing_staging_trees_are_rejected(self):
        (self.lede / "build_dir/target-second/root-rockchip").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            GATE.verify(self.lede)
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(ValueError, "found 0"):
                GATE.verify(Path(empty))

    def test_complete_staging_tree_passes(self):
        self.enable()
        self.assertEqual(len(GATE.FILES), GATE.verify(self.lede))

    @unittest.skipIf(os.name == "nt", "Unix executable mode is checked on the Linux build runner")
    def test_non_executable_worker_is_rejected(self):
        (self.image / "usr/libexec/m28c-automount").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "not executable"):
            GATE.verify(self.lede)


if __name__ == "__main__":
    unittest.main()
