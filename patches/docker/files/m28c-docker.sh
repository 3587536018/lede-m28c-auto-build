#!/bin/sh
# Helpers for the pinned Docker 29 native nftables integration.

m28c_docker_ipv4() {
	printf '%s\n' "$1" | awk -F. '
		NF != 4 { exit 1 }
		{ for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || $i > 255) exit 1 }
		$1 == 0 || $1 == 127 || $1 >= 224 { exit 1 }
	'
}

m28c_docker_default_dns() {
	local bip="$1" address prefix
	# Never guess another network when the user explicitly supplied bip.
	if [ -n "$bip" ]; then
		address=${bip%/*}
		prefix=${bip#*/}
		case "$prefix" in ''|*[!0-9]*) return 0 ;; esac
		[ "$address" != "$bip" ] && [ "$prefix" -le 32 ] || return 0
		m28c_docker_ipv4 "$address" && printf '%s\n' "$address"
		return 0
	fi
	address=$(ip -4 -o addr show dev docker0 scope global 2>/dev/null | awk 'NR == 1 { split($4, a, "/"); print a[1] }')
	if [ -n "$address" ] && m28c_docker_ipv4 "$address"; then
		printf '%s\n' "$address"
		return 0
	fi
	# On first boot docker0 does not exist yet. Use the current router LAN
	# address instead of guessing Docker's auto-selected bridge subnet.
	. /lib/functions/network.sh
	address=""
	network_get_ipaddr address lan
	if [ -n "$address" ] && m28c_docker_ipv4 "$address"; then
		printf '%s\n' "$address"
	else
		logger -t dockerd-init "No router DNS address found; configure dockerd.globals.dns explicitly"
	fi
	return 0
}

m28c_docker_check_blocked_interface() {
	[ "$1" = wan ] || m28c_docker_policy_valid=0
}

m28c_docker_check_firewall_section() {
	local device extra blocked
	config_get device "$1" device
	config_get extra "$1" extra_iptables_args
	config_get blocked "$1" blocked_interfaces
	[ -z "$blocked" ] && [ -z "$extra" ] && return 0
	[ "$device" = docker0 ] && [ -z "$extra" ] || m28c_docker_policy_valid=0
	config_list_foreach "$1" blocked_interfaces m28c_docker_check_blocked_interface
}

m28c_docker_validate_policy() {
	local m28c_docker_policy_valid=1
	config_foreach m28c_docker_check_firewall_section firewall
	if [ "$m28c_docker_policy_valid" != 1 ]; then
		logger -t dockerd-init "Native nftables cannot translate custom legacy blocking options; configure the equivalent fw4 policy and remove those legacy options first"
		return 1
	fi
	return 0
}


# Resolve existing ancestors without creating Docker directories. This also
# catches an alternate data-root symlink pointing into /opt.
m28c_docker_data_path() {
	local probe="$1" suffix="" resolved
	case "$probe" in /*) ;; *) return 1 ;; esac
	while [ "$probe" != / ] && [ ! -e "$probe" ] && [ ! -L "$probe" ]; do
		suffix="/${probe##*/}$suffix"
		probe=${probe%/*}
		[ -n "$probe" ] || probe=/
	done
	resolved=$(readlink -f "$probe") || return 1
	printf '%s\n' "$resolved$suffix" | awk -F/ '
		{ n=0; for(i=1;i<=NF;i++) {
			if ($i == "" || $i == ".") continue
			if ($i == "..") { if(n>0) n--; continue }
			a[++n]=$i
		} }
		END { if(!n) print "/"; else { for(i=1;i<=n;i++) printf "/%s",a[i]; print "" } }
	'
}

m28c_docker_require_storage() {
	local requested="$1" resolved device partition number
	resolved=$(m28c_docker_data_path "$requested") || {
		logger -t dockerd-init "Cannot resolve Docker data-root; refusing startup"
		return 1
	}
	case "$requested:$resolved" in
		/opt:*|/opt/*:*|*:/opt|*:/opt/*) ;;
		*) return 0 ;;
	esac
	if [ -e /etc/uci-defaults/99-expand-data ]; then
		logger -t dockerd-init "Storage initialization is pending; refusing Docker data-root under /opt"
		return 1
	fi
	case "$resolved" in /opt|/opt/*) ;; *)
		logger -t dockerd-init "/opt data-root resolves outside the data partition; refusing startup"
		return 1 ;;
	esac
	# mountinfo identifies the mounted device even when its source uses an alias.
	# /opt must expose the whole filesystem. Docker makes data-root shared by
	# binding it onto itself; allow only the same writable filesystem and exact
	# relative root for those binds. Container mounts below data-root are allowed.
	device=$(M28C_DOCKER_DATA_ROOT="$resolved" awk '
		BEGIN { data_root=ENVIRON["M28C_DOCKER_DATA_ROOT"] }
		function mount_path(value) {
			gsub(/\\040/, " ", value)
			gsub(/\\011/, "\t", value)
			gsub(/\\012/, "\n", value)
			gsub(/\\134/, "\\", value)
			return value
		}
		{
			sep=0
			for(i=7;i<=NF;i++) if($i=="-") { if(sep) bad=1; sep=i }
			if($1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+:[0-9]+$/ ||
			   $4 !~ /^\// || $5 !~ /^\// || sep<7 || NF!=sep+3) { bad=1; next }
			if(seen[$1]++) bad=1
			mp=mount_path($5)
			if(mp=="/" || mp=="/rom" || mp=="/overlay") protected_dev[$3]=1
			if(mp=="/") roots++
			if(mp=="/opt") {
				count++; dev=$3
				if($4!="/" || $(sep+1)!="ext4" || $6 !~ /(^|,)rw(,|$)/ || $(sep+3) !~ /(^|,)rw(,|$)/) bad=1
			}
			if(index(mp,"/opt/")==1 && (data_root==mp || index(data_root,mp "/")==1)) {
				binds++; bind_dev[binds]=$3; bind_root[binds]=mount_path($4); bind_path[binds]=mp
				if($(sep+1)!="ext4" || $6 !~ /(^|,)rw(,|$)/ || $(sep+3) !~ /(^|,)rw(,|$)/) bad=1
			}
		}
		END {
			if(bad || roots!=1 || count!=1 || protected_dev[dev]) exit 1
			for(i=1;i<=binds;i++) if(bind_dev[i]!=dev || bind_root[i]!=substr(bind_path[i],5)) exit 1
			print dev
		}
	' /proc/self/mountinfo) || {
		logger -t dockerd-init "Docker requires a unique independent whole ext4 partition mounted at /opt"
		return 1
	}
	partition=/sys/dev/block/$device
	if [ ! -f "$partition/partition" ] || [ "$(cat "$partition/dev" 2>/dev/null)" != "$device" ]; then
		logger -t dockerd-init "/opt is not a block partition; refusing Docker startup"
		return 1
	fi
	number=$(cat "$partition/partition")
	case "$number" in ''|*[!0-9]*) return 1 ;; esac
	[ "$number" -gt 0 ] || return 1
	return 0
}

m28c_docker_require_json_storage() {
	local data_root=/var/lib/docker kind
	. /usr/share/libubox/jshn.sh
	json_init
	json_load "$(cat "$1")" || {
		logger -t dockerd-init "Cannot parse alternative Docker JSON; refusing startup"
		return 1
	}
	json_get_type kind data-root
	if [ -n "$kind" ]; then
		[ "$kind" = string ] && json_get_var data_root data-root && [ -n "$data_root" ] || {
			logger -t dockerd-init "Docker JSON data-root must be a nonempty string"
			return 1
		}
	fi
	m28c_docker_require_storage "$data_root"
}
