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
