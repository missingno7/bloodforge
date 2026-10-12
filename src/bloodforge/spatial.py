"""Source-backed spatial relationships; neither native truth nor authored intent.

No room taxonomy, visibility flood, collision simulation, or outside-space solid
model is implied. Region memberships deliberately overlap across evidence views.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import math
from pathlib import Path

from .native import NativeMap, read_map
from .store import canonical_json, database, digest
from .validation import validate_map

SPATIAL_VERSION = "spatial-understanding-1.1.0"
DEFAULT_POLICY = {"broad_opening_ratio": 0.5, "expansion_area_ratio": 4.0}
LIMITATIONS = [
    "Native portal adjacency is neither navigability nor visibility.",
    "Groups are competing continuity hypotheses, not rooms or a disjoint hierarchy.",
    "XY polygons use floating-point GEOS geometry; Z is analyzed separately.",
    "Sloped openings, dynamic X-record behavior, underwater state and room-over-room effects remain unresolved.",
    "Sector envelopes do not subtract sprite/actor volumes or prove player clearance.",
    "Sky flags do not establish an enclosed ceiling or a finite physical volume.",
    "Unmapped XY and polygon holes are not inferred to be solid masses or intentional empty space.",
    "Visibility and experiential interpretation require separate viewpoint evidence.",
]


def _components(nodes: list[int], edges: list[tuple[int, int]]) -> list[list[int]]:
    graph = {node: set() for node in nodes}
    for a, b in edges:
        if a in graph and b in graph:
            graph[a].add(b)
            graph[b].add(a)
    remaining = set(nodes)
    result = []
    while remaining:
        pending = [min(remaining)]
        group = set(pending)
        remaining.difference_update(group)
        while pending:
            node = pending.pop()
            unseen = graph[node] & remaining
            remaining.difference_update(unseen)
            group.update(unseen)
            pending.extend(sorted(unseen))
        result.append(sorted(group))
    return result


def _validated_scope(level: NativeMap, selected: list[int], *, explicit: bool) -> tuple[dict, dict]:
    """Prove selected topology while retaining failures elsewhere unchanged.

    Native records and complete, unique wall ownership remain global gates.
    An explicit selection may exclude malformed loops and unrelated portals,
    but every directed portal reference touching it must still be valid.
    Polygon validity is checked separately by ``sector_geometry``.
    """
    validation = validate_map(level)
    dimensions = {key: {field: validation[key][field] for field in ("status", "error_count", "issues")}
                  for key in ("native", "geometry", "portals")}
    for dimension in dimensions:
        if dimensions[dimension]["error_count"] and (dimension == "native" or not explicit):
            raise ValueError(f"spatial analysis requires valid native geometry/portals: {dimension}")
    # A valid range can contain a degenerate polygon. Do not conflate ownership
    # of its records with geometric support, or overwrite duplicate owners.
    ownership_codes = {"sector_wall_range_out_of_bounds", "wall_owned_by_multiple_sectors", "orphan_wall"}
    for issue in dimensions["geometry"]["issues"]:
        if issue["code"] in ownership_codes:
            raise ValueError(f"spatial analysis requires complete unambiguous wall ownership: {issue}")
    owner = {wi: si for si, sector in enumerate(level.sectors)
             for wi in range(sector.wallptr, sector.wallptr + sector.wallnum)}
    chosen = set(selected)
    selected_walls = {wi for wi, si in owner.items() if si in chosen}
    # Include incoming references even when the selected wall is one-sided or
    # points elsewhere. Looking only at outgoing portals would hide corruption.
    incident = selected_walls | {wi for wi, wall in enumerate(level.walls)
                                if wall.nextwall in selected_walls or wall.nextsector in chosen}
    incident |= {level.walls[wi].nextwall for wi in selected_walls
                 if 0 <= level.walls[wi].nextwall < len(level.walls)}
    for issue in dimensions["portals"]["issues"]:
        if issue["index"] in incident:
            raise ValueError(f"selected boundary has invalid incident portal: {issue}")
    for wi in incident:
        wall = level.walls[wi]
        if wall.nextwall >= 0 and owner.get(wall.point2) != owner[wi]:
            raise ValueError(f"selected boundary portal endpoint escapes its owning sector: wall {wi}")
    # Scalar inverted Z is rejected for selected and boundary-neighbor sectors
    # below, including the effective-flat zero-heinum case the validator omits.
    error_count = sum(row["error_count"] for row in dimensions.values())
    return owner, {
        "mode": "explicit-sector-selection" if explicit else "whole-map",
        "selected_polygon_geometry": "supported",
        "wall_ownership": "globally_complete_and_unambiguous",
        "incident_portals": "valid_in_both_directions",
        "whole_map_structural_gate_passed": error_count == 0,
        "whole_map_error_count": error_count,
        "whole_map_dimensions": dimensions,
        "interpretation": "support is limited to selected polygons and incident portals; whole-map structural findings are retained",
    }


def analyze_space(level: NativeMap, source_sha256: str, sectors: list[int] | None = None,
                  *, policy: dict | None = None) -> dict:
    """Measure a selected native region and its boundary against the whole map.

    Source identity must come from the bytes parsed by the caller. This API is
    deterministic; ``inspect_space`` binds it to files and indexed provenance.
    """
    try:
        import shapely
        from shapely.ops import unary_union
        from .spatial_geometry import sector_geometry
    except ImportError as exc:
        raise ValueError('spatial analysis requires pip install "bloodforge[spatial]"') from exc
    rules = dict(DEFAULT_POLICY)
    if policy:
        if set(policy) - set(rules):
            raise ValueError("unknown spatial policy field")
        rules.update(policy)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
           for v in rules.values()):
        raise ValueError("spatial policy values must be finite and positive")
    selected = list(range(len(level.sectors))) if sectors is None else list(sectors)
    if not selected or any(type(i) is not int or not 0 <= i < len(level.sectors) for i in selected):
        raise ValueError("select at least one valid native sector")
    selected = sorted(set(selected))
    owner, scope_validation = _validated_scope(level, selected, explicit=sectors is not None)
    chosen = set(selected)
    needed = chosen | {w.nextsector for wi, w in enumerate(level.walls)
                       if owner[wi] in chosen and w.nextsector >= 0}
    polygons = {}
    geometry_errors = []
    for i in sorted(needed):
        try:
            polygons[i] = sector_geometry(level, i)
        except ValueError as exc:
            if i in chosen:
                raise ValueError(f"selected geometry unsupported: {exc}") from exc
            geometry_errors.append({"sector": i, "scope": "neighbor", "reason": str(exc)})
    facts = {}
    for i in sorted(needed):
        sector, polygon = level.sectors[i], polygons.get(i)
        flat = not ((sector.floorstat & 2 and sector.floorheinum != 0)
                    or (sector.ceilingstat & 2 and sector.ceilingheinum != 0))
        ordered_z = sector.ceilingz <= sector.floorz if flat else None
        if flat and not ordered_z:
            reason = f"effective-flat sector {i} has inverted ceiling/floor Z bounds"
            if i in chosen:
                raise ValueError(reason)
            geometry_errors.append({"sector": i, "scope": "neighbor", "reason": reason})
        sky = bool((sector.floorstat | sector.ceilingstat) & 1)
        parts = ([] if polygon is None else [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms))
        sprites = [j for j, item in enumerate(level.sprites) if item.sectnum == i]
        facts[i] = {
            "id": i, "evidence_kind": "derived-native-geometry", "walls": list(range(sector.wallptr, sector.wallptr + sector.wallnum)),
            "xy_area": polygon.area if polygon is not None else None,
            "xy_perimeter": polygon.length if polygon is not None else None,
            "xy_bounds": list(polygon.bounds) if polygon is not None else None,
            "polygon_parts": len(parts) if polygon is not None else None,
            "hole_count": sum(len(p.interiors) for p in parts) if polygon is not None else None,
            "hole_area": sum(shapely.Polygon(ring).area for p in parts for ring in p.interiors) if polygon is not None else None,
            "flat": flat, "flat_z_bounds_valid": ordered_z, "sky_or_parallax": sky,
            "native_slope_flags": [bool(sector.floorstat & 2), bool(sector.ceilingstat & 2)],
            "floor_z": sector.floorz, "ceiling_z": sector.ceilingz,
            "flat_envelope_volume_xy2_z": polygon.area * (sector.floorz - sector.ceilingz) if flat and ordered_z and not sky and polygon is not None else None,
            "floor_finish": [sector.floorpicnum, sector.floorpal],
            "ceiling_finish": [sector.ceilingpicnum, sector.ceilingpal],
            "floor_shade": sector.floorshade, "ceiling_shade": sector.ceilingshade,
            "opaque_extension_present": bool(sector.extra_data),
            "sprite_ids": sprites,
            "blocking_sprite_ids": [j for j in sprites if level.sprites[j].cstat & 1],
            "navigation": "unverified", "intentional_emptiness": "not_established",
        }
    openings = []
    for wi, wall in enumerate(level.walls):
        if wall.nextwall < 0 or wi > wall.nextwall:
            continue
        a, b = owner[wi], wall.nextsector
        if a not in chosen and b not in chosen:
            continue
        other, end = level.walls[wall.nextwall], level.walls[wall.point2]
        sa, sb = level.sectors[a], level.sectors[b]
        native_flat = facts[a]["flat"] and facts[b]["flat"]
        flat = native_flat and facts[a]["flat_z_bounds_valid"] and facts[b]["flat_z_bounds_valid"]
        width = math.hypot(end.x - wall.x, end.y - wall.y)
        common_height = min(sa.floorz, sb.floorz) - max(sa.ceilingz, sb.ceilingz) if flat else None
        ratio = width / math.sqrt(min(polygons[a].area, polygons[b].area)) if a in polygons and b in polygons else None
        same_planes = flat and (sa.floorz, sa.ceilingz) == (sb.floorz, sb.ceilingz)
        movement_flags = bool((wall.cstat | other.cstat) & 1)
        renderer_flags = bool((wall.cstat | other.cstat) & (16 | 32))
        open_flat = flat and common_height > 0
        openings.append({
            "id": f"portal:{wi}:{wall.nextwall}", "evidence_kind": "derived-native-boundary",
            "sectors": [a, b], "walls": [wi, wall.nextwall],
            "endpoint_geometry": [{"sector": i, "xy_polygon_supported": i in polygons,
                                   "flat_z_bounds_valid": facts[i]["flat_z_bounds_valid"]} for i in (a, b)],
            "aperture_evidence_scope": "reciprocal wall segment and ordered flat base Z only; no polygon-interior or movement proof",
            "endpoints": [[wall.x, wall.y], [end.x, end.y]], "width_xy": width,
            "flat_opening_z": common_height, "floor_delta_z": sb.floorz - sa.floorz if flat else None,
            "ceiling_delta_z": sb.ceilingz - sa.ceilingz if flat else None,
            "native_wall_flags": [wall.cstat, other.cstat], "movement_blocking_flag": movement_flags,
            "masked_or_one_way_flag": renderer_flags, "flat_positive_opening": open_flat,
            "opaque_extensions_present": any(bool(o.extra_data) for o in (sa, sb, wall, other)),
            "opening_width_to_smaller_area_scale": ratio,
            "same_flat_planes": same_planes,
            "same_floor_finish": facts[a]["floor_finish"] == facts[b]["floor_finish"],
            "same_ceiling_finish": facts[a]["ceiling_finish"] == facts[b]["ceiling_finish"],
            "shade_delta": [sb.floorshade - sa.floorshade, sb.ceilingshade - sa.ceilingshade],
            "static_point_connection": "blocked_by_native_flag" if movement_flags else "positive_flat_aperture_only" if open_flat else "closed_flat_aperture" if flat else "unknown_invalid_z_bounds" if native_flat else "unknown_slope",
            "navigation": "unverified", "visibility": "not_observed", "threshold_depth": "not_measured",
        })
    region_rows = []
    for view in ("coplanar", "finish"):
        eligible = [o for o in openings if o["flat_positive_opening"] and not o["movement_blocking_flag"]
                    and not o["masked_or_one_way_flag"] and (
                        o["same_flat_planes"] and o["opening_width_to_smaller_area_scale"] is not None and o["opening_width_to_smaller_area_scale"] >= rules["broad_opening_ratio"]
                        if view == "coplanar" else o["same_floor_finish"] and o["same_ceiling_finish"])]
        for members in _components(selected, [tuple(o["sectors"]) for o in eligible]):
            member_set = set(members)
            boundary = [wi for wi, wall in enumerate(level.walls) if owner[wi] in member_set and wall.nextsector not in member_set]
            internal = [o["id"] for o in eligible if set(o["sectors"]) <= member_set]
            boundary_openings = [o for o in openings if len(set(o["sectors"]) & member_set) == 1]
            union = unary_union([polygons[i] for i in members])
            total_area = sum(polygons[i].area for i in members)
            lengths = Counter()
            for wi in boundary:
                wall = level.walls[wi]
                end = level.walls[wall.point2]
                lengths["one_sided" if wall.nextwall < 0 else "portal_boundary"] += math.hypot(end.x - wall.x, end.y - wall.y)
            region_rows.append({
                "id": f"{view}:{digest(members)[:16]}", "view": view, "sectors": members,
                "evidence_kind": "continuity-hypothesis", "supporting_openings": internal,
                "boundary_walls": boundary, "boundary_openings": [o["id"] for o in boundary_openings],
                "xy_union_area": union.area, "summed_sector_area": total_area,
                "xy_overlap_area": max(0.0, total_area - union.area),
                "boundary_lengths_xy": dict(lengths), "sky_sector_count": sum(facts[i]["sky_or_parallax"] for i in members),
                "native_floor_range_z": [min(facts[i]["floor_z"] for i in members), max(facts[i]["floor_z"] for i in members)],
                "sprite_count": sum(len(facts[i]["sprite_ids"]) for i in members),
                "negative_space": {"mapped_envelope_area_xy": union.area,
                                   "navigable_empty_volume": "not_established",
                                   "compositional_emptiness": "hypothesis_requires_design_evidence",
                                   "unmodeled_outside": "not_classified_as_solid"},
                "limitations": ["selection bounds this grouping", "continuity does not establish a single perceived room", "no sprite/actor subtraction or dynamic-state simulation"],
            })
    coplanar = {sector: r for r in region_rows if r["view"] == "coplanar" for sector in r["sectors"]}
    transitions = []
    for opening in openings:
        a, b = opening["sectors"]
        if a not in chosen or b not in chosen or coplanar[a]["id"] == coplanar[b]["id"]:
            continue
        aa, ab = coplanar[a]["xy_union_area"], coplanar[b]["xy_union_area"]
        expansion = max(aa, ab) / min(aa, ab)
        transitions.append({"opening": opening["id"], "walls": opening["walls"], "sectors": [a, b],
                            "regions": [coplanar[a]["id"], coplanar[b]["id"]],
                            "region_area_ratio": expansion,
                            "candidate_expansion_from_sector": (a if aa < ab else b) if expansion >= rules["expansion_area_ratio"] and opening["flat_positive_opening"] and not opening["movement_blocking_flag"] and not opening["masked_or_one_way_flag"] else None,
                            "evidence_kind": "design-hypothesis",
                            "interpretation": "area contrast across threshold; compression/reveal requires route-view evidence",
                            "navigation": "unverified", "visual_reveal": "not_observed"})
    overlaps = []
    for pos, a in enumerate(selected):
        for b in selected[pos + 1:]:
            overlap = polygons[a].intersection(polygons[b]).area
            if overlap <= 0:
                continue
            sa, sb = level.sectors[a], level.sectors[b]
            vertical = min(sa.floorz, sb.floorz) - max(sa.ceilingz, sb.ceilingz) if facts[a]["flat"] and facts[b]["flat"] else None
            overlaps.append({"sectors": [a, b], "xy_intersection_area": overlap,
                             "flat_vertical_overlap_z": vertical, "evidence_kind": "derived-geometry",
                             "interpretation": "overlapping native envelopes; room-over-room/visibility unresolved"})
    counts = {"sectors": len(level.sectors), "walls": len(level.walls), "sprites": len(level.sprites)}
    return {"schema": "bloodforge.spatial-understanding.v1", "extractor": SPATIAL_VERSION,
            "source": {"sha256": source_sha256}, "policy": rules,
            "geometry_engine": {"name": "shapely", "version": shapely.__version__, "geos": shapely.geos_version_string},
            "scope": {"selected_sectors": selected, "neighbor_sectors": sorted(needed - chosen)},
            "scope_validation": scope_validation,
            "geometry_errors": geometry_errors,
            "native_counts": counts, "sectors": [facts[i] for i in selected],
            "openings": openings, "regions": region_rows, "transitions": transitions, "xy_overlaps": overlaps,
            "whole_level": {"reciprocal_components": _components(list(range(len(level.sectors))),
                              [(owner[wi], w.nextsector) for wi, w in enumerate(level.walls) if w.nextwall >= 0 and wi < w.nextwall])
                              if scope_validation["whole_map_structural_gate_passed"] else None,
                            "interpretation": "native adjacency components, not progression routes"
                            if scope_validation["whole_map_structural_gate_passed"] else
                            "withheld: whole-map structural gate failed; local support does not establish global connectivity"},
            "limitations": LIMITATIONS,
            "claims": {"runtime_traversal_verified": False, "design_intent_established": False,
                       "renderer_visibility_observed": False, "bot_used": False}}


def inspect_space(config, map_path: Path, sectors: list[int] | None = None) -> dict:
    """Bind derived evidence to live bytes, corpus provenance, and existing stores."""
    from .evidence import record_tool_evidence
    payload = map_path.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    result = analyze_space(read_map(map_path), sha, sectors)
    if hashlib.sha256(map_path.read_bytes()).hexdigest() != sha:
        raise ValueError("source MAP changed during spatial analysis")
    result["source"]["path"] = str(map_path.resolve())
    with database(config.knowledge_db) as db, db:
        known = db.execute("SELECT 1 FROM contents WHERE sha256=? AND parse_status='ok'", (sha,)).fetchone()
        result["source"]["provenance"] = [dict(row) for row in db.execute(
            "SELECT relative_path,population,tier,author FROM sources WHERE sha256=? AND active=1 ORDER BY population,relative_path", (sha,))]
        result["knowledge_indexed"] = bool(known)
        if known:
            key = digest({"scope": result["scope"], "selection_mode": result["scope_validation"]["mode"],
                          "policy": result["policy"], "engine": result["geometry_engine"]})
            db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)",
                       (sha, SPATIAL_VERSION, "spatial-analysis", key, canonical_json(result)))
    saved = record_tool_evidence(config, result, "spatial-analysis")
    return result | saved


def compact_space(result: dict) -> dict:
    """Question-oriented summary, with full metrics available through its report."""
    return {"source": result["source"], "extractor": result["extractor"], "scope": result["scope"],
            "scope_validation": {key: value for key, value in result["scope_validation"].items()
                                 if key != "whole_map_dimensions"},
            "questions": {
                "which_sectors_may_form_continuous_spaces": [
                    {key: r[key] for key in ("id", "view", "sectors", "xy_union_area", "supporting_openings")}
                    for r in result["regions"] if len(r["sectors"]) > 1],
                "where_are_candidate_transitions": result["transitions"],
                "what_negative_space_is_measured": {"mapped_sector_area_xy": sum(s["xy_area"] for s in result["sectors"]),
                    "hole_count": sum(s["hole_count"] for s in result["sectors"]),
                    "sprite_count": sum(len(s["sprite_ids"]) for s in result["sectors"]),
                    "interpretation": "native envelopes and holes; no navigability or intentional emptiness claim"}},
            "claims": result["claims"], "limitations": result["limitations"],
            "geometry_errors": result["geometry_errors"],
            **{key: result[key] for key in ("report_path", "report_hash", "knowledge_indexed") if key in result}}


def compare_spaces(before: dict, after: dict) -> dict:
    """Compare native-index-preserving interventions, not arbitrary remappings.

    Call require_index_correspondence on both native maps first. Report scope
    and policy equality cannot by themselves establish native index meaning.
    """
    if before["scope"]["selected_sectors"] != after["scope"]["selected_sectors"]:
        raise ValueError("spatial comparison requires the same native sector selection")
    if before["policy"] != after["policy"]:
        raise ValueError("spatial comparison requires the same analysis policy")
    if before["extractor"] != after["extractor"] or before["geometry_engine"] != after["geometry_engine"]:
        raise ValueError("spatial comparison requires the same extractor and geometry engine")
    first = {s["id"]: s for s in before["sectors"]}
    second = {s["id"]: s for s in after["sectors"]}
    first_openings = {tuple(o["walls"]): o for o in before["openings"]}
    second_openings = {tuple(o["walls"]): o for o in after["openings"]}
    changes = []
    for key in sorted(first_openings.keys() & second_openings.keys()):
        changed = {k: {"before": first_openings[key][k], "after": second_openings[key][k]}
                   for k in first_openings[key] if first_openings[key][k] != second_openings[key][k]}
        if changed:
            changes.append({"walls": list(key), "changes": changed})
    return {"schema": "bloodforge.spatial-comparison.v1", "source_hashes": [before["source"]["sha256"], after["source"]["sha256"]],
            "correspondence": "same native indices; only use with an audited index-preserving intervention",
            "scope_validation": {"before": before["scope_validation"], "after": after["scope_validation"]},
            "native_counts_unchanged": before["native_counts"] == after["native_counts"],
            "selected_floor_area_unchanged": all(first[i]["xy_area"] == second[i]["xy_area"] for i in first),
            "portal_pairs_removed": [list(k) for k in sorted(first_openings.keys() - second_openings.keys())],
            "portal_pairs_added": [list(k) for k in sorted(second_openings.keys() - first_openings.keys())],
            "opening_changes": changes,
            "continuity_memberships_unchanged": [(r["view"], r["sectors"]) for r in before["regions"]] == [(r["view"], r["sectors"]) for r in after["regions"]],
            "runtime_verified": False}


def require_index_correspondence(before: NativeMap, after: NativeMap) -> None:
    """v1 comparisons allow flags, surfaces and heights, not XY reallocation."""
    if any(len(getattr(before, name)) != len(getattr(after, name)) for name in ("sectors", "walls", "sprites")):
        raise ValueError("v1 comparison requires unchanged native record counts")
    if any((a.x, a.y, a.point2) != (b.x, b.y, b.point2) for a, b in zip(before.walls, after.walls)):
        raise ValueError("v1 comparison requires unchanged indexed wall XY and point2 loops")
    if any((a.wallptr, a.wallnum) != (b.wallptr, b.wallnum) for a, b in zip(before.sectors, after.sectors)):
        raise ValueError("v1 comparison requires unchanged native sector wall ownership")


def diagram_svg(level: NativeMap, analysis: dict, cameras: list[dict] = ()) -> str:
    """A source-indexed top-down evidence diagram, never an occupancy map."""
    from html import escape
    from .spatial_geometry import sector_geometry
    polygons = {i: sector_geometry(level, i) for i in analysis["scope"]["selected_sectors"]}
    lo_x = min(g.bounds[0] for g in polygons.values())
    lo_y = min(g.bounds[1] for g in polygons.values())
    hi_x = max(g.bounds[2] for g in polygons.values())
    hi_y = max(g.bounds[3] for g in polygons.values())
    scale = 700 / max(hi_x - lo_x, hi_y - lo_y, 1)
    def xy(x, y):
        return 30 + (x - lo_x) * scale, 65 + (y - lo_y) * scale
    body = ['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="840" viewBox="0 0 800 840">',
            '<rect width="800" height="840" fill="#111820"/>',
            '<text x="24" y="28" fill="#ecf1f5" font-family="sans-serif" font-size="18">Native sector footprints and sampled views</text>',
            '<text x="24" y="49" fill="#a9b5c0" font-family="sans-serif" font-size="12">Unmapped background is unclassified. Labels are source sector IDs.</text>']
    for i, polygon in polygons.items():
        parts = [polygon] if polygon.geom_type == "Polygon" else polygon.geoms
        paths = []
        for part in parts:
            for ring in (part.exterior, *part.interiors):
                points = [xy(x, y) for x, y in ring.coords]
                paths.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z")
        body.append(f'<path d="{" ".join(paths)}" fill="#284c60" fill-rule="evenodd" stroke="#a8c3ce" stroke-width="1"/>')
        point = polygon.representative_point()
        x, y = xy(point.x, point.y)
        body.append(f'<text x="{x:.2f}" y="{y:.2f}" fill="white" font-family="monospace" font-size="12">{i}</text>')
    for opening in analysis["openings"]:
        a, b = [xy(*point) for point in opening["endpoints"]]
        body.append(f'<path d="M{a[0]:.2f},{a[1]:.2f} L{b[0]:.2f},{b[1]:.2f}" stroke="#e2b85b" stroke-width="3"/>')
    for camera in cameras:
        x, y = xy(camera["x"], camera["y"])
        angle = camera.get("angle", 0) * math.pi / 1024
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#f77e67"/>')
        body.append(f'<path d="M{x:.2f},{y:.2f} l{18 * math.cos(angle):.2f},{18 * math.sin(angle):.2f}" stroke="#f77e67" stroke-width="2"/>')
        body.append(f'<text x="{x + 6:.2f}" y="{y - 6:.2f}" fill="#f77e67" font-family="monospace" font-size="11">{escape(camera["id"])}</text>')
    body.append('<text x="24" y="810" fill="#a9b5c0" font-family="sans-serif" font-size="12">Gold: native portal edges. Coral: camera poses, not a verified movement route.</text></svg>')
    return "\n".join(body)
