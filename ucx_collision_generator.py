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
    "version": (1, 1, 0),
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


# ---------------------------------------------------------------------------
# Preset values
# ---------------------------------------------------------------------------

PRESETS = {
    "LOW": {
        "max_hulls": 4,
        "simplify": 0.08,
        "min_size": 0.05,
        "max_verts": 24,
        "remove_small": 0.02,
        "margin": 0.0,
        "accuracy": 0.35,
    },
    "MEDIUM": {
        "max_hulls": 10,
        "simplify": 0.20,
        "min_size": 0.02,
        "max_verts": 32,
        "remove_small": 0.01,
        "margin": 0.0,
        "accuracy": 0.55,
    },
    "HIGH": {
        "max_hulls": 20,
        "simplify": 0.40,
        "min_size": 0.01,
        "max_verts": 48,
        "remove_small": 0.005,
        "margin": 0.0,
        "accuracy": 0.75,
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
        default=10,
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


def _median_split_points(coords):
    """
    Split a point list by AABB longest axis at the median.
    Returns (left, right). Either may be empty on failure.
    """
    if len(coords) < 2:
        return list(coords), []

    _mn, _mx, size = _bbox_extents(coords)
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


def _partition_points(coords, max_parts: int, accuracy: float, remove_small: float):
    """
    Breadth-first AABB median splits until max_parts (or parts are small / few points).
    Always keeps every input point in exactly one part — good surface coverage.
    """
    if not coords:
        return []

    # Deduplicate nearly-identical verts to keep hulls stable
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
    # Higher accuracy => allow more splits toward max_parts
    target = max(1, int(round(1 + (max_parts - 1) * max(0.05, min(1.0, accuracy)))))
    target = min(max_parts, target)

    min_points = max(4, int(8 * (1.0 - accuracy) + 4))  # ~4..12

    safety = 0
    while len(parts) < target and safety < max_parts * 4:
        safety += 1
        # Split the largest remaining part
        idx = max(range(len(parts)), key=lambda i: _bbox_diagonal(parts[i]))
        part = parts[idx]
        diag = _bbox_diagonal(part)
        if len(part) < min_points * 2:
            break
        if remove_small > 0.0 and diag < remove_small * 2.0:
            break
        left, right = _median_split_points(part)
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

        # Vertex-space partition — every working vert contributes to coverage
        all_coords = [v.co.copy() for v in bm_work.verts]
        clusters = _partition_points(
            all_coords,
            max_parts=props.max_hulls,
            accuracy=props.accuracy,
            remove_small=effective_remove,
        )

        target_col = _ensure_ucx_collection(obj, props.create_collection)
        source_mw = obj.matrix_world.copy()

        hull_index = 0
        for coords in clusters:
            if hull_index >= props.max_hulls:
                break

            if len(coords) < 3:
                continue

            if effective_remove > 0.0 and _bbox_diagonal(coords) < effective_remove:
                continue

            hull_bm, reason = _build_hull_mesh(
                coords,
                margin=props.margin,
                max_verts=props.max_verts,
                min_size=effective_min,
            )
            if hull_bm is None:
                continue

            try:
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
