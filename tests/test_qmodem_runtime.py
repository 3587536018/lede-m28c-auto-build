"""Exercise patched QModem shell functions with mocked UCI/procd/sysfs."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/qmodem-f5035b7d'
BASH = shutil.which('bash') or 'C:/Program Files/Git/bin/bash.exe'
PATCH = json.loads((ROOT / 'patches/qmodem/runtime.json').read_text(encoding='utf-8'))


def patched(suffix):
    entry = next(f for f in PATCH['files'] if f['path'].endswith(suffix))
    source = (FIXTURE / entry['path']).read_text(encoding='utf-8')
    for replacement in entry['replacements']:
        assert source.count(replacement['old']) == 1
        source = source.replace(replacement['old'], replacement['new'], 1)
    return source


def function(source, name):
    match = re.search(r'^' + re.escape(name) + r'\(\)\s*\{.*?^\}', source, re.M | re.S)
    assert match, name
    return match.group(0)


class QmodemRuntimeTests(unittest.TestCase):
    def shell(self, source):
        result = subprocess.run([BASH, '-c', source], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr + '\n' + result.stdout)
        return result.stdout.strip()

    def test_all_patched_runtime_scripts_parse(self):
        for entry in PATCH['files']:
            with self.subTest(path=entry['path']):
                source = patched(entry['path'])
                result = subprocess.run([BASH, '-n'], input=source, text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stderr)

    def network_script(self):
        source = patched('/qmodem_network')
        return '\n'.join(function(source, n) for n in ('_m28c_binding', '_update_modem', '_dial', '_hang')) + r'''
service=qmodem_network
g_enabled=1
force_stop=0
state=enabled
port=/dev/ttyUSB2
cfg_generation=2
previous='/sys/bus/usb/devices/2-1.2/|/dev/ttyUSB2|wwan0|2'
config_load() { :; }
config_get() {
    local value
    case "$3" in
        enable_dial) value=1;;
        state) value=$state;;
        path) value=/sys/bus/usb/devices/2-1.2/;;
        at_port) value=$port;;
        network) value=wwan0;;
        m28c_usb_devnum) value=$cfg_generation;;
    esac
    printf -v "$1" '%s' "$value"
}
_procd_ubus_call() { printf '{}'; }
jq() { cat >/dev/null; case "$*" in *QMODEM_BINDING*) printf '%s' "$previous";; *) printf '{}';; esac; }
logger() { :; }
procd_kill() { printf 'kill %s %s\n' "$1" "$2"; }
dial() { printf 'dial %s\n' "$1"; }
hang() { printf 'hang %s\n' "$1"; }
'''

    def test_unchanged_binding_does_not_restart_dial(self):
        self.assertEqual('', self.shell(self.network_script() + '\n_update_modem 2_1_2'))

    def test_changed_tty_restarts_only_matching_instance_without_hang(self):
        out = self.shell(self.network_script() + '\nport=/dev/ttyUSB6\n_update_modem 2_1_2')
        self.assertEqual('kill qmodem_network modem_2_1_2\ndial 2_1_2', out)

    def test_usb_reset_with_same_names_restarts_dial(self):
        out = self.shell(self.network_script() + '\ncfg_generation=3\n_update_modem 2_1_2')
        self.assertEqual('kill qmodem_network modem_2_1_2\ndial 2_1_2', out)

    def test_disabled_device_stops_existing_dial(self):
        self.assertEqual('hang 2_1_2', self.shell(self.network_script() + '\nstate=disabled\n_update_modem 2_1_2'))

    def test_disconnected_device_hang_does_not_touch_reassigned_port(self):
        out = self.shell(self.network_script() + r'''
uci() { case "$*" in *.state) echo disabled;; *.path) echo /missing/modem;; esac; }
_hang 2_1_2
''')
        self.assertEqual('kill qmodem_network modem_2_1_2', out)

    def test_procd_records_binding_for_next_comparison(self):
        out = self.shell(self.network_script() + r'''
procd_open_instance() { :; }
procd_close_instance() { :; }
procd_set_param() { [ "$1" != env ] || printf '%s\n' "$2"; }
_dial 2_1_2
''')
        self.assertEqual('QMODEM_BINDING=/sys/bus/usb/devices/2-1.2/|/dev/ttyUSB2|wwan0|2', out)

    def monitor_script(self):
        source = patched('/modem_monitor.sh')
        return '\n'.join(function(source, n) for n in ('update_cfg', 'modem_binding_ready')) + r'''
Modem_ID=2_1_2
cfg_state=enabled
cfg_port=/dev/ttyUSB2
cfg_ubus=1
cfg_enable=1
cfg_generation=2
tty_parent=/stable/controller/1.2
config_load() { :; }
config_get() {
    local value
    case "$3" in
        at_port) value=$cfg_port;; alias) value=cellular;; use_ubus) value=$cfg_ubus;;
        path) value=/tmp;; state) value=$cfg_state;; data_interface) value=usb;;
        network) value=wwan0;; enable_dial) value=$cfg_enable;;
        m28c_usb_devnum) value=$cfg_generation;;
    esac
    printf -v "$1" '%s' "$value"
}
readlink() { case "$2" in /tmp) echo /stable/controller/1.2;; *) echo "$tty_parent/1.2:1.2/ttyUSB2";; esac; }
'''

    def test_monitor_refreshes_tty_and_clears_old_ubus_flag(self):
        out = self.shell(self.monitor_script() + r'''
update_cfg
cfg_port=/dev/ttyUSB6
cfg_ubus=0
update_cfg
printf '%s|%s' "$AT_PORT" "$use_ubus_flag"
''')
        self.assertEqual('/dev/ttyUSB6|', out)

    def test_monitor_rejects_removed_disabled_and_other_modems_port(self):
        out = self.shell(self.monitor_script() + r'''
update_cfg
modem_binding_ready && echo ready
tty_parent=/stable/controller/1.20
modem_binding_ready || echo wrong-port
tty_parent=/stable/controller/1.2
cfg_state=disabled
update_cfg
modem_binding_ready || echo disabled
cfg_state=enabled
cfg_enable=0
update_cfg
modem_binding_ready || echo dial-disabled
''')
        self.assertEqual('ready\nwrong-port\ndisabled\ndial-disabled', out)

    def monitor_loop(self, change):
        loop = patched('/modem_monitor.sh').split('while true; do', 1)[1]
        return self.monitor_script() + r'''
update_cfg
failed_count=0
Threshold=1
Interval=1
readiness_grace=1
sleeps=0
update_netcfg() { :; }
wait_until_ready() { return 0; }
log() { :; }
loop() { return 1; }
run_actions() { echo recovery-action; }
sleep() {
    sleeps=$((sleeps + 1))
    [ "$sleeps" -le 1 ] || exit 0
''' + change + '\n}\nwhile true; do' + loop

    def test_recovery_is_cancelled_if_binding_changes_during_wait(self):
        self.assertEqual('', self.shell(self.monitor_loop('cfg_generation=3')))

    def test_recovery_is_cancelled_if_modem_disappears_during_wait(self):
        self.assertEqual('', self.shell(self.monitor_loop('cfg_state=disabled')))

    def test_recovery_still_runs_for_same_present_modem(self):
        self.assertEqual('recovery-action', self.shell(self.monitor_loop(':')))

    def init_script(self):
        source = patched('/qmodem_init')
        return '\n'.join(function(source, n) for n in ('_m28c_mark_usb_pending', 'start_service')) + r'''
cfg_block=0
config_load() { :; }
config_get() {
    local value=0
    case "$3" in
        block_auto_probe) value=$cfg_block;;
        data_interface) value=usb;;
        fixed_device) [ "$2" != fixed ] || value=1;;
        state) value=enabled;;
    esac
    printf -v "$1" '%s' "$value"
}
config_foreach() {
    [ "$1" = _m28c_mark_usb_pending ] || return 0
    "$1" auto
    "$1" fixed
}
uci() { printf 'uci %s\n' "$*"; }
_start_scand() { echo daemon-start; }
'''

    def test_boot_marks_automatic_usb_pending_before_starting_scanner(self):
        out = self.shell(self.init_script() + '\nstart_service')
        self.assertEqual('uci -q set qmodem.auto.state=disabled\nuci commit qmodem\ndaemon-start', out)

    def test_blocked_auto_probe_preserves_manual_startup_state(self):
        out = self.shell(self.init_script() + '\ncfg_block=1\nstart_service')
        self.assertEqual('daemon-start', out)

    def test_pending_write_failure_does_not_start_scanner(self):
        out = self.shell(self.init_script() + '\nuci() { return 1; }\nstart_service || echo failed')
        self.assertEqual('failed', out)


if __name__ == '__main__':
    unittest.main()
