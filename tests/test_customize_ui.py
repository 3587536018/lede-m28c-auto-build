"""Regression checks for the build-time patch's fail-closed/upgrade behaviour."""

import importlib.util
from pathlib import Path
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
            package = root / "feeds/istore/luci/luci-app-store"
            controller = package / "luasrc/controller/store.lua"
            controller.parent.mkdir(parents=True)
            (package / "Makefile").write_text("include $(TOPDIR)/rules.mk\n")
            controller.write_text(FIXTURE, encoding="utf-8")
            path, changed = ui.customize(root)
            self.assertEqual(path, controller.resolve())
            self.assertTrue(changed)
            original_bytes = controller.read_bytes()
            self.assertFalse(ui.customize(root)[1])
            self.assertEqual(original_bytes, controller.read_bytes())

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
