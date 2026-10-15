#!/usr/bin/env bash
# Isolated regression tests: no block device, mount, UCI or swap is modified.
set -eu

repo=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d "${TMPDIR:-/tmp}/m28c-storage-test.XXXXXX")
trap 'case "$fixture" in "${TMPDIR:-/tmp}"/m28c-storage-test.*) rm -rf "$fixture" ;; esac' EXIT
mkdir -p "$fixture/sys/mmcblk1p1" "$fixture/sys/mmcblk1p2" "$fixture/sys/mmcblk1p3"
touch "$fixture/sys/mmcblk1p1/partition" "$fixture/sys/mmcblk1p2/partition" "$fixture/sys/mmcblk1p3/partition"
# Source functions without executing first-boot main. Only the read-only sysfs
# inventory path is substituted so label discovery uses our synthetic devices.
sed -e '$d' -e "s|/sys/class/block|$fixture/sys|g" \
  "$repo/files/etc/uci-defaults/99-expand-data" > "$fixture/library.sh"
source "$fixture/library.sh"

DISK=/dev/mmcblk1
BOOT_PARTITION=/dev/mmcblk1p2
PART=/dev/mmcblk1p3
mutations="$fixture/mutations"
touch "$mutations"
record_mutation() { printf '%s\n' "$*" >> "$mutations"; }
uci() { record_mutation "uci $*"; }
mount() { record_mutation "mount $*"; }
mkfs.ext4() { record_mutation "mkfs $*"; }
mkswap() { record_mutation "mkswap $*"; }
swapon() { record_mutation "swapon $*"; }
mkdir() { record_mutation "mkdir $*"; }
dd() { record_mutation "dd $*"; }
fallocate() { record_mutation "fallocate $*"; }
partprobe() { :; }
blockdev() { :; }
sleep() { :; }

parted() { printf '%s\n' "$partition_fixture"; }
partition_fixture='BYT;
/dev/mmcblk1:124735488s:sd/mmc:512:512:msdos:SD:;
1:2048s:67583s:65536s:fat16::boot;
2:67584s:2164735s:2097152s:::;
1:2164736s:124735487s:122570752s:free;'

assert_equal() {
  [ "$1" = "$2" ] || { printf 'FAIL: expected <%s>, received <%s>\n' "$2" "$1" >&2; exit 1; }
}
assert_failure() {
  if "$@"; then printf 'FAIL: expected failure: %s\n' "$*" >&2; exit 1; fi
}
pass() { printf 'PASS: %s\n' "$1"; }

assert_equal "$(partition_plan)" '3 2164736 124735487 512'
pass 'new partition starts after the real root partition'
(
  partition_fixture=${partition_fixture//2164735/4261887}
  partition_fixture=${partition_fixture//2164736/4261888}
  assert_equal "$(partition_plan)" '3 4261888 124735487 512'
)
pass 'larger root partition changes the data start without a fixed offset'
(
  partition_fixture=${partition_fixture//2164736/2164737}
  assert_equal "$(partition_plan)" '3 2166784 124735487 512'
)
pass 'new data start rounds up to a MiB boundary'
(
  partition_fixture='BYT;
/dev/mmcblk1:15591936s:sd/mmc:4096:4096:gpt:SD:;
1:256s:8447s:8192s:fat16:boot:;
2:8448s:270591s:262144s::root:;
1:270592s:15591902s:15321311s:free;'
  assert_equal "$(partition_plan)" '3 270592 15591902 4096'
)
pass 'GPT and 4096-byte logical sectors use correct units'
(
  partition_fixture=${partition_fixture//msdos/unknown}
  assert_failure partition_plan
)
pass 'unknown partition tables are rejected'
(
  partition_fixture=${partition_fixture%$'\n'*}
  partition_fixture="$partition_fixture
3:2164736s:124735487s:122570752s:ext4::;"
  assert_failure partition_plan
)
pass 'an existing data partition consumes space and is never treated as free'
(
  partition_fixture=${partition_fixture//124735487/2300000}
  assert_failure partition_plan
)
pass 'insufficient free space stops creation'
(
  partition_fixture=${partition_fixture//$'2:67584s'/$'3:67584s'}
  assert_failure partition_plan
)
pass 'partition numbering gaps require manual inspection'
(
  parted() { return 1; }
  assert_failure partition_plan
)
pass 'failed partition inspection cannot produce a creation plan'
(
  parted() { printf '%s\n' "$partition_fixture"; return 1; }
  assert_failure partition_plan
)
pass 'a failed parted invocation is rejected even if it emitted table data'

(
  cat > "$fixture/mountinfo" <<'EOF'
28 22 179:34 / /rom ro,relatime - squashfs /dev/root ro
33 22 0:27 / / rw,relatime - overlay overlay rw,lowerdir=/,upperdir=/overlay/upper
44 33 179:35 / /opt rw,relatime - ext4 /dev/mmcblk1p3 rw
EOF
  awk() {
    if [ "$#" -eq 4 ] && [ "$4" = /proc/self/mountinfo ]; then
      command awk "$1" "$2" "$3" "$fixture/mountinfo"
    else command awk "$@"; fi
  }
  readlink() {
    case "$2" in
      /sys/dev/block/179:34) printf '/sys/devices/platform/mmcblk1/mmcblk1p2\n' ;;
      /sys/dev/block/179:35) printf '/sys/devices/platform/mmcblk1/mmcblk1p3\n' ;;
      *) return 1 ;;
    esac
  }
  assert_equal "$(mounted_partition /rom)" '/dev/mmcblk1p2 squashfs /'
  assert_equal "$(mounted_partition /opt)" '/dev/mmcblk1p3 ext4 /'
  assert_failure mounted_partition /missing
)
pass 'mountinfo resolves /dev/root and /opt through real major/minor devices'

partition_disk() {
  case "$1" in /dev/mmcblk1p[123]) printf '%s\n' "$DISK" ;; *) return 1 ;; esac
}
mounted_partition() {
  case "$1" in
    /rom) printf '/dev/mmcblk1p2 squashfs /\n' ;;
    /) printf '/dev/unknown overlay /\n' ;;
    /opt) [ -n "${opt_mount:-}" ] && printf '%s\n' "$opt_mount" ;;
    *) return 1 ;;
  esac
}
assert_equal "$(find_boot_partition)" /dev/mmcblk1p2
pass 'squashfs /rom identifies the boot partition under an overlay root'

blkid() {
  case "$*" in
    '-s LABEL -o value /dev/mmcblk1p3') printf '%s\n' "${test_label:-optdata}" ;;
    *) return 1 ;;
  esac
}
assert_equal "$(find_labeled_partition)" /dev/mmcblk1p3
test_label=opt
assert_equal "$(find_labeled_partition)" /dev/mmcblk1p3
pass 'both optdata and legacy opt labels are reused'
(
  blkid() { printf 'optdata\n'; }
  assert_failure find_labeled_partition
)
pass 'duplicate or root-partition data labels are rejected'

opt_mount='/dev/mmcblk1p3 ext4 /'
verify_opt_mount
pass 'the expected full ext4 partition is accepted at /opt'
(
  mounted_partition() {
    case "$1" in /opt|/overlay) printf '/dev/mmcblk1p3 ext4 /\n' ;; *) return 1 ;; esac
  }
  assert_failure verify_opt_mount
)
pass 'a partition serving as the root overlay cannot also be selected as data'
for opt_mount in '/dev/mmcblk1p2 ext4 /' '/dev/mmcblk1p3 xfs /' '/dev/mmcblk1p3 ext4 /subdir' '/dev/unknown overlay /' ''; do
  assert_failure verify_opt_mount
  assert_failure configure_storage
  assert_failure configure_swap
done
[ ! -s "$mutations" ]
pass 'root, wrong filesystem, bind mounts and missing /opt perform no storage writes'
(
  : > "$mutations"
  # A Bash builtin shim is confined to this subshell; the boot disk is synthetic.
  function [ {
    if [[ "$*" == '-b /dev/mmcblk1 ]' ]]; then return 0; fi
    builtin [ "$@"
  }
  opt_mount='/dev/mmcblk1p3 ext4 /'
  configure_storage() { verify_opt_mount && record_mutation configured-storage; }
  configure_swap() { verify_opt_mount && record_mutation configured-swap; }
  parted() { record_mutation unexpected-partition-write; return 1; }
  main >/dev/null
  main >/dev/null
  assert_equal "$(cat "$mutations")" $'configured-storage\nconfigured-swap\nconfigured-storage\nconfigured-swap'
)
pass 'repeated main runs reuse an existing /opt without creating or formatting partitions'
(
  : > "$mutations"
  opt_mount='/dev/mmcblk1p3 ext4 /'
  find_boot_partition() { return 1; }
  configure_storage() { verify_opt_mount && record_mutation configured-storage; }
  configure_swap() { verify_opt_mount && record_mutation configured-swap; }
  parted() { record_mutation unexpected-partition-write; return 1; }
  main >/dev/null
  assert_equal "$(cat "$mutations")" $'configured-storage\nconfigured-swap'
)
pass 'an existing ext4 /opt is reused even when the boot partition cannot be identified'
(
  : > "$mutations"
  parted() { record_mutation "parted $*"; return 1; }
  partition_plan() { printf '3 2164736 124735487 512\n'; }
  assert_failure create_data_partition
  ! grep -q '^mkfs ' "$mutations"
)
pass 'failed partition creation cannot reach mkfs'
(
  : > "$mutations"
  parted() { record_mutation "parted $*"; }
  partition_plan() { printf '3 2164736 124735487 512\n'; }
  assert_failure create_data_partition
  ! grep -q '^mkfs ' "$mutations"
)
pass 'a missing new block device cannot reach mkfs'

printf 'All storage regression checks passed.\n'
