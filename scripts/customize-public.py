#!/usr/bin/env python3
"""Remove factory credentials and disable newly detected public-image radios."""
import argparse
import re
from pathlib import Path


def disable_new_radios(text: str) -> str:
    """Patch the pinned mac80211 generator without modifying existing UCI keys."""
    for section in ('section', 'iface'):
        pattern = re.compile(
            rf"^([ \t]*set wireless\.\$\{{{section}\}}\.disabled=)'([01])'([ \t]*)$",
            re.MULTILINE,
        )
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise RuntimeError(
                f'Expected exactly one mac80211 {section} disabled default; '
                'inspect the changed upstream Wi-Fi generator before building'
            )
        text = pattern.sub(lambda match: f"{match[1]}'1'{match[3]}", text)
    return text


def customize(root: Path) -> None:
    defaults = root / 'package/lean/default-settings/files/zzz-default-settings'
    text = defaults.read_text(encoding='utf-8')
    wireless = root / 'package/kernel/mac80211/files/lib/wifi/mac80211.sh'
    wireless_text = disable_new_radios(wireless.read_text(encoding='utf-8'))
    shadow = root / 'package/base-files/files/etc/shadow'
    root_lines = [line for line in shadow.read_text(encoding='utf-8').splitlines() if line.startswith('root:')]
    if len(root_lines) != 1 or root_lines[0].split(':')[1] != '':
        raise RuntimeError('The base image must have exactly one root account with no preset password')
    # Remove shared factory password injection, the unrelated 18.06 LuCI feed,
    # and the command which disables package signature verification.
    unwanted = ('/etc/shadow', 'releases\\/18.06.9', "'/check_signature/d'")
    lines = [line for line in text.splitlines() if not any(s in line for s in unwanted)]
    # Validate all inputs before changing either source file. The generator
    # applies to late driver discovery too, after uci-defaults has completed.
    defaults.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    wireless.write_text(wireless_text, encoding='utf-8', newline='\n')
    print('Public defaults: no factory credentials; new Wi-Fi radios disabled; feed versions and signature policy retained')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    customize(parser.parse_args().root.resolve())
