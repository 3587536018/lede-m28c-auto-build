"""Run the firmware's USB identity C code with a native compiler.

CI uses gcc; Windows developers may set CC to a portable TinyCC executable.
These tests never connect to a modem, modify sysfs, or invoke the real UCI.
"""

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
CC = os.environ.get('CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
if not CC and (REPO / '.cache/tinycc/tcc/tcc.exe').is_file():
    CC = str(REPO / '.cache/tinycc/tcc/tcc.exe')

WRAPPER = r'''
#ifdef _WIN32
/* The old MSVCRT bundled with TinyCC uses pre-C99 truncation semantics.
 * Match the firmware's POSIX snprintf contract before loading its header. */
#include <stdio.h>
#include <stdarg.h>
static int c99_snprintf(char *out, size_t len, const char *format, ...) {
    va_list args;
    int needed;
    va_start(args, format);
    needed = _vscprintf(format, args);
    va_end(args);
    if (len) {
        va_start(args, format);
        _vsnprintf(out, len, format, args);
        va_end(args);
        out[len - 1] = 0;
    }
    return needed;
}
#define snprintf c99_snprintf
#endif
#include "m28c_usb_identity.h"
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
EXPORT int test_normalize(const char *real, char *out, size_t len) {
    return normalize_usb_location(real, out, len);
}
EXPORT int test_choose(const struct m28c_usb_binding *items, size_t count,
    const char *slot_section, const char *path, const char *location,
    const char *vid, const char *pid, const char *name, char *out, size_t len) {
    return m28c_choose_usb_section(items, count, slot_section, path,
        location, vid, pid, name, out, len);
}
EXPORT int test_slot(const struct m28c_usb_binding *items, size_t count, const char *slot) {
    return m28c_binding_for_slot(items, count, slot);
}
EXPORT size_t test_binding_size(void) { return sizeof(struct m28c_usb_binding); }
'''


class Binding(ctypes.Structure):
    _fields_ = [(name, ctypes.c_char * size) for name, size in (
        ('section', 128), ('path', 256), ('location', 512),
        ('vid', 16), ('pid', 16), ('name', 128),
    )] + [('fixed', ctypes.c_int), ('present', ctypes.c_int), ('disabled', ctypes.c_int)]


@unittest.skipUnless(CC, 'native USB identity tests require a C compiler (set CC)')
class QModemIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='m28c-usb-native-')
        root = Path(cls.temporary.name)
        source = root / 'identity.c'
        source.write_text(WRAPPER, encoding='utf-8')
        library = root / ('identity.dll' if os.name == 'nt' else 'identity.so')
        args = [CC, '-Wall', '-Werror', '-shared']
        if os.name != 'nt':
            args += ['-fPIC']
        args += ['-I', str(REPO / 'patches/qmodem'), str(source), '-o', str(library)]
        subprocess.run(args, check=True, text=True, capture_output=True)
        cls.library = ctypes.CDLL(str(library))
        cls.library.test_binding_size.restype = ctypes.c_size_t
        if cls.library.test_binding_size() != ctypes.sizeof(Binding):
            raise AssertionError('ctypes binding layout no longer matches the production C header')
        cls.library.test_normalize.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        cls.library.test_normalize.restype = ctypes.c_int
        cls.library.test_choose.argtypes = [ctypes.POINTER(Binding), ctypes.c_size_t] + [ctypes.c_char_p] * 6 + [ctypes.c_void_p, ctypes.c_size_t]
        cls.library.test_choose.restype = ctypes.c_int
        cls.library.test_slot.argtypes = [ctypes.POINTER(Binding), ctypes.c_size_t, ctypes.c_char_p]
        cls.library.test_slot.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        # Windows cannot remove a loaded DLL; explicitly release the test DLL.
        if os.name == 'nt':
            import _ctypes
            _ctypes.FreeLibrary(cls.library._handle)
        cls.temporary.cleanup()

    def normalize(self, path, length=512):
        output = ctypes.create_string_buffer(length)
        code = self.library.test_normalize(path.encode(), output, length)
        return code, output.value.decode()

    def choose(self, bindings, slot='8_1', path='/sys/bus/usb/devices/8-1/',
               location='/sys/devices/platform/fd800000.usb/xhci-hcd/port:1',
               vid='2c7c', pid='0800', name='rm500q-cn', length=128):
        items = (Binding * len(bindings))()
        for item, values in zip(items, bindings):
            for key, value in values.items():
                setattr(item, key, value if isinstance(value, int) else value.encode())
        output = ctypes.create_string_buffer(length)
        code = self.library.test_choose(items, len(items), *[
            value.encode() for value in (slot, path, location, vid, pid, name)
        ], output, length)
        return code, output.value.decode()

    def binding(self, section='2_1', **changes):
        result = dict(section=section, path='/sys/bus/usb/devices/2-1/',
                      location='/sys/devices/platform/fd800000.usb/xhci-hcd/port:1',
                      vid='2c7c', pid='0800', name='rm500q-cn', fixed=0)
        result.update(changes)
        return result

    def test_usb_bus_and_dynamic_xhci_numbers_do_not_change_identity(self):
        old = '/sys/devices/platform/fd800000.usb/xhci-hcd.1.auto/usb2/2-1'
        new = '/sys/devices/platform/fd800000.usb/xhci-hcd.7.auto/usb8/8-1/'
        self.assertEqual((0, '/sys/devices/platform/fd800000.usb/xhci-hcd/port:1'), self.normalize(old))
        self.assertEqual(self.normalize(old), self.normalize(new))
        self.assertEqual((1, '2_1'), self.choose([self.binding()]))

    def test_complete_hub_port_chain_is_kept(self):
        first = '/sys/devices/platform/fd800000.usb/usb2/2-1/2-1.3/2-1.3.4'
        second = '/sys/devices/platform/fd800000.usb/usb8/8-1/8-1.3/8-1.3.4'
        self.assertEqual((0, '/sys/devices/platform/fd800000.usb/port:1.3.4'), self.normalize(first))
        self.assertEqual(self.normalize(first), self.normalize(second))

    def test_different_controller_or_port_remains_distinct(self):
        base = '/sys/devices/platform/fd800000.usb/usb2/2-1'
        for other in (base.replace('fd800000', 'fd880000'), base.replace('2-1', '2-2')):
            with self.subTest(other=other):
                self.assertNotEqual(self.normalize(base), self.normalize(other))

    def test_invalid_and_truncated_sysfs_paths_are_rejected(self):
        invalid = (
            '', '/sys/bus/usb/devices/2-1', '/tmp/usb2/2-1',
            '/sys/devices/platform/mock/usb2',
            '/sys/devices/platform/mock/usb2/8-1',
            '/sys/devices/platform/mock/usb2/2-1/2-2.1',
            '/sys/devices/platform/mock/usb2/2-1:1.0',
            '/sys/devices/platform/mock/usb2/2-1.',
            '/sys/devices/platform/mock/usb2/2-.1',
            '/sys/devices/' + 'a' * 1100 + '/usb2/2-1',
        )
        for path in invalid:
            with self.subTest(path=path[:100]):
                self.assertEqual(-1, self.normalize(path)[0])
        self.assertEqual((-1, ''), self.normalize('/sys/devices/platform/mock/usb2/2-1', length=8))

    def test_two_same_model_modules_follow_their_own_physical_ports(self):
        first = self.binding()
        second = self.binding('4_1', path='/sys/bus/usb/devices/4-1/',
                              location=first['location'].replace('fd800000', 'fd880000'))
        self.assertEqual((1, '2_1'), self.choose([first, second]))
        self.assertEqual((1, '4_1'), self.choose([first, second], location=second['location']))

    def test_duplicate_stable_bindings_are_not_guessed(self):
        self.assertEqual(-1, self.choose([self.binding(), self.binding('4_1')])[0])

    def test_unrelated_same_model_at_new_location_creates_a_new_section(self):
        self.assertEqual((0, '8_1'), self.choose([self.binding()], location='/sys/devices/another/port:1'))

    def test_replacement_vendor_product_or_model_never_inherits_configuration(self):
        for changes in ({'vid': '2cb7'}, {'pid': '0900'}, {'name': 'rm520n-gl'}):
            with self.subTest(changes=changes):
                self.assertEqual((0, '8_1'), self.choose([self.binding()], **changes))

    def test_legacy_configuration_enrolls_only_its_exact_current_path_and_model(self):
        legacy = self.binding(location='', vid='', pid='')
        self.assertEqual((1, '2_1'), self.choose([legacy], path='/sys/bus/usb/devices/2-1'))
        self.assertEqual((0, '8_1'), self.choose([legacy]))
        self.assertEqual((0, '8_1'), self.choose([legacy], path=legacy['path'], name='another-model'))

    def test_duplicate_legacy_paths_are_rejected(self):
        one = self.binding(location='')
        two = self.binding('4_1', location='')
        self.assertEqual(-1, self.choose([one, two], path=one['path'])[0])

    def test_fixed_configuration_cannot_be_rebound(self):
        self.assertEqual(-1, self.choose([self.binding(fixed=1)])[0])

    def test_live_device_at_another_path_cannot_be_stolen(self):
        live = self.binding(present=1)
        self.assertEqual(-1, self.choose([live])[0])
        self.assertEqual((1, '2_1'), self.choose([live], path=live['path']))

    def test_disable_slot_follows_current_path_and_rejects_duplicate_paths(self):
        items = (Binding * 2)()
        items[0].section = b'2_1'
        items[0].path = b'/sys/bus/usb/devices/8-1/'
        items[1].section = b'8_1'
        items[1].path = b'/sys/bus/usb/devices/9-1/'
        self.assertEqual(0, self.library.test_slot(items, 2, b'8-1'))
        self.assertEqual(1, self.library.test_slot(items, 2, b'9-1'))
        self.assertEqual(-1, self.library.test_slot(items, 2, b'2-1'))
        items[1].path = items[0].path
        self.assertEqual(-2, self.library.test_slot(items, 2, b'8-1'))
        items[1].disabled = 1
        self.assertEqual(0, self.library.test_slot(items, 2, b'8-1'))

    def test_historical_slot_name_collision_gets_an_unused_name(self):
        items = [self.binding('8_1', location=''), self.binding('8_1_m28c_1', location='')]
        self.assertEqual((0, '8_1_m28c_2'), self.choose(items))

    def test_missing_identity_and_short_output_buffer_fail(self):
        for changes in ({'location': ''}, {'vid': ''}, {'pid': ''}, {'name': ''}, {'length': 2}):
            with self.subTest(changes=changes):
                self.assertEqual(-1, self.choose([self.binding()], **changes)[0])


if __name__ == '__main__':
    unittest.main()
