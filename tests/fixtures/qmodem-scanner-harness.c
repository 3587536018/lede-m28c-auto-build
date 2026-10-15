/* Test doubles for UCI, sysfs and AT probing. Real patched scanner functions
 * are inserted below at test time, rather than reimplementing their logic. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdarg.h>
#include <time.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
static char *test_strtok_r(char *str, const char *delim, char **save) {
    char *start = str ? str : *save;
    char *end;
    if (!start) return NULL;
    start += strspn(start, delim);
    if (!*start) { *save = NULL; return NULL; }
    end = start + strcspn(start, delim);
    if (*end) { *end = 0; *save = end + 1; } else *save = NULL;
    return start;
}
#define strtok_r test_strtok_r
static int c99_snprintf(char *out, size_t len, const char *format, ...) {
    va_list args;
    int needed;
    va_start(args, format); needed = _vscprintf(format, args); va_end(args);
    if (len) {
        va_start(args, format); _vsnprintf(out, len, format, args); va_end(args);
        out[len - 1] = 0;
    }
    return needed;
}
#define snprintf c99_snprintf
#else
#define EXPORT
#endif

#include "m28c_usb_identity.h"
#define QMODEM_RUN_DIR "/mock/run/qmodem"
#define pthread_mutex_lock(p) ((void)(p))
#define pthread_mutex_unlock(p) ((void)(p))
#define mkdir(path, mode) ((void)(path), (void)(mode))
static int uci_lock;

/* INSERT_TYPES */
/* INSERT_LIST_FUNCTIONS */

struct kv { char key[512]; char value[4096]; };
static struct kv state[512];
static int state_len, reloads, writes;
struct mock_device {
    char slot[128], real[1024], network[128], at[128];
    char vid[16], pid[16], name[128], scan_num[32], current_num[32], protocol[32];
    int present;
};
static struct mock_device devices[8];
static int device_len;

EXPORT void test_reset(void) {
    memset(state, 0, sizeof(state)); memset(devices, 0, sizeof(devices));
    state_len = device_len = reloads = writes = 0;
}
EXPORT void test_put(const char *key, const char *value) {
    int index;
    for (index = 0; index < state_len; index++) if (!strcmp(state[index].key, key)) break;
    if (index == state_len) state_len++;
    if (state_len >= 512) abort();
    snprintf(state[index].key, sizeof(state[index].key), "%s", key);
    snprintf(state[index].value, sizeof(state[index].value), "%s", value);
}
EXPORT const char *test_get(const char *key) {
    for (int i = 0; i < state_len; i++) if (!strcmp(state[i].key, key)) return state[i].value;
    return "";
}
static int uci_get(const char *key, char *out, size_t len) {
    const char *value = test_get(key);
    snprintf(out, len, "%s", value);
    return *value ? 0 : -1;
}
static int uci_set(const char *key, const char *value) { writes++; test_put(key, value); return 0; }
static int uci_del(const char *key) { writes++; test_put(key, ""); return 0; }
static int uci_add_list(const char *key, const char *value) {
    char joined[4096]; const char *previous = test_get(key);
    snprintf(joined, sizeof(joined), "%s%s%s", previous, *previous ? " " : "", value);
    return uci_set(key, joined);
}
static void uci_commit(const char *config) { (void)config; }
static int capture_exec(char *const argv[], char *out, size_t len, int timeout) {
    size_t used = 0;
    (void)argv; (void)timeout;
    out[0] = 0;
    for (int i = 0; i < state_len; i++) {
        if (!state[i].value[0] || strncmp(state[i].key, "qmodem.", 7)) continue;
        int count = snprintf(out + used, len - used, "%s='%s'\n", state[i].key, state[i].value);
        if (count < 0 || (size_t)count >= len - used) return -1;
        used += count;
    }
    return 0;
}
static int run_exec(char *const argv[], int timeout) { (void)argv; (void)timeout; return 0; }
static void log_msg(enum log_level level, const char *format, ...) { (void)level; (void)format; }
static void exec_post_init(const char *section) { (void)section; }
static void reload_network(void) { reloads++; }
static void get_slot_option(const char *slot, const char *option, char *out, size_t len) {
    (void)slot; (void)option; if (len) out[0] = 0;
}

static struct mock_device *device_for_path(const char *path) {
    const char *prefix = "/sys/bus/usb/devices/";
    if (strncmp(path, prefix, strlen(prefix))) return NULL;
    path += strlen(prefix);
    for (int i = 0; i < device_len; i++) {
        size_t n = strlen(devices[i].slot);
        if (!strncmp(path, devices[i].slot, n) && (!path[n] || path[n] == '/')) return &devices[i];
    }
    return NULL;
}
EXPORT void test_device(const char *slot, const char *real, const char *network, const char *at) {
    struct mock_device *d = NULL;
    for (int i = 0; i < device_len; i++) if (!strcmp(devices[i].slot, slot)) d = &devices[i];
    if (!d) { if (device_len >= 8) abort(); d = &devices[device_len++]; }
    snprintf(d->slot, sizeof(d->slot), "%s", slot);
    snprintf(d->real, sizeof(d->real), "%s", real);
    snprintf(d->network, sizeof(d->network), "%s", network);
    snprintf(d->at, sizeof(d->at), "%s", at);
    strcpy(d->vid, "2c7c"); strcpy(d->pid, "0800"); strcpy(d->name, "rm500q-cn");
    strcpy(d->scan_num, "2"); strcpy(d->current_num, "2"); strcpy(d->protocol, "3.10");
    d->present = 1;
}
EXPORT void test_present(const char *slot, int present) {
    for (int i = 0; i < device_len; i++) if (!strcmp(devices[i].slot, slot)) devices[i].present = present;
}
EXPORT void test_generation(const char *slot, const char *current) {
    for (int i = 0; i < device_len; i++) if (!strcmp(devices[i].slot, slot)) snprintf(devices[i].current_num, 32, "%s", current);
}
EXPORT void test_protocol(const char *slot, const char *protocol) {
    for (int i = 0; i < device_len; i++) if (!strcmp(devices[i].slot, slot)) snprintf(devices[i].protocol, 32, "%s", protocol);
}
static int is_dir(const char *path) {
    struct mock_device *d = device_for_path(path);
    return d && d->present;
}
static char *test_realpath(const char *path, char *out) {
    struct mock_device *d = device_for_path(path);
    if (!d || !d->present) return NULL;
    strcpy(out, d->real); return out;
}
#define realpath test_realpath
static int read_file_trim(const char *path, char *out, size_t len) {
    struct mock_device *d = device_for_path(path);
    const char *value = NULL;
    const char *leaf = strrchr(path, '/');
    if (leaf && !strcmp(leaf, "/version")) {
        for (int i = 0; i < device_len; i++) {
            size_t n = (size_t)(leaf - path);
            if (!strncmp(path, devices[i].real, n) && devices[i].present) { value = devices[i].protocol; break; }
        }
    } else if (d && d->present && leaf) {
        if (!strcmp(leaf, "/idVendor")) value = d->vid;
        if (!strcmp(leaf, "/idProduct")) value = d->pid;
        if (!strcmp(leaf, "/devnum")) value = d->current_num;
    }
    if (!value) { if (len) out[0] = 0; return -1; }
    snprintf(out, len, "%s", value); return 0;
}
static void scan_usb_slot(const char *slot, struct scan_result *res) {
    struct mock_device *d = NULL;
    for (int i = 0; i < device_len; i++) if (!strcmp(devices[i].slot, slot)) d = &devices[i];
    if (!d || !d->present) return;
    snprintf(res->modem_path, sizeof(res->modem_path), "/sys/bus/usb/devices/%s/", slot);
    strcpy(res->vid, d->vid); strcpy(res->pid, d->pid); strcpy(res->usb_devnum, d->scan_num);
    sl_add(&res->net_devices, d->network); sl_add(&res->at_ports, d->at);
}
static void scan_pcie_slot(const char *slot, struct scan_result *res) { (void)slot; (void)res; }
static void scan_associated_usb(const char *slot, struct scan_result *res) { (void)slot; (void)res; }
static void validate_ports(struct scan_result *res) {
    if (res->at_ports.len) {
        sl_add(&res->valid_at_ports, res->at_ports.items[0]);
        snprintf(res->preferred_at, sizeof(res->preferred_at), "%s", res->at_ports.items[0]);
    }
}
static int detect_profile(const char *type, struct scan_result *res, struct modem_profile *profile) {
    struct mock_device *d = device_for_path(res->modem_path);
    (void)type;
    if (!d) return -1;
    memset(profile, 0, sizeof(*profile));
    strcpy(profile->name, d->name); strcpy(profile->manufacturer, "quectel");
    strcpy(profile->platform, "qualcomm"); strcpy(profile->pdp_index, "1");
    sl_init(&profile->modes); sl_add(&profile->modes, "qmi"); return 0;
}

/* INSERT_SCANNER_FUNCTIONS */

EXPORT int test_add(const char *slot) { return add_modem(slot, "usb"); }
EXPORT void test_disable(const char *slot) { disable_slot(slot); }
EXPORT int test_reloads(void) { return reloads; }
EXPORT int test_writes(void) { return writes; }
