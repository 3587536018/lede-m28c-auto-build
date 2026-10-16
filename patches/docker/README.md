# Docker package integration

`package.json` pins the unmodified inputs in `coolsnowwolf/packages` commit
`bcc1adfa10c98e5183785d015e4d50ef98f04ab3`, directory `utils/dockerd`.
The byte-exact inputs are retained in `upstream/` for offline regression tests.
Their upstream license and attribution remain applicable; the Docker package
declares Apache-2.0, with Docker's upstream LICENSE installed by its source build.

The customization verifies original/patched SHA256 hashes and unique anchors
before writing any file. It changes the package install recipe itself, so both
the root filesystem and the matching `dockerd_29.6.1-1.m28c1` IPK carry the init,
DNS helper, first-boot policy and nft include. Nothing overrides `/etc/init.d/dockerd`
through the image overlay.

The nftables policy follows the actual #110 device fix. Configuration changes
are intentionally restricted to the default bridge. See `docs/DOCKER-FIREWALL.md`
for retained settings, migration and validation boundaries.
