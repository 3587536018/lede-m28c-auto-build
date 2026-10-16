#!/usr/bin/env bash
# Fixture-only tests. No real devices, system config, mounts or services used.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d "${TMPDIR:-/tmp}/m28c-automount-test.XXXXXX")
trap 'case "$fixture" in "${TMPDIR:-/tmp}"/m28c-automount-test.*) rm -rf "$fixture" ;; esac' EXIT
mkdir -p "$fixture/sys/sda3" "$fixture/mnt" "$fixture/state"
printf '8:3\n' > "$fixture/sys/sda3/dev"
touch "$fixture/mountinfo" "$fixture/mutations"
sed -e '$d' -e "s|/sys/class/block|$fixture/sys|g" \
  -e "s|/proc/self/mountinfo|$fixture/mountinfo|g" \
  -e "s|/var/run/m28c-automount|$fixture/state|g" \
  -e "s|/mnt|$fixture/mnt|g" \
  "$repo/scripts/automount/m28c-automount" > "$fixture/library.sh"
source "$fixture/library.sh"
eval "$(declare -f external_disk | sed '1s/external_disk/real_external_disk/')"
assert_equal() { [ "$1" = "$2" ] || { printf 'FAIL expected <%s> got <%s>\n' "$2" "$1" >&2; exit 1; }; }
assert_failure() { if "$@"; then printf 'FAIL expected failure: %s\n' "$*" >&2; exit 1; fi; }
pass() { printf 'PASS: %s\n' "$1"; }
log() { :; }
block_device() { [ "$1" = sda3 ]; }
disk_of() { case "$1" in sda*) printf 'sda\n' ;; mmcblk1*) printf 'mmcblk1\n' ;; *) return 1 ;; esac; }
mount_disk() { case "$1" in /rom) printf '%s\n' "${system_disk:-mmcblk1}" ;; /opt) printf '%s\n' "${opt_disk:-mmcblk1}" ;; *) return 1 ;; esac; }
external_disk() { [ "${external:-1}" = 1 ]; }
partition_children() { return 0; }
config_load() { return 0; }
config_foreach() {
  case "$2" in global) "$1" global ;; mount) [ "${section_present:-0}" != 1 ] || "$1" card ;; esac
  return 0
}
config_get_bool() { printf -v "$1" '%s' "${auto_mount:-1}"; }
config_get() {
  case "$3" in uuid) printf -v "$1" '%s' "${cfg_test_uuid:-}" ;; label) printf -v "$1" '%s' "${cfg_test_label:-}" ;; device) printf -v "$1" '%s' "${cfg_test_device:-}" ;; esac
}
blkid() {
  case "$*" in
    '-p -s TYPE -o value /dev/sda3') printf '%s\n' "${test_fs:-ext4}" ;;
    '-p -s UUID -o value /dev/sda3') printf '%s\n' "${test_uuid:-aabb-1234}" ;;
    '-p -s LABEL -o value /dev/sda3') printf '%s\n' "${test_label:-data}" ;;
    '-c /dev/null -t UUID='*)
      printf '/dev/sda3\n'
      [ "${duplicate_uuid:-0}" != 1 ] || printf '/dev/sdb3\n' ;;
    *) return 1 ;;
  esac
}
mount() {
  printf 'mount %s\n' "$*" >> "$fixture/mutations"
  printf '90 22 8:3 / %s rw - ext4 /dev/sda3 rw\n' "$fixture/mnt/removable/sda3" >> "$fixture/mountinfo"
}
umount() {
  printf 'umount %s\n' "$*" >> "$fixture/mutations"
  [ "${busy:-0}" != 1 ] || return 1
  : > "$fixture/mountinfo"
}
state="$fixture/state"
add_device sda3
assert_equal "$(wc -l < "$fixture/mutations" | tr -d ' ')" 1
test -f "$state/sda3"
assert_equal "$(cat "$state/sda3")" "8:3 aabb-1234 $fixture/mnt/removable/sda3"
pass 'USB card ext4 mounts under controlled path and records actual device identity'
add_device sda3
assert_equal "$(wc -l < "$fixture/mutations" | tr -d ' ')" 1
pass 'duplicate add does not mount twice'
busy=1
assert_failure remove_device sda3
owns sda3
assert_equal "$(tail -1 "$fixture/mutations")" "umount $fixture/mnt/removable/sda3"
pass 'busy removal keeps ownership and never force/lazy unmounts'
busy=0
remove_device sda3
assert_failure owns sda3
test ! -e "$fixture/mnt/removable/sda3"
pass 'successful removal clears only empty owned target'
remove_device sda3
pass 'duplicate remove is harmless'

for condition in system opt nonexternal disabled explicit_uuid explicit_device bare_device explicit_label global_off kernel squashfs swap unknown duplicate malformed_uuid reserved_label whole_disk; do
  (
    : > "$fixture/mutations"
    case "$condition" in
      system) system_disk=sda ;;
      opt) opt_disk=sda ;;
      nonexternal) external=0 ;;
      disabled|explicit_uuid) section_present=1; cfg_test_uuid=AABB-1234 ;;
      explicit_device) section_present=1; cfg_test_device=/dev/sda3 ;;
      bare_device) section_present=1; cfg_test_device=sda3 ;;
      explicit_label) section_present=1; cfg_test_label=data ;;
      global_off) auto_mount=0 ;;
      kernel) test_fs=kernel ;;
      squashfs) test_fs=squashfs ;;
      swap) test_fs=swap ;;
      unknown) test_fs=unknown ;;
      duplicate) duplicate_uuid=1 ;;
      malformed_uuid) test_uuid='bad$(touch injected)' ;;
      reserved_label) test_label=rootfs ;;
      whole_disk) block_device() { return 0; } ;;
    esac
    if [ "$condition" = whole_disk ]; then add_device sda; else add_device sda3; fi
    assert_equal "$(cat "$fixture/mutations")" ''
  )
  pass "rejects/reserves $condition without mounting"
done
(
  mount_disk() { return 1; }
  assert_failure safe_disk sda
)
pass 'unresolved boot disk fails closed'
mkdir -p "$fixture/mnt/removable/sda3"
printf 'keep\n' > "$fixture/mnt/removable/sda3/important.txt"
: > "$fixture/mutations"
add_device sda3
assert_equal "$(cat "$fixture/mutations")" ''
assert_equal "$(cat "$fixture/mnt/removable/sda3/important.txt")" keep
pass 'existing files at mount target are not hidden'
rm "$fixture/mnt/removable/sda3/important.txt"
rmdir "$fixture/mnt/removable/sda3"
mkdir "$fixture/link-destination"
if ln -s "$fixture/link-destination" "$fixture/mnt/removable/sda3" 2>/dev/null && [ -L "$fixture/mnt/removable/sda3" ]; then
  add_device sda3
  assert_equal "$(cat "$fixture/mutations")" ''
  rm "$fixture/mnt/removable/sda3"
  pass 'symlink target is not followed'
else
  printf 'SKIP: host does not support shell-visible symbolic links\n'
  rmdir "$fixture/mnt/removable/sda3" 2>/dev/null || true
fi
printf '90 22 8:4 / %s rw - ext4 /dev/sdb1 rw\n' "$fixture/mnt/removable/sda3" > "$fixture/mountinfo"
mkdir -p "$fixture/mnt/removable/sda3"
printf '8:3 aabb-1234 %s\n' "$fixture/mnt/removable/sda3" > "$state/sda3"
assert_failure remove_device sda3
assert_equal "$(cat "$fixture/mutations")" ''
pass 'changed mount ownership is not unmounted'

# Inspect the real external-device classifier with synthetic sysfs metadata.
(
  readlink() { printf '%s\n' '/sys/devices/platform/usb1/1-1/block/sda'; }
  real_external_disk sda
)
pass 'USB-connected card reader is classified as external'
mkdir -p "$fixture/sys/mmcblk0/device"
printf 'SD\n' > "$fixture/sys/mmcblk0/device/type"
printf '1\n' > "$fixture/sys/mmcblk0/removable"
real_external_disk mmcblk0
printf 'MMC\n' > "$fixture/sys/mmcblk0/device/type"
assert_failure real_external_disk mmcblk0
pass 'SD removable card is accepted while eMMC is excluded'
for name in loop0 mtdblock0 dm0 '../sda3' 'sda3;bad'; do assert_failure valid_name "$name"; done
pass 'virtual and malformed device names are rejected'

: > "$fixture/mountinfo"
rm "$state/sda3"
rmdir "$fixture/mnt/removable/sda3"
for format in ext2 ext3 ext4 f2fs btrfs vfat exfat ntfs; do
  (
    test_fs=$format
    add_device sda3
    owns sda3
    if [ "$format" = ntfs ]; then
      case "$(tail -1 "$fixture/mutations")" in 'mount -t ntfs3 '*) ;; *) exit 1 ;; esac
    fi
    remove_device sda3
  )
done
pass 'data filesystem whitelist mounts without discard and maps NTFS to ntfs3'
(
  test_label='data" UUID="different" TYPE="kernel'
  add_device sda3
  owns sda3
  assert_equal "$(cat "$state/sda3")" "8:3 aabb-1234 $fixture/mnt/removable/sda3"
  remove_device sda3
)
pass 'quoted label text cannot spoof UUID or filesystem type'

: > "$fixture/lock-trace"
(
  lock_acquire
  printf 'a-start\n' >> "$fixture/lock-trace"
  sleep .2
  printf 'a-end\n' >> "$fixture/lock-trace"
) &
first=$!
(
  lock_acquire
  printf 'b-start\n' >> "$fixture/lock-trace"
  sleep .2
  printf 'b-end\n' >> "$fixture/lock-trace"
) &
second=$!
wait "$first"
wait "$second"
assert_equal "$(sed -n '1s/-start//p' "$fixture/lock-trace")" "$(sed -n '2s/-end//p' "$fixture/lock-trace")"
assert_equal "$(sed -n '3s/-start//p' "$fixture/lock-trace")" "$(sed -n '4s/-end//p' "$fixture/lock-trace")"
test ! -d "$state/lock"
pass 'concurrent hotplug workers serialize and release the lock'
