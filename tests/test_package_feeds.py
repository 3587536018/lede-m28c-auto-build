"""Check the shipped supplemental feeds and pinned public release key."""
import base64
import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
KEY_ID = "d310c6f2833e97f7"
KEY_SOURCE_COMMIT = "fbae29d730f81c892f52e0ff00fe867444aeeae6"
KEY_SOURCE_URL = (
    "https://raw.githubusercontent.com/openwrt/keyring/"
    f"{KEY_SOURCE_COMMIT}/usign/{KEY_ID}"
)
KEY_SHA256 = "e3624aa9be785362a172595b4919b233268871c4365a9b8da2b42ac41745ad95"
MIRROR = "https://mirrors.tencent.com/lede/releases/24.10.5/packages/aarch64_generic"
FEEDS = ("base", "luci", "packages", "routing", "telephony")


class PackageFeedTests(unittest.TestCase):
    def test_only_verified_supplemental_repositories_are_enabled(self):
        text = (ROOT / "files/etc/opkg/distfeeds.conf").read_text(encoding="utf-8")
        entries = [line.split() for line in text.splitlines() if line and not line.startswith("#")]
        self.assertEqual(entries, [["src/gz", f"openwrt_{feed}", f"{MIRROR}/{feed}"] for feed in FEEDS])

    def test_first_boot_comment_rewrite_does_not_enable_foreign_feeds(self):
        text = (ROOT / "files/etc/opkg/distfeeds.conf").read_text(encoding="utf-8")
        # The locked upstream 99-default-settings removes '# ' from this file.
        # A conventional commented-out core line would silently become active.
        after_defaults = text.replace("downloads.openwrt.org", "mirrors.tencent.com/lede").replace("# ", "")
        self.assertEqual(after_defaults, text)
        entries = [line for line in after_defaults.splitlines() if line and not line.startswith("#")]
        self.assertFalse(any("/targets/" in line or "/kmods/" in line for line in entries))
        self.assertFalse(any(line.rsplit("/", 1)[-1] in {"istore", "openclash", "qmodem"} for line in entries))

    def test_release_public_key_matches_pinned_upstream_bytes_and_identity(self):
        data = (ROOT / f"files/etc/opkg/keys/{KEY_ID}").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), KEY_SHA256)
        lines = data.decode("ascii").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "untrusted comment: Public usign key for 24.10 release builds")
        decoded = base64.b64decode(lines[1], validate=True)
        self.assertEqual(len(decoded), 42)  # algorithm + key ID + Ed25519 public key
        self.assertEqual(decoded[:2], b"Ed")
        self.assertEqual(decoded[2:10].hex(), KEY_ID)

    def test_key_provenance_and_kernel_constraint_are_documented(self):
        text = (ROOT / "docs/PACKAGE-FEEDS.md").read_text(encoding="utf-8")
        for expected in (KEY_SOURCE_COMMIT, KEY_SOURCE_URL, KEY_SHA256,
                         "6.12.104", "6.6.119", "option check_signature"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
