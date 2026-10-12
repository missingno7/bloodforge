import unittest
import zlib

from bloodforge.native import (
    NativeMap, NativeMapError, Sector, Sprite, Wall, encode_map, parse_map,
)


class NativeMapTests(unittest.TestCase):
    def test_synthetic_records_roundtrip_and_crc(self):
        sector = Sector(wallptr=0, wallnum=4, floorz=4096, extra=2,
                        lotag=17, extra_data=bytes(range(60)))
        walls = [
            Wall(x=0, y=0, point2=1, extra=1, extra_data=b"W" * 24),
            Wall(x=512, y=0, point2=2),
            Wall(x=512, y=512, point2=3),
            Wall(x=0, y=512, point2=0),
        ]
        sprite = Sprite(x=128, y=256, z=1024, sectnum=0, statnum=6, ang=512,
                        extra=1, extra_data=b"S" * 56)
        board = NativeMap(sectors=[sector], walls=walls, sprites=[sprite],
                          start_x=256, start_y=256, start_z=0, start_sector=0)

        encoded = encode_map(board)
        self.assertEqual(zlib.crc32(encoded[:-4]) & 0xFFFFFFFF,
                         int.from_bytes(encoded[-4:], "little"))
        decoded = parse_map(encoded)
        self.assertEqual(decoded.sectors, [sector])
        self.assertEqual(decoded.walls, walls)
        self.assertEqual(decoded.sprites, [sprite])
        self.assertEqual(encode_map(decoded), encoded)

    def test_preserves_opaque_extra_header_and_extension_tails(self):
        raw_header = bytearray(NativeMap().raw_extra_header)
        raw_header[12:28] = b"opaque-header-12"
        raw_header[64:76] = (56).to_bytes(4, "little") + (24).to_bytes(4, "little") + (60).to_bytes(4, "little")
        board = NativeMap(sectors=[Sector(wallnum=3, extra=1, extra_data=b"x" * 60)],
                          walls=[Wall(point2=1), Wall(point2=2), Wall(point2=0)],
                          raw_extra_header=bytes(raw_header))
        decoded = parse_map(encode_map(board))
        self.assertEqual(decoded.raw_extra_header, bytes(raw_header))
        self.assertEqual(decoded.sectors[0].extra_data, b"x" * 60)

    def test_rejects_bad_crc_truncation_and_unknown_version(self):
        encoded = encode_map(NativeMap())
        with self.assertRaisesRegex(NativeMapError, "CRC mismatch"):
            parse_map(encoded[:-1] + bytes([encoded[-1] ^ 1]))
        truncated = encode_map(NativeMap(sectors=[Sector()]))[:-5]
        truncated += (zlib.crc32(truncated) & 0xFFFFFFFF).to_bytes(4, "little")
        with self.assertRaisesRegex(NativeMapError, "truncated"):
            parse_map(truncated)
        unsupported = bytearray(encoded)
        unsupported[4:6] = (0x0800).to_bytes(2, "little")
        unsupported[-4:] = (zlib.crc32(unsupported[:-4]) & 0xFFFFFFFF).to_bytes(4, "little")
        with self.assertRaisesRegex(NativeMapError, "unsupported Blood MAP version"):
            parse_map(unsupported)

    def test_rejects_extension_size_and_sky_count_mismatch(self):
        board = NativeMap()
        board.raw_extra_header = bytearray(board.raw_extra_header)
        board.raw_extra_header[68:72] = (8).to_bytes(4, "little", signed=True)
        with self.assertRaisesRegex(NativeMapError, "XWALL record size"):
            encode_map(board)
        board = NativeMap(sky_bits=1)
        with self.assertRaisesRegex(NativeMapError, "sky_offsets"):
            encode_map(board)


if __name__ == "__main__":
    unittest.main()
