"""Run the patched Docker init and first-boot policy with mocked OpenWrt APIs."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/docker"
SPEC = importlib.util.spec_from_file_location("customize_docker", ROOT / "scripts/customize-docker.py")
CUSTOMIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CUSTOMIZE)
BASH = shutil.which("bash")
if not BASH and Path("C:/Program Files/Git/bin/bash.exe").is_file():
    BASH = "C:/Program Files/Git/bin/bash.exe"


def populate(root):
    package = root / "feeds/packages/utils/dockerd"
    for target, fixture in (("Makefile", "Makefile"), ("files/dockerd.init", "files_dockerd.init"),
                            ("files/etc/config/dockerd", "dockerd.config")):
        path = package / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PATCH / "upstream" / fixture, path)
    return package


class PackagePatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="m28c-docker-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = populate(self.root)

    def apply(self):
        with contextlib.redirect_stdout(io.StringIO()):
            CUSTOMIZE.customize(self.root)

    def snapshot(self):
        return {str(p.relative_to(self.package)): p.read_bytes()
                for p in self.package.rglob("*") if p.is_file()}

    def test_patch_reaches_matching_ipk_install_and_has_distinct_release(self):
        self.apply()
        make = (self.package / "Makefile").read_text()
        self.assertIn("PKG_VERSION:=29.6.1\nPKG_RELEASE:=1.m28c1\n", make)
        for path in ("files/dockerd.init", "files/m28c-docker.sh", "files/fw4-lan-published.nft",
                     "files/90-m28c-docker-nftables"):
            self.assertIn("./" + path, make)
            self.assertTrue((self.package / path).is_file())
        self.assertIn("+firewall4", make)
        self.assertIn("option firewall_backend 'nftables'", (self.package / "files/etc/config/dockerd").read_text())

    def test_second_application_is_byte_identical(self):
        self.apply()
        first = self.snapshot()
        self.apply()
        self.assertEqual(first, self.snapshot())

    def test_any_upstream_drift_fails_before_writes(self):
        for entry in json.loads((PATCH / "package.json").read_text())["files"]:
            with self.subTest(path=entry["path"]):
                populate(self.root)
                path = self.package / entry["path"]
                path.write_text(path.read_text() + "# unexpected drift\n")
                before = self.snapshot()
                with self.assertRaisesRegex(CUSTOMIZE.PatchError, "source drift"):
                    self.apply()
                self.assertEqual(before, self.snapshot())

    def test_existing_unknown_helper_is_not_overwritten(self):
        path = self.package / "files/m28c-docker.sh"
        path.write_text("# user helper\n")
        before = self.snapshot()
        with self.assertRaisesRegex(CUSTOMIZE.PatchError, "differs from managed"):
            self.apply()
        self.assertEqual(before, self.snapshot())

    def test_policies_are_scoped_without_rule_flush(self):
        default = (PATCH / "files/90-m28c-docker-nftables").read_text()
        include = (PATCH / "files/fw4-lan-published.nft").read_text()
        self.assertIn('oifname "docker0" ct status dnat', include)
        self.assertIn(".position=chain-prepend", default)
        self.assertIn(".chain=forward_lan", default)
        for forbidden in ("flush", "restart", " reload", "iptables "):
            self.assertNotIn(forbidden, "\n".join(l for l in default.splitlines() if not l.startswith("#")))


CONFIG_HARNESS = r'''
declare -A cfg
loaded=dockerd
mutations=0
config_load() { loaded=$1; }
config_get() { local result="${cfg[$loaded.$2.$3]-${4-}}"; printf -v "$1" '%s' "$result"; }
config_get_bool() { config_get "$@"; }
config_foreach() {
    local callback=$1 type=$2 key
    shift 2
    for key in "${!cfg[@]}"; do
        if [[ "$key" == "$loaded."* && "${key#$loaded.}" != *.* && "${cfg[$key]}" == "$type" ]]; then
            "$callback" "${key#$loaded.}" "$@"
        fi
    done
}
config_list_foreach() { local item; for item in ${cfg[$loaded.$1.$2]-}; do "$3" "$item"; done; }
uci() {
    [ "${1-}" != -q ] || shift
    local action=$1 key=${2-}
    case "$action" in
        get) [[ -v cfg[$key] ]] || return 1; printf '%s\n' "${cfg[$key]}" ;;
        set) cfg[${key%%=*}]=${key#*=}; mutations=$((mutations+1)) ;;
        add_list) cfg[${key%%=*}]="${cfg[${key%%=*}]-} ${key#*=}"; mutations=$((mutations+1)) ;;
        commit) : ;;
        *) echo "unexpected UCI action $action" >&2; return 1 ;;
    esac
}
logger() { printf 'LOG: %s\n' "$*" >&2; }
extra_command() { :; }
network_get_ipaddr() { printf -v "$1" '%s' "${lan_address-192.0.2.1}"; }
ip() { [ -z "${bridge_address-}" ] || printf '3: docker0 inet %s/16 scope global docker0\n' "$bridge_address"; }
cfg[dockerd.globals]=globals
cfg[dockerd.globals.firewall_backend]=nftables
cfg[dockerd.firewall]=firewall
cfg[dockerd.firewall.device]=docker0
cfg[dockerd.firewall.blocked_interfaces]=wan
cfg[firewall.lan]=zone
cfg[firewall.lan.name]=lan
cfg[firewall.wan]=zone
cfg[firewall.wan.name]=wan
'''

JSON_HARNESS = r'''
json_init() { json_lines=""; json_key=""; }
json_add_string() { json_lines+="${json_key}${1}=${2}"$'\n'; }
json_add_boolean() { json_add_string "$@"; }
json_add_array() { json_key="$1[]"; }
json_close_array() { json_key=""; }
json_add_object() { json_key="$1."; }
json_close_object() { json_key=""; }
json_dump() { printf '%s' "$json_lines"; }
procd_open_instance() { echo STARTED; }
procd_set_param() { :; }
procd_close_instance() { :; }
procd_send_signal() { echo RELOADED; }
'''


@unittest.skipUnless(BASH, "Docker shell regression checks require bash")
class DockerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="m28c-docker-shell-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        package = populate(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            CUSTOMIZE.customize(self.root)
        self.init = (package / "files/dockerd.init").read_text()
        self.helper = (PATCH / "files/m28c-docker.sh").read_text().replace(". /lib/functions/network.sh", ": # mocked network API")
        self.defaults = (PATCH / "files/90-m28c-docker-nftables").read_text().replace(". /lib/functions.sh", ": # mocked UCI API")
        self.defaults = self.defaults.replace('. /usr/libexec/m28c-docker.sh', ': # helper loaded by harness')
        self.init = self.init.replace('. /usr/share/libubox/jshn.sh', ': # mocked JSON API')
        self.init = self.init.replace('. /usr/libexec/m28c-docker.sh', ': # helper loaded by harness')
        self.init = self.init.replace('[ -f /etc/config/dockerd ]', 'true')
        self.init = self.init.replace('DOCKER_CONF_DIR="/tmp/dockerd"', f'DOCKER_CONF_DIR="{self.root.as_posix()}/daemon"')
        self.init = self.init.replace('local nofile=$(cat /proc/sys/fs/nr_open)', 'local nofile=65536')
        self.init += '\niptables_add_blocking_rule() { echo LEGACY >&2; }\n'

    def shell(self, body, defaults=False):
        script = CONFIG_HARNESS + JSON_HARNESS + self.helper + "\n"
        script += self.defaults.rsplit("\ndocker_firewall_defaults", 1)[0] if defaults else self.init
        result = subprocess.run([BASH, "--noprofile", "--norc", "-s"], input=script + "\n" + body,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result.stdout, result.stderr

    def test_native_backend_preserves_uci_and_proxy_values_and_skips_legacy(self):
        out, err = self.shell(r'''
cfg[dockerd.globals.data_root]=/opt/custom
cfg[dockerd.globals.dns]='192.0.2.53 198.51.100.53'
cfg[dockerd.globals.bip]=172.20.0.1/24
cfg[dockerd.proxies.http_proxy]=http://proxy.invalid:3128
process_config || exit $?
cat "$DOCKERD_CONF"
''')
        self.assertIn("firewall-backend=nftables", out)
        self.assertIn("iptables=1", out)
        self.assertIn("ip6tables=0", out)
        self.assertIn("data-root=/opt/custom", out)
        self.assertIn("bip=172.20.0.1/24", out)
        self.assertIn("proxies.http-proxy=http://proxy.invalid:3128", out)
        self.assertEqual(2, out.count("dns[]="))
        self.assertIn("dns[]=198.51.100.53", out)
        self.assertNotIn("LEGACY", err)

    def test_dns_uses_bip_then_existing_bridge_then_current_lan(self):
        cases = (("cfg[dockerd.globals.bip]=172.25.0.1/24\nbridge_address=172.20.0.1", "172.25.0.1"),
                 ("bridge_address=172.30.0.1", "172.30.0.1"),
                 ("lan_address=198.51.100.1", "198.51.100.1"))
        for setup, expected in cases:
            with self.subTest(expected=expected):
                out, _ = self.shell(setup + '\nprocess_config || exit $?\ncat "$DOCKERD_CONF"')
                self.assertIn("dns[]=" + expected, out)

    def test_missing_or_invalid_dns_candidates_are_not_guessed(self):
        for setup in ("lan_address=", "cfg[dockerd.globals.bip]=999.0.0.1/24", "cfg[dockerd.globals.bip]=172.20.0.1/64"):
            with self.subTest(setup=setup):
                out, _ = self.shell(setup + '\nprocess_config || exit $?\ncat "$DOCKERD_CONF"')
                self.assertNotIn("dns[]=", out)

    def test_explicit_legacy_backend_retains_legacy_helper(self):
        out, err = self.shell('cfg[dockerd.globals.firewall_backend]=iptables\nprocess_config || exit $?\ncat "$DOCKERD_CONF"')
        self.assertIn("firewall-backend=iptables", out)
        self.assertIn("LEGACY", err)
        self.assertNotIn("dns[]=", out)

    def test_unsupported_legacy_policy_prevents_start_and_reload(self):
        for setup in ("cfg[dockerd.firewall.blocked_interfaces]='wan guest'", "cfg[dockerd.firewall.device]=br-custom",
                      "cfg[dockerd.firewall.extra_iptables_args]='--match conntrack'"):
            with self.subTest(setup=setup):
                out, err = self.shell(setup + '\nstart_service && exit 97\nreload_service && exit 98\ntrue')
                self.assertNotIn("STARTED", out)
                self.assertNotIn("RELOADED", out)
                self.assertIn("cannot translate", err)

    def test_alternate_config_file_bypasses_generated_options(self):
        alternate = self.root / "user-daemon.json"
        alternate.write_text('{"firewall-backend":"iptables","dns":["203.0.113.53"]}')
        out, err = self.shell(f'''
cfg[dockerd.globals.alt_config_file]='{alternate.as_posix()}'
cfg[dockerd.firewall.blocked_interfaces]=guest
ln() {{ printf 'ALTERNATE %s\\n' "$2"; }}
process_config || exit $?
''')
        self.assertIn("ALTERNATE " + alternate.as_posix(), out)
        self.assertNotIn("cannot translate", err)
        self.assertNotIn("LEGACY", err)
        self.assertEqual('{"firewall-backend":"iptables","dns":["203.0.113.53"]}', alternate.read_text())

    def test_delayed_migration_loads_policy_before_start_and_only_once(self):
        defaults = self.root / "pending-defaults"
        defaults.write_text("#!/bin/sh\nexit 0\n", newline="\n")
        self.init = self.init.replace("local defaults=/etc/uci-defaults/90-m28c-docker-nftables",
                                      f"local defaults='{defaults.as_posix()}'")
        out, _ = self.shell('fw4() { echo "FW4_$1"; }\nstart_service || exit $?\nstart_service || exit $?')
        self.assertEqual(["FW4_check", "FW4_reload", "STARTED", "STARTED"], out.splitlines())
        self.assertFalse(defaults.exists())

    def test_failed_delayed_policy_load_keeps_pending_file_and_stops_docker(self):
        defaults = self.root / "pending-defaults"
        defaults.write_text("#!/bin/sh\nexit 0\n", newline="\n")
        self.init = self.init.replace("local defaults=/etc/uci-defaults/90-m28c-docker-nftables",
                                      f"local defaults='{defaults.as_posix()}'")
        out, _ = self.shell('fw4() { [ "$1" = check ]; }\nstart_service && exit 99\ntrue')
        self.assertNotIn("STARTED", out)
        self.assertTrue(defaults.exists())

    def test_first_boot_creates_zone_before_policy_and_second_run_preserves_changes(self):
        out, _ = self.shell(r'''
docker_firewall_defaults || exit $?
[ "${cfg[network.docker.device]}" = docker0 ] || exit 90
[ "${cfg[firewall.docker.name]}" = docker ] || exit 91
[ "${cfg[firewall.m28c_docker_wan_guard.target]}" = REJECT ] || exit 92
cfg[firewall.m28c_docker_wan_guard.enabled]=0
cfg[firewall.m28c_docker_to_wan.dest]=vpn
before=$mutations
docker_firewall_defaults || exit $?
[ "$mutations" = "$before" ] || exit 93
echo "${cfg[firewall.m28c_docker_wan_guard.enabled]} ${cfg[firewall.m28c_docker_to_wan.dest]}"
''', defaults=True)
        self.assertEqual("0 vpn\n", out)

    def test_existing_anonymous_docker_zone_is_preserved(self):
        out, _ = self.shell(r'''
cfg[firewall.cfgABC]=zone
cfg[firewall.cfgABC.name]=docker
cfg[firewall.cfgABC.input]=REJECT
cfg[firewall.cfgABC.network]=docker
docker_firewall_defaults || exit $?
[[ ! -v cfg[firewall.docker] ]] || exit 90
echo "${cfg[firewall.cfgABC.input]}"
''', defaults=True)
        self.assertEqual("REJECT\n", out)

    def test_existing_legacy_and_alternate_configs_get_no_first_boot_policy(self):
        for setup in ("cfg[dockerd.globals.firewall_backend]=iptables",
                      "cfg[dockerd.globals.alt_config_file]=/etc/custom-daemon.json"):
            with self.subTest(setup=setup):
                out, _ = self.shell(setup + '\ndocker_firewall_defaults || exit $?\necho "$mutations"', defaults=True)
                self.assertEqual("0\n", out)

    def test_preserved_110_config_without_backend_is_migrated(self):
        out, _ = self.shell(r'''
unset 'cfg[dockerd.globals.firewall_backend]'
cfg[dockerd.globals.dns]='203.0.113.53'
docker_firewall_defaults && exit 99
[ "$mutations" = 0 ] || exit 98
docker_firewall_defaults --after-mounts || exit $?
echo "${cfg[dockerd.globals.firewall_backend]} ${cfg[dockerd.globals.dns]} ${cfg[firewall.m28c_docker_wan_guard.target]}"
''', defaults=True)
        self.assertEqual("nftables 203.0.113.53 REJECT\n", out)

    def test_preserved_swarm_state_does_not_switch_backend(self):
        swarm = self.root / "data/swarm"
        swarm.mkdir(parents=True)
        (swarm / "fixture-state").write_text("preserved")
        out, err = self.shell(f'''
unset 'cfg[dockerd.globals.firewall_backend]'
cfg[dockerd.globals.data_root]='{swarm.parent.as_posix()}'
docker_firewall_defaults --after-mounts && exit 99
echo "$mutations"
''', defaults=True)
        self.assertEqual("0\n", out)
        self.assertIn("Swarm state preserved", err)

    def test_missing_standard_zone_does_not_create_invalid_policy(self):
        out, err = self.shell(r'''
unset 'cfg[firewall.wan]' 'cfg[firewall.wan.name]'
docker_firewall_defaults && exit 99
echo "$mutations"
''', defaults=True)
        self.assertEqual("0\n", out)
        self.assertIn("LAN/WAN zones missing", err)

    def test_custom_network_is_not_overwritten(self):
        out, err = self.shell(r'''
cfg[network.docker]=interface
cfg[network.docker.device]=br-custom
docker_firewall_defaults && exit 99
echo "$mutations ${cfg[network.docker.device]}"
''', defaults=True)
        self.assertEqual("0 br-custom\n", out)
        self.assertIn("preserved", err)


if __name__ == "__main__":
    unittest.main()
