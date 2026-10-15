#!/usr/bin/env python3
"""Apply a small, checked iStore language fix to an updated LEDE source tree.

Run after feeds update/install: python3 scripts/customize-ui.py path/to/lede
Only the language selector and menu caption are changed. Upstream drift aborts
the build instead of silently producing an English first-install experience.
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
    source = controller.read_text(encoding="utf-8")
    result = patch_controller(source)
    if result != source:
        with controller.open("w", encoding="utf-8", newline="\n") as output:
            output.write(result)
    return controller, result != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lede_root", type=Path, help="LEDE source root with feeds already installed")
    args = parser.parse_args()
    try:
        path, changed = customize(args.lede_root.resolve())
    except (OSError, UnicodeError, PatchError) as error:
        print(f"UI customization failed: {error}", file=sys.stderr)
        return 1
    print(f"iStore Chinese UI: {'patched' if changed else 'already patched'} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
