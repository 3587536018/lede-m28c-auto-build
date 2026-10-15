/* M28C_USB_BINDING_V1: included after the scanner's UCI helpers. */
static int m28c_usb_location(const char *path, char *location, size_t location_len)
{
	char real[4096], protocol[32] = "", *root_end;
	if (!realpath(path, real) || normalize_usb_location(real, location, location_len)) return -1;
	/* Linux exposes the root hub's bcdUSB descriptor as "version", e.g.
	 * " 3.00". USB2/USB3 roots on one controller must stay distinct. */
	root_end = strstr(real, "/usb");
	while (root_end && !isdigit((unsigned char)root_end[4])) root_end = strstr(root_end + 4, "/usb");
	if (!root_end || !(root_end = strchr(root_end + 4, '/'))) return -1;
	*root_end = '\0';
	if (strlen(real) + sizeof("/version") > sizeof(real)) return -1;
	strcat(real, "/version");
	if (read_file_trim(real, protocol, sizeof(protocol))) return -1;
	{
		char *dot = strchr(protocol, '.');
		if (!dot || dot == protocol || strlen(dot + 1) != 2 || !m28c_digits(dot + 1)) return -1;
		for (char *p = protocol; p < dot; p++) if (!isdigit((unsigned char)*p)) return -1;
	}
	if (strlen(location) + strlen(protocol) + sizeof("/usb-version:") > location_len) return -1;
	strcat(location, "/usb-version:"); strcat(location, protocol);
	return 0;
}

static int m28c_load_bindings(struct m28c_usb_binding *items, size_t *count)
{
	char *out = calloc(1, 65536), *line, *save = NULL;
	char *argv[] = { "uci", "-X", "-q", "show", "qmodem", NULL };
	int rc = -1;
	*count = 0;
	if (!out) return -1;
	if (capture_exec(argv, out, 65536, 3) || strlen(out) >= 65535) goto done;
	for (line = strtok_r(out, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
		char *eq, key[256], fixed[16], state[32], live_location[M28C_USB_LOCATION_LEN];
		struct m28c_usb_binding *b;
		if (strncmp(line, "qmodem.", 7) || !(eq = strchr(line + 7, '='))) continue;
		if (strcmp(eq + 1, "modem-device") && strcmp(eq + 1, "'modem-device'")) continue;
		*eq = '\0';
		if (*count >= M28C_MAX_BINDINGS || strlen(line + 7) >= sizeof(items[0].section)) goto done;
		for (char *p = line + 7; *p; p++) if (!isalnum((unsigned char)*p) && *p != '_') goto done;
		b = &items[(*count)++];
		snprintf(b->section, sizeof(b->section), "%s", line + 7);
#define M28C_GET(field, option) do { \
		snprintf(key, sizeof(key), "qmodem.%s.%s", b->section, option); \
		uci_get(key, b->field, sizeof(b->field)); \
	} while (0)
		M28C_GET(path, "path");
		M28C_GET(location, "usb_location");
		M28C_GET(vid, "usb_vendor");
		M28C_GET(pid, "usb_product");
		M28C_GET(name, "name");
		b->present = is_dir(b->path);
		/* After a bus-number swap the old path may name the other controller.
		 * It counts as still present only while its physical location agrees. */
		if (b->location[0] && b->present)
			b->present = !m28c_usb_location(b->path, live_location, sizeof(live_location)) &&
				!strcmp(live_location, b->location);
#undef M28C_GET
		snprintf(key, sizeof(key), "qmodem.%s.fixed_device", b->section);
		uci_get(key, fixed, sizeof(fixed));
		b->fixed = !strcmp(fixed, "1");
		snprintf(key, sizeof(key), "qmodem.%s.state", b->section);
		uci_get(key, state, sizeof(state));
		b->disabled = !strcmp(state, "disabled");
	}
	rc = 0;
done:
	free(out);
	return rc;
}

/* Manual devices remain literal path bindings. Their historical section name
 * is not a reason to suppress discovery at an unrelated current slot. */
static int m28c_fixed_usb_slot(const char *slot, char *section, size_t len)
{
	struct m28c_usb_binding *items = calloc(M28C_MAX_BINDINGS, sizeof(*items));
	char path[256];
	size_t count;
	int found = 0;
	if (!items) return -1;
	if (m28c_load_bindings(items, &count)) { free(items); return -1; }
	snprintf(path, sizeof(path), "/sys/bus/usb/devices/%s/", slot);
	for (size_t i = 0; i < count; i++) {
		if (items[i].fixed && m28c_same_path(items[i].path, path)) {
			if (found || snprintf(section, len, "%s", items[i].section) >= (int)len) { free(items); return -1; }
			found = 1;
		}
	}
	free(items);
	return found;
}

/* Caller holds uci_lock across selection and the following UCI updates. */
static int m28c_select_usb_binding(const char *slot, const struct scan_result *res,
	const struct modem_profile *profile, char *section, size_t len, char *location, size_t location_len)
{
	struct m28c_usb_binding *items = calloc(M28C_MAX_BINDINGS, sizeof(*items));
	char slot_section[128], key[512], current_vid[16] = "", current_pid[16] = "", type[64];
	char devnum[32] = "";
	size_t count;
	int rc = -1;
	if (!items) return -1;
	if (m28c_usb_location(res->modem_path, location, location_len)) goto done;
	snprintf(key, sizeof(key), "%s/idVendor", res->modem_path);
	if (read_file_trim(key, current_vid, sizeof(current_vid))) goto done;
	snprintf(key, sizeof(key), "%s/idProduct", res->modem_path);
	if (read_file_trim(key, current_pid, sizeof(current_pid))) goto done;
	if (strcmp(current_vid, res->vid) || strcmp(current_pid, res->pid)) goto done;
	snprintf(key, sizeof(key), "%s/devnum", res->modem_path);
	if (read_file_trim(key, devnum, sizeof(devnum)) || !m28c_digits(devnum) || strcmp(devnum, res->usb_devnum)) goto done;
	if (m28c_load_bindings(items, &count)) goto done;
	section_from_slot(slot, slot_section, sizeof(slot_section));
	rc = m28c_choose_usb_section(items, count, slot_section, res->modem_path, location,
		res->vid, res->pid, profile->name, section, len);
	if (rc >= 0) {
		snprintf(key, sizeof(key), "qmodem.%s", section);
		if (!uci_get(key, type, sizeof(type)) && strcmp(type, "modem-device")) rc = -1;
	}
	if (rc >= 0) {
		/* Preserve manual bindings and never let two active logical devices
		 * control the same current physical path after replacing a module. */
		for (size_t i = 0; i < count; i++) {
			if (strcmp(items[i].section, section) && items[i].fixed &&
			    m28c_same_path(items[i].path, res->modem_path)) { rc = -1; goto done; }
		}
		for (size_t i = 0; i < count; i++) {
			if (strcmp(items[i].section, section) && !items[i].disabled &&
			    m28c_same_path(items[i].path, res->modem_path)) {
				snprintf(key, sizeof(key), "qmodem.%s.state", items[i].section);
				uci_set(key, "disabled");
			}
		}
	}
done:
	free(items);
	return rc;
}
