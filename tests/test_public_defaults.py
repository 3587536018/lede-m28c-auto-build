"""Public-image regression checks against unmodified LEDE 8e247d214 sources."""

import contextlib
import importlib.util
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / 'tests/fixtures/lede-8e247d214'
DEFAULTS = Path('package/lean/default-settings/files/zzz-default-settings')
WIRELESS = Path('package/kernel/mac80211/files/lib/wifi/mac80211.sh')
SHADOW = Path('package/base-files/files/etc/shadow')
SPEC = importlib.util.spec_from_file_location('customize_public', REPO / 'scripts/customize-public.py')
PUBLIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLIC)
BASH = shutil.which('bash')
if not BASH and Path('C:/Program Files/Git/bin/bash.exe').is_file():
    BASH = 'C:/Program Files/Git/bin/bash.exe'


class PublicDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='m28c-public-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)

    def customize(self):
        with contextlib.redirect_stdout(io.StringIO()):
            PUBLIC.customize(self.root)

    def snapshot(self):
        return {path: (self.root / path).read_bytes() for path in (DEFAULTS, WIRELESS, SHADOW)}

    def test_factory_hash_injection_and_incompatible_feed_changes_are_removed(self):
        original = (self.root / DEFAULTS).read_text(encoding='utf-8')
        shadow = (self.root / SHADOW).read_bytes()
        self.assertEqual(2, original.count('/etc/shadow'))
        self.customize()
        patched = (self.root / DEFAULTS).read_text(encoding='utf-8')
        self.assertNotIn('/etc/shadow', patched)
        self.assertNotIn('18.06.9', patched)
        self.assertNotIn("'/check_signature/d'", patched)
        self.assertEqual(4, len(original.splitlines()) - len(patched.splitlines()))
        self.assertIn("set system.@system[0].timezone='CST-8'", patched)
        self.assertIn("sed -i 's#downloads.openwrt.org#mirrors.tencent.com/lede#g'", patched)
        self.assertEqual(shadow, (self.root / SHADOW).read_bytes())

    def test_generator_sets_only_new_radio_defaults(self):
        original = (self.root / WIRELESS).read_text(encoding='utf-8')
        self.customize()
        patched = (self.root / WIRELESS).read_text(encoding='utf-8')
        restored = patched
        for name, value in (('ssid', 'openwr'), ('encryption', ''), ('key', '')):
            restored = restored.replace(f'\t{name}="{value}"', f'\t{name}="$default_{name}"', 1)
        self.assertEqual(original, restored)

    def test_repeated_customization_is_byte_identical(self):
        self.customize()
        first = self.snapshot()
        self.customize()
        self.assertEqual(first, self.snapshot())

    def test_generator_drift_fails_without_mutating_any_source(self):
        path = self.root / WIRELESS
        original = path.read_text(encoding='utf-8')
        target = "set wireless.${iface}.disabled='0'"
        for changed in (
            original.replace(target, ''),
            original.replace(target, "set wireless.${iface}.disabled='${enabled}'"),
            original + '\n' + target + '\n',
        ):
            with self.subTest(change=changed[-100:]):
                path.write_text(changed, encoding='utf-8')
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, 'exactly one mac80211 iface'):
                    self.customize()
                self.assertEqual(before, self.snapshot())

    def test_nonempty_missing_or_duplicate_factory_root_account_is_rejected(self):
        path = self.root / SHADOW
        for shadow in ('root:fixture-hash:0:0:99999:7:::\n', 'nobody:*:0:0:99999:7:::\n', 'root::0:0:99999:7:::\nroot::0:0:99999:7:::\n'):
            with self.subTest(shadow=shadow):
                path.write_text(shadow, encoding='utf-8')
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, 'exactly one root account'):
                    self.customize()
                self.assertEqual(before, self.snapshot())

    def test_preexisting_user_keys_and_passwords_are_not_touched(self):
        live_shadow = self.root / 'files/etc/shadow'
        live_wifi = self.root / 'files/etc/config/wireless'
        live_wifi.parent.mkdir(parents=True)
        live_shadow.write_text('root:fixture-owner-hash:0:0:99999:7:::\n', encoding='utf-8')
        live_wifi.write_text("config wifi-iface 'owner'\n\toption key 'fixture-owner-key'\n", encoding='utf-8')
        before = (live_shadow.read_bytes(), live_wifi.read_bytes())
        self.customize()
        self.assertEqual(before, (live_shadow.read_bytes(), live_wifi.read_bytes()))

    @unittest.skipUnless(BASH, 'runtime generator check requires Bash')
    def test_late_radio_discovery_emits_enabled_openwr_without_password(self):
        self.customize()
        script = r'''
set -eu
append() { :; }
uci() { if [ "$*" = '-q batch' ]; then cat; fi; }
source "$1"
mac80211_get_defaults() {
    default_country=CN
    default_encryption=sae
    default_ssid=upstream-name
    default_key=upstream-key
    default_mac_count=
}
devidx=0
mac80211_emit_detect phy0 platform/mock-radio 5g 36 HE80 ''
'''
        result = subprocess.run([BASH, '-c', script, 'generator-test', (self.root / WIRELESS).as_posix()], check=True, text=True, capture_output=True)
        self.assertIn("set wireless.radio0.disabled='0'", result.stdout)
        self.assertIn("set wireless.default_radio0.disabled='0'", result.stdout)
        self.assertIn("set wireless.default_radio0.key=''", result.stdout)
        self.assertIn("set wireless.default_radio0.ssid='openwr'", result.stdout)
        self.assertIn("set wireless.default_radio0.encryption='none'", result.stdout)
        self.assertNotIn('upstream-key', result.stdout)
        self.assertNotIn("disabled='1'", result.stdout)

    def test_uci_defaults_never_rewrite_existing_wireless_sections(self):
        defaults = (REPO / 'files/etc/uci-defaults/zzzz-custom').read_text(encoding='utf-8')
        self.assertNotRegex(defaults, r'(?m)^[^#\n]*(?:wireless\.|config_load wireless|config_foreach .*wifi)')



if __name__ == '__main__':
    unittest.main()
