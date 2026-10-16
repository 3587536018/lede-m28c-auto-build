#!/usr/bin/env python3
"""Apply a small, checked iStore language fix to an updated LEDE source tree.

Run after feeds update/install: python3 scripts/customize-ui.py path/to/lede
Keep iStore's language selection and native LuCI translation packaging in sync.
Upstream drift aborts before any file is changed instead of silently omitting
the Simplified Chinese catalogs used by iStore and its task dialogs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


MARKER = "M28C_ISTORE_LANGUAGE_V1"
ORIGINAL_LANGUAGE = '''local function vue_lang()
    local i18n = require("luci.i18n")
    local lang = i18n.translate("istore_vue_lang")
    if lang == "istore_vue_lang" or lang == "" then
        lang = "en"
    end
    return lang
end'''

# LuCI's current Lua compatibility runtime no longer has i18n.load(). Its
# setlanguage() loads every matching *.lang.lmo, including iStore.zh-cn.lmo.
PATCHED_LANGUAGE = '''local function vue_lang()
    -- M28C_ISTORE_LANGUAGE_V1: keep LuCI and iStore's Vue language in sync.
    local i18n = require("luci.i18n")
    local fs = require("nixio.fs")
    local uci = require("luci.model.uci").cursor()
    local function normalize(value)
        local lang = tostring(value or ""):lower():gsub("_", "-")
        if lang == "zh" or lang == "zh-hans" then lang = "zh-cn" end
        if lang == "zh-hant" then lang = "zh-tw" end
        if lang == "en-us" or lang == "en-gb" then lang = "en" end
        if lang == "auto" or not lang:match("^[a-z][a-z]%-?[a-z]*$") then
            return nil
        end
        return lang
    end
    local configured = normalize(uci:get("luci", "main", "lang"))
    local active = normalize(i18n.context and i18n.context.lang)
    local selected = configured or active
    local catalog_dir = i18n.i18ndir or "/usr/lib/lua/luci/i18n/"
    if selected and fs.access(catalog_dir .. "iStore." .. selected .. ".lmo") then
        if type(i18n.load) == "function" then
            i18n.load("iStore", selected)
        elseif type(i18n.setlanguage) == "function" then
            i18n.setlanguage(selected)
        end
    end
    local translated = normalize(i18n.translate("istore_vue_lang"))
    local function available(lang)
        return lang and fs.access("/www/luci-static/istore/i18n/" .. lang .. ".json")
    end
    -- Honour an explicit LuCI language even when a stale catalogue is loaded.
    if available(configured) then return configured end
    if available(translated) then return translated end
    if available(active) then return active end
    return "en"
end'''

LANGUAGE_FUNCTION = re.compile(r"^local function vue_lang\(\)\s*\n.*?^end[ \t]*$", re.M | re.S)
MENU_ENTRY = re.compile(
    r'(entry\(\{\s*"admin"\s*,\s*"store"\s*\}\s*,\s*'
    r'call\("redirect_index"\)\s*,\s*_)\("iStore"\)(\s*,\s*31\s*\))'
)
MENU_LABEL = '_("iStore 应用商店")'

# Both upstream packages compile their catalogs inside src/Makefile instead of
# using the top-level LuciTranslation package rule. The pinned luci.mk maps
# po/zh-cn to the build tag zh_Hans, so the old direct lookup silently finds
# nothing. Use the same source-directory and runtime-locale aliases as LuCI.
CATALOG_MARKER = "M28C_NATIVE_CATALOG_ALIASES_V1"
ORIGINAL_CATALOG_INSTALL = '''\t$(foreach lang,$(LUCI_LANGUAGES),$(foreach po,$(wildcard ${CURDIR}/po/$(lang)/*.po), \\
\tpo2lmo $(po) \\
\t\t$(DESTDIR)$(LUCI_LIBRARYDIR)/i18n/$(basename $(notdir $(po))).$(lang).lmo;))'''
PATCHED_CATALOG_INSTALL = '''\t# M28C_NATIVE_CATALOG_ALIASES_V1: use LuCI's PO and runtime locale aliases.
\t$(foreach lang,$(LUCI_LANGUAGES),$(foreach po,$(wildcard ${CURDIR}/po/$(call LuciPoDir,$(lang))/*.po), \\
\tpo2lmo $(po) \\
\t\t$(DESTDIR)$(LUCI_LIBRARYDIR)/i18n/$(basename $(notdir $(po))).$(firstword $(LUCI_LC_ALIAS.$(lang)) $(lang)).lmo;))'''


class PatchError(RuntimeError):
    """The checked upstream code could not be safely modified."""


def normalized(source: str) -> str:
    return re.sub(r"\s+", "", source)


def patch_controller(source: str) -> str:
    functions = list(LANGUAGE_FUNCTION.finditer(source))
    if len(functions) != 1:
        raise PatchError(f"expected one vue_lang() function, found {len(functions)}")
    existing = functions[0].group(0)
    if MARKER in source:
        if normalized(existing) != normalized(PATCHED_LANGUAGE):
            raise PatchError("existing M28C language patch was changed; review before updating")
        if source.count(MENU_LABEL) != 1:
            raise PatchError("existing M28C menu caption is missing or duplicated")
        return source
    if normalized(existing) != normalized(ORIGINAL_LANGUAGE):
        raise PatchError("upstream vue_lang() changed; review the language patch before building")
    entries = list(MENU_ENTRY.finditer(source))
    if len(entries) != 1:
        raise PatchError(f"expected one original iStore menu entry, found {len(entries)}")
    result = LANGUAGE_FUNCTION.sub(lambda _: PATCHED_LANGUAGE, source, count=1)
    return MENU_ENTRY.sub(lambda m: m.group(1) + '("iStore 应用商店")' + m.group(2), result, count=1)


def patch_catalog_install(source: str) -> str:
    if source.count("include $(TOPDIR)/feeds/luci/luci.mk") != 1:
        raise PatchError("native catalog installer must include the pinned luci.mk once")
    if CATALOG_MARKER in source:
        if (source.count(CATALOG_MARKER) != 1
                or source.count(PATCHED_CATALOG_INSTALL) != 1
                or ORIGINAL_CATALOG_INSTALL in source):
            raise PatchError("existing native catalog patch was changed; review before updating")
        return source
    if source.count(ORIGINAL_CATALOG_INSTALL) != 1:
        raise PatchError("upstream native catalog install loop changed or is duplicated")
    return source.replace(ORIGINAL_CATALOG_INSTALL, PATCHED_CATALOG_INSTALL, 1)


def locate_controller(lede_root: Path) -> Path:
    feed = (lede_root / "feeds" / "istore").resolve()
    if not feed.is_dir():
        raise PatchError(f"iStore feed not found: {feed}; update/install feeds first")
    controllers = [
        path.resolve() for path in feed.rglob("store.lua")
        if path.parts[-3:] == ("luasrc", "controller", "store.lua")
        and ".git" not in path.relative_to(feed).parts
    ]
    if len(controllers) != 1:
        raise PatchError(f"expected one iStore controller under {feed}, found {len(controllers)}")
    controller = controllers[0]
    if not controller.is_relative_to(feed) or not (controller.parents[2] / "Makefile").is_file():
        raise PatchError("iStore controller is outside its feed or has no package Makefile")
    return controller


def customize(lede_root: Path) -> tuple[Path, bool]:
    controller = locate_controller(lede_root)
    feed = (lede_root / "feeds/istore").resolve()
    store = controller.parents[2]
    pending = [(controller, patch_controller(controller.read_text(encoding="utf-8")))]
    for package, catalog in ((store, "iStore"), (store.parent / "luci-lib-taskd", "lib-tasks")):
        installer = (package / "src/Makefile").resolve()
        po = (package / "src/po/zh-cn" / (catalog + ".po")).resolve()
        if (not installer.is_relative_to(feed) or not po.is_relative_to(feed)
                or not installer.is_file() or not po.is_file() or not po.stat().st_size):
            raise PatchError(f"missing or unsafe native Chinese catalog installer: {package.name}")
        pending.append((installer, patch_catalog_install(installer.read_text(encoding="utf-8"))))

    # Validate every input first: a taskd mismatch must not leave the controller
    # or the store installer partially patched.
    changed = False
    for path, result in pending:
        if path.read_text(encoding="utf-8") != result:
            with path.open("w", encoding="utf-8", newline="\n") as output:
                output.write(result)
            changed = True
    return controller, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lede_root", type=Path, help="LEDE source root with feeds already installed")
    args = parser.parse_args()
    try:
        path, changed = customize(args.lede_root.resolve())
    except (OSError, UnicodeError, PatchError) as error:
        print(f"UI customization failed: {error}", file=sys.stderr)
        return 1
    print(f"iStore Chinese UI: {'patched' if changed else 'already patched'} {path}; "
          "native iStore/taskd catalog aliases verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
