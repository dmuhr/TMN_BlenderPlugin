bl_info = {
    "name": "Miniature Voxeler",
    "author": "Diego Muhr",
    "version": (6, 0, 0),
    "blender": (5, 0, 1),
    "location": "3D View > Sidebar > Miniature Voxeler",
    "description": "Block remesh, transfer texture, create Lego-color face materials, and generate Lego skin meshes for miniature voxel workflows",
    "category": "Object",
}

import bpy
import bmesh
import gpu
import json
import os
import shutil
from collections import deque
from gpu_extras.batch import batch_for_shader
from math import atan2, cos, floor, hypot, log2, pi, radians, sin
from time import perf_counter
from bpy_extras import view3d_utils
from mathutils import Vector, geometry
from mathutils.bvhtree import BVHTree
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import (
    IntProperty,
    FloatProperty,
    FloatVectorProperty,
    BoolProperty,
    PointerProperty,
    StringProperty,
    EnumProperty,
)

ADDON_VERSION_TEXT = "v.6.0.0"
VOXEL_MESH_FORMAT_VERSION = 405
VOXEL_MESH_FORMAT_VERSION_KEY = "mv_voxel_mesh_format_version"


def srgb_channel_to_linear(value):
    value = value / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return (
        srgb_channel_to_linear(int(hex_color[0:2], 16)),
        srgb_channel_to_linear(int(hex_color[2:4], 16)),
        srgb_channel_to_linear(int(hex_color[4:6], 16)),
    )

FIXED_LEGO_PALETTE = [
    ("Deep Night", hex_to_linear_rgb("#0f0d1c")),
    ("Muted Periwinkle", hex_to_linear_rgb("#7b86a9")),
    ("Pale Ice Blue", hex_to_linear_rgb("#bbd1d3")),
    ("White", hex_to_linear_rgb("#ffffff")),
    ("Electric Cyan", hex_to_linear_rgb("#62e0f9")),
    ("Soft Cobalt", hex_to_linear_rgb("#5575c7")),
    ("Deep Violet", hex_to_linear_rgb("#4a3571")),
    ("Lavender Blue", hex_to_linear_rgb("#8d5fb3")),
    ("Light Cornflower", hex_to_linear_rgb("#9cbcff")),
    ("Royal Purple", hex_to_linear_rgb("#723a84")),
    ("Hot Magenta", hex_to_linear_rgb("#e955ae")),
    ("Peach", hex_to_linear_rgb("#ffc7a2")),
    ("Coral Red", hex_to_linear_rgb("#e86262")),
    ("Wine Rose", hex_to_linear_rgb("#943c59")),
    ("Warm Orange", hex_to_linear_rgb("#e88a4c")),
    ("Soft Yellow", hex_to_linear_rgb("#ffed6b")),
    ("Fresh Green", hex_to_linear_rgb("#67d758")),
    ("Deep Teal", hex_to_linear_rgb("#3b786c")),
    ("Aqua Teal", hex_to_linear_rgb("#4db8af")),
    ("Mint Glow", hex_to_linear_rgb("#a1ffce")),
]

FIXED_LEGO_PALETTE_ITEMS = [
    (str(index), name, f"Use {name} for this Lego color slot")
    for index, (name, color) in enumerate(FIXED_LEGO_PALETTE)
]

DEBUG_LEGO_COLORS = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (1.0, 1.0, 1.0),
)

WORKFLOW_ROLE_STYLES = {
    'SOURCE': {
        "label": "Source",
        "icon": 'OBJECT_DATA',
        "divider": "---- SOURCE ----",
    },
    'BUILDING': {
        "label": "Building",
        "icon": 'MOD_BUILD',
        "divider": "---- BUILDING ----",
    },
    'PLATFORM': {
        "label": "Platform",
        "icon": 'MESH_GRID',
        "divider": "---- PLATFORM ----",
    },
    'EXPORT': {
        "label": "Export",
        "icon": 'EXPORT',
        "divider": "---- EXPORT ----",
    },
}


def get_fixed_palette_color(slot_palette_index):
    return FIXED_LEGO_PALETTE[int(slot_palette_index)][1]


def find_nearest_fixed_palette_index(color):
    return min(
        range(len(FIXED_LEGO_PALETTE)),
        key=lambda index: color_distance_sq(color, FIXED_LEGO_PALETTE[index][1]),
    )


def set_material_base_color(material, color):
    if material is None:
        return

    color_rgba = (
        clamp01(color[0]),
        clamp01(color[1]),
        clamp01(color[2]),
        1.0,
    )

    material.diffuse_color = color_rgba

    if material.use_nodes and material.node_tree is not None:
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                node.inputs["Base Color"].default_value = color_rgba
                break


def get_material_base_color(material):
    if material is None:
        return (0.8, 0.8, 0.8)

    if material.use_nodes and material.node_tree is not None:
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                color = node.inputs["Base Color"].default_value
                return (float(color[0]), float(color[1]), float(color[2]))

    color = material.diffuse_color
    return (float(color[0]), float(color[1]), float(color[2]))


def apply_single_material_to_object(obj, material):
    mesh = obj.data
    mesh.materials.clear()
    mesh.materials.append(material)
    for poly in mesh.polygons:
        poly.material_index = 0
    mesh.update()


def get_workflow_role_style(role):
    return WORKFLOW_ROLE_STYLES.get(role, WORKFLOW_ROLE_STYLES['BUILDING'])


def update_palette_slot_material(settings, context, slot_index):
    color = get_fixed_palette_color(getattr(settings, f"lego_palette_slot_{slot_index + 1}"))
    setattr(settings, f"lego_palette_slot_color_{slot_index + 1}", color)

    obj = get_blocks_object(settings)
    if obj is None:
        apply_platform_foot_color_slot(settings)
        return
    if bool(obj.get("mv_debug_colors_active", False)):
        apply_platform_foot_color_slot(settings)
        return

    if slot_index < len(obj.data.materials):
        set_material_base_color(obj.data.materials[slot_index], color)
        obj.data.update()
    apply_platform_foot_color_slot(settings)


def update_lego_palette_slot_1(settings, context):
    update_palette_slot_material(settings, context, 0)


def update_lego_palette_slot_2(settings, context):
    update_palette_slot_material(settings, context, 1)


def update_lego_palette_slot_3(settings, context):
    update_palette_slot_material(settings, context, 2)


def update_lego_palette_slot_4(settings, context):
    update_palette_slot_material(settings, context, 3)


def update_color_skin_base_slot(settings, context):
    base_slot_index = int(settings.color_skin_base_slot)
    settings["color_skin_slot_1"] = (base_slot_index != 0)
    settings["color_skin_slot_2"] = (base_slot_index != 1)
    settings["color_skin_slot_3"] = (base_slot_index != 2)
    settings["color_skin_slot_4"] = (base_slot_index != 3)


def update_lego_color_count(settings, context):
    if settings.selected_lego_palette_slot >= settings.lego_color_count:
        settings.selected_lego_palette_slot = settings.lego_color_count - 1


def update_platform_foot_color_slot(settings, context):
    apply_platform_foot_color_slot(settings)


def get_source_validation_key(building_obj, platform_obj):
    if building_obj is None or platform_obj is None:
        return ""
    return f"{building_obj.name}|{platform_obj.name}"


def source_pair_is_validated(settings, building_obj, platform_obj):
    return settings.source_validation_key == get_source_validation_key(building_obj, platform_obj)


def get_building_object(settings):
    obj = getattr(settings, "building_object", None)
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_building_copy_object(settings):
    building_obj = get_building_object(settings)
    if building_obj is None:
        return None
    obj = bpy.data.objects.get(get_building_copy_name(get_root_name(building_obj.name)))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def create_building_body_copy(context, settings):
    building_obj = get_building_object(settings)
    if building_obj is None:
        return None

    root_name = get_root_name(building_obj.name)
    body_name = get_building_copy_name(root_name)
    existing_obj = bpy.data.objects.get(body_name)
    if existing_obj is not None:
        remove_object_if_exists(existing_obj)

    body_obj = building_obj.copy()
    body_obj.data = building_obj.data.copy()
    body_obj.name = body_name
    body_obj.data.name = body_name
    for collection in building_obj.users_collection:
        collection.objects.link(body_obj)
        break
    else:
        context.collection.objects.link(body_obj)

    set_metadata(body_obj, root_name, building_obj.name)
    body_obj.matrix_world = building_obj.matrix_world.copy()
    body_obj.hide_set(False)
    building_obj.hide_set(True)
    return body_obj


def ensure_building_body_object(context, settings):
    body_obj = get_building_copy_object(settings)
    if body_obj is not None:
        return body_obj
    return create_building_body_copy(context, settings)


def get_voxel_source_object(settings):
    copy_obj = get_building_copy_object(settings)
    if copy_obj is not None:
        return copy_obj
    return get_building_object(settings)


def get_platform_object(settings):
    obj = getattr(settings, "platform_object", None)
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def has_building_object(context):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return False
    return get_building_object(settings) is not None


def has_source_objects(context):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return False
    return get_building_object(settings) is not None and get_platform_object(settings) is not None


def get_blocks_object(settings):
    building_obj = get_building_object(settings)
    if building_obj is not None:
        blocks_name = get_blocks_name(get_root_name(building_obj.name))
        blocks_obj = bpy.data.objects.get(blocks_name)
        if blocks_obj is not None and blocks_obj.type == 'MESH':
            return blocks_obj

    candidates = [
        obj
        for obj in bpy.data.objects
        if obj.type == 'MESH' and obj.name.endswith("_Blocks") and obj.get("mv_voxel_cells_json", "")
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def has_blocks_object(context):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return False
    return get_blocks_object(settings) is not None


def get_texture_source_object(settings):
    override_name = settings.texture_source_name.strip()
    if override_name:
        return bpy.data.objects.get(override_name)

    return get_building_copy_object(settings)


def get_view3d_window_region(area):
    for region in area.regions:
        if region.type == 'WINDOW':
            return region
    return None


def is_event_in_region(region, event):
    return (
        region.x <= event.mouse_x <= region.x + region.width and
        region.y <= event.mouse_y <= region.y + region.height
    )


def is_event_in_view3d_ui_region(context, event):
    if context.area is None or context.area.type != 'VIEW_3D':
        return False

    for region in context.area.regions:
        if region.type == 'WINDOW':
            continue
        if region.type in {'UI', 'TOOLS', 'HEADER', 'TOOL_HEADER'} and is_event_in_region(region, event):
            return True

    return False


def raycast_active_face(context, event):
    obj, face_index, _, _ = raycast_active_face_details(context, event)
    return obj, face_index


def raycast_active_face_details(context, event):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return None, None, None, None

    obj = get_blocks_object(settings)
    if obj is None:
        return None, None, None, None

    if context.area is None or context.area.type != 'VIEW_3D':
        return None, None, None, None

    region = get_view3d_window_region(context.area)
    region_3d = getattr(context.space_data, "region_3d", None)
    if region is None or region_3d is None:
        return None, None, None, None

    mouse_x = event.mouse_x - region.x
    mouse_y = event.mouse_y - region.y

    if mouse_x < 0 or mouse_y < 0 or mouse_x > region.width or mouse_y > region.height:
        return None, None, None, None

    coord = (mouse_x, mouse_y)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, coord)
    ray_direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, coord)
    depsgraph = context.evaluated_depsgraph_get()

    hit, location, normal, face_index, hit_obj, matrix = context.scene.ray_cast(
        depsgraph,
        ray_origin,
        ray_direction,
    )

    if hit and hit_obj == obj and face_index >= 0:
        return obj, face_index, location, normal

    return obj, None, None, None


def raycast_face_at_region_coord(context, obj, region, region_3d, coord):
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, coord)
    ray_direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, coord)
    depsgraph = context.evaluated_depsgraph_get()

    hit, _location, _normal, face_index, hit_obj, _matrix = context.scene.ray_cast(
        depsgraph,
        ray_origin,
        ray_direction,
    )

    if hit and hit_obj == obj and face_index >= 0:
        return face_index
    return None


def get_mouse_region_coord(context, event):
    if context.area is None or context.area.type != 'VIEW_3D':
        return None, None, None

    region = get_view3d_window_region(context.area)
    region_3d = getattr(context.space_data, "region_3d", None)
    if region is None or region_3d is None:
        return None, None, None

    mouse_x = event.mouse_x - region.x
    mouse_y = event.mouse_y - region.y

    if mouse_x < 0 or mouse_y < 0 or mouse_x > region.width or mouse_y > region.height:
        return None, None, None

    return region, region_3d, (mouse_x, mouse_y)


def get_polygon_center(mesh, poly):
    center = Vector((0.0, 0.0, 0.0))
    for vertex_index in poly.vertices:
        center += mesh.vertices[vertex_index].co
    return center / max(1, len(poly.vertices))


def collect_brush_face_indices(context, event, obj, brush_size, face_centers_world=None):
    hit_obj, face_index = raycast_active_face(context, event)
    if hit_obj is None or face_index is None:
        return set()

    mesh = hit_obj.data
    radius = max(1.0, float(brush_size))

    if radius <= 1.0:
        return {face_index}

    region, region_3d, mouse_coord = get_mouse_region_coord(context, event)
    if region is None:
        return {face_index}

    radius_sq = radius * radius
    face_indices = {face_index}

    for poly in mesh.polygons:
        if face_centers_world is not None and poly.index < len(face_centers_world):
            center = face_centers_world[poly.index]
        else:
            center = hit_obj.matrix_world @ get_polygon_center(mesh, poly)
        screen_coord = view3d_utils.location_3d_to_region_2d(region, region_3d, center)
        if screen_coord is None:
            continue

        dx = screen_coord.x - mouse_coord[0]
        dy = screen_coord.y - mouse_coord[1]
        if (dx * dx) + (dy * dy) <= radius_sq and raycast_face_at_region_coord(context, hit_obj, region, region_3d, screen_coord) == poly.index:
            face_indices.add(poly.index)

    return face_indices


def collect_box_face_indices(context, obj, start_coord, end_coord, face_centers_world=None):
    if obj is None or start_coord is None or end_coord is None:
        return set()

    region = get_view3d_window_region(context.area)
    region_3d = getattr(context.space_data, "region_3d", None)
    if region is None or region_3d is None:
        return set()

    min_x = min(start_coord[0], end_coord[0])
    max_x = max(start_coord[0], end_coord[0])
    min_y = min(start_coord[1], end_coord[1])
    max_y = max(start_coord[1], end_coord[1])
    mesh = obj.data
    face_indices = set()

    for poly in mesh.polygons:
        if face_centers_world is not None and poly.index < len(face_centers_world):
            center = face_centers_world[poly.index]
        else:
            center = obj.matrix_world @ get_polygon_center(mesh, poly)
        screen_coord = view3d_utils.location_3d_to_region_2d(region, region_3d, center)
        if screen_coord is None:
            continue
        if not (min_x <= screen_coord.x <= max_x and min_y <= screen_coord.y <= max_y):
            continue
        if raycast_face_at_region_coord(context, obj, region, region_3d, screen_coord) == poly.index:
            face_indices.add(poly.index)

    return face_indices


def normalize_lasso_coords(lasso_coords):
    coords = []
    for coord in lasso_coords:
        point = (float(coord[0]), float(coord[1]))
        if not coords or hypot(point[0] - coords[-1][0], point[1] - coords[-1][1]) >= 0.5:
            coords.append(point)
    if len(coords) > 2 and hypot(coords[0][0] - coords[-1][0], coords[0][1] - coords[-1][1]) < 0.5:
        coords.pop()
    return coords


def point_in_triangle_2d(point, a, b, c):
    px, py = point
    ax, ay = a
    bx, by = b
    cx, cy = c

    d1 = ((px - bx) * (ay - by)) - ((ax - bx) * (py - by))
    d2 = ((px - cx) * (by - cy)) - ((bx - cx) * (py - cy))
    d3 = ((px - ax) * (cy - ay)) - ((cx - ax) * (py - ay))
    has_negative = d1 < -1e-6 or d2 < -1e-6 or d3 < -1e-6
    has_positive = d1 > 1e-6 or d2 > 1e-6 or d3 > 1e-6
    return not (has_negative and has_positive)


def build_lasso_triangles_2d(lasso_coords):
    coords = normalize_lasso_coords(lasso_coords)
    if len(coords) < 3:
        return [], []

    vertices = [Vector((coord[0], coord[1], 0.0)) for coord in coords]
    try:
        triangle_indices = geometry.tessellate_polygon([vertices])
    except Exception:
        triangle_indices = []

    triangles = []
    for triangle in triangle_indices:
        if len(triangle) == 3:
            triangles.append((coords[triangle[0]], coords[triangle[1]], coords[triangle[2]]))
    return coords, triangles


def point_in_lasso_triangles_2d(point, triangles):
    return any(point_in_triangle_2d(point, a, b, c) for a, b, c in triangles)


def collect_lasso_face_indices(context, obj, lasso_coords, face_centers_world=None):
    if obj is None or not lasso_coords or len(lasso_coords) < 3:
        return set()

    lasso_coords, lasso_triangles = build_lasso_triangles_2d(lasso_coords)
    if not lasso_triangles:
        return set()

    region = get_view3d_window_region(context.area)
    region_3d = getattr(context.space_data, "region_3d", None)
    if region is None or region_3d is None:
        return set()

    min_x = min(coord[0] for coord in lasso_coords)
    max_x = max(coord[0] for coord in lasso_coords)
    min_y = min(coord[1] for coord in lasso_coords)
    max_y = max(coord[1] for coord in lasso_coords)
    mesh = obj.data
    face_indices = set()

    for poly in mesh.polygons:
        if face_centers_world is not None and poly.index < len(face_centers_world):
            center = face_centers_world[poly.index]
        else:
            center = obj.matrix_world @ get_polygon_center(mesh, poly)
        screen_coord = view3d_utils.location_3d_to_region_2d(region, region_3d, center)
        if screen_coord is None:
            continue
        if not (min_x <= screen_coord.x <= max_x and min_y <= screen_coord.y <= max_y):
            continue
        if not point_in_lasso_triangles_2d((screen_coord.x, screen_coord.y), lasso_triangles):
            continue
        if raycast_face_at_region_coord(context, obj, region, region_3d, screen_coord) == poly.index:
            face_indices.add(poly.index)

    return face_indices


def get_voxel_face_key_from_mesh(mesh, face_index):
    return (
        get_face_attribute_value(mesh, "mv_cell_i", face_index),
        get_face_attribute_value(mesh, "mv_cell_j", face_index),
        get_face_attribute_value(mesh, "mv_cell_k", face_index),
        get_face_attribute_value(mesh, "mv_face_dir", face_index),
    )


def get_voxel_cell_key_from_mesh(mesh, face_index):
    key = get_voxel_face_key_from_mesh(mesh, face_index)
    return key[:3]


def serialize_voxel_face_slots(face_slots):
    data = [
        [key[0], key[1], key[2], key[3], slot]
        for key, slot in sorted(face_slots.items())
    ]
    return json.dumps(data, separators=(",", ":"))


def deserialize_voxel_face_slots(obj):
    raw = obj.get("mv_voxel_face_slots_json", "")
    if not raw:
        return {}

    try:
        values = json.loads(raw)
    except Exception:
        return {}

    face_slots = {}
    for item in values:
        if len(item) != 5:
            continue
        face_slots[(int(item[0]), int(item[1]), int(item[2]), int(item[3]))] = int(item[4])
    return face_slots


def collect_voxel_face_slots_from_mesh(obj, cells=None):
    mesh = obj.data
    face_slots = {}
    if not mesh.polygons:
        return face_slots
    required_attrs = ("mv_cell_i", "mv_cell_j", "mv_cell_k", "mv_face_dir")
    if any(mesh.attributes.get(attr_name) is None for attr_name in required_attrs):
        return face_slots

    for poly in mesh.polygons:
        key = get_voxel_face_key_from_mesh(mesh, poly.index)
        if key[3] < 0:
            continue
        face_slots[key] = int(poly.material_index)
    return face_slots


def store_voxel_face_slots(obj, face_slots):
    obj["mv_voxel_face_slots_json"] = serialize_voxel_face_slots(face_slots)


def sync_voxel_cell_slots_from_face_slots(cells, face_slots):
    counts_by_cell = {}
    for key, slot in face_slots.items():
        cell = key[:3]
        counts = counts_by_cell.setdefault(cell, {})
        counts[int(slot)] = counts.get(int(slot), 0) + 1
    for cell, counts in counts_by_cell.items():
        if cell in cells and counts:
            cells[cell] = max(counts.items(), key=lambda item: (item[1], -item[0]))[0]
    return cells


def sync_voxel_color_state_from_mesh(obj):
    cells = deserialize_voxel_cells(obj)
    if not cells:
        return {}
    face_slots = collect_voxel_face_slots_from_mesh(obj, cells)
    sync_voxel_cell_slots_from_face_slots(cells, face_slots)
    store_voxel_face_slots(obj, face_slots)
    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    if origin is not None and voxel_size > 0.0:
        store_voxel_state(obj, origin, voxel_size, cells)
    return face_slots


def assign_bottom_voxel_faces_to_slot(obj, slot_index):
    if obj is None or obj.type != 'MESH' or not obj.data.polygons:
        return 0

    mesh = obj.data
    slot_index = max(0, min(int(slot_index), max(0, len(mesh.materials) - 1)))
    face_dirs = get_face_int_attribute_values(mesh, "mv_face_dir", default_value=-1)
    changed_count = 0

    for poly in mesh.polygons:
        is_bottom = face_dirs[poly.index] == 5
        if not is_bottom:
            world_normal = obj.matrix_world.to_3x3() @ poly.normal
            is_bottom = world_normal.z < -0.9
        if not is_bottom or poly.material_index == slot_index:
            continue
        poly.material_index = slot_index
        changed_count += 1

    if changed_count:
        cells = deserialize_voxel_cells(obj)
        face_slots = collect_voxel_face_slots_from_mesh(obj, cells)
        store_voxel_face_slots(obj, face_slots)
        mesh.update()

    return changed_count


def get_voxel_face_centers_world(obj):
    mesh = obj.data
    matrix = obj.matrix_world
    return [matrix @ get_polygon_center(mesh, poly) for poly in mesh.polygons]


def get_voxel_cell_world_corners(obj, coord, inflate=0.0):
    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    if origin is None or voxel_size <= 0.0:
        return []

    i, j, k = coord
    base = origin + Vector((i * voxel_size, j * voxel_size, k * voxel_size))
    low = -float(inflate)
    high = 1.0 + float(inflate)
    corners = [
        obj.matrix_world @ Vector((base.x + (x * voxel_size), base.y + (y * voxel_size), base.z + (z * voxel_size)))
        for x, y, z in (
            (low, low, low), (high, low, low), (high, high, low), (low, high, low),
            (low, low, high), (high, low, high), (high, high, high), (low, high, high),
        )
    ]
    return corners


def get_voxel_cell_wire_coords(obj, coord, inflate=0.0):
    corners = get_voxel_cell_world_corners(obj, coord, inflate)
    if not corners:
        return []
    edge_indices = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    coords = []
    for start, end in edge_indices:
        coords.append(tuple(corners[start]))
        coords.append(tuple(corners[end]))
    return coords


def get_voxel_cell_face_coords(obj, coord, inflate=0.0):
    corners = get_voxel_cell_world_corners(obj, coord, inflate)
    if not corners:
        return []
    face_indices = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    )
    coords = []
    for a, b, c, d in face_indices:
        coords.extend((tuple(corners[a]), tuple(corners[b]), tuple(corners[c])))
        coords.extend((tuple(corners[a]), tuple(corners[c]), tuple(corners[d])))
    return coords


def draw_voxel_wire_cells(obj, coords, color):
    if obj is None or not coords:
        return
    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except ValueError:
        shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.0)
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
    except Exception:
        pass
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    try:
        gpu.state.depth_test_set('NONE')
    except Exception:
        pass
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def draw_voxel_transparent_cells(obj, coords, color):
    if obj is None or not coords:
        return
    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except ValueError:
        shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": coords})
    gpu.state.blend_set('ALPHA')
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
    except Exception:
        pass
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    try:
        gpu.state.depth_test_set('NONE')
    except Exception:
        pass
    gpu.state.blend_set('NONE')


def paint_faces_with_brush(context, event, obj, slot_index, brush_size, face_slots=None, commit_face_slots=True, face_centers_world=None, undo_batch=None):
    face_indices = collect_brush_face_indices(context, event, obj, brush_size, face_centers_world)
    if not face_indices:
        return 0

    return paint_face_indices(obj, slot_index, face_indices, face_slots, commit_face_slots, undo_batch)


def paint_face_indices(obj, slot_index, face_indices, face_slots=None, commit_face_slots=True, undo_batch=None):
    mesh = obj.data
    changed_count = 0

    for paint_face_index in face_indices:
        previous_slot = int(mesh.polygons[paint_face_index].material_index)
        if mesh.polygons[paint_face_index].material_index != slot_index:
            if undo_batch is not None:
                undo_batch.append({
                    "face_index": int(paint_face_index),
                    "face_key": get_voxel_face_key_from_mesh(mesh, paint_face_index),
                    "previous_slot": previous_slot,
                    "new_slot": int(slot_index),
                })
            mesh.polygons[paint_face_index].material_index = slot_index
            changed_count += 1
        if face_slots is not None:
            key = get_voxel_face_key_from_mesh(mesh, paint_face_index)
            if key[3] >= 0:
                face_slots[key] = int(slot_index)

    if changed_count:
        if face_slots is not None and commit_face_slots:
            store_voxel_face_slots(obj, face_slots)
        mesh.update()
    return changed_count


def assign_selected_edit_faces_to_slot(obj, settings, slot_index):
    if obj is None or obj.type != 'MESH':
        return 0

    mesh = obj.data
    for material_slot_index in range(settings.lego_color_count):
        material = ensure_lego_color_material(
            obj,
            material_slot_index,
            get_slot_palette_color(settings, material_slot_index),
        )
        if material_slot_index < len(mesh.materials):
            mesh.materials[material_slot_index] = material
        else:
            mesh.materials.append(material)

    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    selected_faces = [face for face in bm.faces if face.select and face.is_valid]
    if not selected_faces:
        return 0

    face_slots = deserialize_voxel_face_slots(obj)
    if not face_slots:
        face_slots = collect_voxel_face_slots_from_mesh(obj)

    changed_count = 0
    for face in selected_faces:
        if face.material_index != slot_index:
            face.material_index = slot_index
            changed_count += 1
        key = get_voxel_face_key_from_mesh(mesh, face.index)
        if key[3] >= 0:
            face_slots[key] = int(slot_index)

    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    store_voxel_face_slots(obj, face_slots)
    cells = deserialize_voxel_cells(obj)
    if cells:
        sync_voxel_cell_slots_from_face_slots(cells, face_slots)
        origin = get_stored_voxel_origin(obj)
        voxel_size = float(obj.get("mv_voxel_size", 0.0))
        if origin is not None and voxel_size > 0.0:
            store_voxel_state(obj, origin, voxel_size, cells)
    return changed_count


def get_voxel_size_scene_units(context, settings, source_obj):
    explicit_size = mm_to_scene_units(context, settings.voxel_size_mm)
    if explicit_size > 0.0:
        return explicit_size

    max_dimension = max(float(source_obj.dimensions.x), float(source_obj.dimensions.y), float(source_obj.dimensions.z))
    derived_size = (max_dimension / max(1, 2 ** settings.octree_depth)) * max(settings.scale, 0.0001)
    minimum_size = mm_to_scene_units(context, 0.1)
    return max(derived_size, minimum_size)


def get_object_world_bounds(obj):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_corner = Vector((
        min(corner.x for corner in corners),
        min(corner.y for corner in corners),
        min(corner.z for corner in corners),
    ))
    max_corner = Vector((
        max(corner.x for corner in corners),
        max(corner.y for corner in corners),
        max(corner.z for corner in corners),
    ))
    return min_corner, max_corner


def point_in_polygon_xy(point, polygon):
    if len(polygon) < 3:
        return False

    x, y = point
    inside = False
    for index, coord in enumerate(polygon):
        x1, y1 = coord[0], coord[1]
        next_coord = polygon[(index + 1) % len(polygon)]
        x2, y2 = next_coord[0], next_coord[1]
        if (y1 > y) == (y2 > y):
            continue
        hit_x = (x2 - x1) * ((y - y1) / max(1e-12, (y2 - y1))) + x1
        if x < hit_x:
            inside = not inside
    return inside


def orientation_xy(a, b, c):
    value = ((b[1] - a[1]) * (c[0] - b[0])) - ((b[0] - a[0]) * (c[1] - b[1]))
    if abs(value) <= 1e-9:
        return 0
    return 1 if value > 0.0 else 2


def on_segment_xy(a, b, c):
    return (
        min(a[0], c[0]) - 1e-9 <= b[0] <= max(a[0], c[0]) + 1e-9 and
        min(a[1], c[1]) - 1e-9 <= b[1] <= max(a[1], c[1]) + 1e-9
    )


def segments_intersect_xy(a1, a2, b1, b2):
    o1 = orientation_xy(a1, a2, b1)
    o2 = orientation_xy(a1, a2, b2)
    o3 = orientation_xy(b1, b2, a1)
    o4 = orientation_xy(b1, b2, a2)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment_xy(a1, b1, a2):
        return True
    if o2 == 0 and on_segment_xy(a1, b2, a2):
        return True
    if o3 == 0 and on_segment_xy(b1, a1, b2):
        return True
    if o4 == 0 and on_segment_xy(b1, a2, b2):
        return True
    return False


def polygon_intersects_rect_xy(polygon, rect_min, rect_max):
    if len(polygon) < 3:
        return False

    rect_points = [
        (rect_min[0], rect_min[1]),
        (rect_max[0], rect_min[1]),
        (rect_max[0], rect_max[1]),
        (rect_min[0], rect_max[1]),
    ]
    rect_edges = list(zip(rect_points, rect_points[1:] + rect_points[:1]))
    poly_points = [(coord[0], coord[1]) for coord in polygon]
    poly_edges = list(zip(poly_points, poly_points[1:] + poly_points[:1]))

    for point in rect_points:
        if point_in_polygon_xy(point, polygon):
            return True

    for point in poly_points:
        if rect_min[0] <= point[0] <= rect_max[0] and rect_min[1] <= point[1] <= rect_max[1]:
            return True

    for rect_a, rect_b in rect_edges:
        for poly_a, poly_b in poly_edges:
            if segments_intersect_xy(rect_a, rect_b, poly_a, poly_b):
                return True

    return False


def get_platform_keepout_data(settings):
    walls_obj = get_platform_walls_object(settings)
    if walls_obj is None:
        return None

    rings, _ = get_stored_platform_rings_data(walls_obj)
    rings = [coords for coords in rings if len(coords) >= 3]
    if not rings:
        return None

    top_z = max(
        float(sum(coord[2] for coord in coords) / len(coords))
        for coords in rings
    )
    return {
        "polygons": rings,
        "top_z": top_z,
    }


def cell_intersects_top_loop_keepout(cell_center, voxel_size, keepout, z_floor):
    if keepout is None:
        return False

    half = voxel_size * 0.5
    cell_min = Vector((cell_center.x - half, cell_center.y - half, cell_center.z - half))
    cell_max = Vector((cell_center.x + half, cell_center.y + half, cell_center.z + half))
    if cell_max.z <= z_floor or cell_min.z >= keepout["top_z"]:
        return False

    for polygon in keepout.get("polygons", []):
        if polygon_intersects_rect_xy(
            polygon,
            (cell_min.x, cell_min.y),
            (cell_max.x, cell_max.y),
        ):
            return True
    return False


def count_unique_ray_hits(obj_eval, point_local, direction, ray_distance, merge_distance):
    origin = point_local.copy()
    hits = 0
    last_location = None

    for _ in range(512):
        hit, location, normal, face_index = obj_eval.ray_cast(origin, direction, distance=ray_distance)
        if not hit or face_index < 0:
            break
        if last_location is None or (location - last_location).length > merge_distance:
            hits += 1
            last_location = location.copy()
        origin = location + (direction * merge_distance)

    return hits


def is_point_inside_evaluated_mesh(obj_eval, point_world, ray_distance, epsilon=1e-6):
    inverse_world = obj_eval.matrix_world.inverted()
    point_local = inverse_world @ point_world
    directions = (
        Vector((1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
        Vector((0.819, 0.337, 0.463)).normalized(),
        Vector((-0.271, 0.914, 0.302)).normalized(),
    )
    merge_distance = max(float(epsilon), float(ray_distance) * 1e-7)
    inside_votes = 0

    for index, direction in enumerate(directions):
        hits = count_unique_ray_hits(obj_eval, point_local, direction, ray_distance, merge_distance)
        if hits % 2 == 1:
            inside_votes += 1
        checked_count = index + 1
        remaining_count = len(directions) - checked_count
        if inside_votes >= 3:
            return True
        if inside_votes + remaining_count < 3:
            return False

    return False


def collect_top_open_empty_voxels(cells, counts):
    protected = set()
    if not cells:
        return protected

    max_i, max_j, max_k = counts
    for i in range(max_i):
        for j in range(max_j):
            for k in range(max_k - 1, -1, -1):
                coord = (i, j, k)
                if coord in cells:
                    break
                protected.add(coord)
    return protected


def collect_exterior_empty_voxels(cells, counts, seed_empty=None):
    outside = set()
    queue = deque()
    max_i, max_j, max_k = counts

    def enqueue_if_empty(coord):
        if (
            coord[0] < 0 or coord[0] >= max_i or
            coord[1] < 0 or coord[1] >= max_j or
            coord[2] < 0 or coord[2] >= max_k
        ):
            return
        if coord in cells or coord in outside:
            return
        outside.add(coord)
        queue.append(coord)

    if seed_empty:
        for coord in seed_empty:
            enqueue_if_empty(coord)

    for i in range(max_i):
        for j in range(max_j):
            enqueue_if_empty((i, j, 0))
            enqueue_if_empty((i, j, max_k - 1))
    for i in range(max_i):
        for k in range(max_k):
            enqueue_if_empty((i, 0, k))
            enqueue_if_empty((i, max_j - 1, k))
    for j in range(max_j):
        for k in range(max_k):
            enqueue_if_empty((0, j, k))
            enqueue_if_empty((max_i - 1, j, k))

    neighbors = (
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
        (0, 0, 1),
        (0, 0, -1),
    )
    while queue:
        i, j, k = queue.popleft()
        for di, dj, dk in neighbors:
            enqueue_if_empty((i + di, j + dj, k + dk))

    return outside


def fill_enclosed_voxel_cavities(cells, counts, fill_slot=0, protected_empty=None, should_fill_empty=None):
    if not cells:
        return 0

    if protected_empty is None:
        protected_empty = set()

    max_i, max_j, max_k = counts
    outside = set(protected_empty)
    queue = deque()

    def enqueue_if_empty(coord):
        if coord in cells or coord in outside:
            return
        outside.add(coord)
        queue.append(coord)

    for i in range(max_i):
        for j in range(max_j):
            enqueue_if_empty((i, j, 0))
            enqueue_if_empty((i, j, max_k - 1))
    for i in range(max_i):
        for k in range(max_k):
            enqueue_if_empty((i, 0, k))
            enqueue_if_empty((i, max_j - 1, k))
    for j in range(max_j):
        for k in range(max_k):
            enqueue_if_empty((0, j, k))
            enqueue_if_empty((max_i - 1, j, k))

    neighbors = (
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
        (0, 0, 1),
        (0, 0, -1),
    )
    while queue:
        i, j, k = queue.popleft()
        for di, dj, dk in neighbors:
            coord = (i + di, j + dj, k + dk)
            if (
                coord[0] < 0 or coord[0] >= max_i or
                coord[1] < 0 or coord[1] >= max_j or
                coord[2] < 0 or coord[2] >= max_k
            ):
                continue
            enqueue_if_empty(coord)

    filled_count = 0
    for i in range(max_i):
        for j in range(max_j):
            for k in range(max_k):
                coord = (i, j, k)
                if coord in cells or coord in outside:
                    continue
                if coord in protected_empty:
                    continue
                if should_fill_empty is not None and not should_fill_empty(coord):
                    continue
                cells[coord] = int(fill_slot)
                filled_count += 1

    return filled_count


def fill_vertical_voxel_columns(cells, fill_slot=0, protected_empty=None, should_fill_empty=None):
    if not cells:
        return 0

    if protected_empty is None:
        protected_empty = set()

    z_bounds_by_column = {}
    for i, j, k in cells.keys():
        key = (i, j)
        bounds = z_bounds_by_column.get(key)
        if bounds is None:
            z_bounds_by_column[key] = [k, k]
        else:
            bounds[0] = min(bounds[0], k)
            bounds[1] = max(bounds[1], k)

    filled_count = 0
    for (i, j), (min_k, max_k) in z_bounds_by_column.items():
        for k in range(min_k, max_k + 1):
            coord = (i, j, k)
            if coord in cells:
                continue
            if coord in protected_empty:
                continue
            if should_fill_empty is not None and not should_fill_empty(coord):
                continue
            cells[coord] = int(fill_slot)
            filled_count += 1

    return filled_count


def fill_enclosed_xy_voxel_slice_holes(cells, fill_slot=0, protected_empty=None, should_fill_empty=None):
    if not cells:
        return 0

    if protected_empty is None:
        protected_empty = set()

    cells_by_z = {}
    for i, j, k in cells.keys():
        cells_by_z.setdefault(k, set()).add((i, j))

    xy_neighbors = (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
    )
    filled_count = 0

    for k, occupied in cells_by_z.items():
        min_i = min(coord[0] for coord in occupied) - 1
        max_i = max(coord[0] for coord in occupied) + 1
        min_j = min(coord[1] for coord in occupied) - 1
        max_j = max(coord[1] for coord in occupied) + 1

        outside = {(min_i, min_j)}
        queue = deque([(min_i, min_j)])
        while queue:
            i, j = queue.popleft()
            for di, dj in xy_neighbors:
                neighbor = (i + di, j + dj)
                if neighbor in outside or neighbor in occupied:
                    continue
                if min_i <= neighbor[0] <= max_i and min_j <= neighbor[1] <= max_j:
                    outside.add(neighbor)
                    queue.append(neighbor)

        for i in range(min_i + 1, max_i):
            for j in range(min_j + 1, max_j):
                if (i, j) in occupied or (i, j) in outside:
                    continue
                coord = (i, j, k)
                if coord in cells:
                    continue
                if coord in protected_empty:
                    continue
                if should_fill_empty is not None and not should_fill_empty(coord):
                    continue
                cells[coord] = int(fill_slot)
                filled_count += 1

    return filled_count


def clamp_voxel_index(value, limit):
    return max(0, min(int(value), int(limit) - 1))


def get_voxel_index(value, origin_value, voxel_size):
    return int(floor((float(value) - float(origin_value)) / float(voxel_size)))


def triangle_intersects_aabb(triangle, box_min, box_max):
    tri_min = Vector((
        min(vertex.x for vertex in triangle),
        min(vertex.y for vertex in triangle),
        min(vertex.z for vertex in triangle),
    ))
    tri_max = Vector((
        max(vertex.x for vertex in triangle),
        max(vertex.y for vertex in triangle),
        max(vertex.z for vertex in triangle),
    ))
    if (
        tri_max.x < box_min.x or tri_min.x > box_max.x or
        tri_max.y < box_min.y or tri_min.y > box_max.y or
        tri_max.z < box_min.z or tri_min.z > box_max.z
    ):
        return False

    center = (box_min + box_max) * 0.5
    half = (box_max - box_min) * 0.5
    a, b, c = triangle
    edge_ab = b - a
    edge_bc = c - b
    edge_ca = a - c
    normal = edge_ab.cross(edge_bc)
    if normal.length <= 1e-12:
        return False

    plane_radius = (
        half.x * abs(normal.x) +
        half.y * abs(normal.y) +
        half.z * abs(normal.z)
    )
    plane_distance = normal.dot(center - a)
    if abs(plane_distance) > plane_radius:
        return False

    box_axes = (
        Vector((1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
    )
    edges = (edge_ab, edge_bc, edge_ca)
    for edge in edges:
        for box_axis in box_axes:
            axis = edge.cross(box_axis)
            if axis.length <= 1e-12:
                continue
            tri_offsets = [axis.dot(vertex - center) for vertex in triangle]
            tri_min_proj = min(tri_offsets)
            tri_max_proj = max(tri_offsets)
            box_radius = (
                half.x * abs(axis.x) +
                half.y * abs(axis.y) +
                half.z * abs(axis.z)
            )
            if tri_min_proj > box_radius or tri_max_proj < -box_radius:
                return False

    return True


def mark_triangle_surface_voxels(triangle, origin, voxel_size, counts, cells, slot_index=0):
    tri_min = Vector((
        min(vertex.x for vertex in triangle),
        min(vertex.y for vertex in triangle),
        min(vertex.z for vertex in triangle),
    ))
    tri_max = Vector((
        max(vertex.x for vertex in triangle),
        max(vertex.y for vertex in triangle),
        max(vertex.z for vertex in triangle),
    ))
    min_i = clamp_voxel_index(get_voxel_index(tri_min.x, origin.x, voxel_size), counts[0])
    min_j = clamp_voxel_index(get_voxel_index(tri_min.y, origin.y, voxel_size), counts[1])
    min_k = clamp_voxel_index(get_voxel_index(tri_min.z, origin.z, voxel_size), counts[2])
    max_i = clamp_voxel_index(get_voxel_index(tri_max.x, origin.x, voxel_size), counts[0])
    max_j = clamp_voxel_index(get_voxel_index(tri_max.y, origin.y, voxel_size), counts[1])
    max_k = clamp_voxel_index(get_voxel_index(tri_max.z, origin.z, voxel_size), counts[2])

    for i in range(min_i, max_i + 1):
        x = origin.x + (i * voxel_size)
        for j in range(min_j, max_j + 1):
            y = origin.y + (j * voxel_size)
            for k in range(min_k, max_k + 1):
                z = origin.z + (k * voxel_size)
                box_min = Vector((x, y, z))
                box_max = box_min + Vector((voxel_size, voxel_size, voxel_size))
                if triangle_intersects_aabb(triangle, box_min, box_max):
                    cells[(i, j, k)] = int(slot_index)


def mark_surface_voxel_cells_from_object(obj_eval, origin, voxel_size, counts, cells):
    mesh = obj_eval.to_mesh()
    if mesh is None:
        return 0

    start_count = len(cells)
    try:
        world_vertices = [obj_eval.matrix_world @ vertex.co for vertex in mesh.vertices]
        for poly in mesh.polygons:
            indices = list(poly.vertices)
            if len(indices) < 3:
                continue
            first = indices[0]
            for index in range(1, len(indices) - 1):
                triangle = (
                    world_vertices[first],
                    world_vertices[indices[index]],
                    world_vertices[indices[index + 1]],
                )
                mark_triangle_surface_voxels(triangle, origin, voxel_size, counts, cells)
    finally:
        obj_eval.to_mesh_clear()

    return len(cells) - start_count


def get_voxel_cell_face_vectors():
    return [
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
        (0, 0, 1),
        (0, 0, -1),
    ]


def serialize_voxel_cells(cells):
    data = [[coord[0], coord[1], coord[2], slot] for coord, slot in sorted(cells.items())]
    return json.dumps(data, separators=(",", ":"))


def deserialize_voxel_cells(obj):
    raw = obj.get("mv_voxel_cells_json", "")
    if not raw:
        return {}

    cells = {}
    try:
        values = json.loads(raw)
    except Exception:
        return {}

    for item in values:
        if len(item) != 4:
            continue
        cells[(int(item[0]), int(item[1]), int(item[2]))] = int(item[3])
    return cells


def store_voxel_state(obj, origin, voxel_size, cells):
    obj["mv_voxel_origin"] = [float(origin.x), float(origin.y), float(origin.z)]
    obj["mv_voxel_size"] = float(voxel_size)
    obj["mv_voxel_cells_json"] = serialize_voxel_cells(cells)


def get_stored_voxel_origin(obj):
    values = obj.get("mv_voxel_origin", None)
    if values is None or len(values) != 3:
        return None
    return Vector((float(values[0]), float(values[1]), float(values[2])))


def ensure_face_int_attribute(mesh, name, values):
    existing = mesh.attributes.get(name)
    if existing is not None:
        mesh.attributes.remove(existing)
    attribute = mesh.attributes.new(name=name, type='INT', domain='FACE')
    attribute.data.foreach_set("value", values)


def get_face_int_attribute_values(mesh, name, default_value=0):
    attribute = mesh.attributes.get(name)
    if attribute is None or len(attribute.data) != len(mesh.polygons):
        return [default_value] * len(mesh.polygons)
    return [int(item.value) for item in attribute.data]


def get_voxel_mesh_format_version(obj):
    try:
        return int(obj.get(VOXEL_MESH_FORMAT_VERSION_KEY, 0))
    except Exception:
        return 0


def mark_voxel_mesh_format_current(obj):
    obj[VOXEL_MESH_FORMAT_VERSION_KEY] = int(VOXEL_MESH_FORMAT_VERSION)


def voxel_mesh_has_open_or_inverted_surface(obj):
    if obj is None or obj.type != 'MESH' or not obj.data.polygons:
        return False

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        has_bad_edges = any(len(edge.link_faces) != 2 for edge in bm.edges)
        if has_bad_edges:
            return True
        return bm.calc_volume(signed=True) <= 0.0
    finally:
        bm.free()


def rebuild_voxel_mesh_from_cells(obj, origin, voxel_size, cells, face_slots=None, store_state=True):
    if face_slots is None:
        face_slots = deserialize_voxel_face_slots(obj)
        if not face_slots and obj.data.polygons:
            face_slots = collect_voxel_face_slots_from_mesh(obj, cells)

    directions = [
        ((1, 0, 0), ((1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0))),
        ((-1, 0, 0), ((0, 1, 0), (0, 1, 1), (0, 0, 1), (0, 0, 0))),
        ((0, 1, 0), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))),
        ((0, -1, 0), ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0))),
        ((0, 0, 1), ((0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1))),
        ((0, 0, -1), ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))),
    ]

    verts = []
    faces = []
    material_indices = []
    face_cell_i = []
    face_cell_j = []
    face_cell_k = []
    face_dir = []
    vert_map = {}

    def get_vert_index(grid_key):
        if grid_key in vert_map:
            return vert_map[grid_key]
        index = len(verts)
        verts.append((
            origin.x + (grid_key[0] * voxel_size),
            origin.y + (grid_key[1] * voxel_size),
            origin.z + (grid_key[2] * voxel_size),
        ))
        vert_map[grid_key] = index
        return index

    for coord, slot_index in sorted(cells.items()):
        i, j, k = coord
        for dir_index, (neighbor_offset, corners) in enumerate(directions):
            neighbor = (
                i + neighbor_offset[0],
                j + neighbor_offset[1],
                k + neighbor_offset[2],
            )
            if neighbor in cells:
                continue

            face = []
            for corner in corners:
                face.append(get_vert_index((i + corner[0], j + corner[1], k + corner[2])))
            faces.append(tuple(reversed(face)))
            face_key = (i, j, k, dir_index)
            material_indices.append(max(0, int(face_slots.get(face_key, slot_index))))
            face_cell_i.append(i)
            face_cell_j.append(j)
            face_cell_k.append(k)
            face_dir.append(dir_index)

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()

    for poly_index, poly in enumerate(obj.data.polygons):
        poly.material_index = material_indices[poly_index] if poly_index < len(material_indices) else 0

    if faces:
        ensure_face_int_attribute(obj.data, "mv_cell_i", face_cell_i)
        ensure_face_int_attribute(obj.data, "mv_cell_j", face_cell_j)
        ensure_face_int_attribute(obj.data, "mv_cell_k", face_cell_k)
        ensure_face_int_attribute(obj.data, "mv_face_dir", face_dir)

    visible_face_slots = {
        (face_cell_i[index], face_cell_j[index], face_cell_k[index], face_dir[index]): int(material_indices[index])
        for index in range(len(material_indices))
    }
    if store_state:
        store_voxel_face_slots(obj, visible_face_slots)
        store_voxel_state(obj, origin, voxel_size, cells)
    mark_voxel_mesh_format_current(obj)
    obj.data.update()


def ensure_current_voxel_mesh_format(obj):
    if obj is None:
        return False

    needs_rebuild = (
        get_voxel_mesh_format_version(obj) < VOXEL_MESH_FORMAT_VERSION or
        voxel_mesh_has_open_or_inverted_surface(obj)
    )
    if not needs_rebuild:
        return False

    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    cells = deserialize_voxel_cells(obj)
    if origin is None or voxel_size <= 0.0 or not cells:
        return False

    face_slots = collect_voxel_face_slots_from_mesh(obj, cells)
    if not face_slots:
        face_slots = deserialize_voxel_face_slots(obj)
    else:
        sync_voxel_cell_slots_from_face_slots(cells, face_slots)

    rebuild_voxel_mesh_from_cells(obj, origin, voxel_size, cells, face_slots)
    return True


def remove_xy_voxel_wall_layers(obj, layer_count):
    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    if origin is None or voxel_size <= 0.0:
        return None

    cells = deserialize_voxel_cells(obj)
    if not cells:
        return 0

    layer_count = max(0, int(layer_count))
    removed_count = 0
    xy_neighbors = (
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
    )

    for _ in range(layer_count):
        to_remove = set()
        for coord in cells.keys():
            i, j, k = coord
            for offset in xy_neighbors:
                neighbor = (
                    i + offset[0],
                    j + offset[1],
                    k + offset[2],
                )
                if neighbor not in cells:
                    to_remove.add(coord)
                    break

        if not to_remove:
            break

        for coord in to_remove:
            cells.pop(coord, None)
        removed_count += len(to_remove)

    rebuild_voxel_mesh_from_cells(obj, origin, voxel_size, cells)
    return removed_count


def add_voxel_layer_under_lowest_cells(obj):
    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    if origin is None or voxel_size <= 0.0:
        return None

    cells = deserialize_voxel_cells(obj)
    if not cells:
        return 0

    bottom_cells_by_column = {}
    for coord, slot_index in cells.items():
        i, j, k = coord
        column_key = (i, j)
        existing = bottom_cells_by_column.get(column_key)
        if existing is None or k < existing[0]:
            bottom_cells_by_column[column_key] = (k, int(slot_index))

    added_count = 0
    for (i, j), (bottom_k, slot_index) in bottom_cells_by_column.items():
        coord = (i, j, bottom_k - 1)
        if coord not in cells:
            cells[coord] = slot_index
            added_count += 1

    if added_count:
        rebuild_voxel_mesh_from_cells(obj, origin, voxel_size, cells)
    return added_count


def generate_voxel_cells_from_object(context, source_obj, settings):
    depsgraph = context.evaluated_depsgraph_get()
    source_eval = source_obj.evaluated_get(depsgraph)
    voxel_size = get_voxel_size_scene_units(context, settings, source_obj)
    bbox_min, bbox_max = get_object_world_bounds(source_obj)
    origin = bbox_min - Vector((voxel_size, voxel_size, voxel_size))
    counts = (
        max(1, int((bbox_max.x - bbox_min.x) / voxel_size) + 3),
        max(1, int((bbox_max.y - bbox_min.y) / voxel_size) + 3),
        max(1, int((bbox_max.z - bbox_min.z) / voxel_size) + 3),
    )
    cells = {}

    surface_cell_count = mark_surface_voxel_cells_from_object(source_eval, origin, voxel_size, counts, cells)
    top_open_empty_voxels = set()
    exterior_empty_voxels = set()
    cavity_fill_count = 0
    vertical_fill_count = 0
    xy_slice_fill_count = 0

    if bool(getattr(settings, "voxel_fill_interior", False)):
        top_open_empty_voxels = collect_top_open_empty_voxels(cells, counts)
        exterior_empty_voxels = collect_exterior_empty_voxels(cells, counts, seed_empty=top_open_empty_voxels)
        bbox_diagonal = (bbox_max - bbox_min).length
        ray_distance = max(voxel_size * max(counts) * 2.0, bbox_diagonal * 2.0, voxel_size * 8.0)

        def should_fill_empty(coord):
            center = origin + Vector((
                (coord[0] + 0.5) * voxel_size,
                (coord[1] + 0.5) * voxel_size,
                (coord[2] + 0.5) * voxel_size,
            ))
            return is_point_inside_evaluated_mesh(source_eval, center, ray_distance, epsilon=voxel_size * 0.01)

        cavity_fill_count = fill_enclosed_voxel_cavities(cells, counts, protected_empty=exterior_empty_voxels, should_fill_empty=should_fill_empty)
        vertical_fill_count = fill_vertical_voxel_columns(cells, protected_empty=exterior_empty_voxels, should_fill_empty=should_fill_empty)
        xy_slice_fill_count = fill_enclosed_xy_voxel_slice_holes(cells, protected_empty=exterior_empty_voxels, should_fill_empty=should_fill_empty)
    stats = {
        "surface_cell_count": surface_cell_count,
        "top_open_empty_count": len(top_open_empty_voxels),
        "exterior_empty_count": len(exterior_empty_voxels),
        "cavity_fill_count": cavity_fill_count,
        "vertical_fill_count": vertical_fill_count,
        "xy_slice_fill_count": xy_slice_fill_count,
    }
    return origin, voxel_size, cells, stats


def get_blender_blocks_octree_depth_for_target_size(context, settings, source_obj):
    target_voxel_size = mm_to_scene_units(context, settings.voxel_size_mm)
    if target_voxel_size <= 0.0:
        return int(settings.octree_depth)

    max_dimension = max(float(source_obj.dimensions.x), float(source_obj.dimensions.y), float(source_obj.dimensions.z))
    if max_dimension <= 0.0:
        return int(settings.octree_depth)

    scale = max(float(settings.scale), 0.0001)
    depth = int(round(log2(max_dimension * scale / target_voxel_size)))
    return max(1, min(depth, 24))


def configure_blender_blocks_remesh_modifier(context, modifier, settings, source_obj):
    modifier.mode = 'BLOCKS'
    modifier.octree_depth = get_blender_blocks_octree_depth_for_target_size(context, settings, source_obj)
    modifier.scale = float(settings.scale)
    modifier.threshold = float(settings.threshold)
    if hasattr(modifier, "use_remove_disconnected"):
        modifier.use_remove_disconnected = bool(settings.remove_disconnected)
    if hasattr(modifier, "use_smooth_shade"):
        modifier.use_smooth_shade = False


def apply_blender_blocks_remesh(context, obj, settings, source_obj):
    set_active_object(context, obj)
    modifier = obj.modifiers.new("Miniature Voxeler Blocks", 'REMESH')
    configure_blender_blocks_remesh_modifier(context, modifier, settings, source_obj)
    applied_depth = int(modifier.octree_depth)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.data.update()
    return applied_depth


def cluster_sorted_grid_values(values, tolerance):
    if not values:
        return []

    sorted_values = sorted(float(value) for value in values)
    clusters = []
    cluster_sum = sorted_values[0]
    cluster_count = 1
    cluster_ref = sorted_values[0]

    for value in sorted_values[1:]:
        if abs(value - cluster_ref) <= tolerance:
            cluster_sum += value
            cluster_count += 1
            continue
        clusters.append(cluster_sum / cluster_count)
        cluster_sum = value
        cluster_count = 1
        cluster_ref = value

    clusters.append(cluster_sum / cluster_count)
    return clusters


def infer_voxel_size_from_block_mesh(mesh, fallback_voxel_size):
    coords_by_axis = (
        [vertex.co.x for vertex in mesh.vertices],
        [vertex.co.y for vertex in mesh.vertices],
        [vertex.co.z for vertex in mesh.vertices],
    )
    rough_tolerance = max(abs(float(fallback_voxel_size)) * 1e-5, 1e-7)
    diffs = []

    for coords in coords_by_axis:
        values = cluster_sorted_grid_values(coords, rough_tolerance)
        for index in range(len(values) - 1):
            diff = values[index + 1] - values[index]
            if diff > rough_tolerance:
                diffs.append(diff)

    if not diffs:
        return max(float(fallback_voxel_size), 1e-7)

    smallest = min(diffs)
    close_diffs = [diff for diff in diffs if diff <= smallest * 1.25]
    return sum(close_diffs) / len(close_diffs)


def get_block_mesh_grid(mesh, fallback_voxel_size):
    voxel_size = infer_voxel_size_from_block_mesh(mesh, fallback_voxel_size)
    tolerance = max(voxel_size * 1e-4, 1e-7)
    xs = cluster_sorted_grid_values([vertex.co.x for vertex in mesh.vertices], tolerance)
    ys = cluster_sorted_grid_values([vertex.co.y for vertex in mesh.vertices], tolerance)
    zs = cluster_sorted_grid_values([vertex.co.z for vertex in mesh.vertices], tolerance)
    if not xs or not ys or not zs:
        return Vector((0.0, 0.0, 0.0)), voxel_size, (0, 0, 0)

    origin = Vector((xs[0], ys[0], zs[0]))
    counts = (
        max(0, int(round((xs[-1] - xs[0]) / voxel_size))),
        max(0, int(round((ys[-1] - ys[0]) / voxel_size))),
        max(0, int(round((zs[-1] - zs[0]) / voxel_size))),
    )
    return origin, voxel_size, counts


def grid_plane_index(value, origin_value, voxel_size):
    return int(round((float(value) - float(origin_value)) / float(voxel_size)))


def block_range_from_bounds(min_value, max_value, origin_value, voxel_size, limit):
    start = grid_plane_index(min_value, origin_value, voxel_size)
    end = grid_plane_index(max_value, origin_value, voxel_size)
    start = max(0, min(start, limit))
    end = max(0, min(end, limit))
    return start, end


def extract_solid_voxel_cells_from_block_mesh(obj, fallback_voxel_size):
    mesh = obj.data
    origin, voxel_size, counts = get_block_mesh_grid(mesh, fallback_voxel_size)
    if voxel_size <= 0.0 or counts[0] <= 0 or counts[1] <= 0 or counts[2] <= 0:
        return origin, voxel_size, {}, {
            "surface_cell_count": 0,
            "top_open_empty_count": 0,
            "exterior_empty_count": 0,
            "cavity_fill_count": 0,
            "vertical_fill_count": 0,
            "xy_slice_fill_count": 0,
        }

    row_events = {}
    for poly in mesh.polygons:
        normal = poly.normal
        if abs(normal.x) < 0.9 or abs(normal.x) < abs(normal.y) or abs(normal.x) < abs(normal.z):
            continue

        verts = [mesh.vertices[index].co for index in poly.vertices]
        x_plane = grid_plane_index(sum(vertex.x for vertex in verts) / len(verts), origin.x, voxel_size)
        y_min = min(vertex.y for vertex in verts)
        y_max = max(vertex.y for vertex in verts)
        z_min = min(vertex.z for vertex in verts)
        z_max = max(vertex.z for vertex in verts)
        j_start, j_end = block_range_from_bounds(y_min, y_max, origin.y, voxel_size, counts[1])
        k_start, k_end = block_range_from_bounds(z_min, z_max, origin.z, voxel_size, counts[2])

        for j in range(j_start, j_end):
            for k in range(k_start, k_end):
                row_events.setdefault((j, k), set()).add(x_plane)

    cells = {}
    unpaired_rows = 0
    for (j, k), event_set in row_events.items():
        events = sorted(event for event in event_set if 0 <= event <= counts[0])
        if len(events) < 2:
            continue
        if len(events) % 2 != 0:
            unpaired_rows += 1
            events = events[:-1]
        for index in range(0, len(events), 2):
            start_i = max(0, min(events[index], counts[0]))
            end_i = max(0, min(events[index + 1], counts[0]))
            for i in range(start_i, end_i):
                cells[(i, j, k)] = 0

    stats = {
        "surface_cell_count": len(cells),
        "top_open_empty_count": 0,
        "exterior_empty_count": 0,
        "cavity_fill_count": 0,
        "vertical_fill_count": 0,
        "xy_slice_fill_count": 0,
        "unpaired_block_rows": unpaired_rows,
    }
    return origin, voxel_size, cells, stats


def generate_blender_block_voxel_cells_from_object(context, remesh_obj, settings, source_obj):
    fallback_voxel_size = get_voxel_size_scene_units(context, settings, source_obj)
    applied_depth = apply_blender_blocks_remesh(context, remesh_obj, settings, source_obj)
    origin, voxel_size, cells, stats = extract_solid_voxel_cells_from_block_mesh(remesh_obj, fallback_voxel_size)
    stats["applied_octree_depth"] = applied_depth
    return origin, voxel_size, cells, stats


def get_face_attribute_value(mesh, attribute_name, face_index, default_value=0):
    attribute = mesh.attributes.get(attribute_name)
    if attribute is None or face_index < 0 or face_index >= len(attribute.data):
        return default_value
    return int(attribute.data[face_index].value)


def get_slot_for_new_voxel_cell(cells, face_slots, target_coord, source_cell=None, source_face_dir=None, fallback_slot=0):
    if source_cell is not None and source_face_dir is not None:
        source_face_key = (source_cell[0], source_cell[1], source_cell[2], source_face_dir)
        if source_face_key in face_slots:
            return int(face_slots[source_face_key])
        if source_cell in cells:
            return int(cells[source_cell])

    best_distance_sq = None
    best_count_by_slot = {}
    for radius in range(1, 5):
        current_counts = {}
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy), abs(dz)) != radius:
                        continue
                    coord = (target_coord[0] + dx, target_coord[1] + dy, target_coord[2] + dz)
                    if coord not in cells:
                        continue
                    distance_sq = (dx * dx) + (dy * dy) + (dz * dz)
                    if best_distance_sq is None or distance_sq < best_distance_sq:
                        best_distance_sq = distance_sq
                        current_counts = {int(cells[coord]): 1}
                    elif distance_sq == best_distance_sq:
                        slot = int(cells[coord])
                        current_counts[slot] = current_counts.get(slot, 0) + 1

        if best_distance_sq is not None:
            best_count_by_slot = current_counts
            break

    if best_count_by_slot:
        return max(best_count_by_slot.items(), key=lambda item: (item[1], -item[0]))[0]
    return int(fallback_slot)


def get_grid_brush_radius_cells(brush_size):
    radius_cells = max(0, min(8, int((max(1.0, float(brush_size)) - 1.0) / 12.0)))
    return radius_cells


def get_grid_brush_offsets_for_face(face_dir, brush_size):
    radius_cells = get_grid_brush_radius_cells(brush_size)
    if radius_cells <= 0:
        return [(0, 0, 0)]

    offsets = []
    for a in range(-radius_cells, radius_cells + 1):
        for b in range(-radius_cells, radius_cells + 1):
            if (a * a) + (b * b) > radius_cells * radius_cells:
                continue
            if face_dir in (0, 1):
                offsets.append((0, a, b))
            elif face_dir in (2, 3):
                offsets.append((a, 0, b))
            else:
                offsets.append((a, b, 0))
    return offsets


def get_grid_brush_sphere_offsets(brush_size):
    radius_cells = get_grid_brush_radius_cells(brush_size)
    if radius_cells <= 0:
        return [(0, 0, 0)]

    offsets = []
    radius_sq = radius_cells * radius_cells
    for dx in range(-radius_cells, radius_cells + 1):
        for dy in range(-radius_cells, radius_cells + 1):
            for dz in range(-radius_cells, radius_cells + 1):
                if (dx * dx) + (dy * dy) + (dz * dz) <= radius_sq:
                    offsets.append((dx, dy, dz))
    return offsets


def get_grid_brush_target_coords(center_coord, face_dir, brush_size):
    return [
        (
            center_coord[0] + offset[0],
            center_coord[1] + offset[1],
            center_coord[2] + offset[2],
        )
        for offset in get_grid_brush_sphere_offsets(brush_size)
    ]


def get_grid_brush_plane_target_coords(center_coord, face_dir, brush_size):
    return [
        (
            center_coord[0] + offset[0],
            center_coord[1] + offset[1],
            center_coord[2] + offset[2],
        )
        for offset in get_grid_brush_offsets_for_face(face_dir, brush_size)
    ]


def get_axis_for_face_dir(face_dir):
    if face_dir in (0, 1):
        return 0
    if face_dir in (2, 3):
        return 1
    return 2


def get_face_dir_from_neighbor_delta(delta):
    directions = get_voxel_cell_face_vectors()
    for index, direction in enumerate(directions):
        if tuple(direction) == tuple(delta):
            return index
    return None


def get_fallback_face_dir_from_ray(local_direction):
    components = [abs(local_direction.x), abs(local_direction.y), abs(local_direction.z)]
    axis = components.index(max(components))
    if axis == 0:
        return 1 if local_direction.x > 0 else 0
    if axis == 1:
        return 3 if local_direction.y > 0 else 2
    return 5 if local_direction.z > 0 else 4


def ray_aabb_intersection(ray_origin, ray_direction, bbox_min, bbox_max):
    t_min = -float("inf")
    t_max = float("inf")
    for axis in range(3):
        origin_value = ray_origin[axis]
        direction_value = ray_direction[axis]
        min_value = bbox_min[axis]
        max_value = bbox_max[axis]
        if abs(direction_value) < 1e-12:
            if origin_value < min_value or origin_value > max_value:
                return None
            continue
        t1 = (min_value - origin_value) / direction_value
        t2 = (max_value - origin_value) / direction_value
        if t1 > t2:
            t1, t2 = t2, t1
        t_min = max(t_min, t1)
        t_max = min(t_max, t2)
        if t_min > t_max:
            return None
    return t_min, t_max


def build_voxel_grid_cache(obj, cells):
    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    if origin is None or voxel_size <= 0.0 or not cells:
        return None
    min_i = min(coord[0] for coord in cells) - 1
    min_j = min(coord[1] for coord in cells) - 1
    min_k = min(coord[2] for coord in cells) - 1
    max_i = max(coord[0] for coord in cells) + 2
    max_j = max(coord[1] for coord in cells) + 2
    max_k = max(coord[2] for coord in cells) + 2
    return {
        "origin": origin,
        "voxel_size": voxel_size,
        "bounds": [min_i, min_j, min_k, max_i, max_j, max_k],
        "matrix": obj.matrix_world.copy(),
        "inverse_matrix": obj.matrix_world.inverted(),
        "inverse_rotation": obj.matrix_world.inverted().to_3x3(),
    }


def expand_voxel_grid_cache(cache, coord):
    if cache is None:
        return
    bounds = cache["bounds"]
    bounds[0] = min(bounds[0], coord[0] - 1)
    bounds[1] = min(bounds[1], coord[1] - 1)
    bounds[2] = min(bounds[2], coord[2] - 1)
    bounds[3] = max(bounds[3], coord[0] + 2)
    bounds[4] = max(bounds[4], coord[1] + 2)
    bounds[5] = max(bounds[5], coord[2] + 2)


def get_voxel_cursor_hit(context, event, obj, cells, grid_cache=None, mode='ADD'):
    if not cells:
        return None

    region, region_3d, mouse_coord = get_mouse_region_coord(context, event)
    if region is None:
        return None

    if grid_cache is None:
        grid_cache = build_voxel_grid_cache(obj, cells)
    if grid_cache is None:
        return None
    origin = grid_cache["origin"]
    voxel_size = grid_cache["voxel_size"]
    min_i, min_j, min_k, max_i, max_j, max_k = grid_cache["bounds"]

    ray_origin_world = view3d_utils.region_2d_to_origin_3d(region, region_3d, mouse_coord)
    ray_direction_world = view3d_utils.region_2d_to_vector_3d(region, region_3d, mouse_coord)
    inv_matrix = grid_cache["inverse_matrix"]
    ray_origin = inv_matrix @ ray_origin_world
    ray_direction = (grid_cache["inverse_rotation"] @ ray_direction_world).normalized()
    bbox_min = Vector((
        origin.x + (min_i * voxel_size),
        origin.y + (min_j * voxel_size),
        origin.z + (min_k * voxel_size),
    ))
    bbox_max = Vector((
        origin.x + (max_i * voxel_size),
        origin.y + (max_j * voxel_size),
        origin.z + (max_k * voxel_size),
    ))

    intersection = ray_aabb_intersection(ray_origin, ray_direction, bbox_min, bbox_max)
    if intersection is None:
        return None
    t_min, t_max = intersection
    if t_max < 0.0:
        return None

    t = max(0.0, t_min) + (voxel_size * 1e-5)
    point = ray_origin + (ray_direction * t)
    current = [
        int((point.x - origin.x) // voxel_size),
        int((point.y - origin.y) // voxel_size),
        int((point.z - origin.z) // voxel_size),
    ]

    step = [1 if ray_direction[axis] > 0.0 else -1 for axis in range(3)]
    t_max_axis = [0.0, 0.0, 0.0]
    t_delta = [0.0, 0.0, 0.0]
    for axis in range(3):
        direction_value = ray_direction[axis]
        if abs(direction_value) < 1e-12:
            t_max_axis[axis] = float("inf")
            t_delta[axis] = float("inf")
            continue
        boundary_index = current[axis] + (1 if step[axis] > 0 else 0)
        boundary = origin[axis] + (boundary_index * voxel_size)
        t_max_axis[axis] = (boundary - ray_origin[axis]) / direction_value
        t_delta[axis] = voxel_size / abs(direction_value)

    max_steps = ((max_i - min_i) + (max_j - min_j) + (max_k - min_k) + 16) * 3
    previous = None
    previous_axis = None
    for _ in range(max_steps):
        cell = tuple(current)
        if mode == 'REMOVE' and cell in cells:
            face_dir = None
            if previous is not None:
                delta = (previous[0] - cell[0], previous[1] - cell[1], previous[2] - cell[2])
                face_dir = get_face_dir_from_neighbor_delta(delta)
            elif previous_axis is not None:
                delta = [0, 0, 0]
                delta[previous_axis] = -step[previous_axis]
                face_dir = get_face_dir_from_neighbor_delta(tuple(delta))
            return {
                "cell": cell,
                "face_dir": face_dir if face_dir is not None else get_fallback_face_dir_from_ray(ray_direction),
                "previous": previous,
            }
        if mode == 'ADD' and cell in cells and previous is not None and previous not in cells:
            delta = (previous[0] - cell[0], previous[1] - cell[1], previous[2] - cell[2])
            face_dir = get_face_dir_from_neighbor_delta(delta)
            return {
                "cell": cell,
                "face_dir": face_dir if face_dir is not None else get_fallback_face_dir_from_ray(ray_direction),
                "previous": previous,
            }
        if cell in cells:
            if previous is not None:
                delta = (previous[0] - cell[0], previous[1] - cell[1], previous[2] - cell[2])
                face_dir = get_face_dir_from_neighbor_delta(delta)
            elif previous_axis is not None:
                delta = [0, 0, 0]
                delta[previous_axis] = -step[previous_axis]
                face_dir = get_face_dir_from_neighbor_delta(tuple(delta))
            else:
                face_dir = get_fallback_face_dir_from_ray(ray_direction)
            return {
                "cell": cell,
                "face_dir": face_dir if face_dir is not None else get_fallback_face_dir_from_ray(ray_direction),
                "previous": previous,
            }

        axis = min(range(3), key=lambda item: t_max_axis[item])
        if t_max_axis[axis] > t_max + voxel_size:
            break
        previous = cell
        previous_axis = axis
        current[axis] += step[axis]
        t_max_axis[axis] += t_delta[axis]

    return None


def get_voxel_cursor_edit_target(context, event, obj, mode, cells, face_slots, fallback_slot, grid_cache=None):
    hit = get_voxel_cursor_hit(context, event, obj, cells, grid_cache, mode)
    if hit is None:
        return None

    cell = hit["cell"]
    face_dir = hit["face_dir"]
    directions = get_voxel_cell_face_vectors()
    if mode == 'REMOVE':
        return {
            "cell": cell,
            "target": cell,
            "slot": None,
            "face_dir": face_dir,
        }

    offset = directions[face_dir]
    target = (
        cell[0] + offset[0],
        cell[1] + offset[1],
        cell[2] + offset[2],
    )
    if target in cells:
        return None
    return {
        "cell": cell,
        "target": target,
        "slot": None,
        "fallback_slot": fallback_slot,
        "face_dir": face_dir,
    }


def get_voxel_plane_edit_target(context, event, obj, cells, fallback_slot, grid_cache, face_dir, plane_coord):
    region, region_3d, mouse_coord = get_mouse_region_coord(context, event)
    if region is None or grid_cache is None:
        return None

    origin = grid_cache["origin"]
    voxel_size = grid_cache["voxel_size"]
    axis = get_axis_for_face_dir(face_dir)
    ray_origin_world = view3d_utils.region_2d_to_origin_3d(region, region_3d, mouse_coord)
    ray_direction_world = view3d_utils.region_2d_to_vector_3d(region, region_3d, mouse_coord)
    ray_origin = grid_cache["inverse_matrix"] @ ray_origin_world
    ray_direction = (grid_cache["inverse_rotation"] @ ray_direction_world).normalized()
    if abs(ray_direction[axis]) < 1e-12:
        return None

    plane_value = origin[axis] + ((plane_coord + 0.5) * voxel_size)
    t = (plane_value - ray_origin[axis]) / ray_direction[axis]
    if t < 0.0:
        return None

    point = ray_origin + (ray_direction * t)
    target = [
        int((point.x - origin.x) // voxel_size),
        int((point.y - origin.y) // voxel_size),
        int((point.z - origin.z) // voxel_size),
    ]
    target[axis] = plane_coord
    target = tuple(target)
    direction = get_voxel_cell_face_vectors()[face_dir]
    source_cell = (
        target[0] - direction[0],
        target[1] - direction[1],
        target[2] - direction[2],
    )
    if source_cell not in cells or target in cells:
        return None
    return {
        "cell": source_cell,
        "target": target,
        "slot": None,
        "fallback_slot": fallback_slot,
        "face_dir": face_dir,
    }


def get_voxel_remove_plane_edit_target(context, event, obj, grid_cache, face_dir, plane_coord):
    region, region_3d, mouse_coord = get_mouse_region_coord(context, event)
    if region is None or grid_cache is None:
        return None

    origin = grid_cache["origin"]
    voxel_size = grid_cache["voxel_size"]
    axis = get_axis_for_face_dir(face_dir)
    ray_origin_world = view3d_utils.region_2d_to_origin_3d(region, region_3d, mouse_coord)
    ray_direction_world = view3d_utils.region_2d_to_vector_3d(region, region_3d, mouse_coord)
    ray_origin = grid_cache["inverse_matrix"] @ ray_origin_world
    ray_direction = (grid_cache["inverse_rotation"] @ ray_direction_world).normalized()
    if abs(ray_direction[axis]) < 1e-12:
        return None

    plane_value = origin[axis] + ((plane_coord + 0.5) * voxel_size)
    t = (plane_value - ray_origin[axis]) / ray_direction[axis]
    if t < 0.0:
        return None

    point = ray_origin + (ray_direction * t)
    target = [
        int((point.x - origin.x) // voxel_size),
        int((point.y - origin.y) // voxel_size),
        int((point.z - origin.z) // voxel_size),
    ]
    target[axis] = plane_coord
    min_i, min_j, min_k, max_i, max_j, max_k = grid_cache["bounds"]
    if (
        target[0] < min_i or target[0] >= max_i or
        target[1] < min_j or target[1] >= max_j or
        target[2] < min_k or target[2] >= max_k
    ):
        return None

    return {
        "cell": tuple(target),
        "target": tuple(target),
        "slot": None,
        "face_dir": face_dir,
    }


def collect_voxel_edit_targets_direct(context, event, obj, action, brush_size, cells, face_slots, fallback_slot):
    hit_obj, face_index, _, _ = raycast_active_face_details(context, event)
    if hit_obj is None or face_index is None:
        return {}

    mesh = hit_obj.data
    cell = get_voxel_cell_key_from_mesh(mesh, face_index)
    face_dir = get_face_attribute_value(mesh, "mv_face_dir", face_index)
    direction_vectors = get_voxel_cell_face_vectors()
    if face_dir < 0 or face_dir >= len(direction_vectors):
        return {}

    touched = {}
    face_offset = direction_vectors[face_dir]
    for brush_offset in get_grid_brush_offsets_for_face(face_dir, brush_size):
        source_cell = (
            cell[0] + brush_offset[0],
            cell[1] + brush_offset[1],
            cell[2] + brush_offset[2],
        )
        if action == 'REMOVE':
            if source_cell in cells:
                touched[source_cell] = None
            continue

        if source_cell not in cells:
            continue
        target = (
            source_cell[0] + face_offset[0],
            source_cell[1] + face_offset[1],
            source_cell[2] + face_offset[2],
        )
        if target in cells:
            continue
        touched[target] = get_slot_for_new_voxel_cell(
            cells,
            face_slots,
            target,
            source_cell=source_cell,
            source_face_dir=face_dir,
            fallback_slot=fallback_slot,
        )
    return touched


def edit_voxel_cells_with_brush(context, event, obj, action, slot_index, brush_size, cells=None, face_slots=None, rebuild=True, face_centers_world=None, fast_direct=True):

    origin = get_stored_voxel_origin(obj)
    voxel_size = float(obj.get("mv_voxel_size", 0.0))
    if origin is None or voxel_size <= 0.0:
        return 0

    if cells is None:
        cells = deserialize_voxel_cells(obj)
    if not cells:
        return 0
    if face_slots is None:
        face_slots = collect_voxel_face_slots_from_mesh(obj, cells)

    if fast_direct:
        touched = collect_voxel_edit_targets_direct(
            context,
            event,
            obj,
            action,
            brush_size,
            cells,
            face_slots,
            slot_index,
        )
    else:
        face_indices = collect_brush_face_indices(context, event, obj, brush_size, face_centers_world)
        if not face_indices:
            return 0

        mesh = obj.data
        direction_vectors = get_voxel_cell_face_vectors()
        touched = {}
        for current_face_index in face_indices:
            cell = (
                get_face_attribute_value(mesh, "mv_cell_i", current_face_index),
                get_face_attribute_value(mesh, "mv_cell_j", current_face_index),
                get_face_attribute_value(mesh, "mv_cell_k", current_face_index),
            )
            face_dir = get_face_attribute_value(mesh, "mv_face_dir", current_face_index)
            if face_dir < 0 or face_dir >= len(direction_vectors):
                continue

            if action == 'REMOVE':
                touched[cell] = None
            elif action == 'ADD':
                offset = direction_vectors[face_dir]
                target = (cell[0] + offset[0], cell[1] + offset[1], cell[2] + offset[2])
                touched[target] = get_slot_for_new_voxel_cell(
                    cells,
                    face_slots,
                    target,
                    source_cell=cell,
                    source_face_dir=face_dir,
                    fallback_slot=slot_index,
                )

    if not touched:
        return 0

    changed = False
    for coord, new_slot in touched.items():
        if action == 'REMOVE':
            if coord in cells:
                del cells[coord]
                changed = True
        elif action == 'ADD':
            if coord not in cells:
                cells[coord] = int(new_slot)
                changed = True

    if not changed:
        return 0

    if rebuild:
        rebuild_voxel_mesh_from_cells(obj, origin, voxel_size, cells, face_slots)
    return len(touched)
