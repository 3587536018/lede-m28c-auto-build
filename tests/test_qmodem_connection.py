"""Run actual QModem connection detection against netifd JSON and sysfs fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_qmodem_runtime import BASH, patched, function


@unittest.skipUnless(shutil.which('jq'), 'connection detection requires jq')
class QmodemConnectionTests(unittest.TestCase):
    def detect(self, interfaces, *, missing=False, at_connected=False, query_failure=None):
        with tempfile.TemporaryDirectory(prefix='m28c-connect-') as directory:
            root = Path(directory)
            (root / 'netifd.json').write_text(json.dumps({'interface': interfaces}), encoding='utf-8')
            source = patched('/generic.sh')
            code = '\n'.join(function(source, name) for name in (
                'get_cgpaddr_ipv4', 'm28c_netifd_connected', 'get_connect_status'))
            # The production script still uses literal /sys paths; only this
            # process maps them to real isolated directories and symbolic links.
            code = code.replace('/sys/devices', '$T/devices').replace('/sys/class/net', '$T/class/net')
            setup = r'''
set -e
T=$(cd "$1" && pwd)
mkdir -p "$T/devices/modem/usb-if/net/wwan0" "$T/devices/other" "$T/class/net/wwan0_1" "$T/class/net/eth1" "$T/class/net/wwan0"
ln -s "$T/devices/modem/usb-if/net/wwan0" "$T/class/net/wwan0_1/device"
ln -s "$T/devices/modem/usb-if" "$T/class/net/wwan0/device"
ln -s "$T/devices/other" "$T/class/net/eth1/device"
[ -L "$T/class/net/wwan0_1/device" ] || exit 2
modem_path="$T/devices/modem"
ubus() { [ "$*" = 'call network.interface dump' ] || return 2; cat "$T/netifd.json"; }
get_driver() { echo qmi_wwan; }
cmd_cgact_query() { [ "$AT_CONNECTED" = 0 ] || echo '+CGACT: 1,1'; }
cmd_cgpaddr() { echo '+CGPADDR: 1,"192.0.2.20"'; }
add_plain_info_entry() { printf '%s\n' "$2"; }
at_port=/dev/fixture-only
vendor=quectel
platform=qualcomm
'''
            # readlink resolves the entire physical hierarchy, including QMAP's
            # extra /net/wwan0 level; a similarly named or unrelated WAN fails.
            if missing:
                setup += '\nmodem_path="$T/devices/missing"\n'
            if query_failure:
                setup += '\n' + query_failure + '() { return 1; }\n'
            env = dict(os.environ, MSYS='winsymlinks:sys', AT_CONNECTED='1' if at_connected else '0')
            result = subprocess.run([BASH, '-c', setup + '\n' + code + '\nset +e\nget_connect_status\n',
                                     'connection-test', root.as_posix()],
                                    text=True, capture_output=True, env=env, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
            return result.stdout.strip()

    def interface(self, device='wwan0_1', up=True, ipv4='192.0.2.10', ipv6=None):
        return {'interface': 'fixture_modem', 'up': up, 'l3_device': device,
                'ipv4-address': [{'address': ipv4}] if ipv4 is not None else [],
                'ipv6-address': [{'address': ipv6}] if ipv6 is not None else []}

    def test_qmap_up_with_at_inactive_is_connected(self):
        self.assertEqual('Yes', self.detect([self.interface()]))

    def test_physical_base_interface_is_connected(self):
        self.assertEqual('Yes', self.detect([self.interface(device='wwan0')]))

    def test_ipv6_only_global_address_is_connected(self):
        self.assertEqual('Yes', self.detect([self.interface(ipv4=None, ipv6='2001:db8::2')]))

    def test_other_wan_does_not_make_modem_connected(self):
        self.assertEqual('No', self.detect([self.interface(device='eth1')]))

    def test_missing_modem_with_stale_netifd_is_disconnected(self):
        self.assertEqual('No', self.detect([self.interface()], missing=True))

    def test_down_interface_with_stale_address_is_disconnected(self):
        self.assertEqual('No', self.detect([self.interface(up=False)]))

    def test_no_address_or_only_unusable_address_is_disconnected(self):
        for ipv4, ipv6 in ((None, None), ('0.0.0.0', '::'), ('169.254.1.2', None),
                           (None, 'fe80::1'), (None, 'FE90::1'), (None, 'fea0::1'), (None, 'febf::1')):
            with self.subTest(ipv4=ipv4, ipv6=ipv6):
                self.assertEqual('No', self.detect([self.interface(ipv4=ipv4, ipv6=ipv6)]))

    def test_invalid_or_missing_device_is_not_used(self):
        for device in ('../wwan0_1', 'wwan0_2', '', 'wwan0_1;echo injected'):
            with self.subTest(device=device):
                self.assertEqual('No', self.detect([self.interface(device=device)]))

    def test_existing_at_connected_behavior_is_preserved(self):
        self.assertEqual('Yes', self.detect([], at_connected=True))

    def test_empty_netifd_is_disconnected(self):
        self.assertEqual('No', self.detect([]))

    def test_query_failure_cannot_fabricate_a_connection(self):
        for tool in ('ubus', 'jq'):
            with self.subTest(tool=tool):
                self.assertEqual('No', self.detect([self.interface()], query_failure=tool))
