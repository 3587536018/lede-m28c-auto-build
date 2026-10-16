"""Regression checks for the build-time patch's fail-closed/upgrade behaviour."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None


SPEC = importlib.util.spec_from_file_location(
    "customize_ui", Path(__file__).resolve().parents[1] / "scripts" / "customize-ui.py"
)
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)

MENU = '    entry({"admin", "store"}, call("redirect_index"), _("iStore"), 31)'
FIXTURE = 'module("luci.controller.store", package.seeall)\n\nfunction index()\n' + MENU + '\nend\n\n' + ui.ORIGINAL_LANGUAGE + '\n\nfunction untouched()\n    return "keep upstream"\nend\n'

# Exact src/Makefile shapes from the locked linkease/istore revision
# 07573209abc9377a128c56a3f9322ebc8d9b7d31, independent of patch constants.
SRC_PREFIX = '''clean:
compile:

include $(TOPDIR)/rules.mk

LUCI_NAME:=luci-lib-dummy
INCLUDE_DIR:=./dummy

include $(TOPDIR)/feeds/luci/luci.mk

install:
'''
NATIVE_LOOP = '''\tmkdir -p "$(DESTDIR)$(LUCI_LIBRARYDIR)/i18n"
\t$(foreach lang,$(LUCI_LANGUAGES),$(foreach po,$(wildcard ${CURDIR}/po/$(lang)/*.po), \\
\tpo2lmo $(po) \\
\t\t$(DESTDIR)$(LUCI_LIBRARYDIR)/i18n/$(basename $(notdir $(po))).$(lang).lmo;))
'''
TASKD_SRC = SRC_PREFIX + NATIVE_LOOP
STORE_SRC = SRC_PREFIX + '''\t[ -n "$(APP_STORE_VERSION)" ]
\tmkdir -p "$(DESTDIR)/www/luci-static" "$(DESTDIR)/etc"
\tcp -a "$(FRONTEND_DIST)/luci-static/istore" "$(DESTDIR)/www/luci-static/"
\t$(SED) 's#\\.js"#.js?v=$(APP_STORE_VERSION)"#g' "$(DESTDIR)/www/luci-static/istore/index.js"
\techo "$(APP_STORE_VERSION)" > "$(DESTDIR)/etc/.app_store.version"
ifneq ($(USE_APK),)
\tcp -pR ./root-apk/* "$(DESTDIR)/"
else
\tcp -pR ./root-opkg/* "$(DESTDIR)/"
\tmkdir -p "$(DESTDIR)/etc/opkg/keys"
\tcp -a ./key-build.pub $(DESTDIR)/etc/opkg/keys/`$(STAGING_DIR_HOST)/bin/usign -F -p ./key-build.pub`
endif
''' + NATIVE_LOOP


def populate_feed(root):
    packages = root / "feeds/istore/luci"
    controller = packages / "luci-app-store/luasrc/controller/store.lua"
    controller.parent.mkdir(parents=True)
    controller.write_text(FIXTURE, encoding="utf-8")
    for name, source, catalog in (
        ("luci-app-store", STORE_SRC, "iStore"),
        ("luci-lib-taskd", TASKD_SRC, "lib-tasks"),
    ):
        package = packages / name
        (package / "src/po/zh-cn").mkdir(parents=True)
        (package / "Makefile").write_text("include $(TOPDIR)/rules.mk\n")
        (package / "src/Makefile").write_text(source, encoding="utf-8")
        (package / "src/po/zh-cn" / (catalog + ".po")).write_text(
            'msgid "Finished at:"\nmsgstr "完成于："\n', encoding="utf-8")
    return controller


class CustomizeUiTests(unittest.TestCase):
    def test_changes_only_language_function_and_menu_caption(self):
        patched = ui.patch_controller(FIXTURE)
        restored = patched.replace(ui.PATCHED_LANGUAGE, ui.ORIGINAL_LANGUAGE).replace(
            ui.MENU_LABEL, '_("iStore")'
        )
        self.assertEqual(restored, FIXTURE)
        self.assertEqual(ui.patch_controller(patched), patched)

    def test_rejects_upstream_language_changes(self):
        changed = FIXTURE.replace('lang = "en"', 'lang = browser_language()')
        with self.assertRaisesRegex(ui.PatchError, "upstream vue_lang"):
            ui.patch_controller(changed)

    def test_rejects_missing_or_duplicate_menu_entries(self):
        for source in (FIXTURE.replace(MENU, ""), FIXTURE + MENU):
            with self.subTest(source=source):
                with self.assertRaises(ui.PatchError):
                    ui.patch_controller(source)

    def test_rejects_duplicate_functions(self):
        with self.assertRaisesRegex(ui.PatchError, "expected one vue_lang"):
            ui.patch_controller(FIXTURE + ui.ORIGINAL_LANGUAGE)

    def test_rejects_partial_or_modified_existing_patch(self):
        patched = ui.patch_controller(FIXTURE)
        for changed in (
            patched.replace('i18n.load("iStore", selected)', 'i18n.load("store", selected)'),
            patched.replace(ui.MENU_LABEL, '_("iStore")'),
        ):
            with self.subTest(source=changed):
                with self.assertRaises(ui.PatchError):
                    ui.patch_controller(changed)

    def test_locates_feed_source_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller = populate_feed(root)
            path, changed = ui.customize(root)
            self.assertEqual(path, controller.resolve())
            self.assertTrue(changed)
            original_bytes = controller.read_bytes()
            self.assertFalse(ui.customize(root)[1])
            self.assertEqual(original_bytes, controller.read_bytes())

    def test_repairs_catalogs_when_controller_was_already_patched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller = populate_feed(root)
            controller.write_text(ui.patch_controller(FIXTURE), encoding="utf-8")
            self.assertTrue(ui.customize(root)[1])
            for name in ("luci-app-store", "luci-lib-taskd"):
                installer = root / "feeds/istore/luci" / name / "src/Makefile"
                self.assertIn(ui.CATALOG_MARKER, installer.read_text(encoding="utf-8"))

    def test_catalog_drift_or_missing_po_leaves_all_files_unchanged(self):
        for damage in ("taskd-loop", "store-loop", "missing-po", "empty-po"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                populate_feed(root)
                packages = root / "feeds/istore/luci"
                if damage.endswith("loop"):
                    name = "luci-lib-taskd" if damage.startswith("taskd") else "luci-app-store"
                    installer = packages / name / "src/Makefile"
                    installer.write_text(installer.read_text().replace("po2lmo $(po)", "new-compiler $(po)"))
                else:
                    po = packages / "luci-lib-taskd/src/po/zh-cn/lib-tasks.po"
                    if damage == "empty-po":
                        po.write_text("")
                    else:
                        po.unlink()
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with self.assertRaises(ui.PatchError):
                    ui.customize(root)
                self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_drift_does_not_mutate_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "feeds/istore/luci/luci-app-store"
            controller = package / "luasrc/controller/store.lua"
            controller.parent.mkdir(parents=True)
            (package / "Makefile").touch()
            changed = FIXTURE.replace('lang = "en"', 'lang = "ja"')
            controller.write_text(changed, encoding="utf-8")
            before = controller.read_bytes()
            with self.assertRaises(ui.PatchError):
                ui.customize(root)
            self.assertEqual(controller.read_bytes(), before)

    def test_missing_or_ambiguous_feed_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ui.PatchError):
                ui.locate_controller(root)
            for name in ("luci-app-store", "unexpected-copy"):
                package = root / "feeds/istore" / name
                controller = package / "luasrc/controller/store.lua"
                controller.parent.mkdir(parents=True)
                (package / "Makefile").touch()
                controller.write_text(FIXTURE, encoding="utf-8")
            with self.assertRaisesRegex(ui.PatchError, "found 2"):
                ui.locate_controller(root)


class NativeCatalogInstallTests(unittest.TestCase):
    def test_real_installers_preserve_other_install_steps_and_are_idempotent(self):
        for source in (STORE_SRC, TASKD_SRC):
            with self.subTest(source=source):
                result = ui.patch_catalog_install(source)
                self.assertEqual(result.replace(ui.PATCHED_CATALOG_INSTALL,
                                                ui.ORIGINAL_CATALOG_INSTALL), source)
                self.assertEqual(ui.patch_catalog_install(result), result)

    def test_rejects_missing_duplicate_or_changed_original_loop(self):
        for source in (
            TASKD_SRC.replace(NATIVE_LOOP, ""), TASKD_SRC + NATIVE_LOOP,
            TASKD_SRC.replace("po2lmo $(po)", "po2lmo --changed $(po)"),
            TASKD_SRC.replace("include $(TOPDIR)/feeds/luci/luci.mk", ""),
        ):
            with self.subTest(source=source), self.assertRaises(ui.PatchError):
                ui.patch_catalog_install(source)

    def test_rejects_partial_or_modified_catalog_patch(self):
        patched = ui.patch_catalog_install(TASKD_SRC)
        for source in (
            patched.replace("$(call LuciPoDir,$(lang))", "$(lang)"),
            patched.replace("$(firstword $(LUCI_LC_ALIAS.$(lang)) $(lang))", "$(lang)"),
            patched.replace(ui.CATALOG_MARKER, ""),
            patched + ui.PATCHED_CATALOG_INSTALL,
        ):
            with self.subTest(source=source), self.assertRaises(ui.PatchError):
                ui.patch_catalog_install(source)

    def test_gnu_make_resolves_real_po_directories_and_runtime_catalog_names(self):
        make = os.environ.get("MAKE_EXE") or shutil.which("make")
        if not make:
            self.skipTest("GNU make is required for native catalog rule expansion")
        # Native rules from pinned luci.mk. Execute GNU make expansion rather
        # than asserting textual substitutions that could reproduce the bug.
        native_rules = '''LUCI_LANGUAGES_RAW:=$(filter-out templates,$(notdir $(wildcard ${CURDIR}/po/*)))
LUCI_LANGUAGES:=$(sort $(foreach lang,$(LUCI_LANGUAGES_RAW),$(if $(filter zh-cn,$(lang)),zh_Hans,$(lang))))
LUCI_LC_ALIAS.zh_Hans=zh-cn
LUCI_PO_DIR_ALIAS.zh_Hans=zh-cn
LUCI_LIBRARYDIR=/usr/lib/lua/luci
define LuciPoDir
$(strip $(if $(wildcard ${CURDIR}/po/$(1)),$(1),$(firstword $(foreach alias,$(LUCI_PO_DIR_ALIAS.$(1)),$(if $(wildcard ${CURDIR}/po/$(alias)),$(alias))))))
endef
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rules.mk").touch()
            (root / "feeds/luci").mkdir(parents=True)
            (root / "feeds/luci/luci.mk").write_text(native_rules)
            for source, catalog in ((STORE_SRC, "iStore"), (TASKD_SRC, "lib-tasks")):
                for po_language in ("zh-cn", "zh_Hans"):
                    with self.subTest(catalog=catalog, po_language=po_language):
                        src = root / (catalog + "-" + po_language)
                        (src / "po" / po_language).mkdir(parents=True)
                        (src / "po" / po_language / (catalog + ".po")).touch()
                        (src / "po/zh-tw").mkdir()
                        (src / "po/zh-tw" / (catalog + ".po")).touch()
                        installer = src / "Makefile"
                        installer.write_text(ui.patch_catalog_install(source), newline="\n")
                        completed = subprocess.run(
                            [make, "--no-print-directory", "-n", "install",
                             "TOPDIR=" + root.as_posix(), "DESTDIR=/catalog-test"],
                            cwd=src, check=True, capture_output=True, text=True)
                        commands = [line for line in completed.stdout.splitlines() if line.startswith("po2lmo ")]
                        self.assertTrue(commands, completed.stdout)
                        combined = " ".join(commands)
                        self.assertIn(f"/po/{po_language}/{catalog}.po", combined)
                        self.assertIn(f"/i18n/{catalog}.zh-cn.lmo", combined)
                        self.assertIn(f"/i18n/{catalog}.zh-tw.lmo", combined)
                        self.assertNotIn(f"/i18n/{catalog}.zh_Hans.lmo", combined)


@unittest.skipUnless(LuaRuntime, "optional Lua runtime checks require Python package lupa")
class IStoreLanguageRuntimeTests(unittest.TestCase):
    def run_language(self, configured, active="zh-cn", translated="istore_vue_lang", resources=("zh-cn", "en"), legacy=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().configured_language = configured
        lua.globals().active_language = active
        lua.globals().translated_language = translated
        lua.globals().legacy_runtime = legacy
        lua.globals().resources = lua.table_from(resources)
        result = lua.execute('''
            local files = {}
            for _, lang in ipairs(resources) do
                files["/www/luci-static/istore/i18n/" .. lang .. ".json"] = true
                files["/usr/lib/lua/luci/i18n/iStore." .. lang .. ".lmo"] = true
            end
            catalog_loaded = nil
            local i18n = {
                context = { lang = active_language },
                i18ndir = "/usr/lib/lua/luci/i18n/",
                translate = function() return translated_language end
            }
            if legacy_runtime then
                i18n.load = function(name, lang) catalog_loaded = name .. ":" .. lang end
            else
                i18n.setlanguage = function(lang) catalog_loaded = "all:" .. lang end
            end
            local modules = {
                ["luci.i18n"] = i18n,
                ["nixio.fs"] = { access = function(path) return files[path] == true end },
                ["luci.model.uci"] = { cursor = function()
                    return { get = function() return configured_language end }
                end }
            }
            require = function(name) return modules[name] end
        ''' + ui.PATCHED_LANGUAGE + '\nreturn vue_lang()')
        return result, lua.globals().catalog_loaded

    def test_chinese_locale_variants_work_without_translation_key(self):
        for locale in ("zh_cn", "zh-CN", "zh-cn", "zh-Hans"):
            with self.subTest(locale=locale):
                self.assertEqual(self.run_language(locale), ("zh-cn", "all:zh-cn"))

    def test_legacy_catalog_name_preserves_case(self):
        self.assertEqual(self.run_language("zh_cn", legacy=True), ("zh-cn", "iStore:zh-cn"))

    def test_auto_uses_active_language_if_translation_is_missing(self):
        self.assertEqual(self.run_language("auto", active="zh_CN")[0], "zh-cn")

    def test_explicit_english_wins_over_a_stale_chinese_catalog(self):
        self.assertEqual(self.run_language("en", translated="zh-cn")[0], "en")

    def test_missing_resource_falls_back_to_english(self):
        self.assertEqual(self.run_language("zh-cn", resources=("en",))[0], "en")

    def test_translation_locale_is_normalized(self):
        self.assertEqual(self.run_language("auto", active=None, translated="zh_CN")[0], "zh-cn")

    def test_invalid_language_is_not_used_as_a_resource_path(self):
        self.assertEqual(self.run_language("../../secret", active=None)[0], "en")


if __name__ == "__main__":
    unittest.main()
