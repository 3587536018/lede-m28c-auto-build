/* SPDX-License-Identifier: MIT
 * Stable USB port identity for the M28C firmware. No serial/IMEI is stored.
 * This header deliberately has no UCI or sysfs dependencies for native tests.
 */
#ifndef M28C_USB_IDENTITY_H
#define M28C_USB_IDENTITY_H

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#define M28C_USB_LOCATION_LEN 512
#define M28C_MAX_BINDINGS 64

struct m28c_usb_binding {
	char section[128];
	char path[256];
	char location[M28C_USB_LOCATION_LEN];
	char vid[16];
	char pid[16];
	char name[128];
	int fixed;
	int present;
	int disabled;
};

static int m28c_digits(const char *s)
{
	if (!s || !*s) return 0;
	for (; *s; s++) if (!isdigit((unsigned char)*s)) return 0;
	return 1;
}

static int m28c_port_chain(const char *s)
{
	int digit = 0;
	if (!s || !*s) return 0;
	for (; *s; s++) {
		if (isdigit((unsigned char)*s)) digit = 1;
		else if (*s == '.' && digit) digit = 0;
		else return 0;
	}
	return digit;
}

/* Keep the actual controller and the complete downstream hub port chain.
 * Linux's usbN, N-P and xhci-hcd.N.auto numbers are enumeration artifacts.
 */
static int normalize_usb_location(const char *real, char *out, size_t len)
{
	char copy[1024], controller[768] = "", bus[32] = "", chain[128] = "";
	char *part, *next;
	int found_root = 0;
	if (!out || !len) return -1;
	out[0] = '\0';
	if (!real || strncmp(real, "/sys/devices/", 13) || strlen(real) >= sizeof(copy)) return -1;
	memcpy(copy, real, strlen(real) + 1);
	part = copy + 1;
	while (part && *part) {
		next = strchr(part, '/');
		if (next) *next++ = '\0';
		if (!found_root && !strncmp(part, "usb", 3) && m28c_digits(part + 3)) {
			if (strlen(part + 3) >= sizeof(bus) || !controller[0]) return -1;
			strcpy(bus, part + 3);
			found_root = 1;
		} else if (!found_root) {
			const char *token = part;
			char index[32];
			if (!strncmp(part, "xhci-hcd.", 9)) {
				const char *suffix = strstr(part + 9, ".auto");
				if (suffix && !suffix[5] && (size_t)(suffix - part - 9) < sizeof(index)) {
					memcpy(index, part + 9, (size_t)(suffix - part - 9));
					index[suffix - part - 9] = '\0';
					if (m28c_digits(index)) token = "xhci-hcd";
				}
			}
			if (strlen(controller) + strlen(token) + 2 > sizeof(controller)) return -1;
			strcat(controller, "/"); strcat(controller, token);
		} else {
			char *dash = strchr(part, '-');
			if (!dash) return -1;
			*dash++ = '\0';
			if (strcmp(part, bus) || !m28c_port_chain(dash) || strlen(dash) >= sizeof(chain)) return -1;
			if (chain[0] && (strncmp(dash, chain, strlen(chain)) || dash[strlen(chain)] != '.')) return -1;
			strcpy(chain, dash);
		}
		part = next;
	}
	if (!found_root || !chain[0]) return -1;
	if (snprintf(out, len, "%s/port:%s", controller, chain) >= (int)len) {
		out[0] = '\0'; return -1;
	}
	return 0;
}

static int m28c_same_path(const char *a, const char *b)
{
	size_t na = strlen(a), nb = strlen(b);
	while (na && a[na - 1] == '/') na--;
	while (nb && b[nb - 1] == '/') nb--;
	return na && na == nb && !strncmp(a, b, na);
}

/* Match the current persisted path, never the historical section name. */
static int m28c_binding_for_slot(const struct m28c_usb_binding *items, size_t count, const char *slot)
{
	int found = -1;
	for (size_t i = 0; i < count; i++) {
		char path[256];
		char *last;
		size_t n;
		if (items[i].disabled) continue;
		snprintf(path, sizeof(path), "%s", items[i].path);
		n = strlen(path);
		while (n && path[n - 1] == '/') path[--n] = '\0';
		last = strrchr(path, '/');
		if (last && !strcmp(last + 1, slot)) {
			if (found >= 0) return -2;
			found = (int)i;
		}
	}
	return found;
}

/* Return 1 for a preserved section, 0 for a fresh section, -1 on ambiguity.
 * Legacy records without identity may only be enrolled at their exact current
 * path and model. An old absent path is never guessed by model or serial.
 */
static int m28c_choose_usb_section(const struct m28c_usb_binding *items, size_t count,
	const char *slot_section, const char *path, const char *location,
	const char *vid, const char *pid, const char *name, char *out, size_t len)
{
	const struct m28c_usb_binding *match = NULL;
	size_t matches = 0;
	int candidate;
	if (!location[0] || !vid[0] || !pid[0] || !name[0]) return -1;
	for (size_t i = 0; i < count; i++) {
		const struct m28c_usb_binding *b = &items[i];
		if (b->location[0] && !strcmp(b->location, location) &&
		    !strcmp(b->vid, vid) && !strcmp(b->pid, pid) && !strcmp(b->name, name)) {
			match = b; matches++;
		}
	}
	if (matches > 1) return -1;
	if (!matches) {
		for (size_t i = 0; i < count; i++) {
			const struct m28c_usb_binding *b = &items[i];
			if (!b->location[0] && m28c_same_path(b->path, path) && !strcmp(b->name, name)) {
				match = b; matches++;
			}
		}
	}
	if (matches > 1 || (match && match->fixed)) return -1;
	if (match && match->present && !m28c_same_path(match->path, path)) return -1;
	if (match) {
		if (snprintf(out, len, "%s", match->section) >= (int)len) return -1;
		return 1;
	}
	/* A different module or unmigrated historical record can occupy the old
	 * slot-derived name. Preserve it and choose an unused new section name. */
	for (candidate = 0; candidate <= (int)count; candidate++) {
		int used = 0, n;
		n = candidate ? snprintf(out, len, "%s_m28c_%d", slot_section, candidate) : snprintf(out, len, "%s", slot_section);
		if (n >= (int)len) return -1;
		for (size_t i = 0; i < count; i++) if (!strcmp(items[i].section, out)) used = 1;
		if (!used) return 0;
	}
	return -1;
}

#endif
