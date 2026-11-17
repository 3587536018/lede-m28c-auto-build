# Pinned erofs-utils source archive

`erofs-utils-1.8.10.tar.gz` is the unchanged upstream source archive used by
the locked LEDE `tools/erofs-utils/Makefile`. It is 201,240 bytes, with SHA256
`05eb4edebe11decce6ecb34e98d2f80c8cd283c2f2967d8ba7efd58418570514`, exactly
matching the existing `PKG_HASH`.

It was recovered from the official snapshot URL with `?download=1` after
build #116's normal source download failed. The full public URL, retrieval
time and pinned Makefile provenance are recorded in `erofs-utils-1.8.10.json`.

Before `make download`, `scripts/seed-erofs-source.py` verifies the locked
LEDE revision, complete upstream Makefile, version/hash definition and this
archive. It seeds only a missing `lede/dl/erofs-utils-1.8.10.tar.gz` by atomic
publication of a verified file. An existing valid cache stays untouched;
corruption, source drift and symlink paths stop the build. The helper performs
no download, extraction or archive-code execution.

The tool version, upstream build definition and `sources.lock.json` are
unchanged. Normal OpenWrt source verification and build rules still apply.
The archive includes its upstream license files.
