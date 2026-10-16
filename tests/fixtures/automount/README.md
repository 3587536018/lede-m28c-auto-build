# Pinned automount source fixtures

These are unmodified source files retrieved from the locked revisions in
`sources.lock.json`, not files generated from the patch implementation.

- LEDE `8e247d214f071ef0cb3a31c879d4daf224849d3e`:
  `package/lean/automount/Makefile`, `files/15-automount`,
  `files/zzz-move-automount`; `package/system/fstools/files/mount.hotplug`,
  `files/fstab.default`, and `package/system/fstools/Makefile`.
- Packages `bcc1adfa10c98e5183785d015e4d50ef98f04ab3`:
  `net/samba4/files/smb.auto` and `net/samba4/Makefile`.

The patch checks the normalized LF source SHA256 before changing anything.
