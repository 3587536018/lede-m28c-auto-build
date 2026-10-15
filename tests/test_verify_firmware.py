"""Exercise the firmware boundary with independently encoded fwtool fixtures.

The wire format and raw CRC are taken from fwimage.h, fwtool.c and crc32.h
at openwrt/fwtool commit 8f7fe925ca205c8e8e2d0d1b16218c1e148d5173.
Fixture construction does not call the verifier or use its format constants.
"""

import gzip
import importlib.util
import json
from pathlib import Path
import random
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_firmware", ROOT / "scripts" / "verify-firmware.py"
)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def raw_crc32(data):
    """Bitwise form of upstream's reflected CRC with no final complement."""
    value = 0xFFFFFFFF
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
    return value


def append_chunk(prefix, payload, kind):
    body = prefix + payload
    # fwtool stores network-order integers and counts the 16-byte trailer.
    trailer = struct.pack(
        ">IIB3xI", 0x46577830, raw_crc32(body), kind, len(payload) + 16
    )
    return body + trailer


def metadata():
    return {
        "metadata_version": "1.1",
        "compat_version": "1.0",
        "supported_devices": ["widora,mangopi-m28c"],
        "version": {
            "dist": "OpenWrt",
            "version": "SNAPSHOT",
            "revision": "r7892-8e247d214",
            "target": "rockchip/armv8",
            "board": "widora_mangopi-m28c",
        },
    }


def with_metadata(compressed, info=None, header=b"\0" * 8):
    document = metadata() if info is None else info
    payload = header + json.dumps(document, separators=(",", ":")).encode("utf-8")
    return append_chunk(compressed, payload, 1)


class VerifyFirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reproducible content spanning several decompression output blocks.
        cls.compressed = gzip.compress(bytes(range(256)) * 12289, mtime=0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "mangopi-m28c-sysupgrade.img.gz"

    def verify(self, content):
        self.image.write_bytes(content)
        return VERIFY.verify_image(self.image)

    def reject(self, content):
        with self.assertRaises(ValueError):
            self.verify(content)

    def test_fixture_crc_matches_published_crc32_check_vector(self):
        self.assertEqual(raw_crc32(b"123456789"), 0x340BC6D9)
        self.assertEqual(raw_crc32(b""), 0xFFFFFFFF)

    def test_valid_gzip_with_fwtool_metadata_returns_document(self):
        self.assertEqual(self.verify(with_metadata(self.compressed)), metadata())

    def test_incompressible_input_spans_file_read_boundaries(self):
        payload = random.Random(28).randbytes(1024 * 1024 + 137)
        compressed = gzip.compress(payload, mtime=0)
        self.assertGreater(len(compressed), 1024 * 1024)
        self.assertEqual(self.verify(with_metadata(compressed)), metadata())

    def test_gzip_without_fwtool_metadata_is_rejected(self):
        self.reject(self.compressed)

    def test_empty_or_non_gzip_images_are_rejected(self):
        for content in (b"", b"not firmware", with_metadata(b"plain disk image")):
            with self.subTest(content=content[:24]):
                self.reject(content)

    def test_gzip_truncation_is_rejected_even_with_valid_fwtool_crc(self):
        for length in (1, 4, 8, 20):
            with self.subTest(removed_bytes=length):
                self.reject(with_metadata(self.compressed[:-length]))

    def test_gzip_crc_damage_is_rejected_even_with_valid_fwtool_crc(self):
        damaged = bytearray(self.compressed)
        damaged[-8] ^= 1
        self.reject(with_metadata(bytes(damaged)))

    def test_gzip_size_damage_is_rejected_even_with_valid_fwtool_crc(self):
        damaged = bytearray(self.compressed)
        damaged[-4] ^= 1
        self.reject(with_metadata(bytes(damaged)))

    def test_concatenated_gzip_members_are_rejected(self):
        self.reject(with_metadata(self.compressed + gzip.compress(b"second member", mtime=0)))

    def test_junk_between_gzip_and_metadata_is_rejected(self):
        for junk in (b"\0", b"trailing garbage"):
            with self.subTest(junk=junk):
                self.reject(with_metadata(self.compressed + junk))

    def test_junk_after_gzip_or_metadata_is_rejected(self):
        for prefix in (self.compressed, with_metadata(self.compressed)):
            for junk in (b"\0", b"FWx0" + b"\0" * 12, b"trailing garbage"):
                with self.subTest(prefix_size=len(prefix), junk=junk):
                    self.reject(prefix + junk)

    def test_metadata_crc_damage_is_rejected(self):
        damaged = bytearray(with_metadata(self.compressed))
        damaged[-12] ^= 1
        self.reject(bytes(damaged))

    def test_metadata_content_damage_is_rejected(self):
        original = with_metadata(self.compressed)
        damaged = original.replace(b"r7892", b"r7893")
        self.assertNotEqual(damaged, original)
        self.reject(damaged)

    def test_trailer_size_cannot_underflow_or_point_before_image(self):
        original = with_metadata(self.compressed)
        for size in (0, 15, 16, 23, len(original) + 1, 0xFFFFFFFF):
            with self.subTest(size=size):
                self.reject(original[:-4] + struct.pack(">I", size))

    def test_nonzero_trailer_padding_is_rejected(self):
        damaged = bytearray(with_metadata(self.compressed))
        damaged[-7] = 1
        self.reject(bytes(damaged))

    def test_metadata_header_version_and_flags_must_be_zero(self):
        for header in (struct.pack(">II", 1, 0), struct.pack(">II", 0, 1)):
            with self.subTest(header=header):
                self.reject(with_metadata(self.compressed, header=header))

    def test_invalid_json_or_non_object_metadata_is_rejected(self):
        for document in (b"{", b"\xff", b"[]", b"null", b'"metadata"'):
            with self.subTest(document=document):
                self.reject(append_chunk(self.compressed, b"\0" * 8 + document, 1))

    def test_supported_devices_requires_exact_list_membership(self):
        for devices in (
            ["friendlyarm,nanopi-r5s"],
            ["prefix-widora,mangopi-m28c-suffix"],
            "widora,mangopi-m28c",
            [],
            None,
        ):
            info = metadata()
            info["supported_devices"] = devices
            with self.subTest(devices=devices):
                self.reject(with_metadata(self.compressed, info))

    def test_missing_supported_devices_is_rejected(self):
        info = metadata()
        del info["supported_devices"]
        self.reject(with_metadata(self.compressed, info))

    def test_wrong_target_or_board_is_rejected(self):
        for key, value in (("target", "x86/64"), ("board", "friendlyarm_nanopi-r5s")):
            info = metadata()
            info["version"][key] = value
            with self.subTest(field=key):
                self.reject(with_metadata(self.compressed, info))

    def test_metadata_and_compatibility_versions_are_checked(self):
        for key, value in (("metadata_version", "2.0"), ("compat_version", "2.0")):
            info = metadata()
            info[key] = value
            with self.subTest(field=key):
                self.reject(with_metadata(self.compressed, info))

    def test_oversized_metadata_is_rejected(self):
        info = metadata()
        info["unexpected_padding"] = "x" * (30 * 1024)
        self.reject(with_metadata(self.compressed, info))

    def test_duplicate_metadata_and_unknown_chunk_types_are_rejected(self):
        valid = with_metadata(self.compressed)
        self.reject(with_metadata(valid))
        self.reject(append_chunk(valid, b"unknown extension", 2))

    def test_optional_signature_frame_is_accepted(self):
        # This only checks fwtool framing/integrity, not signer authenticity.
        signed = append_chunk(with_metadata(self.compressed), b"fixture signature", 0)
        self.assertEqual(self.verify(signed), metadata())

    def test_signature_crc_damage_is_rejected(self):
        signed = bytearray(append_chunk(with_metadata(self.compressed), b"signature", 0))
        signed[-12] ^= 1
        self.reject(bytes(signed))

    def test_valid_signature_crc_does_not_hide_invalid_metadata_crc(self):
        inner = bytearray(with_metadata(self.compressed))
        inner[-12] ^= 1
        self.reject(append_chunk(bytes(inner), b"signature", 0))

    def test_signature_without_metadata_is_rejected(self):
        self.reject(append_chunk(self.compressed, b"signature", 0))

    def test_oversized_or_duplicate_signatures_are_rejected(self):
        valid = with_metadata(self.compressed)
        self.reject(append_chunk(valid, b"x" * 1025, 0))
        signed = append_chunk(valid, b"signature", 0)
        self.reject(append_chunk(signed, b"second signature", 0))


if __name__ == "__main__":
    unittest.main()
