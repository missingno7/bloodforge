"""Reader and deterministic writer for Blood's native version 7 MAP files.

The implementation is based on the documented byte layout and independently
packs fields with :mod:`struct`; no game or third-party implementation code is
included here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct
import zlib


SIGNATURE = b"BLM\x1a"
VERSION = 0x0700
_HEADER_KEY = 0x7474614D
_MAX_COUNT = 32767
_MAX_EXTRA_SIZE = 4096


class NativeMapError(ValueError):
    """A malformed, unsupported, or unrepresentable native Blood MAP."""


@dataclass
class Sector:
    wallptr: int = 0
    wallnum: int = 0
    ceilingz: int = 0
    floorz: int = 8192
    ceilingstat: int = 0
    floorstat: int = 0
    ceilingpicnum: int = 0
    ceilingheinum: int = 0
    ceilingshade: int = 0
    ceilingpal: int = 0
    ceilingxpanning: int = 0
    ceilingypanning: int = 0
    floorpicnum: int = 0
    floorheinum: int = 0
    floorshade: int = 0
    floorpal: int = 0
    floorxpanning: int = 0
    floorypanning: int = 0
    visibility: int = 0
    filler: int = 0
    lotag: int = 0
    hitag: int = 0
    extra: int = -1
    extra_data: bytes = b""


@dataclass
class Wall:
    x: int = 0
    y: int = 0
    point2: int = 0
    nextwall: int = -1
    nextsector: int = -1
    cstat: int = 0
    picnum: int = 0
    overpicnum: int = 0
    shade: int = 0
    pal: int = 0
    xrepeat: int = 8
    yrepeat: int = 8
    xpanning: int = 0
    ypanning: int = 0
    lotag: int = 0
    hitag: int = 0
    extra: int = -1
    extra_data: bytes = b""


@dataclass
class Sprite:
    x: int = 0
    y: int = 0
    z: int = 0
    cstat: int = 0
    picnum: int = 0
    shade: int = 0
    pal: int = 0
    clipdist: int = 32
    filler: int = 0
    xrepeat: int = 32
    yrepeat: int = 32
    xoffset: int = 0
    yoffset: int = 0
    sectnum: int = -1
    statnum: int = 0
    ang: int = 0
    owner: int = -1
    index: int = 0
    yvel: int = 0
    zvel: int = 0
    lotag: int = 0
    hitag: int = 0
    extra: int = -1
    extra_data: bytes = b""


def _default_extra_header() -> bytes:
    raw = bytearray(128)
    struct.pack_into("<iii", raw, 64, 56, 24, 60)
    return bytes(raw)


@dataclass
class NativeMap:
    sectors: list[Sector] = field(default_factory=list)
    walls: list[Wall] = field(default_factory=list)
    sprites: list[Sprite] = field(default_factory=list)
    start_x: int = 0
    start_y: int = 0
    start_z: int = 0
    start_angle: int = 0
    start_sector: int = -1
    version: int = VERSION
    visibility: int = 0
    sky_type: int = 0
    revision: int = 0
    sky_bits: int = 0
    sky_offsets: list[int] = field(default_factory=lambda: [0])
    matt_id: int = _HEADER_KEY
    raw_extra_header: bytes = field(default_factory=_default_extra_header)


# Wire descriptions, using explicit little-endian scalar widths.
_MAIN = struct.Struct("<iiihhhiibihhh")
_SECTOR = struct.Struct("<hhiiHHhhbBBBhhbBBBBBhhh")
_WALL = struct.Struct("<iihhhHhhbBBBBBhhh")
_SPRITE = struct.Struct("<iiiHhbBBBBBbbhhhhhhhhhh")
assert _MAIN.size == 37
assert _SECTOR.size == 40
assert _WALL.size == 32
assert _SPRITE.size == 44


def _crypt(data: bytes, key: int) -> bytes:
    return bytes(value ^ ((key + offset) & 0xFF) for offset, value in enumerate(data))


def _need(data: bytes, offset: int, size: int, label: str) -> tuple[bytes, int]:
    end = offset + size
    if size < 0 or end > len(data):
        raise NativeMapError(f"truncated {label} at offset 0x{offset:x} (need {size} bytes)")
    return data[offset:end], end


def _sizes(raw: bytes) -> tuple[int, int, int]:
    if len(raw) != 128:
        raise NativeMapError("version 7 extra header must be 128 bytes")
    xsprite, xwall, xsector = struct.unpack_from("<iii", raw, 64)
    sizes = (xsector, xwall, xsprite)
    minimums = (60, 24, 56)
    for label, size, minimum in zip(("XSECTOR", "XWALL", "XSPRITE"), sizes, minimums):
        if not minimum <= size <= _MAX_EXTRA_SIZE:
            raise NativeMapError(f"unsupported {label} record size {size}")
    return sizes


def _unpack_record(codec: struct.Struct, data: bytes, names: tuple[str, ...], cls: type,
                   extra_data: bytes = b""):
    return cls(**dict(zip(names, codec.unpack(data))), extra_data=extra_data)


_SECTOR_NAMES = ("wallptr", "wallnum", "ceilingz", "floorz", "ceilingstat", "floorstat",
                 "ceilingpicnum", "ceilingheinum", "ceilingshade", "ceilingpal",
                 "ceilingxpanning", "ceilingypanning", "floorpicnum", "floorheinum",
                 "floorshade", "floorpal", "floorxpanning", "floorypanning", "visibility",
                 "filler", "lotag", "hitag", "extra")
_WALL_NAMES = ("x", "y", "point2", "nextwall", "nextsector", "cstat", "picnum",
               "overpicnum", "shade", "pal", "xrepeat", "yrepeat", "xpanning",
               "ypanning", "lotag", "hitag", "extra")
_SPRITE_NAMES = ("x", "y", "z", "cstat", "picnum", "shade", "pal", "clipdist", "filler",
                 "xrepeat", "yrepeat", "xoffset", "yoffset", "sectnum", "statnum", "ang",
                 "owner", "index", "yvel", "zvel", "lotag", "hitag", "extra")


def parse_map(data: bytes | bytearray | memoryview) -> NativeMap:
    """Parse Blood native MAP v7 bytes, checking the file CRC and all bounds."""
    data = bytes(data)
    if len(data) < 6 + _MAIN.size + 128 + 4:
        raise NativeMapError("file is too short to be a Blood MAP v7")
    if data[:4] != SIGNATURE:
        raise NativeMapError(f"invalid Blood MAP signature {data[:4]!r}")
    version = struct.unpack_from("<H", data, 4)[0]
    if version != VERSION:
        raise NativeMapError(f"unsupported Blood MAP version 0x{version:04x}; only 0x0700 is supported")
    stored_crc = struct.unpack_from("<I", data, len(data) - 4)[0]
    actual_crc = zlib.crc32(data[:-4]) & 0xFFFFFFFF
    if stored_crc != actual_crc:
        raise NativeMapError(f"CRC mismatch: stored {stored_crc:08x}, computed {actual_crc:08x}")

    offset = 6
    raw, offset = _need(data, offset, _MAIN.size, "main header")
    (start_x, start_y, start_z, start_angle, start_sector, sky_bits, visibility, matt_id,
     sky_type, revision, nsectors, nwalls, nsprites) = _MAIN.unpack(_crypt(raw, _HEADER_KEY))
    for name, count in (("sectors", nsectors), ("walls", nwalls), ("sprites", nsprites)):
        if not 0 <= count <= _MAX_COUNT:
            raise NativeMapError(f"invalid {name} count {count}")
    if not 0 <= sky_bits <= 8:
        raise NativeMapError(f"unsafe sky_bits value {sky_bits}")

    raw, offset = _need(data, offset, 128, "extra header")
    raw_extra_header = _crypt(raw, nwalls)
    sector_extra_size, wall_extra_size, sprite_extra_size = _sizes(raw_extra_header)
    sky_size = (1 << sky_bits) * 2
    raw, offset = _need(data, offset, sky_size, "sky offsets")
    sky_offsets_data = _crypt(raw, sky_size)
    sky_offsets = list(struct.unpack("<" + "h" * (1 << sky_bits), sky_offsets_data))

    def records(count: int, codec: struct.Struct, names: tuple[str, ...], kind: str,
                key: int, extra_size: int, cls: type):
        nonlocal offset
        output = []
        for index in range(count):
            raw_record, offset = _need(data, offset, codec.size, f"{kind}[{index}]")
            values = codec.unpack(_crypt(raw_record, key))
            # The final short is the `extra` owner index. Positive values name an
            # extended record; zero and negative values have none.
            has_extra = values[-1] > 0
            opaque = b""
            if has_extra:
                opaque, offset = _need(data, offset, extra_size, f"{kind}[{index}] extension")
            output.append(cls(**dict(zip(names, values)), extra_data=opaque))
        return output

    sectors = records(nsectors, _SECTOR, _SECTOR_NAMES, "sector", revision * 40,
                      sector_extra_size, Sector)
    walls = records(nwalls, _WALL, _WALL_NAMES, "wall", (revision * 40) | _HEADER_KEY,
                    wall_extra_size, Wall)
    sprites = records(nsprites, _SPRITE, _SPRITE_NAMES, "sprite", (revision * 44) | _HEADER_KEY,
                      sprite_extra_size, Sprite)
    if offset != len(data) - 4:
        raise NativeMapError(f"unexpected {len(data) - 4 - offset} bytes before CRC at 0x{offset:x}")
    return NativeMap(sectors=sectors, walls=walls, sprites=sprites, start_x=start_x,
                     start_y=start_y, start_z=start_z, start_angle=start_angle,
                     start_sector=start_sector, version=version, visibility=visibility,
                     sky_type=sky_type, revision=revision, sky_bits=sky_bits,
                     sky_offsets=sky_offsets, matt_id=matt_id, raw_extra_header=raw_extra_header)


def read_map(path: str | Path) -> NativeMap:
    """Read and parse a Blood MAP v7 from *path*."""
    return parse_map(Path(path).read_bytes())


def _pack_checked(codec: struct.Struct, values: tuple[int, ...], label: str) -> bytes:
    try:
        return codec.pack(*values)
    except (struct.error, OverflowError) as exc:
        raise NativeMapError(f"{label} field is outside its on-disk range: {exc}") from exc


def _record_bytes(objects: list, codec: struct.Struct, names: tuple[str, ...], kind: str,
                  key: int, extra_size: int) -> bytes:
    output = bytearray()
    for index, obj in enumerate(objects):
        vals = tuple(getattr(obj, name) for name in names)
        raw = _pack_checked(codec, vals, f"{kind}[{index}]")
        output.extend(_crypt(raw, key))
        if obj.extra > 0:
            extra = bytes(obj.extra_data)
            if not extra:
                extra = bytes(extra_size)
            if len(extra) != extra_size:
                raise NativeMapError(f"{kind}[{index}] extension is {len(extra)} bytes; expected {extra_size}")
            output.extend(extra)
    return bytes(output)


def encode_map(map_data: NativeMap) -> bytes:
    """Encode a version 7 map deterministically and recompute its CRC-32."""
    if map_data.version != VERSION:
        raise NativeMapError(f"unsupported Blood MAP version 0x{map_data.version:04x}; only 0x0700 is supported")
    counts = (len(map_data.sectors), len(map_data.walls), len(map_data.sprites))
    if any(count > _MAX_COUNT for count in counts):
        raise NativeMapError(f"map record counts exceed signed 16-bit limit: {counts}")
    if not 0 <= map_data.sky_bits <= 8:
        raise NativeMapError(f"unsafe sky_bits value {map_data.sky_bits}")
    sky_count = 1 << map_data.sky_bits
    if len(map_data.sky_offsets) != sky_count:
        raise NativeMapError(f"sky_offsets has {len(map_data.sky_offsets)} values, expected {sky_count}")
    extra_header = bytes(map_data.raw_extra_header)
    sector_extra_size, wall_extra_size, sprite_extra_size = _sizes(extra_header)

    main = _pack_checked(_MAIN, (map_data.start_x, map_data.start_y, map_data.start_z,
                                  map_data.start_angle, map_data.start_sector, map_data.sky_bits,
                                  map_data.visibility, map_data.matt_id, map_data.sky_type,
                                  map_data.revision, *counts), "main header")
    body = bytearray(SIGNATURE + struct.pack("<H", VERSION))
    body.extend(_crypt(main, _HEADER_KEY))
    body.extend(_crypt(extra_header, len(map_data.walls)))
    sky = _pack_checked(struct.Struct("<" + "h" * sky_count), tuple(map_data.sky_offsets), "sky offsets")
    body.extend(_crypt(sky, len(sky)))
    body.extend(_record_bytes(map_data.sectors, _SECTOR, _SECTOR_NAMES, "sector",
                              map_data.revision * 40, sector_extra_size))
    body.extend(_record_bytes(map_data.walls, _WALL, _WALL_NAMES, "wall",
                              (map_data.revision * 40) | _HEADER_KEY, wall_extra_size))
    body.extend(_record_bytes(map_data.sprites, _SPRITE, _SPRITE_NAMES, "sprite",
                              (map_data.revision * 44) | _HEADER_KEY, sprite_extra_size))
    body.extend(struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF))
    return bytes(body)


def write_map(map_data: NativeMap, path: str | Path) -> None:
    """Write a deterministic version 7 MAP to *path*."""
    Path(path).write_bytes(encode_map(map_data))
