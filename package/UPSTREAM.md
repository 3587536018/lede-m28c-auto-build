# Additional software sources

The USB rebinding changes under `patches/qmodem` apply to FUjr/QModem commit
`f5035b7d6d643666556e5c3fe7e765b3ebe79379`. Original test fixtures retain its
complete upstream LICENSE (MPL 2.0 with an additional prohibition on commercial
use). These sources and their modifications are not relicensed by the root
repository's MIT license. The standalone new USB identity header has its own
MIT notice. See `docs/QMODEM-USB-REBIND.md` for the changed behavior.

Homebox integration files and the LuCI application are from
`jjm2473/openwrt-apps`, commit `e2e8e742f5ce3265445d0540dae2fa4c60976db5`.
The LuCI Makefile is adjusted to use this build's `feeds/luci/luci.mk`.

The ARM64 Homebox executable is downloaded at build time from the original
`XGHeaven/homebox` release `v0.0.0-dev.2020062901`, with a pinned SHA-256.
This avoids the upstream recipe's unpinned npm dependencies and `go install @latest`.
The inspected device runs this Homebox generation. No device binaries or data
are copied into the repository.

The original release source archive has no standalone license file; this recipe
does not claim a license for the upstream binary. The LuCI source retains its
Apache-2.0 notices. The new `luci-app-m28c` dashboard uses the repository's MIT license.
