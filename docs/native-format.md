# Native Blood MAP v7

`bloodforge.native` reads and writes the native Blood version `0x0700` map
format. It retains each complete extended sector, wall, and sprite record as
opaque bytes, as well as the decrypted 128-byte extra header. It rebuilds the
file framing and CRC when writing. Unsupported versions, impossible sizes,
truncation, trailing data, bad CRCs, and fields outside their wire ranges raise
`NativeMapError`.

## On-disk layout

All values are little-endian. The file consists of the four-byte `BLM\x1a`
signature, a 16-bit version, a 37-byte main header, a 128-byte extra header,
`2 ** sky_bits` signed 16-bit sky offsets, sector records (40 bytes), wall
records (32 bytes), sprite records (44 bytes), and a final CRC-32 over all
preceding bytes. Main and extra headers, sky offsets, and base records are
obfuscated by XORing each byte with the low byte of an incrementing key.
Version 7 uses key `0x7474614d` for the main header; the extra-header key is the
wall count; the sky key is its byte length; sector, wall, and sprite keys are
derived from the map revision and the base record sizes. An extended record
immediately follows each base record whose signed `extra` value is positive.
Its size comes from the extra header. The writer initializes absent opaque
extended records with zero bytes.

The parser's basic models expose familiar Build/Blood fields through
`Sector`, `Wall`, and `Sprite`. The remaining engine-specific extension
records are intentionally opaque and preserved byte for byte.

## Source and licensing notes

The wire layout and field widths were cross-checked against the read-only
`llmapper/docs/format.md` notes and `llmapper/bloodmap/format.py`, and against
NBlood's loader and record declarations in `source/blood/src/db.cpp`,
`source/blood/src/common_game.h`, and `source/build/include/build.h`. NBlood's
source identifies itself as GPL version 2 in its file headers. No NBlood
implementation code was copied into BloodForge; this module independently
packs the documented scalar fields and preserves opaque record payloads.

The inspected llmapper checkout had no top-level `LICENSE` or `COPYING` file.
Its parser and writer were treated as read-only reference material, not copied
or imported. BloodForge includes no commercial map or game assets.
