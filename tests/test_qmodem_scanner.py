"""Execute the patched C scanner's add/remove paths with simulated UCI/sysfs."""

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_qmodem_identity import CC

REPO = Path(__file__).resolve().parents[1]
SCANNER = REPO / 'tests/fixtures/qmodem-f5035b7d/application/modem_scan/src/modem_scand.c'
SPEC = importlib.util.spec_from_file_location('customize_qmodem_scanner', REPO / 'scripts/customize-qmodem.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def extract_function(source, name):
    pattern = rf'^static [^\n]*\b{re.escape(name)}\([^;]*?\)\s*\n\{{.*?^\}}[ \t]*$'
    matches = list(re.finditer(pattern, source, re.M | re.S))
    if len(matches) != 1:
        raise AssertionError(f'Expected one real scanner function {name}, found {len(matches)}')
    return matches[0][0]


@unittest.skipUnless(CC, 'native scanner tests require a C compiler (set CC)')
class QModemScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='m28c-scanner-native-')
        root = Path(cls.temporary.name)
        entry = json.loads((PATCH.PATCH_DIR / 'scanner.json').read_text(encoding='utf-8'))['files'][0]
        scanner = PATCH.patch_text(SCANNER.read_text(encoding='utf-8'), entry)
        source = (REPO / 'tests/fixtures/qmodem-scanner-harness.c').read_text(encoding='utf-8')
        types = []
        for name in ('log_level', 'str_list', 'modem_profile', 'scan_result'):
            match = re.search(rf'^(?:struct|enum) {name} \{{.*?^\}};', scanner, re.M | re.S)
            if not match:
                raise AssertionError(f'Missing actual scanner type {name}')
            types.append(match[0])
        source = source.replace('/* INSERT_TYPES */', '\n\n'.join(types))
        source = source.replace('/* INSERT_LIST_FUNCTIONS */', '\n\n'.join(
            extract_function(scanner, name) for name in ('sl_init', 'sl_free', 'sl_contains', 'sl_add')
        ))
        source = source.replace('/* INSERT_SCANNER_FUNCTIONS */', '\n\n'.join(
            extract_function(scanner, name) for name in (
                'section_from_slot', 'join_list', 'm28c_usb_location',
                'm28c_load_bindings', 'm28c_fixed_usb_slot',
                'm28c_select_usb_binding', 'add_modem', 'disable_slot',
            )
        ))
        (root / 'scanner.c').write_text(source, encoding='utf-8')
        library = root / ('scanner.dll' if os.name == 'nt' else 'scanner.so')
        args = [CC, '-Wall', '-Werror', '-Wno-unused-function', '-shared']
        if os.name != 'nt':
            args += ['-fPIC', '-D_GNU_SOURCE']
        args += ['-I', str(PATCH.PATCH_DIR), str(root / 'scanner.c'), '-o', str(library)]
        result = subprocess.run(args, text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(f'Native scanner compilation failed:\n{result.stdout}\n{result.stderr}')
        cls.library = ctypes.CDLL(str(library))
        for name, argtypes, restype in (
            ('test_reset', [], None),
            ('test_put', [ctypes.c_char_p, ctypes.c_char_p], None),
            ('test_get', [ctypes.c_char_p], ctypes.c_char_p),
            ('test_device', [ctypes.c_char_p] * 4, None),
            ('test_present', [ctypes.c_char_p, ctypes.c_int], None),
            ('test_generation', [ctypes.c_char_p, ctypes.c_char_p], None),
            ('test_protocol', [ctypes.c_char_p, ctypes.c_char_p], None),
            ('test_add', [ctypes.c_char_p], ctypes.c_int),
            ('test_disable', [ctypes.c_char_p], None),
            ('test_reloads', [], ctypes.c_int),
            ('test_writes', [], ctypes.c_int),
        ):
            function = getattr(cls.library, name)
            function.argtypes, function.restype = argtypes, restype

    @classmethod
    def tearDownClass(cls):
        if os.name == 'nt':
            import _ctypes
            _ctypes.FreeLibrary(cls.library._handle)
        cls.temporary.cleanup()

    def setUp(self):
        self.library.test_reset()

    def get(self, key):
        return self.library.test_get(key.encode()).decode()

    def put(self, key, value):
        self.library.test_put(key.encode(), value.encode())

    def device(self, slot='2-1', controller='fd800000', network='wwan0', at='/dev/ttyUSB2', xhci=1):
        bus = slot.split('-')[0]
        real = f'/sys/devices/platform/{controller}.usb/xhci-hcd.{xhci}.auto/usb{bus}/{slot}'
        self.library.test_device(*[s.encode() for s in (slot, real, network, at)])

    def add(self, slot='2-1'):
        return self.library.test_add(slot.encode())

    def test_initial_scan_registers_real_usb_protocol_and_ports(self):
        self.device()
        self.assertEqual(0, self.add())
        self.assertEqual('modem-device', self.get('qmodem.2_1'))
        self.assertEqual('/dev/ttyUSB2', self.get('qmodem.2_1.at_port'))
        self.assertEqual('wwan0', self.get('qmodem.2_1.network'))
        self.assertEqual('1', self.get('qmodem.main.modem_count'))
        self.assertTrue(self.get('qmodem.2_1.usb_location').endswith('/usb-version:3.10'))

    def test_reboot_rebinding_refreshes_path_tty_network_and_preserves_user_configuration(self):
        self.device()
        self.assertEqual(0, self.add())
        preserved = {
            'qmodem.2_1.apn': 'owner.apn', 'qmodem.2_1.username': 'owner',
            'qmodem.2_1.password': 'fixture-only', 'qmodem.2_1.pdp_type': 'ipv4',
            'qmodem.2_1.metric': '42', 'qmodem.2_1.enable_dial': '0',
            'qmodem.2_1.alias': 'Owner modem', 'network.2_1.proto': 'dhcp',
            'firewall.owner.network': '2_1', 'qmodem.2_1.monitor_enable': '1',
        }
        for key, value in preserved.items(): self.put(key, value)
        self.library.test_present(b'2-1', 0)
        self.device('8-1', network='wwan3', at='/dev/ttyUSB6', xhci=7)
        self.assertEqual(0, self.add('8-1'))
        self.assertEqual('/sys/bus/usb/devices/8-1/', self.get('qmodem.2_1.path'))
        self.assertEqual('/dev/ttyUSB6', self.get('qmodem.2_1.at_port'))
        self.assertEqual('wwan3', self.get('qmodem.2_1.network'))
        self.assertEqual('', self.get('qmodem.8_1'))
        self.assertEqual('1', self.get('qmodem.main.modem_count'))
        for key, value in preserved.items():
            with self.subTest(key=key): self.assertEqual(value, self.get(key))

    def test_two_identical_modules_keep_distinct_sections_when_bus_numbers_change(self):
        self.device(); self.assertEqual(0, self.add())
        self.device('4-1', controller='fd880000', network='wwan1', at='/dev/ttyUSB6')
        self.assertEqual(0, self.add('4-1'))
        self.put('qmodem.2_1.apn', 'first.apn'); self.put('qmodem.4_1.apn', 'second.apn')
        self.library.test_present(b'2-1', 0); self.library.test_present(b'4-1', 0)
        self.device('8-1', controller='fd880000', network='wwan0', at='/dev/ttyUSB2', xhci=7)
        self.device('10-1', controller='fd800000', network='wwan1', at='/dev/ttyUSB6', xhci=8)
        self.assertEqual(0, self.add('8-1')); self.assertEqual(0, self.add('10-1'))
        self.assertEqual('/sys/bus/usb/devices/10-1/', self.get('qmodem.2_1.path'))
        self.assertEqual('/sys/bus/usb/devices/8-1/', self.get('qmodem.4_1.path'))
        self.assertEqual('first.apn', self.get('qmodem.2_1.apn'))
        self.assertEqual('second.apn', self.get('qmodem.4_1.apn'))
        self.assertEqual('2', self.get('qmodem.main.modem_count'))

    def test_two_identical_modules_can_swap_the_previous_bus_numbers(self):
        self.device(); self.assertEqual(0, self.add())
        self.device('4-1', controller='fd880000', network='wwan1', at='/dev/ttyUSB6')
        self.assertEqual(0, self.add('4-1'))
        self.put('qmodem.2_1.apn', 'first.apn'); self.put('qmodem.4_1.apn', 'second.apn')
        self.device('2-1', controller='fd880000', network='wwan0', at='/dev/ttyUSB2', xhci=7)
        self.device('4-1', controller='fd800000', network='wwan1', at='/dev/ttyUSB6', xhci=8)
        self.assertEqual(0, self.add('2-1'))
        self.assertEqual(0, self.add('4-1'))
        self.assertEqual('/sys/bus/usb/devices/4-1/', self.get('qmodem.2_1.path'))
        self.assertEqual('/sys/bus/usb/devices/2-1/', self.get('qmodem.4_1.path'))
        self.assertEqual('first.apn', self.get('qmodem.2_1.apn'))
        self.assertEqual('second.apn', self.get('qmodem.4_1.apn'))
        self.assertEqual('2', self.get('qmodem.main.modem_count'))

    def test_stale_manual_tty_overrides_are_removed_after_reenumeration(self):
        self.device(); self.assertEqual(0, self.add())
        self.put('qmodem.2_1.override_at_port', '/dev/ttyUSB2')
        self.put('qmodem.2_1.sms_at_port', '/dev/ttyUSB2')
        self.library.test_present(b'2-1', 0)
        self.device('8-1', at='/dev/ttyUSB6')
        self.assertEqual(0, self.add('8-1'))
        self.assertEqual('', self.get('qmodem.2_1.override_at_port'))
        self.assertEqual('', self.get('qmodem.2_1.sms_at_port'))
        self.put('qmodem.2_1.override_at_port', '/dev/ttyUSB6')
        self.assertEqual(0, self.add('8-1'))
        self.assertEqual('/dev/ttyUSB6', self.get('qmodem.2_1.override_at_port'))

    def test_invalid_root_protocol_is_rejected_without_creating_a_section(self):
        self.device()
        for protocol in ('', 'usb3', '3..10', '.10', '3.'):
            with self.subTest(protocol=protocol):
                self.library.test_protocol(b'2-1', protocol.encode())
                self.assertNotEqual(0, self.add())
                self.assertEqual(0, self.library.test_writes())

    def test_late_remove_does_not_disable_rebound_or_reappeared_modem(self):
        self.device(); self.assertEqual(0, self.add())
        self.library.test_present(b'2-1', 0)
        self.device('8-1', at='/dev/ttyUSB6'); self.assertEqual(0, self.add('8-1'))
        writes = self.library.test_writes()
        self.library.test_disable(b'2-1')
        self.library.test_disable(b'8-1')
        self.assertEqual(writes, self.library.test_writes())
        self.assertEqual('enabled', self.get('qmodem.2_1.state'))
        self.library.test_present(b'8-1', 0)
        self.library.test_disable(b'8-1')
        self.assertEqual('disabled', self.get('qmodem.2_1.state'))
        self.assertEqual('', self.get('qmodem.8_1.state'))

    def test_hotplug_generation_change_rejects_stale_scan_before_any_write(self):
        self.device()
        self.library.test_generation(b'2-1', b'3')
        self.assertNotEqual(0, self.add())
        self.assertEqual(0, self.library.test_writes())
        self.assertEqual(0, self.library.test_reloads())

    def test_usb2_and_usb3_roots_of_same_controller_do_not_merge(self):
        self.device(); self.assertEqual(0, self.add())
        self.device('4-1', network='wwan1', at='/dev/ttyUSB6')
        self.library.test_protocol(b'4-1', b'2.00')
        self.assertEqual(0, self.add('4-1'))
        self.assertEqual('2', self.get('qmodem.main.modem_count'))
        self.assertTrue(self.get('qmodem.4_1.usb_location').endswith('/usb-version:2.00'))

    def test_fixed_device_is_protected_by_current_path_not_historical_section_name(self):
        self.put('qmodem.2_1', 'modem-device')
        self.put('qmodem.2_1.path', '/sys/bus/usb/devices/9-1/')
        self.put('qmodem.2_1.fixed_device', '1')
        self.put('qmodem.2_1.apn', 'manual.apn')
        self.device()
        self.assertEqual(0, self.add())
        self.assertEqual('modem-device', self.get('qmodem.2_1_m28c_1'))
        self.assertEqual('manual.apn', self.get('qmodem.2_1.apn'))
        writes = self.library.test_writes()
        self.device('9-1', controller='fd880000')
        self.assertEqual(0, self.add('9-1'))
        self.assertEqual(writes, self.library.test_writes())
        self.assertEqual('', self.get('qmodem.9_1'))

    def test_replaced_module_disables_old_dial_without_deleting_its_settings(self):
        self.device(); self.assertEqual(0, self.add())
        self.put('qmodem.2_1.name', 'previous-model')
        self.put('qmodem.2_1.apn', 'previous.apn')
        self.assertEqual(0, self.add())
        self.assertEqual('disabled', self.get('qmodem.2_1.state'))
        self.assertEqual('previous.apn', self.get('qmodem.2_1.apn'))
        self.assertEqual('enabled', self.get('qmodem.2_1_m28c_1.state'))
        self.assertEqual('rm500q-cn', self.get('qmodem.2_1_m28c_1.name'))
        self.library.test_present(b'2-1', 0)
        self.library.test_disable(b'2-1')
        self.assertEqual('disabled', self.get('qmodem.2_1_m28c_1.state'))


if __name__ == '__main__':
    unittest.main()
