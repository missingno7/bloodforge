"""Evidence-backed structural pattern discovery for native Blood maps.

The initial miner is deliberately narrow: it recognizes only straight runs of
at least three static, axis-aligned rectangular sectors with full-width
reciprocal, non-blocking portal edges and a constant floor rise. A returned
instance is a structural candidate, never proof of gameplay behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .native import NativeMap


@dataclass(frozen=True)
class StairCandidate:
    """A strict staircase candidate with original MAP provenance.

    ``portal_wall_ids`` contains one source-side wall index for each link in
    ``source_sector_indices`` order. ``status`` is intentionally never promoted
    beyond ``candidate`` by this geometry-only miner.
    """

    source_sector_indices: list[int]
    portal_wall_ids: list[int]
    family_signature: str
    parameters: dict[str, Any]
    residuals: list[str]
    limitations: list[str]
    status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StairCandidate":
        """Restore a candidate serialized by :meth:`to_dict`."""
        return cls(
            source_sector_indices=[int(x) for x in value["source_sector_indices"]],
            portal_wall_ids=[int(x) for x in value["portal_wall_ids"]],
            family_signature=str(value["family_signature"]),
            parameters=dict(value["parameters"]),
            residuals=list(value.get("residuals", [])),
            limitations=list(value.get("limitations", [])),
            status=str(value.get("status", "candidate")),
        )


@dataclass(frozen=True)
class _Rect:
    sector: int
    left: int
    top: int
    right: int
    bottom: int
    floor: int
    ceiling: int


def _rectangles(level: NativeMap) -> dict[int, _Rect]:
    """Return only untagged, flat sectors whose polygon is exactly a rectangle."""
    result: dict[int, _Rect] = {}
    for si, sector in enumerate(level.sectors):
        if sector.wallnum != 4 or sector.wallptr < 0 or sector.wallptr + 4 > len(level.walls):
            continue
        # Build sector stat bit 1 marks a sloped plane; nonzero heinum is also
        # rejected so malformed or noncanonical slope metadata cannot pass.
        if sector.floorstat & 2 or sector.ceilingstat & 2 or sector.floorheinum or sector.ceilingheinum:
            continue
        if sector.ceilingz >= sector.floorz:
            continue
        # Tags and XSECTOR data commonly represent mechanisms or triggers.
        if sector.extra > 0 or sector.lotag or sector.hitag:
            continue
        wall_ids = range(sector.wallptr, sector.wallptr + sector.wallnum)
        wall_id_set = set(wall_ids)
        points = [(level.walls[wi].x, level.walls[wi].y) for wi in wall_ids]
        if len(set(points)) != 4:
            continue
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        if left == right or top == bottom or set(points) != {
            (left, top), (right, top), (right, bottom), (left, bottom)
        }:
            continue
        valid_loop = True
        targets: list[int] = []
        for wi in wall_ids:
            wall = level.walls[wi]
            if wall.point2 not in wall_id_set:
                valid_loop = False
                break
            targets.append(wall.point2)
            endpoint = level.walls[wall.point2]
            if wall.x != endpoint.x and wall.y != endpoint.y:
                valid_loop = False
                break
            if (wall.x, wall.y) == (endpoint.x, endpoint.y):
                valid_loop = False
                break
            # Any wall action/tag/extension is outside the static family.
            if wall.lotag or wall.hitag or wall.extra > 0:
                valid_loop = False
                break
        if len(set(targets)) != 4:
            valid_loop = False
        else:
            # Confirm all four walls form one loop, not two disjoint cycles.
            visited: set[int] = set()
            current = sector.wallptr
            while current not in visited and current in wall_id_set:
                visited.add(current)
                current = level.walls[current].point2
            if current != sector.wallptr or len(visited) != 4:
                valid_loop = False
        if not valid_loop:
            continue
        result[si] = _Rect(si, left, top, right, bottom, sector.floorz, sector.ceilingz)
    return result


def _shared_run_edge(a: _Rect, b: _Rect) -> tuple[str, str] | None:
    """Return (axis, direction from a to b) for full-edge adjacent equal boxes."""
    if (a.top, a.bottom) == (b.top, b.bottom):
        if a.right == b.left:
            return "x", "east"
        if b.right == a.left:
            return "x", "west"
    if (a.left, a.right) == (b.left, b.right):
        if a.bottom == b.top:
            return "y", "south"
        if b.bottom == a.top:
            return "y", "north"
    return None


def _portal_links(level: NativeMap, rects: dict[int, _Rect]) -> dict[tuple[int, int], int]:
    """Find exact reciprocal portals whose edges connect matching rectangles."""
    links: dict[tuple[int, int], int] = {}
    for si, rect in rects.items():
        sector = level.sectors[si]
        for wi in range(sector.wallptr, sector.wallptr + sector.wallnum):
            wall = level.walls[wi]
            sj = wall.nextsector
            if sj not in rects or sj == si or not 0 <= wall.nextwall < len(level.walls):
                continue
            target = level.sectors[sj]
            if not target.wallptr <= wall.nextwall < target.wallptr + target.wallnum:
                continue
            # Bit 0 is the blocking bit. Require the reciprocal references and
            # reversed, exact edge endpoints instead of trusting nextsector.
            if wall.cstat & 1:
                continue
            back = level.walls[wall.nextwall]
            if back.nextwall != wi or back.nextsector != si or back.cstat & 1:
                continue
            if not target.wallptr <= back.point2 < target.wallptr + target.wallnum:
                continue
            end = level.walls[wall.point2]
            back_end = level.walls[back.point2]
            if (wall.x, wall.y) != (back_end.x, back_end.y) or (end.x, end.y) != (back.x, back.y):
                continue
            if _shared_run_edge(rect, rects[sj]) is None:
                continue
            links[(si, sj)] = wi
    return links


def discover_stairs(level: NativeMap) -> list[StairCandidate]:
    """Discover strict straight staircase candidates in *level*.

    Source order follows ascent: native floor z decreases by a constant
    positive ``rise`` at each portal. Coordinates use Build's native x/y/z;
    origin is the minimum-x/minimum-y corner of the first (lowest) tread.
    """
    rects = _rectangles(level)
    links = _portal_links(level, rects)
    # Directed edge source -> destination is always in the ascent direction.
    eligible_edges: list[tuple[int, int, int, str]] = []
    for (a_idx, b_idx), portal in links.items():
        a, b = rects[a_idx], rects[b_idx]
        orientation = _shared_run_edge(a, b)
        if orientation is None or a.ceiling != b.ceiling or b.floor >= a.floor:
            continue
        axis, cardinal = orientation
        if axis == "x" and (a.bottom - a.top) != (b.bottom - b.top):
            continue
        if axis == "y" and (a.right - a.left) != (b.right - b.left):
            continue
        eligible_edges.append((a_idx, b_idx, portal, cardinal))

    outgoing: dict[int, int] = {}
    incoming: dict[int, int] = {}
    for source, destination, _portal, _direction in eligible_edges:
        outgoing[source] = outgoing.get(source, 0) + 1
        incoming[destination] = incoming.get(destination, 0) + 1
    # Drop every edge touching a fork or merge, rather than choosing one based
    # on record order. This keeps evidence deterministic and conservative.
    unambiguous = [
        edge for edge in eligible_edges
        if outgoing[edge[0]] == 1 and incoming[edge[1]] == 1
    ]
    next_step: dict[int, tuple[int, int, str]] = {}
    previous: dict[int, int] = {}
    for source, destination, portal, cardinal in unambiguous:
        next_step[source] = (destination, portal, cardinal)
        previous[destination] = source

    candidates: list[StairCandidate] = []
    consumed: set[int] = set()
    for start in sorted(rects):
        if start in previous or start in consumed or start not in next_step:
            continue
        indices = [start]
        portals: list[int] = []
        directions: list[str] = []
        while indices[-1] in next_step:
            dst, portal, cardinal = next_step[indices[-1]]
            if dst in indices:
                break
            indices.append(dst)
            portals.append(portal)
            directions.append(cardinal)
        if len(indices) < 3 or len(set(directions)) != 1:
            continue
        run = [rects[i] for i in indices]
        widths = {r.bottom - r.top if directions[0] in ("east", "west") else r.right - r.left for r in run}
        treads = {r.right - r.left if directions[0] in ("east", "west") else r.bottom - r.top for r in run}
        rises = {run[i].floor - run[i + 1].floor for i in range(len(run) - 1)}
        ceilings = {r.ceiling for r in run}
        if len(widths) != 1 or len(treads) != 1 or len(rises) != 1 or len(ceilings) != 1:
            continue
        # Every portal edge must be the full shared edge, verified by exact
        # endpoint reciprocity; ensure floors are strictly monotone by the rise.
        width, tread, rise = next(iter(widths)), next(iter(treads)), next(iter(rises))
        if width <= 0 or tread <= 0 or rise <= 0:
            continue
        direction = directions[0]
        first = run[0]
        params = {
            "count": len(run), "width": width, "tread": tread, "rise": rise,
            "floor_z": first.floor, "ceiling_z": first.ceiling,
            "origin_x": first.left, "origin_y": first.top,
            "direction": direction,
        }
        candidates.append(StairCandidate(
            source_sector_indices=indices,
            portal_wall_ids=portals,
            family_signature="stairs/rect-axis/straight/v1",
            parameters=params,
            residuals=["0 native coordinate units for rectangle, portal, and step-parameter fit"],
            limitations=[
                "Static structural candidate only; no runtime traversal or playability claim.",
                "Detector covers straight axis-aligned runs of flat four-wall rectangles only.",
            ],
            status="candidate",
        ))
        consumed.update(indices)
    return candidates
