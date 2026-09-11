# SPDX-License-Identifier: MIT
"""
UCX Collision Generator
=======================
Automatic convex (UCX) collision mesh generation for rocks/cliffs/terrain
intended for Unreal Engine 5 FBX export.

Blender 4.3.2+  |  No external libraries (bpy / bmesh / mathutils only).

Algorithm overview
------------------
Recursive spatial convex decomposition (AABB longest-axis median splits on
face-center clusters) approximating V-HACD without the VHACD library.
"""

bl_info = {
    "name": "UCX Collision Generator",
    "author": "Blender Assistant",
    "version": (1, 3, 0),
    "blender": (4, 3, 0),
    "location": "View3D > Sidebar > UCX",
    "description": "Generate UCX convex collision meshes for Unreal Engine 5",
    "category": "Object",
}

import re
import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector
from mathutils.bvhtree import BVHTree


# ---------------------------------------------------------------------------
# Preset values
# ---------------------------------------------------------------------------

PRESETS = {
    "LOW": {
        "max_hulls": 12,
        "simplify": 0.15,
        "min_size": 0.05,
        "max_verts": 32,
        "remove_small": 0.02,
        "margin": 0.0,
        "accuracy": 0.45,
        "preserve_cavities": True,
    },
    "MEDIUM": {
        "max_hulls": 18,
        "simplify": 0.22,
        "min_size": 0.02,
        "max_verts": 36,
        "remove_small": 0.01,
        "margin": 0.0,
        "accuracy": 0.60,
        "preserve_cavities": True,
    },
    "HIGH": {
        "max_hulls": 28,
        "simplify": 0.40,
        "min_size": 0.01,
        "max_verts": 48,
        "remove_small": 0.005,
        "margin": 0.0,
        "accuracy": 0.80,
        "preserve_cavities": True,
    },
}


def _apply_preset(scene, preset_key: str) -> None:
    """Copy preset values onto the scene property group."""
    values = PRESETS.get(preset_key)
    if not values:
        return
    props = scene.ucx_collision_props
    props.max_hulls = values["max_hulls"]
    props.simplify = values["simplify"]
    props.min_size = values["min_size"]
    props.max_verts = values["max_verts"]
    props.remove_small = values["remove_small"]
    props.margin = values["margin"]
    props.accuracy = values["accuracy"]
    if "preserve_cavities" in values:
        props.preserve_cavities = values["preserve_cavities"]


def _on_preset_update(self, context):
    """EnumProperty update: apply preset defaults (user may tweak afterwards)."""
    _apply_preset(context.scene, self.preset)


# ---------------------------------------------------------------------------
# Scene properties
# ---------------------------------------------------------------------------

class UCXCollisionProperties(PropertyGroup):
    """Settings exposed in the UCX sidebar panel."""

    preset: EnumProperty(
        name="Preset",
        description="Quality preset (applies default values; tweak afterwards)",
        items=(
            ("LOW", "Low", "Few hulls, heavy simplification — fast / cheap collision"),
            ("MEDIUM", "Medium", "Balanced hull count and quality"),
            ("HIGH", "High", "More hulls, finer detail — better fit, heavier"),
        ),
        default="MEDIUM",
        update=_on_preset_update,
    )

    max_hulls: IntProperty(
        name="Max Convex Hulls",
        description="Maximum number of convex hull pieces per source mesh",
        default=18,
        min=1,
        max=64,
    )

    accuracy: FloatProperty(
        name="Target Accuracy",
        description=(
            "How aggressively to split (higher = more splits / tighter fit). "
            "Used with convexity / size heuristics"
        ),
        default=0.55,
        min=0.05,
        max=1.0,
        subtype="FACTOR",
    )

    min_size: FloatProperty(
        name="Min Hull Size",
        description="Discard hulls whose bbox diagonal is below this (local units)",
        default=0.02,
        min=0.0001,
        soft_max=10.0,
        precision=4,
    )

    simplify: FloatProperty(
        name="Geometry Simplification",
        description=(
            "Decimate ratio for the working copy (fraction of faces kept). "
            "Lower = coarser working mesh = faster"
        ),
        default=0.20,
        min=0.01,
        max=1.0,
        subtype="FACTOR",
    )

    max_verts: IntProperty(
        name="Max Verts Per Hull",
        description="Limit vertices on each convex hull (0 = no limit)",
        default=32,
        min=0,
        max=256,
    )

    remove_small: FloatProperty(
        name="Remove Tiny Fragments",
        description=(
            "Discard clusters whose bbox diagonal is below this before hulling "
            "(local units)"
        ),
        default=0.01,
        min=0.0,
        soft_max=5.0,
        precision=4,
    )

    margin: FloatProperty(
        name="Collision Margin",
        description=(
            "Expand each hull from its centroid by this amount (local units). "
            "0 = exact hull"
        ),
        default=0.0,
        min=0.0,
        soft_max=1.0,
        precision=4,
    )

    create_collection: BoolProperty(
        name="Create UCX Collection",
        description="Put generated UCX objects into a collection named UCX_<source>",
        default=True,
    )

    preserve_cavities: BoolProperty(
        name="Preserve Cavities",
        description=(
            "Prefer splits that avoid filling tunnels/arches/overhangs. "
            "Limits hull inflation so passages stay walkable"
        ),
        default=True,
    )


# ---------------------------------------------------------------------------
# Naming / matching helpers
# ---------------------------------------------------------------------------

_UCX_SUFFIX_RE = re.compile(r"^UCX_(.+)_\d{2,}$")


def ucx_name(source_name: str, index: int) -> str:
    """Strict UE naming: UCX_<source>_00, UCX_<source>_01, ..."""
    return f"UCX_{source_name}_{index:02d}"


def ucx_pattern_for_source(source_name: str) -> re.Pattern:
    """Match UCX_<exact_source_name>_<digits> with at least two digits."""
    return re.compile(rf"^UCX_{re.escape(source_name)}_\d+$")


def find_ucx_objects(source_name: str, objects=None):
    """Return objects whose names match UCX_<source>_\\d+."""
    if objects is None:
        objects = bpy.data.objects
    pattern = ucx_pattern_for_source(source_name)
    return [obj for obj in objects if pattern.match(obj.name)]



# ---------------------------------------------------------------------------
# Geometry helpers (bmesh / mathutils only)
# ---------------------------------------------------------------------------

def _bbox_diagonal(coords) -> float:
    """Axis-aligned bounding-box diagonal length for a sequence of Vectors."""
    if not coords:
        return 0.0
    mn = Vector((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)))
    mx = Vector((max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)))
    return (mx - mn).length


def _bbox_extents(coords):
    """Return (min_corner, max_corner, size_vector) for coords."""
    if not coords:
        z = Vector((0, 0, 0))
        return z, z, z
    mn = Vector((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)))
    mx = Vector((max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)))
    return mn, mx, mx - mn



def _surface_air_threshold(size) -> float:
    """
    Distance from surface that counts as deep air (or solid core).
    Tuned so tunnel bores register without swallowing thin solid shells.
    """
    shortest = min(abs(size.x), abs(size.y), abs(size.z))
    return max(shortest * 0.09, size.length * 0.032, 1e-5)


def _median_split_points(coords, axis: int | None = None):
    """
    Split a point list at the median.
    If axis is None, use AABB longest axis.
    Returns (left, right). Either may be empty on failure.
    """
    if len(coords) < 2:
        return list(coords), []

    _mn, _mx, size = _bbox_extents(coords)
    if axis is None:
        axis = 0
        if size.y >= size.x and size.y >= size.z:
            axis = 1
        elif size.z >= size.x and size.z >= size.y:
            axis = 2

    ordered = sorted(coords, key=lambda c: c[axis])
    mid = len(ordered) // 2
    if mid == 0:
        mid = 1
    if mid >= len(ordered):
        mid = len(ordered) - 1
    left = ordered[:mid]
    right = ordered[mid:]
    if not left or not right:
        return list(coords), []
    return left, right


def _aabb_volume(coords) -> float:
    if not coords:
        return 0.0
    _mn, _mx, size = _bbox_extents(coords)
    return max(size.x, 1e-12) * max(size.y, 1e-12) * max(size.z, 1e-12)


def _cavity_ratio(coords, bvh, grid: int = 5) -> float:
    """
    Fraction of AABB grid samples that are open-air cavities.

    Method (works on open meshes):
    1) Mark samples far from the surface as "deep".
    2) Flood-fill from the AABB boundary through deep samples.
    3) Deep samples reachable from the boundary = exterior air / tunnels.
       Deep samples NOT reachable = solid interior (keep).
    """
    if bvh is None or len(coords) < 8:
        return 0.0
    mn, mx, size = _bbox_extents(coords)
    if size.length < 1e-12:
        return 0.0

    # Distance threshold: far from surface => deep (air or solid core)
    thr = max(size.length * 0.065, 1e-5)
    deep = [[[False for _ in range(grid)] for _ in range(grid)] for _ in range(grid)]
    points = [[[None for _ in range(grid)] for _ in range(grid)] for _ in range(grid)]

    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                p = Vector((
                    mn.x + size.x * ((i + 0.5) / grid),
                    mn.y + size.y * ((j + 0.5) / grid),
                    mn.z + size.z * ((k + 0.5) / grid),
                ))
                points[i][j][k] = p
                nearest = bvh.find_nearest(p)
                if nearest is None or nearest[0] is None:
                    deep[i][j][k] = True
                else:
                    deep[i][j][k] = nearest[3] > thr

    # Flood from boundary deep cells
    from collections import deque
    q = deque()
    seen = [[[False for _ in range(grid)] for _ in range(grid)] for _ in range(grid)]
    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                if i in (0, grid - 1) or j in (0, grid - 1) or k in (0, grid - 1):
                    if deep[i][j][k]:
                        q.append((i, j, k))
                        seen[i][j][k] = True

    air = 0
    while q:
        i, j, k = q.popleft()
        air += 1
        for di, dj, dk in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
            ni, nj, nk = i + di, j + dj, k + dk
            if not (0 <= ni < grid and 0 <= nj < grid and 0 <= nk < grid):
                continue
            if seen[ni][nj][nk] or not deep[ni][nj][nk]:
                continue
            seen[ni][nj][nk] = True
            q.append((ni, nj, nk))

    total = grid * grid * grid
    return air / max(total, 1)




def _sample_exterior_air_points(coords, bvh, grid: int = 8):
    """Return local-space sample points that sit in exterior air cavities."""
    if bvh is None or len(coords) < 16:
        return []
    mn, mx, size = _bbox_extents(coords)
    thr = _surface_air_threshold(size)
    deep = [[[False] * grid for _ in range(grid)] for _ in range(grid)]
    points = [[[None] * grid for _ in range(grid)] for _ in range(grid)]
    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                p = Vector((
                    mn.x + size.x * ((i + 0.5) / grid),
                    mn.y + size.y * ((j + 0.5) / grid),
                    mn.z + size.z * ((k + 0.5) / grid),
                ))
                points[i][j][k] = p
                nearest = bvh.find_nearest(p)
                if nearest is None or nearest[0] is None:
                    deep[i][j][k] = True
                else:
                    deep[i][j][k] = nearest[3] > thr

    from collections import deque
    q = deque()
    seen = [[[False] * grid for _ in range(grid)] for _ in range(grid)]
    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                if i in (0, grid - 1) or j in (0, grid - 1) or k in (0, grid - 1):
                    if deep[i][j][k]:
                        q.append((i, j, k))
                        seen[i][j][k] = True
    air = []
    while q:
        i, j, k = q.popleft()
        if 0 < i < grid - 1 and 0 < j < grid - 1 and 0 < k < grid - 1:
            air.append(points[i][j][k])
        for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            ni, nj, nk = i + di, j + dj, k + dk
            if not (0 <= ni < grid and 0 <= nj < grid and 0 <= nk < grid):
                continue
            if seen[ni][nj][nk] or not deep[ni][nj][nk]:
                continue
            seen[ni][nj][nk] = True
            q.append((ni, nj, nk))
    return air


def _air_is_enclosed(coords, bvh, samples: int = 12) -> bool:
    """
    True if bbox center looks like a tunnel bore via ray probes:
    at least one axis has long clearance both ways (open ends), and at least
    two axes have short clearance both ways (walls). Solid rocks have short
    clearance on all axes; open cliffs usually fail the center-distance gate.
    """
    if bvh is None or len(coords) < 16:
        return False
    mn, mx, size = _bbox_extents(coords)
    thr = _surface_air_threshold(size)
    shortest = min(abs(size.x), abs(size.y), abs(size.z))
    center = (mn + mx) * 0.5

    nearest = bvh.find_nearest(center)
    if nearest is None or nearest[0] is None:
        center_dist = size.length
    else:
        center_dist = nearest[3]
    # Near a surface => not sitting in a bore
    if center_dist < thr * 1.15:
        return False
    # Solid rocks: center-to-surface is a large fraction of the short AABB axis.
    # Tunnel/arch bores are a smaller pocket inside a larger mass.
    if center_dist > shortest * 0.28:
        return False

    axis_dirs = [
        (Vector((1, 0, 0)), Vector((-1, 0, 0))),
        (Vector((0, 1, 0)), Vector((0, -1, 0))),
        (Vector((0, 0, 1)), Vector((0, 0, -1))),
    ]

    def ray_hit_dist(direction) -> float:
        # BVH ray_cast: (location, normal, index, distance) or Nones
        hit = bvh.ray_cast(center, direction)
        if hit is None or hit[0] is None:
            return size.length
        return float(hit[3])

    open_thr = max(shortest * 0.32, center_dist * 1.8, thr * 2.5)
    wall_thr = max(center_dist * 2.2, thr * 2.0, shortest * 0.18)

    open_axes = 0
    wall_axes = 0
    for dpos, dneg in axis_dirs:
        hp = ray_hit_dist(dpos)
        hn = ray_hit_dist(dneg)
        if hp >= open_thr and hn >= open_thr:
            open_axes += 1
        if hp <= wall_thr and hn <= wall_thr:
            wall_axes += 1

    return open_axes >= 1 and wall_axes >= 2


def _has_through_tunnel(coords, bvh, grid: int = 7) -> bool:
    """
    True if *exterior* open-air cells (flooded from the AABB boundary) form a
    passage through the volume near its interior. Solid cores are ignored.
    """
    if bvh is None or len(coords) < 16:
        return False
    mn, mx, size = _bbox_extents(coords)
    if size.length < 1e-12:
        return False
    thr = _surface_air_threshold(size)
    deep = [[[False] * grid for _ in range(grid)] for _ in range(grid)]
    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                p = Vector((
                    mn.x + size.x * ((i + 0.5) / grid),
                    mn.y + size.y * ((j + 0.5) / grid),
                    mn.z + size.z * ((k + 0.5) / grid),
                ))
                nearest = bvh.find_nearest(p)
                if nearest is None or nearest[0] is None:
                    deep[i][j][k] = True
                else:
                    deep[i][j][k] = nearest[3] > thr

    from collections import deque

    # Exterior air only: flood deep cells from the AABB boundary
    exterior = [[[False] * grid for _ in range(grid)] for _ in range(grid)]
    q = deque()
    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                if i in (0, grid - 1) or j in (0, grid - 1) or k in (0, grid - 1):
                    if deep[i][j][k]:
                        q.append((i, j, k))
                        exterior[i][j][k] = True
    while q:
        i, j, k = q.popleft()
        for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            ni, nj, nk = i + di, j + dj, k + dk
            if not (0 <= ni < grid and 0 <= nj < grid and 0 <= nk < grid):
                continue
            if exterior[ni][nj][nk] or not deep[ni][nj][nk]:
                continue
            exterior[ni][nj][nk] = True
            q.append((ni, nj, nk))

    def through_on_axis(axis: int) -> bool:
        q = deque()
        seen = [[[False] * grid for _ in range(grid)] for _ in range(grid)]
        mid = (grid - 1) * 0.5
        center_tol = max(1.25, grid * 0.34)

        for a in range(grid):
            for b in range(grid):
                idx = [0, 0, 0]
                idx[axis] = 0
                idx[(axis + 1) % 3] = a
                idx[(axis + 2) % 3] = b
                i, j, k = idx
                if exterior[i][j][k]:
                    q.append((i, j, k, False))
                    seen[i][j][k] = True

        while q:
            i, j, k, hit_center = q.popleft()
            perp_dists = [abs([i, j, k][ax] - mid) for ax in range(3) if ax != axis]
            near_center = (
                max(perp_dists) <= center_tol
                and abs([i, j, k][axis] - mid) <= (grid * 0.42)
            )
            hit_center = hit_center or near_center
            if [i, j, k][axis] == grid - 1 and hit_center:
                return True
            for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                ni, nj, nk = i + di, j + dj, k + dk
                if not (0 <= ni < grid and 0 <= nj < grid and 0 <= nk < grid):
                    continue
                if seen[ni][nj][nk] or not exterior[ni][nj][nk]:
                    continue
                seen[ni][nj][nk] = True
                q.append((ni, nj, nk, hit_center))
        return False

    return any(through_on_axis(ax) for ax in (0, 1, 2))


def _empty_space_ratio(coords, grid: int = 6, bvh=None) -> float:
    """
    Backward-compatible name: prefer BVH cavity ratio when available,
    else fall back to distance-to-points heuristic.
    """
    if bvh is not None:
        return _cavity_ratio(coords, bvh, grid=max(4, grid - 1))
    if len(coords) < 8:
        return 0.0
    mn, mx, size = _bbox_extents(coords)
    if size.length < 1e-12:
        return 0.0
    thr = max(size.length * 0.06, 1e-6)
    thr2 = thr * thr
    empty = 0
    total = 0
    step = max(1, len(coords) // 1500)
    sample_pts = coords[::step]
    for i in range(grid):
        for j in range(grid):
            for k in range(grid):
                p = Vector((
                    mn.x + size.x * ((i + 0.5) / grid),
                    mn.y + size.y * ((j + 0.5) / grid),
                    mn.z + size.z * ((k + 0.5) / grid),
                ))
                total += 1
                dmin = min((p - q).length_squared for q in sample_pts)
                if dmin > thr2:
                    empty += 1
    return empty / max(total, 1)


def _best_axis_split(coords, preserve_cavities: bool, bvh=None):
    """
    Try splitting on X/Y/Z; pick the split that best reduces filled AABB volume.
    When preserve_cavities, prefer splits that lower outside-mesh (cavity) ratio.
    """
    parent_vol = _aabb_volume(coords)
    parent_empty = _empty_space_ratio(coords, bvh=bvh) if preserve_cavities else 0.0
    best = None  # (score, left, right)

    for axis in (0, 1, 2):
        left, right = _median_split_points(coords, axis=axis)
        if len(left) < 3 or len(right) < 3:
            continue
        child_vol = _aabb_volume(left) + _aabb_volume(right)
        score = child_vol / max(parent_vol, 1e-12)
        if preserve_cavities:
            le = _empty_space_ratio(left, bvh=bvh)
            re = _empty_space_ratio(right, bvh=bvh)
            child_empty = 0.5 * (le + re)
            score = 0.55 * score + 0.45 * (child_empty / max(parent_empty, 1e-3))
        if best is None or score < best[0]:
            best = (score, left, right)

    if best is None:
        left, right = _median_split_points(coords, axis=None)
        return left, right
    return best[1], best[2]


def _expand_hull_to_cover(bm, cluster_coords, pad_frac: float = 0.02, max_scale: float = 1.06) -> None:
    """
    Uniformly scale hull verts from centroid so the hull AABB covers the
    cluster AABB (plus a small pad). Caps scale to avoid filling cavities.
    """
    if not bm.verts or len(cluster_coords) < 1:
        return
    hcoords = [v.co.copy() for v in bm.verts]
    hmn, hmx, _ = _bbox_extents(hcoords)
    cmn, cmx, _ = _bbox_extents(cluster_coords)
    csize = cmx - cmn
    pad = csize * pad_frac
    cmn = cmn - pad
    cmx = cmx + pad

    cen = sum(hcoords, Vector((0, 0, 0))) / len(hcoords)

    def axis_scale(h0, h1, c0, c1, mid):
        hs = max(abs(h0 - mid), abs(h1 - mid), 1e-12)
        cs = max(abs(c0 - mid), abs(c1 - mid), 0.0)
        return max(1.0, cs / hs)

    sx = axis_scale(hmn.x, hmx.x, cmn.x, cmx.x, cen.x)
    sy = axis_scale(hmn.y, hmx.y, cmn.y, cmx.y, cen.y)
    sz = axis_scale(hmn.z, hmx.z, cmn.z, cmx.z, cen.z)
    s = max(sx, sy, sz)
    if s <= 1.0001:
        return
    s = min(s, max_scale)
    for v in bm.verts:
        v.co = cen + (v.co - cen) * s


def _partition_points(
    coords,
    max_parts: int,
    accuracy: float,
    remove_small: float,
    preserve_cavities: bool = True,
    bvh=None,
):
    """
    Breadth-first splits until max_parts.
    Picks the part with worst cavity/empty score (or largest diag), and splits
    on the axis that best reduces filled volume / emptiness.
    """
    if not coords:
        return []

    unique = []
    seen = set()
    for c in coords:
        key = (round(c.x, 5), round(c.y, 5), round(c.z, 5))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c.copy())
    coords = unique

    parts = [coords]
    target = max(1, int(max_parts))
    min_points = max(4, int(10 * (1.0 - accuracy) + 4))

    safety = 0
    while len(parts) < target and safety < max_parts * 6:
        safety += 1

        def part_priority(i):
            p = parts[i]
            diag = _bbox_diagonal(p)
            if preserve_cavities:
                # Split cavity-heavy regions first (outside-mesh air in AABB)
                return (_empty_space_ratio(p, bvh=bvh), diag)
            return (diag, 0.0)

        idx = max(range(len(parts)), key=part_priority)
        part = parts[idx]
        diag = _bbox_diagonal(part)
        if len(part) < min_points * 2:
            # try another part
            candidates = [i for i in range(len(parts)) if len(parts[i]) >= min_points * 2]
            if not candidates:
                break
            idx = max(candidates, key=part_priority)
            part = parts[idx]
            diag = _bbox_diagonal(part)
        if remove_small > 0.0 and diag < remove_small * 2.0:
            break

        left, right = _best_axis_split(
            part, preserve_cavities=preserve_cavities, bvh=bvh
        )
        if len(left) < 3 or len(right) < 3:
            break
        parts[idx] = left
        parts.append(right)

    return parts


def _decimate_bmesh(bm, ratio: float) -> None:
    """
    Reduce face count of a working bmesh toward ratio * current faces.
    Uses dissolve of short edges — good enough for collision proxy generation.
    """
    if ratio >= 0.999 or len(bm.faces) < 8:
        return

    target_faces = max(4, int(len(bm.faces) * ratio))
    if target_faces >= len(bm.faces):
        return

    try:
        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=0.1,
            use_dissolve_boundaries=False,
            verts=list(bm.verts),
            edges=list(bm.edges),
        )
    except Exception:
        pass

    safety = 0
    while len(bm.faces) > target_faces and safety < 64:
        safety += 1
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        edges = sorted(bm.edges, key=lambda e: e.calc_length())
        dissolve_count = max(1, (len(bm.faces) - target_faces) // 2)
        to_dissolve = [e for e in edges[:dissolve_count] if e.is_valid]
        if not to_dissolve:
            break
        try:
            bmesh.ops.dissolve_edges(
                bm,
                edges=to_dissolve,
                use_verts=True,
                use_face_split=False,
            )
        except Exception:
            break
        try:
            bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=list(bm.edges))
        except Exception:
            pass

    bm.normal_update()
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()


def _build_hull_mesh(
    cluster_coords,
    margin: float,
    max_verts: int,
    min_size: float,
):
    """
    Build a convex hull bmesh from coordinates.
    Returns (bmesh, discarded_reason) — bmesh is None if discarded.
    Caller owns the returned bmesh and must free it / convert it.
    """
    if len(cluster_coords) < 3:
        return None, "too_few_verts"

    diag = _bbox_diagonal(cluster_coords)
    if diag < min_size:
        return None, "below_min_size"

    bm = bmesh.new()
    try:
        for co in cluster_coords:
            bm.verts.new(co)
        bm.verts.ensure_lookup_table()

        try:
            hull_result = bmesh.ops.convex_hull(bm, input=list(bm.verts))
        except Exception:
            bm.free()
            return None, "hull_failed"

        # geom_interior / geom_unused often overlap — dedupe by BMVert identity
        to_delete = []
        seen = set()
        for g in list(hull_result.get("geom_interior", [])) + list(
            hull_result.get("geom_unused", [])
        ):
            if not isinstance(g, bmesh.types.BMVert):
                continue
            if not g.is_valid:
                continue
            vid = g.index
            if vid in seen:
                continue
            seen.add(vid)
            to_delete.append(g)
        if to_delete:
            bmesh.ops.delete(bm, geom=to_delete, context="VERTS")

        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        loose = [v for v in bm.verts if v.is_valid and not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")

        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        if len(bm.faces) < 1 or len(bm.verts) < 3:
            bm.free()
            return None, "empty_hull"

        # Margin: expand verts from centroid
        if margin > 0.0 and bm.verts:
            centroid = Vector((0, 0, 0))
            for v in bm.verts:
                centroid += v.co
            centroid /= len(bm.verts)
            for v in bm.verts:
                direction = v.co - centroid
                length = direction.length
                if length > 1e-12:
                    v.co = centroid + direction.normalized() * (length + margin)
                else:
                    v.co = centroid + Vector((margin, 0, 0))

        if max_verts > 0 and len(bm.verts) > max_verts:
            _limit_hull_verts(bm, max_verts)

        coords_out = [v.co.copy() for v in bm.verts]
        if _bbox_diagonal(coords_out) < min_size:
            bm.free()
            return None, "below_min_size_after"

        return bm, None
    except Exception:
        try:
            bm.free()
        except Exception:
            pass
        return None, "exception"


def _limit_hull_verts(bm, max_verts: int) -> None:
    """Reduce hull vertex count toward max_verts, then rebuild convex hull."""
    if max_verts < 4 or len(bm.verts) <= max_verts:
        return

    coords = [v.co.copy() for v in bm.verts]
    centroid = sum(coords, Vector((0, 0, 0))) / len(coords)
    coords.sort(key=lambda c: (c - centroid).length, reverse=True)
    keep = coords[:max_verts]
    bm.clear()
    for co in keep:
        bm.verts.new(co)
    bm.verts.ensure_lookup_table()
    try:
        hull_result = bmesh.ops.convex_hull(bm, input=list(bm.verts))
        # geom_interior / geom_unused often overlap — dedupe by BMVert identity
        to_delete = []
        seen = set()
        for g in list(hull_result.get("geom_interior", [])) + list(
            hull_result.get("geom_unused", [])
        ):
            if not isinstance(g, bmesh.types.BMVert):
                continue
            if not g.is_valid:
                continue
            vid = g.index
            if vid in seen:
                continue
            seen.add(vid)
            to_delete.append(g)
        if to_delete:
            bmesh.ops.delete(bm, geom=to_delete, context="VERTS")
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        loose = [v for v in bm.verts if v.is_valid and not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
    except Exception:
        pass


def _evaluated_mesh_to_bmesh(obj, depsgraph) -> bmesh.types.BMesh:
    """
    Build a bmesh from the evaluated mesh in the object's local space.
    Does NOT modify the source object or apply modifiers permanently.
    """
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        return bm
    finally:
        eval_obj.to_mesh_clear()


def _ensure_ucx_collection(source_obj, create: bool):
    """
    Return the collection that should receive UCX objects.
    If create is True, ensure a collection named UCX_<source> exists and
    is linked under the same parent collections as the source when possible.
    """
    if not create:
        # Prefer the first collection the source belongs to
        if source_obj.users_collection:
            return source_obj.users_collection[0]
        return bpy.context.scene.collection

    col_name = f"UCX_{source_obj.name}"
    col = bpy.data.collections.get(col_name)
    if col is None:
        col = bpy.data.collections.new(col_name)
        # Link under the same parent as the source if possible
        linked = False
        for parent in source_obj.users_collection:
            if col.name not in parent.children:
                try:
                    parent.children.link(col)
                    linked = True
                    break
                except RuntimeError:
                    pass
        if not linked:
            # Fall back to scene master collection
            if col.name not in bpy.context.scene.collection.children:
                bpy.context.scene.collection.children.link(col)
    return col


def _link_object_exclusive(obj, collection):
    """Link object to collection; unlink from other collections."""
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def _purge_mesh_datablock(mesh):
    """Remove a mesh datablock if it has no users."""
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def delete_ucx_for_source(source_name: str) -> int:
    """
    Delete all objects matching UCX_<exact_source_name>_\\d+.
    Also removes orphaned mesh datablocks and empty UCX_<name> collections.
    Returns number of objects deleted.
    """
    to_delete = find_ucx_objects(source_name)
    count = 0
    meshes_to_check = []

    for obj in to_delete:
        if obj.type == "MESH" and obj.data:
            meshes_to_check.append(obj.data)
        bpy.data.objects.remove(obj, do_unlink=True)
        count += 1

    for mesh in meshes_to_check:
        _purge_mesh_datablock(mesh)

    # Remove empty dedicated collection if present
    col_name = f"UCX_{source_name}"
    col = bpy.data.collections.get(col_name)
    if col is not None and len(col.objects) == 0 and len(col.children) == 0:
        bpy.data.collections.remove(col)

    return count



def _point_inside_convex_mesh(point, mesh, eps: float = 1e-5) -> bool:
    """True if point is inside a convex mesh (outward normals assumed)."""
    import bmesh as _bm
    bm = _bm.new()
    try:
        bm.from_mesh(mesh)
        bm.normal_update()
        if not bm.faces:
            return False
        for f in bm.faces:
            v0 = f.verts[0].co
            # Outside if in front of any face plane
            if (point - v0).dot(f.normal) > eps:
                return False
        return True
    finally:
        bm.free()


def _bore_sample_points(coords, bvh, count: int = 48):
    """
    Sample exterior-air points near the AABB center along the most open axis.
    These approximate the tunnel/arch centerline for plug tests.
    """
    if bvh is None or len(coords) < 16:
        return []
    mn, mx, size = _bbox_extents(coords)
    thr = _surface_air_threshold(size)
    center = (mn + mx) * 0.5
    # Pick axis with longest clearance both ways from center (tunnel axis)
    best_axis = 0
    best_clear = -1.0
    axes = (
        (0, Vector((1, 0, 0)), Vector((-1, 0, 0))),
        (1, Vector((0, 1, 0)), Vector((0, -1, 0))),
        (2, Vector((0, 0, 1)), Vector((0, 0, -1))),
    )
    for ax, dpos, dneg in axes:
        hp = bvh.ray_cast(center, dpos)
        hn = bvh.ray_cast(center, dneg)
        dp = size.length if hp is None or hp[0] is None else float(hp[3])
        dn = size.length if hn is None or hn[0] is None else float(hn[3])
        clear = min(dp, dn)
        if clear > best_clear:
            best_clear = clear
            best_axis = ax

    pts = []
    for i in range(count):
        t = (i + 0.5) / count
        p = Vector(center)
        p[best_axis] = mn[best_axis] + size[best_axis] * t
        n = bvh.find_nearest(p)
        if n is None or n[0] is None or n[3] > thr * 0.9:
            pts.append(p)
    return pts


def generate_ucx_for_object(obj, props, report_fn=None) -> int:
    """
    Generate UCX convex collision meshes for a single mesh object.
    Never modifies the source. Returns number of hulls created.
    """
    if report_fn is None:
        report_fn = lambda _t, _m: None

    if obj.type != "MESH":
        report_fn({"WARNING"}, f"Skipping non-mesh object: {obj.name}")
        return 0

    depsgraph = bpy.context.evaluated_depsgraph_get()
    bm_work = None
    created_meshes = []
    created_objects = []
    hull_count = 0

    try:
        bm_work = _evaluated_mesh_to_bmesh(obj, depsgraph)

        if bm_work is None or len(bm_work.verts) == 0 or len(bm_work.faces) == 0:
            report_fn({"WARNING"}, f"Empty mesh, skipping: {obj.name}")
            return 0

        # Triangulate then decimate working copy (never touches the source)
        try:
            bmesh.ops.triangulate(bm_work, faces=list(bm_work.faces))
        except Exception:
            pass
        bm_work.faces.ensure_lookup_table()
        bm_work.verts.ensure_lookup_table()

        _decimate_bmesh(bm_work, props.simplify)
        bm_work.faces.ensure_lookup_table()
        bm_work.verts.ensure_lookup_table()

        if len(bm_work.verts) < 3:
            report_fn({"WARNING"}, f"No geometry after simplification: {obj.name}")
            return 0

        # Adaptive min size: user value is a fraction-like absolute in local units,
        # but never discard parts that are still a meaningful fraction of the mesh.
        mesh_diag = _bbox_diagonal([v.co.copy() for v in bm_work.verts])
        effective_min = min(props.min_size, mesh_diag * 0.02)
        effective_remove = min(props.remove_small, mesh_diag * 0.01) if props.remove_small > 0 else 0.0

        # Fuller vert cloud for fitting hulls (separate from light partition cloud)
        bm_full = _evaluated_mesh_to_bmesh(obj, depsgraph)
        try:
            try:
                bmesh.ops.triangulate(bm_full, faces=list(bm_full.faces))
            except Exception:
                pass
            full_ratio = min(1.0, max(props.simplify * 2.5, 0.35))
            if len(bm_full.faces) > 80000:
                _decimate_bmesh(bm_full, full_ratio)
            bm_full.verts.ensure_lookup_table()
            full_coords = [v.co.copy() for v in bm_full.verts]
        finally:
            bm_full.free()

        # Partition on lighter working cloud, then refill each region from fuller cloud
        work_coords = [v.co.copy() for v in bm_work.verts]
        # BVH of the working mesh — distinguishes solid interior vs open air cavities
        try:
            work_bvh = BVHTree.FromBMesh(bm_work, epsilon=0.0)
        except Exception:
            work_bvh = None

        # Optionally boost hull budget when the mesh looks cavity-heavy (tunnels/arches)
        hull_budget = props.max_hulls
        cavity_mode = False
        if props.preserve_cavities and work_bvh is not None:
            # Tunnel/arch detection: through-passage near center AND air at the
            # center is enclosed by surfaces in multiple directions (not an
            # open cliff backside).
            through = _has_through_tunnel(work_coords, work_bvh, grid=7)
            enclosed = _air_is_enclosed(work_coords, work_bvh)
            cavity_mode = bool(through and enclosed)
            if cavity_mode:
                # Tunnels need enough pieces even on Low, otherwise hulls span the bore
                hull_budget = min(64, max(hull_budget, 20, int(hull_budget * 1.6) + 4))

        clusters = _partition_points(
            work_coords,
            max_parts=hull_budget,
            accuracy=props.accuracy,
            remove_small=effective_remove,
            preserve_cavities=cavity_mode,
            bvh=work_bvh,
        )

        target_col = _ensure_ucx_collection(obj, props.create_collection)
        source_mw = obj.matrix_world.copy()

        # Extra splits on cavity-heavy clusters (ring slabs that would fill tunnels)
        if cavity_mode:
            hard_cap = min(56, max(hull_budget + 10, int(hull_budget * 1.5)))
            guard = 0
            while len(clusters) < hard_cap and guard < hard_cap * 2:
                guard += 1
                worst_i = None
                worst_empty = 0.22
                for i, seed in enumerate(clusters):
                    if len(seed) < 12:
                        continue
                    e = _empty_space_ratio(seed, bvh=work_bvh)
                    if e > worst_empty:
                        worst_empty = e
                        worst_i = i
                if worst_i is None:
                    break
                left, right = _best_axis_split(
                    clusters[worst_i], preserve_cavities=True, bvh=work_bvh
                )
                if len(left) < 3 or len(right) < 3:
                    break
                clusters[worst_i] = left
                clusters.append(right)
            hull_budget = max(hull_budget, len(clusters))

        # Assign fuller-res verts:
        # - cavities: seed AABB containment (avoids merging opposite tunnel walls)
        # - otherwise: nearest centroid (best solid-rock coverage)
        centroids = []
        seed_bounds = []
        for seed_coords in clusters:
            if not seed_coords:
                centroids.append(None)
                seed_bounds.append(None)
                continue
            centroids.append(sum(seed_coords, Vector((0, 0, 0))) / len(seed_coords))
            mn, mx, size = _bbox_extents(seed_coords)
            pad = size.length * 0.06
            seed_bounds.append((mn - Vector((pad, pad, pad)), mx + Vector((pad, pad, pad))))

        assigned = [[] for _ in clusters]
        for c in full_coords:
            best_i = None
            if cavity_mode:
                # Prefer containing seed AABB (nearest centroid among hits)
                # so opposite tunnel walls stay separate.
                best_d = float("inf")
                for i, bounds in enumerate(seed_bounds):
                    if bounds is None or centroids[i] is None:
                        continue
                    mn, mx = bounds
                    if mn.x <= c.x <= mx.x and mn.y <= c.y <= mx.y and mn.z <= c.z <= mx.z:
                        d = (c - centroids[i]).length_squared
                        if d < best_d:
                            best_d = d
                            best_i = i
            if best_i is None:
                best_i = 0
                best_d = float("inf")
                for i, cen in enumerate(centroids):
                    if cen is None:
                        continue
                    d = (c - cen).length_squared
                    if d < best_d:
                        best_d = d
                        best_i = i
            assigned[best_i].append(c)

        hull_index = 0
        # Allow a few extra iterations when cavity splits enqueue more clusters
        loop_cap = hull_budget + (12 if cavity_mode else 0)
        for coords in assigned:
            if hull_index >= loop_cap:
                break

            if len(coords) < 3:
                continue

            if effective_remove > 0.0 and _bbox_diagonal(coords) < effective_remove:
                continue

            max_input = max(800, int(props.max_verts) * 40) if props.max_verts > 0 else 4000
            max_input = min(max_input, 6000)
            if len(coords) > max_input:
                centroid = sum(coords, Vector((0, 0, 0))) / len(coords)
                coords = sorted(coords, key=lambda c: (c - centroid).length, reverse=True)[:max_input]

            hull_bm, reason = _build_hull_mesh(
                coords,
                margin=props.margin,
                max_verts=props.max_verts,
                min_size=effective_min,
            )
            if hull_bm is None:
                continue

            try:
                # Slight fit expand — keep modest in cavity mode so bore stays open
                max_scale = 1.03 if cavity_mode else 1.12
                _expand_hull_to_cover(
                    hull_bm, coords, pad_frac=0.01, max_scale=max_scale
                )

                # If hull still plugs open air, try to split; only drop when a
                # split succeeded (otherwise keep for coverage).
                if cavity_mode and work_bvh is not None:
                    hcoords = [v.co.copy() for v in hull_bm.verts]
                    if _cavity_ratio(hcoords, work_bvh, grid=5) > 0.32:
                        split_ok = False
                        if len(coords) >= 16 and hull_index + 2 <= hull_budget + 8:
                            left, right = _best_axis_split(
                                coords, preserve_cavities=True, bvh=work_bvh
                            )
                            if len(left) >= 3 and len(right) >= 3:
                                assigned.append(left)
                                assigned.append(right)
                                split_ok = True
                        if split_ok:
                            continue

                mesh_data = bpy.data.meshes.new(ucx_name(obj.name, hull_index))
                hull_bm.to_mesh(mesh_data)
                mesh_data.update()
                created_meshes.append(mesh_data)

                ucx_obj = bpy.data.objects.new(ucx_name(obj.name, hull_index), mesh_data)
                # Geometry is in source local space; match world transform exactly
                ucx_obj.matrix_world = source_mw
                ucx_obj.display_type = "WIRE"
                ucx_obj.show_in_front = True

                _link_object_exclusive(ucx_obj, target_col)
                created_objects.append(ucx_obj)
                hull_index += 1
                hull_count += 1
            finally:
                hull_bm.free()

        # Post-process: clear tunnel bore plugs using real convex containment
        # of centerline/bore samples (AABB air-fraction misses long thin tunnels).
        if cavity_mode and work_bvh is not None and created_objects:
            bore_pts = _bore_sample_points(work_coords, work_bvh, count=48)
            if bore_pts:
                survivors = []
                for ucx_obj in created_objects:
                    mesh = ucx_obj.data
                    hit = sum(1 for p in bore_pts if _point_inside_convex_mesh(p, mesh))
                    frac = hit / len(bore_pts)
                    if frac <= 0.08:
                        survivors.append(ucx_obj)
                        continue
                    # Shrink toward centroid until bore is mostly clear
                    ok = False
                    for _step in range(10):
                        if len(mesh.vertices) < 3:
                            break
                        cen = Vector((0, 0, 0))
                        for v in mesh.vertices:
                            cen += v.co
                        cen /= len(mesh.vertices)
                        for v in mesh.vertices:
                            v.co = cen + (v.co - cen) * 0.90
                        mesh.update()
                        hit = sum(1 for p in bore_pts if _point_inside_convex_mesh(p, mesh))
                        frac = hit / len(bore_pts)
                        if frac <= 0.08:
                            ok = True
                            break
                    if ok or frac <= 0.15:
                        survivors.append(ucx_obj)
                    else:
                        bpy.data.objects.remove(ucx_obj, do_unlink=True)
                        _purge_mesh_datablock(mesh)
                        hull_count -= 1
                created_objects[:] = survivors

        if hull_count == 0:
            report_fn({"WARNING"}, f"Zero hulls produced for: {obj.name}")

        return hull_count

    except Exception as exc:
        # Cleanup partial results on hard failure
        for o in created_objects:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass
        for m in created_meshes:
            _purge_mesh_datablock(m)
        report_fn({"ERROR"}, f"Failed on {obj.name}: {exc}")
        return 0

    finally:
        if bm_work is not None:
            bm_work.free()


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class OBJECT_OT_ucx_generate(Operator):
    """Generate UCX convex collision meshes for selected mesh objects"""

    bl_idname = "object.ucx_generate"
    bl_label = "Generate UCX Collisions"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ucx_collision_props
        selected = list(context.selected_objects)

        if not selected:
            self.report({"WARNING"}, "No objects selected")
            return {"CANCELLED"}

        mesh_objs = [o for o in selected if o.type == "MESH"]
        skipped = [o for o in selected if o.type != "MESH"]

        for o in skipped:
            self.report({"WARNING"}, f"Skipping non-mesh: {o.name}")

        if not mesh_objs:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        total = 0
        for obj in mesh_objs:
            # Replace any existing UCX for this source so UE names stay clean
            # (avoids Blender .001 suffixes that break UCX_* recognition).
            delete_ucx_for_source(obj.name)
            total += generate_ucx_for_object(obj, props, self.report)

        if total == 0:
            self.report({"WARNING"}, "No UCX hulls were created")
            return {"FINISHED"}

        self.report({"INFO"}, f"Created {total} UCX hull(s) for {len(mesh_objs)} object(s)")
        return {"FINISHED"}


class OBJECT_OT_ucx_delete(Operator):
    """Delete UCX collision objects matching selected sources"""

    bl_idname = "object.ucx_delete"
    bl_label = "Delete UCX"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        selected = list(context.selected_objects)
        if not selected:
            self.report({"WARNING"}, "No objects selected")
            return {"CANCELLED"}

        total = 0
        for obj in selected:
            # Allow selecting either the source or an existing UCX piece:
            # If name matches UCX_*_NN, resolve source name from it.
            m = _UCX_SUFFIX_RE.match(obj.name)
            if m:
                source_name = m.group(1)
            else:
                source_name = obj.name
            total += delete_ucx_for_source(source_name)

        if total == 0:
            self.report({"WARNING"}, "No matching UCX objects found")
        else:
            self.report({"INFO"}, f"Deleted {total} UCX object(s)")
        return {"FINISHED"}


class OBJECT_OT_ucx_regenerate(Operator):
    """Delete existing UCX for selection, then generate anew"""

    bl_idname = "object.ucx_regenerate"
    bl_label = "Regenerate UCX"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ucx_collision_props
        selected = list(context.selected_objects)

        if not selected:
            self.report({"WARNING"}, "No objects selected")
            return {"CANCELLED"}

        # Delete first
        for obj in selected:
            m = _UCX_SUFFIX_RE.match(obj.name)
            source_name = m.group(1) if m else obj.name
            delete_ucx_for_source(source_name)

        mesh_objs = [o for o in selected if o.type == "MESH"]
        # If user selected only UCX objects, try to find the original sources
        if not mesh_objs:
            # Resolve sources from UCX names and regenerate those if present
            sources = []
            for obj in selected:
                m = _UCX_SUFFIX_RE.match(obj.name)
                if m:
                    src = bpy.data.objects.get(m.group(1))
                    if src and src.type == "MESH" and src not in sources:
                        sources.append(src)
            mesh_objs = sources

        skipped = [o for o in selected if o.type != "MESH" and not _UCX_SUFFIX_RE.match(o.name)]
        for o in skipped:
            self.report({"WARNING"}, f"Skipping non-mesh: {o.name}")

        if not mesh_objs:
            self.report({"ERROR"}, "No mesh sources to regenerate")
            return {"CANCELLED"}

        total = 0
        for obj in mesh_objs:
            total += generate_ucx_for_object(obj, props, self.report)

        if total == 0:
            self.report({"WARNING"}, "No UCX hulls were created")
        else:
            self.report({"INFO"}, f"Regenerated {total} UCX hull(s)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# UI Panel
# ---------------------------------------------------------------------------

class VIEW3D_PT_ucx_collision(Panel):
    """Sidebar panel: View3D > N-panel > UCX"""

    bl_label = "UCX Collision Generator"
    bl_idname = "VIEW3D_PT_ucx_collision"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UCX"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ucx_collision_props

        col = layout.column(align=True)
        col.operator(OBJECT_OT_ucx_generate.bl_idname, icon="MESH_ICOSPHERE")
        col.operator(OBJECT_OT_ucx_delete.bl_idname, icon="TRASH")
        col.operator(OBJECT_OT_ucx_regenerate.bl_idname, icon="FILE_REFRESH")

        layout.separator()
        layout.prop(props, "preset", text="Preset")

        box = layout.box()
        box.label(text="Settings", icon="SETTINGS")
        col = box.column(align=True)
        col.prop(props, "max_hulls")
        col.prop(props, "accuracy")
        col.prop(props, "min_size")
        col.prop(props, "simplify")
        col.prop(props, "max_verts")
        col.prop(props, "remove_small")
        col.prop(props, "margin")

        layout.separator()
        layout.prop(props, "create_collection")
        layout.prop(props, "preserve_cavities")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    UCXCollisionProperties,
    OBJECT_OT_ucx_generate,
    OBJECT_OT_ucx_delete,
    OBJECT_OT_ucx_regenerate,
    VIEW3D_PT_ucx_collision,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ucx_collision_props = PointerProperty(type=UCXCollisionProperties)
    # Apply Medium defaults once on fresh register if still at defaults
    # (Enum default already MEDIUM; update callback fires on change only,
    # so seed values explicitly to match Medium preset.)
    # Note: cannot access context.scene reliably at register time for all files;
    # property defaults already match Medium.


def unregister():
    if hasattr(bpy.types.Scene, "ucx_collision_props"):
        del bpy.types.Scene.ucx_collision_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
