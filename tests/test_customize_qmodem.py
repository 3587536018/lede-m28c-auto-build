"""Checked QModem patch integration against the exact locked source files."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'tests/fixtures/qmodem-f5035b7d'
SCANNER = Path('application/modem_scan/src/modem_scand.c')
HEADER = SCANNER.with_name('m28c_usb_identity.h')
SPEC = importlib.util.spec_from_file_location('customize_qmodem', REPO / 'scripts/customize-qmodem.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class QModemPatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='m28c-qmodem-patch-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.feed = self.root / 'feeds/qmodem'
        shutil.copytree(FIXTURE, self.feed)

    def customize(self):
        with contextlib.redirect_stdout(io.StringIO()):
            PATCH.customize(self.root)

    def snapshot(self):
        return {path.relative_to(self.feed): path.read_bytes()
                for path in self.feed.rglob('*') if path.is_file()}

    def test_patch_outputs_match_checked_hashes_and_reapply_identically(self):
        self.customize()
        once = self.snapshot()
        for manifest_name in ('scanner.json', 'runtime.json'):
            manifest = json.loads((PATCH.PATCH_DIR / manifest_name).read_text(encoding='utf-8'))
            for entry in manifest['files']:
                with self.subTest(path=entry['path']):
                    text = (self.feed / entry['path']).read_text(encoding='utf-8')
                    self.assertEqual(entry['patched_sha256'], PATCH.digest(text))
        self.assertEqual((PATCH.PATCH_DIR / HEADER.name).read_bytes(), (self.feed / HEADER).read_bytes())
        self.customize()
        self.assertEqual(once, self.snapshot())

    def test_upstream_drift_is_rejected_before_any_file_changes(self):
        for relative in (SCANNER, Path('application/qmodem/files/etc/init.d/qmodem_network')):
            with self.subTest(path=relative):
                path = self.feed / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n/* simulated upstream drift */\n')
                before = self.snapshot()
                with self.assertRaisesRegex(PATCH.PatchError, 'source drift'):
                    self.customize()
                self.assertEqual(before, self.snapshot())
                path.write_bytes(original)

    def test_changed_managed_header_is_rejected_before_writing(self):
        path = self.feed / HEADER
        path.write_text('/* local incompatible edit */\n', encoding='utf-8')
        before = self.snapshot()
        with self.assertRaisesRegex(PATCH.PatchError, 'header differs'):
            self.customize()
        self.assertEqual(before, self.snapshot())

    def test_changed_already_patched_file_is_rejected(self):
        self.customize()
        path = self.feed / SCANNER
        path.write_bytes(path.read_bytes() + b'\n/* edit after customization */\n')
        before = self.snapshot()
        with self.assertRaisesRegex(PATCH.PatchError, 'source drift'):
            self.customize()
        self.assertEqual(before, self.snapshot())

    def test_invalid_manifest_anchor_and_output_digest_are_rejected(self):
        original = 'original source\n'
        base = dict(path='fixture.c', sha256=PATCH.digest(original),
                    patched_sha256=PATCH.digest('modified source\n'))
        for replacements, message in (
            ([dict(old='absent', new='modified')], 'anchor is not unique'),
            ([dict(old='original', new='unexpected')], 'checksum mismatch'),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(PATCH.PatchError, message):
                    PATCH.patch_text(original, dict(base, replacements=replacements))


if __name__ == '__main__':
    unittest.main()
