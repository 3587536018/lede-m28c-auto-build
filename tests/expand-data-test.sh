#!/usr/bin/env bash
# Fixture-only storage tests: no real device, mount, UCI or swap is modified.
set -eu
# Git Bash needs MSYS=winsymlinks:sys before startup for real symlink fixtures.
repo=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d "${TMPDIR:-/tmp}/m28c-storage-test.XXXXXX")
trap 'case "$fixture" in "${TMPDIR:-/tmp}"/m28c-storage-test.*) rm -rf "$fixture" ;; esac' EXIT
checks=0
assert_equal() { [ "$1" = "$2" ] || { printf 'FAIL expected <%s>, received <%s>\n' "$2" "$1" >&2; exit 1; }; }
assert_failure() { if "$@"; then printf 'FAIL expected failure: %s\n' "$*" >&2; exit 1; fi; }
pass() { checks=$((checks + 1)); printf 'PASS: %s\n' "$1"; }
record() { printf '%s\n' "$*" >> "$case_dir/mutations"; }
count() { awk -v name="$1" '$1 == name { n++ } END { print n+0 }' "$case_dir/mutations"; }
no_format() { assert_equal "$(count mkfs)" 0; }
no_swap() {
  if grep -Eq '^(mkswap|swapon|swapoff|dd|fallocate) ' "$case_dir/mutations"; then
    printf 'FAIL unexpected swap/allocation operation\n' >&2; cat "$case_dir/mutations" >&2; exit 1
  fi
  [ ! -e "$opt_path/swapfile" ]
}
table_without_data='BYT;
/dev/mmcblk1:124735488s:sd/mmc:512:512:msdos:SD:;
1:2048s:67583s:65536s:fat16::boot;
2:67584s:2164735s:2097152s:::;
1:2164736s:124735487s:122570752s:free;'
table_with_data='BYT;
/dev/mmcblk1:124735488s:sd/mmc:512:512:msdos:SD:;
1:2048s:67583s:65536s:fat16::boot;
2:67584s:2164735s:2097152s:::;
3:2164736s:124735487s:122570752s:ext4::;'
old_uuid=11111111-1111-4111-8111-111111111111
new_uuid=22222222-2222-4222-8222-222222222222

# File-backed state survives command substitutions and failed initialization.
make_partition() {
  command mkdir -p "$case_dir/sys/$1"
  printf '%s\n' "$2" > "$case_dir/sys/$1/partition"
  printf '%s\n' "$3" > "$case_dir/sys/$1/start"
  printf '%s\n' "$4" > "$case_dir/sys/$1/size"
  printf '%s\n' "$5" > "$case_dir/sys/$1/dev"
}
setup_case() {
  case_dir="$fixture/$1"; opt_path="$case_dir/opt"; state_path="$case_dir/initialized"
  command mkdir -p "$case_dir/sys" "$case_dir/fs" "$opt_path"
  printf '%s\n' "$table_with_data" > "$case_dir/table"
  printf '28 22 179:34 / /rom ro - squashfs /dev/root ro\n33 22 0:27 / / rw - overlay overlay rw\n' > "$case_dir/mountinfo"
  printf 'Filename Type Size Used Priority\n' > "$case_dir/swaps"
  printf 'root=/dev/mmcblk1p2\n' > "$case_dir/cmdline"
  : > "$case_dir/mutations"
  make_partition mmcblk1p1 1 2048 65536 179:33
  make_partition mmcblk1p2 2 67584 2097152 179:34
  make_partition mmcblk1p3 3 2164736 122570752 179:35
  command mkdir -p "$case_dir/sys/mmcblk1"
  printf '124735488\n' > "$case_dir/sys/mmcblk1/size"
  printf '179:32\n' > "$case_dir/sys/mmcblk1/dev"
  printf 'optdata\n' > "$case_dir/fs/mmcblk1p3.label"
  printf 'ext4\n' > "$case_dir/fs/mmcblk1p3.type"
  printf '%s\n' "$old_uuid" > "$case_dir/fs/mmcblk1p3.uuid"
  sed -e '$d' -e "s|/sys/class/block|$case_dir/sys|g" \
    -e "s|/proc/self/mountinfo|$case_dir/mountinfo|g" \
    -e "s|/proc/swaps|$case_dir/swaps|g" -e "s|/proc/cmdline|$case_dir/cmdline|g" \
    -e "s|/etc/m28c-opt-initialized|$state_path|g" -e "s|/var/lock|$case_dir/lock|g" \
    "$repo/files/etc/uci-defaults/99-expand-data" > "$case_dir/library.sh"
  source "$case_dir/library.sh"
  # Only parent-device resolution is mocked. Geometry, table/mountinfo parsing,
  # checkpoints, selection and main's retry flow use the production functions.
  partition_disk() {
    case "$1" in /dev/mmcblk1p[1-9]) printf '/dev/mmcblk1\n' ;; /dev/sda[1-9]) printf '/dev/sda\n' ;; *) return 1 ;; esac
  }
  canonical_device() { printf '%s\n' "$1"; }
  DISK=/dev/mmcblk1; BOOT_PARTITION=/dev/mmcblk1p2; PART=/dev/mmcblk1p3
}
function [ {
  if [[ "$#" == 3 && ( "$1" == -b || "$1" == -e ) && "$2" == /dev/* ]]; then
    builtin [ -d "$case_dir/sys/${2##*/}" ]
  elif [[ "$#" == 4 && "$1" == '!' && ( "$2" == -b || "$2" == -e ) && "$3" == /dev/* ]]; then
    builtin [ ! -d "$case_dir/sys/${3##*/}" ]
  elif [[ "$#" == 3 && "$1" == -* && ( "$2" == /opt || "$2" == /opt/* ) ]]; then
    builtin [ "$1" "$opt_path${2#/opt}" ]
  elif [[ "$#" == 4 && "$1" == '!' && "$2" == -* && ( "$3" == /opt || "$3" == /opt/* ) ]]; then
    builtin [ '!' "$2" "$opt_path${3#/opt}" ]
  else builtin [ "$@"; fi
}
readlink() {
  if [ "${1:-}" = -f ]; then
    case "${2:-}" in
      /sys/dev/block/179:33) printf '/sys/devices/mmcblk1/mmcblk1p1\n'; return ;;
      /sys/dev/block/179:34) printf '/sys/devices/mmcblk1/mmcblk1p2\n'; return ;;
      /sys/dev/block/179:35) printf '/sys/devices/mmcblk1/mmcblk1p3\n'; return ;;
      /sys/dev/block/8:1) printf '/sys/devices/sda/sda1\n'; return ;;
      /sys/dev/block/*) return 1 ;;
      /dev/*) printf '%s\n' "$2"; return ;;
    esac
  fi
  command readlink "$@"
}
parted() {
  case " $* " in
    *' print free '*|*' print '*) cat "$case_dir/table" ;;
    *' mkpart '*)
      record "parted $*"
      [ "${fail_parted:-0}" != 1 ] || return 1
      [ "${missing_new_partition:-0}" != 1 ] || return 0
      make_partition mmcblk1p3 3 "${created_start:-2164736}" 122570752 179:35
      printf '%s\n' "$table_with_data" > "$case_dir/table"
      ;;
    *) printf 'FAIL unmocked parted invocation: %s\n' "$*" >&2; return 1 ;;
  esac
}
blkid() {
  local field= arg device= previous=
  for arg in "$@"; do
    [ "$previous" != -s ] || field=$arg
    case "$arg" in /dev/*) device=${arg##*/} ;; esac
    previous=$arg
  done
  case "$field" in
    TYPE) cat "$case_dir/fs/$device.type" 2>/dev/null ;;
    LABEL) cat "$case_dir/fs/$device.label" 2>/dev/null ;;
    UUID) cat "$case_dir/fs/$device.uuid" 2>/dev/null ;;
    *) return 1 ;;
  esac
}
mkfs.ext4() {
  local arg device=
  record "mkfs $*"
  [ "${fail_mkfs:-0}" != 1 ] || return 1
  for arg in "$@"; do case "$arg" in /dev/*) device=${arg##*/} ;; esac; done
  [ "$device" = mmcblk1p3 ] || { printf 'FAIL unsafe format target %s\n' "$device" >&2; return 1; }
  printf 'ext4\n' > "$case_dir/fs/$device.type"
  printf 'optdata\n' > "$case_dir/fs/$device.label"
  printf '%s\n' "$new_uuid" > "$case_dir/fs/$device.uuid"
}
mount() {
  record "mount $*"
  [ "${fail_mount:-0}" != 1 ] || return 1
  [ "$*" = '-t ext4 /dev/mmcblk1p3 /opt' ] || return 1
  printf '44 33 179:35 / /opt rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo"
}
umount() {
  record "umount $*"
  [ "$#" = 1 ] && [ "$1" = /opt ] || return 1
  [ "${busy_opt:-0}" != 1 ] || return 1
  awk '$5 != "/opt"' "$case_dir/mountinfo" > "$case_dir/mountinfo.next"
  mv "$case_dir/mountinfo.next" "$case_dir/mountinfo"
}
mkdir() {
  case "$*" in '-p /opt') command mkdir -p "$opt_path" ;; '-p /opt/docker') command mkdir -p "$opt_path/docker" ;; *) command mkdir "$@" ;; esac
}
uci() {
  case "$*" in
    '-q get dockerd.globals') printf 'globals\n' ;;
    '-q get fstab.swap.device') [ -n "${swap_device:-}" ] && printf '%s\n' "$swap_device" ;;
    '-q get '*) return 1 ;;
    '-q show fstab') printf 'fstab.@global[0]=global\n' ;;
    *) record "uci $*"; [ "${fail_fstab:-0}:$*" != '1:-q commit fstab' ] ;;
  esac
}
mkswap() { record "mkswap $*"; return 1; }
swapon() { record "swapon $*"; return 1; }
swapoff() { record "swapoff $*"; return 1; }
dd() { record "dd $*"; return 1; }
fallocate() { record "fallocate $*"; return 1; }
partprobe() { :; }
blockdev() { :; }
sleep() { :; }
sync() { :; }
logger() { :; }
pidof() { return 1; }

(
  setup_case partition-plan
  printf '%s\n' "$table_without_data" > "$case_dir/table"
  assert_equal "$(partition_plan)" '3 2164736 124735487 512'
)
pass 'new partition starts after the real root partition'
(
  setup_case larger-root
  table=${table_without_data//2164735/4261887}; table=${table//2164736/4261888}
  printf '%s\n' "$table" > "$case_dir/table"
  assert_equal "$(partition_plan)" '3 4261888 124735487 512'
)
pass 'larger root partition changes the data start without a fixed offset'
(
  setup_case alignment
  printf '%s\n' "${table_without_data//2164736/2164737}" > "$case_dir/table"
  assert_equal "$(partition_plan)" '3 2166784 124735487 512'
)
pass 'new data start rounds up to a MiB boundary'
(
  setup_case logical-sectors
  cat > "$case_dir/table" <<'EOF'
BYT;
/dev/mmcblk1:15591936s:sd/mmc:4096:4096:gpt:SD:;
1:256s:8447s:8192s:fat16:boot:;
2:8448s:270591s:262144s::root:;
1:270592s:15591902s:15321311s:free;
EOF
  assert_equal "$(partition_plan)" '3 270592 15591902 4096'
)
pass 'GPT and 4096-byte logical sectors use correct units'
for scenario in unknown-table consumed insufficient gap failed-inspection partial-inspection; do
  (
    setup_case "$scenario"
    table=$table_without_data
    case "$scenario" in
      unknown-table) table=${table//msdos/unknown} ;;
      consumed) table=$table_with_data ;;
      insufficient) table=${table//124735487/2300000} ;;
      gap) table=${table//$'2:67584s'/$'3:67584s'} ;;
      failed-inspection) parted() { return 1; } ;;
      partial-inspection) parted() { cat "$case_dir/table"; return 1; } ;;
    esac
    printf '%s\n' "$table" > "$case_dir/table"
    assert_failure partition_plan
    no_format
  )
  pass "partition plan rejects $scenario"
done
(
  setup_case mountinfo
  printf '44 33 179:35 / /opt rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo"
  assert_equal "$(mounted_partition /rom)" '/dev/mmcblk1p2 squashfs /'
  assert_equal "$(mounted_partition /opt)" '/dev/mmcblk1p3 ext4 /'
  assert_failure mounted_partition /missing
  assert_equal "$(find_boot_partition)" /dev/mmcblk1p2
)
pass 'mountinfo resolves /dev/root and /opt through real major/minor devices'
(
  setup_case existing-first-boot
  main >/dev/null
  assert_equal "$(count mkfs)" 1
  assert_equal "$(count mount)" 1
  [ -s "$state_path" ]
  no_swap
  main >/dev/null
  assert_equal "$(count mkfs)" 1
  assert_equal "$(count mount)" 1
  no_swap
  # Simulate the next ordinary boot before fstab has mounted /opt.
  awk '$5 != "/opt"' "$case_dir/mountinfo" > "$case_dir/next-mountinfo"
  mv "$case_dir/next-mountinfo" "$case_dir/mountinfo"
  main >/dev/null
  assert_equal "$(count mkfs)" 1
  assert_equal "$(count mount)" 2
  no_swap
)
pass 'eligible existing data is cleared once; reruns and ordinary reboots never format again'
(
  setup_case recreated-with-signature
  command rm -rf "$case_dir/sys/mmcblk1p3"
  printf '%s\n' "$table_without_data" > "$case_dir/table"
  printf 'exfat\n' > "$case_dir/fs/mmcblk1p3.type"
  main >/dev/null
  assert_equal "$(count parted)" 1
  assert_equal "$(count mkfs)" 1
  assert_equal "$(count mount)" 1
  assert_equal "$(cat "$case_dir/fs/mmcblk1p3.type")" ext4
  no_swap
)
pass 'new trailing partition with old filesystem signature is initialized and mounted'
(
  setup_case existing-exfat
  rm "$case_dir/fs/mmcblk1p3.label"
  printf 'exfat\n' > "$case_dir/fs/mmcblk1p3.type"
  main >/dev/null
  assert_equal "$(count parted)" 0
  assert_equal "$(count mkfs)" 1
  assert_equal "$(count mount)" 1
  no_swap
)
pass 'existing unlabeled exFAT p3 in the exact data geometry is cleared and mounted'
for failure in mount fstab; do
  (
    setup_case "retry-$failure"
    case "$failure" in mount) fail_mount=1 ;; fstab) fail_fstab=1 ;; esac
    assert_failure main
    assert_equal "$(count mkfs)" 1
    [ -s "$state_path" ]
    fail_mount=0; fail_fstab=0
    main >/dev/null
    assert_equal "$(count mkfs)" 1
    no_swap
  )
  pass "$failure failure retries from persistent checkpoint without another format"
done
(
  setup_case interrupted-format
  fail_mkfs=1
  assert_failure main
  assert_equal "$(count mkfs)" 1
  [ -s "$state_path" ]
  fail_mkfs=0
  assert_failure main
  assert_equal "$(count mkfs)" 1
  no_swap
)
pass 'pending format checkpoint refuses a blind destructive retry'
(
  setup_case checkpoint-mismatch
  fail_mount=1
  assert_failure main
  printf '33333333-3333-4333-8333-333333333333\n' > "$case_dir/fs/mmcblk1p3.uuid"
  fail_mount=0
  assert_failure main
  assert_equal "$(count mkfs)" 1
  assert_equal "$(count mount)" 1
)
pass 'checkpoint UUID mismatch stops without formatting or mounting another filesystem'
for scenario in checkpoint-write prepared-write sync-before-format malformed-state geometry-state; do
  (
    setup_case "$scenario"
    case "$scenario" in
      checkpoint-write)
        mv() { [ "${*: -1}" != "$state_path" ] || return 1; command mv "$@"; }
        ;;
      prepared-write)
        mv() {
          if [ "${*: -1}" = "$state_path" ] && grep -q '|prepared|' "$2"; then return 1; fi
          command mv "$@"
        }
        ;;
      sync-before-format) sync() { return 1; } ;;
      malformed-state) printf 'not a checkpoint\n' > "$state_path" ;;
      geometry-state) printf 'm28c-opt-v1|prepared|/dev/mmcblk1p2|/dev/mmcblk1p3|2164736|1|%s\n' "$new_uuid" > "$state_path" ;;
    esac
    assert_failure main
    if [ "$scenario" = prepared-write ]; then
      assert_equal "$(count mkfs)" 1
    else no_format; fi
    assert_equal "$(count mount)" 0
    if [ "$scenario" = prepared-write ] || [ "$scenario" = sync-before-format ]; then
      # Restore working persistence; the retained pending checkpoint still
      # requires inspection rather than silently performing another mkfs.
      unset -f mv
      sync() { :; }
      assert_failure main
      if [ "$scenario" = prepared-write ]; then assert_equal "$(count mkfs)" 1; else no_format; fi
    fi
  )
  pass "$scenario fails closed and cannot repeat a destructive format"
done
(
  setup_case mounted-first-boot
  printf '44 33 179:35 / /opt rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo"
  main >/dev/null
  assert_equal "$(count umount)" 1
  assert_equal "$(count mkfs)" 1
  assert_equal "$(grep '^umount ' "$case_dir/mutations")" 'umount /opt'
  no_swap
)
pass 'first initialization normally unmounts eligible /opt before clearing it'
(
  setup_case mounted-busy
  printf '44 33 179:35 / /opt rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo"
  busy_opt=1
  assert_failure main
  no_format
  assert_equal "$(grep '^umount ' "$case_dir/mutations")" 'umount /opt'
)
pass 'busy /opt is never formatted or force/lazy unmounted'
for scenario in boot-label root-label duplicate-label external-opt overlay-mounted other-mount child-mount foreign-child active-swap active-swapfile holder loop-backed running-docker; do
  (
    setup_case "unsafe-$scenario"
    case "$scenario" in
      boot-label) rm "$case_dir/fs/mmcblk1p3.label"; printf 'optdata\n' > "$case_dir/fs/mmcblk1p1.label" ;;
      root-label) rm "$case_dir/fs/mmcblk1p3.label"; printf 'optdata\n' > "$case_dir/fs/mmcblk1p2.label" ;;
      duplicate-label) printf 'opt\n' > "$case_dir/fs/mmcblk1p1.label" ;;
      external-opt)
        make_partition sda1 1 2048 122570752 8:1
        printf '44 33 8:1 / /opt rw - ext4 /dev/sda1 rw\n' >> "$case_dir/mountinfo"
        ;;
      overlay-mounted) printf '44 33 179:35 / /overlay rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo" ;;
      other-mount) printf '44 33 179:35 / /mnt/data rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo" ;;
      child-mount) printf '44 33 179:35 / /opt/data rw - ext4 /dev/mmcblk1p3 rw\n' >> "$case_dir/mountinfo" ;;
      foreign-child) printf '44 33 8:1 / /opt/data rw - ext4 /dev/sda1 rw\n' >> "$case_dir/mountinfo" ;;
      active-swap) printf '/dev/mmcblk1p3 partition 8192 0 -2\n' >> "$case_dir/swaps" ;;
      active-swapfile) printf '/opt/swapfile file 8192 0 -2\n' >> "$case_dir/swaps" ;;
      holder) command mkdir -p "$case_dir/sys/mmcblk1p3/holders/dm-0" ;;
      loop-backed)
        command mkdir -p "$case_dir/sys/loop0/loop"
        printf '/dev/mmcblk1p3\n' > "$case_dir/sys/loop0/loop/backing_file"
        ;;
      running-docker) pidof() { printf '321\n'; } ;;
    esac
    assert_failure main
    no_format
    no_swap
  )
  pass "$scenario cannot select or format an unsafe data partition"
done
for scenario in docker-data hidden-file other-directory; do
  (
    setup_case "mountpoint-$scenario"
    case "$scenario" in
      docker-data) command mkdir -p "$opt_path/docker"; printf 'old data\n' > "$opt_path/docker/state" ;;
      hidden-file) printf 'old data\n' > "$opt_path/.old" ;;
      other-directory) command mkdir -p "$opt_path/containerd" ;;
    esac
    main >/dev/null
    assert_equal "$(count mkfs)" 1
    assert_equal "$(count mount)" 1
    no_swap
  )
  pass "unmounted /opt containing $scenario no longer vetoes initialization"
done
for scenario in symlink regular-file; do
  (
    setup_case "invalid-mountpoint-$scenario"
    rmdir "$opt_path"
    case "$scenario" in
      symlink)
        command mkdir -p "$case_dir/link-target"
        ln -s "$case_dir/link-target" "$opt_path"
        [ -L "$opt_path" ] || { printf 'FAIL enable MSYS=winsymlinks:sys for Git Bash\n' >&2; exit 1; }
        ;;
      regular-file) printf 'not a directory\n' > "$opt_path" ;;
    esac
    assert_failure main
    no_format
    assert_equal "$(count parted)" 0
    assert_equal "$(count mount)" 0
  )
  pass "/opt $scenario is rejected before any storage mutation"
done
for scenario in creation-failed missing-block geometry-mismatch existing-table-missing-node; do
  (
    setup_case "$scenario"
    command rm -rf "$case_dir/sys/mmcblk1p3"
    printf '%s\n' "$table_without_data" > "$case_dir/table"
    case "$scenario" in
      creation-failed) fail_parted=1 ;;
      missing-block) missing_new_partition=1 ;;
      geometry-mismatch) created_start=2164737 ;;
      existing-table-missing-node) printf '%s\n' "$table_with_data" > "$case_dir/table" ;;
    esac
    assert_failure main
    no_format
    no_swap
    [ "$scenario" != existing-table-missing-node ] || assert_equal "$(count parted)" 0
  )
  pass "$scenario cannot reach filesystem creation"
done
for scenario in project-swap unrelated-swap no-swap-entry; do
  (
    setup_case "$scenario"
    case "$scenario" in project-swap) swap_device=/opt/swapfile ;; unrelated-swap) swap_device=/mnt/user-swap ;; no-swap-entry) swap_device= ;; esac
    main >/dev/null
    grep -Fxq 'uci -q set fstab.@global[0].auto_swap=0' "$case_dir/mutations"
    grep -Fxq 'uci -q set fstab.@global[0].anon_swap=0' "$case_dir/mutations"
    if [ "$scenario" = project-swap ]; then
      grep -Fxq 'uci -q delete fstab.swap' "$case_dir/mutations"
    else
      ! grep -Fxq 'uci -q delete fstab.swap' "$case_dir/mutations"
    fi
    no_swap
  )
  pass "$scenario disables automatic swap and only removes the historical project swap entry"
done
(
  setup_case already-locked
  command mkdir -p "$case_dir/lock/m28c-opt-init"
  assert_failure main
  assert_equal "$(cat "$case_dir/mutations")" ''
)
pass 'concurrent initialization lock prevents all storage mutations'
printf 'All %s storage regression checks passed.\n' "$checks"
