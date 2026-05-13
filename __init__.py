bl_info = {
    "name": "Miniature Voxeler",
    "author": "OpenAI",
    "version": (3, 11, 8),
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
from math import atan2, cos, floor, hypot, pi, radians, sin
from time import perf_counter
from bpy_extras import view3d_utils
from mathutils import Vector, geometry
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

ADDON_VERSION_TEXT = "v.3.11.8"


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
    if building_obj is None:
        return None

    blocks_name = get_blocks_name(get_root_name(building_obj.name))
    blocks_obj = bpy.data.objects.get(blocks_name)
    if blocks_obj is None or blocks_obj.type != 'MESH':
        return None
    return blocks_obj


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


def paint_faces_with_brush(context, event, obj, slot_index, brush_size, face_slots=None, commit_face_slots=True, face_centers_world=None):
    face_indices = collect_brush_face_indices(context, event, obj, brush_size, face_centers_world)
    if not face_indices:
        return 0

    return paint_face_indices(obj, slot_index, face_indices, face_slots, commit_face_slots)


def paint_face_indices(obj, slot_index, face_indices, face_slots=None, commit_face_slots=True):
    mesh = obj.data
    changed_count = 0

    for paint_face_index in face_indices:
        if mesh.polygons[paint_face_index].material_index != slot_index:
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


def fill_enclosed_voxel_cavities(cells, counts, fill_slot=0):
    if not cells:
        return 0

    max_i, max_j, max_k = counts
    outside = set()
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
                cells[coord] = int(fill_slot)
                filled_count += 1

    return filled_count


def fill_vertical_voxel_columns(cells, fill_slot=0):
    if not cells:
        return 0

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

    def get_vert_index(position):
        key = (
            round(float(position[0]), 9),
            round(float(position[1]), 9),
            round(float(position[2]), 9),
        )
        if key in vert_map:
            return vert_map[key]
        index = len(verts)
        verts.append(tuple(position))
        vert_map[key] = index
        return index

    for coord, slot_index in sorted(cells.items()):
        i, j, k = coord
        base = origin + Vector((i * voxel_size, j * voxel_size, k * voxel_size))
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
                position = (
                    base.x + (corner[0] * voxel_size),
                    base.y + (corner[1] * voxel_size),
                    base.z + (corner[2] * voxel_size),
                )
                face.append(get_vert_index(position))
            faces.append(tuple(face))
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
    obj.data.update()


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
    cavity_fill_count = fill_enclosed_voxel_cavities(cells, counts)
    vertical_fill_count = fill_vertical_voxel_columns(cells)
    stats = {
        "surface_cell_count": surface_cell_count,
        "cavity_fill_count": cavity_fill_count,
        "vertical_fill_count": vertical_fill_count,
    }
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


# ------------------------------------------------------------
# Properties
# ------------------------------------------------------------

class MINIATUREVOXELER_PG_settings(PropertyGroup):
    building_object: PointerProperty(
        name="Building",
        description="Mesh object used for all voxel and skin operations",
        type=bpy.types.Object,
        poll=lambda self, obj: obj is not None and obj.type == 'MESH',
    )

    platform_object: PointerProperty(
        name="Platform",
        description="Mesh object reserved as the platform reference",
        type=bpy.types.Object,
        poll=lambda self, obj: obj is not None and obj.type == 'MESH',
    )

    source_validation_key: StringProperty(
        name="Source Validation Key",
        description="Internal marker for the Building/Platform pair that passed transform and size validation",
        default="",
        options={'HIDDEN'},
    )

    show_platform_steps: BoolProperty(
        name="---- PLATFORM ----",
        description="Show or hide the platform workflow steps",
        default=True,
    )

    show_building_steps: BoolProperty(
        name="---- BUILDING ----",
        description="Show or hide the building workflow steps",
        default=True,
    )

    show_export_steps: BoolProperty(
        name="---- EXPORT ----",
        description="Show or hide the export workflow steps",
        default=True,
    )

    export_directory: StringProperty(
        name="Export Folder",
        description="Folder for batch STL export and the optional Blender file copy",
        default="//MiniatureVoxeler_Export",
        subtype='DIR_PATH',
    )

    octree_depth: IntProperty(
        name="Octree Depth",
        default=7,
        min=1,
        max=24,
    )

    scale: FloatProperty(
        name="Scale",
        default=0.9,
        min=0.0001,
        max=0.99,
    )

    voxel_size_mm: FloatProperty(
        name="Voxel Size (mm)",
        description="Explicit cube size for the custom voxelizer. Set to 0 to derive the size from Octree Depth and Scale",
        default=0.0,
        min=0.0,
        soft_max=25.0,
        precision=3,
    )

    threshold: FloatProperty(
        name="Threshold",
        default=1.0,
        soft_min=0.0,
        soft_max=10.0,
    )

    remove_disconnected: BoolProperty(
        name="Remove Disconnected",
        description="Remove small disconnected pieces during the block remesh step",
        default=True,
    )

    platform_limited_dissolve_angle: FloatProperty(
        name="Max Angle",
        description="Maximum angle used by Limited Dissolve on the hole selection mesh",
        subtype='ANGLE',
        default=radians(5.0),
        min=0.0,
        max=pi,
    )

    texture_source_name: StringProperty(
        name="Source Override",
        description="Optional source object name for texture transfer. Leave empty to use the _body copy automatically",
        default="",
    )

    texture_size: IntProperty(
        name="Texture Size",
        description="Resolution of the baked texture",
        default=2048,
        min=128,
        max=8192,
    )

    texture_margin: IntProperty(
        name="Bake Margin",
        description="Padding in pixels around UV islands during baking",
        default=16,
        min=0,
        max=128,
    )

    lego_color_sample_mode: EnumProperty(
        name="Texture Read",
        description="How each face reads color from the texture",
        items=[
            ('AVERAGE', "UV Average", "Average the texture color from all UV corners of the face"),
            ('CENTER', "Face Center", "Sample the texture once from the average UV position of the face"),
            ('MEDIAN', "Median Corners", "Use the median corner color for a more stable flat result"),
        ],
        default='AVERAGE',
    )

    lego_color_count: IntProperty(
        name="Number of Colors",
        description="How many fixed palette colors to create and assign to faces",
        default=4,
        min=1,
        max=4,
        update=update_lego_color_count,
    )

    lego_color_assign_mode: EnumProperty(
        name="Color Assignment",
        description="How sampled texture colors are grouped into Lego materials",
        items=[
            ('ADAPTIVE', "Adaptive Palette", "Build up to four color groups from sampled face colors"),
            ('LUMINANCE', "Brightness Bands", "Group faces by brightness into up to four bands"),
        ],
        default='ADAPTIVE',
    )

    lego_smooth_mode: EnumProperty(
        name="Smooth Mode",
        description="Choose how Smooth Colors edits the current Lego color slots",
        items=[
            ('SPECKLES', "Clean Speckles", "Replace isolated noisy faces when enough neighboring faces agree on another slot"),
            ('MAJORITY', "Majority Vote", "Let the strongest neighboring slot win; useful for broader simplification after texture transfer"),
            ('ISLANDS', "Small Islands", "Replace tiny connected islands of one slot with the dominant surrounding slot"),
        ],
        default='SPECKLES',
    )

    lego_smooth_weight: FloatProperty(
        name="Smooth Weight",
        description="Higher values make neighboring colors more likely to override the current face color",
        default=0.7,
        min=0.0,
        max=1.0,
        precision=2,
    )

    lego_smooth_passes: IntProperty(
        name="Smooth Passes",
        description="How many times the smoothing pass is applied",
        default=1,
        min=1,
        max=10,
    )

    lego_smooth_min_neighbors: IntProperty(
        name="Min Neighbors",
        description="Minimum neighboring faces that must support the replacement slot before a color changes",
        default=2,
        min=1,
        max=32,
    )

    lego_smooth_max_island_faces: IntProperty(
        name="Max Island Faces",
        description="Small Islands mode only: largest same-color connected face island that may be replaced",
        default=8,
        min=1,
        max=512,
    )

    lego_smooth_include_corners: BoolProperty(
        name="Include Corners",
        description="Count faces touching at vertices as neighbors too; stronger cleanup, but less edge-preserving",
        default=False,
    )

    lego_smooth_protect_slot: EnumProperty(
        name="Protect Slot",
        description="Optional slot that Smooth Colors will never replace",
        items=[
            ('NONE', "None", "Allow every slot to be smoothed"),
            ('0', "Slot 1", "Never replace faces assigned to slot 1"),
            ('1', "Slot 2", "Never replace faces assigned to slot 2"),
            ('2', "Slot 3", "Never replace faces assigned to slot 3"),
            ('3', "Slot 4", "Never replace faces assigned to slot 4"),
        ],
        default='NONE',
    )

    selected_lego_palette_slot: IntProperty(
        name="Selected Color",
        description="Palette color used by the paint brush",
        default=0,
        min=0,
        max=3,
    )

    lego_paint_tool_mode: EnumProperty(
        name="Tool",
        description="How the selected slot is painted in Object Mode",
        items=[
            ('PAINT', "Brush", "Paint the selected slot with the circular brush"),
            ('BOX_PAINT', "Rectangle", "Paint the selected slot inside a dragged rectangle"),
            ('LASSO_PAINT', "Lasso", "Paint the selected slot inside a freehand lasso shape"),
        ],
        default='PAINT',
    )

    platform_foot_color_slot: EnumProperty(
        name="_foot Slot",
        description="Palette slot assigned to the _foot object",
        items=[
            ('0', "Slot 1", "Color _foot with slot 1"),
            ('1', "Slot 2", "Color _foot with slot 2"),
            ('2', "Slot 3", "Color _foot with slot 3"),
            ('3', "Slot 4", "Color _foot with slot 4"),
        ],
        default='0',
        update=update_platform_foot_color_slot,
    )

    debug_palette_slot_color_1: FloatVectorProperty(name="Debug Red", subtype='COLOR', size=3, min=0.0, max=1.0, default=DEBUG_LEGO_COLORS[0])
    debug_palette_slot_color_2: FloatVectorProperty(name="Debug Green", subtype='COLOR', size=3, min=0.0, max=1.0, default=DEBUG_LEGO_COLORS[1])
    debug_palette_slot_color_3: FloatVectorProperty(name="Debug Blue", subtype='COLOR', size=3, min=0.0, max=1.0, default=DEBUG_LEGO_COLORS[2])
    debug_palette_slot_color_4: FloatVectorProperty(name="Debug White", subtype='COLOR', size=3, min=0.0, max=1.0, default=DEBUG_LEGO_COLORS[3])

    fixed_palette_color_1: FloatVectorProperty(name="Deep Night", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[0][1])
    fixed_palette_color_2: FloatVectorProperty(name="Muted Periwinkle", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[1][1])
    fixed_palette_color_3: FloatVectorProperty(name="Pale Ice Blue", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[2][1])
    fixed_palette_color_4: FloatVectorProperty(name="White", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[3][1])
    fixed_palette_color_5: FloatVectorProperty(name="Electric Cyan", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[4][1])
    fixed_palette_color_6: FloatVectorProperty(name="Soft Cobalt", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[5][1])
    fixed_palette_color_7: FloatVectorProperty(name="Deep Violet", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[6][1])
    fixed_palette_color_8: FloatVectorProperty(name="Lavender Blue", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[7][1])
    fixed_palette_color_9: FloatVectorProperty(name="Light Cornflower", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[8][1])
    fixed_palette_color_10: FloatVectorProperty(name="Royal Purple", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[9][1])
    fixed_palette_color_11: FloatVectorProperty(name="Hot Magenta", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[10][1])
    fixed_palette_color_12: FloatVectorProperty(name="Peach", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[11][1])
    fixed_palette_color_13: FloatVectorProperty(name="Coral Red", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[12][1])
    fixed_palette_color_14: FloatVectorProperty(name="Wine Rose", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[13][1])
    fixed_palette_color_15: FloatVectorProperty(name="Warm Orange", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[14][1])
    fixed_palette_color_16: FloatVectorProperty(name="Soft Yellow", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[15][1])
    fixed_palette_color_17: FloatVectorProperty(name="Fresh Green", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[16][1])
    fixed_palette_color_18: FloatVectorProperty(name="Deep Teal", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[17][1])
    fixed_palette_color_19: FloatVectorProperty(name="Aqua Teal", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[18][1])
    fixed_palette_color_20: FloatVectorProperty(name="Mint Glow", subtype='COLOR', size=3, min=0.0, max=1.0, default=FIXED_LEGO_PALETTE[19][1])

    lego_palette_slot_1: EnumProperty(
        name="Slot 1",
        description="Fixed palette color for material slot 1",
        items=FIXED_LEGO_PALETTE_ITEMS,
        default='12',
        update=update_lego_palette_slot_1,
    )

    lego_palette_slot_color_1: FloatVectorProperty(
        name="Slot 1 Color",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=FIXED_LEGO_PALETTE[12][1],
    )

    lego_palette_slot_2: EnumProperty(
        name="Slot 2",
        description="Fixed palette color for material slot 2",
        items=FIXED_LEGO_PALETTE_ITEMS,
        default='15',
        update=update_lego_palette_slot_2,
    )

    lego_palette_slot_color_2: FloatVectorProperty(
        name="Slot 2 Color",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=FIXED_LEGO_PALETTE[15][1],
    )

    lego_palette_slot_3: EnumProperty(
        name="Slot 3",
        description="Fixed palette color for material slot 3",
        items=FIXED_LEGO_PALETTE_ITEMS,
        default='5',
        update=update_lego_palette_slot_3,
    )

    lego_palette_slot_color_3: FloatVectorProperty(
        name="Slot 3 Color",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=FIXED_LEGO_PALETTE[5][1],
    )

    lego_palette_slot_4: EnumProperty(
        name="Slot 4",
        description="Fixed palette color for material slot 4",
        items=FIXED_LEGO_PALETTE_ITEMS,
        default='3',
        update=update_lego_palette_slot_4,
    )

    lego_palette_slot_color_4: FloatVectorProperty(
        name="Slot 4 Color",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=FIXED_LEGO_PALETTE[3][1],
    )

    lego_paint_brush_size: IntProperty(
        name="Brush Size",
        description="Screen-space paint radius in pixels. 1 paints only the hovered face",
        default=9,
        min=1,
        soft_max=80,
        max=300,
    )

    outer_skin_mm: FloatProperty(
        name="Solidify Thickness (mm)",
        description="Solidify thickness for separated skin pieces",
        default=0.4,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    skin_solidify_offset: FloatProperty(
        name="Solidify Offset",
        description="Solidify offset for separated skin pieces",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
    )

    skin_solidify_even_thickness: BoolProperty(name="Even Thickness", default=True)

    color_skin_base_slot: EnumProperty(
        name="Base Slot",
        description="Material slot that remains as the base object surface",
        items=[
            ('0', "Slot 1", "Use material slot 1 as the base"),
            ('1', "Slot 2", "Use material slot 2 as the base"),
            ('2', "Slot 3", "Use material slot 3 as the base"),
            ('3', "Slot 4", "Use material slot 4 as the base"),
        ],
        default='0',
        update=update_color_skin_base_slot,
    )

    color_skin_slot_1: BoolProperty(name="Slot 1", default=False)
    color_skin_slot_2: BoolProperty(name="Slot 2", default=True)
    color_skin_slot_3: BoolProperty(name="Slot 3", default=True)
    color_skin_slot_4: BoolProperty(name="Slot 4", default=True)

    platform_merge_distance: FloatProperty(
        name="Merge Distance",
        description="Distance used when merging nearby wall vertices",
        default=0.001,
        min=0.0,
        precision=6,
        step=0.1,
        unit='LENGTH',
    )

    platform_cleanup_dissolve_angle: FloatProperty(
        name="Dissolve Angle",
        description="Angle threshold for Limited Dissolve during cutter cleanup",
        default=radians(2.0),
        min=0.0,
        max=pi,
        subtype='ANGLE',
        precision=3,
    )

    platform_ring_gap_tolerance: FloatProperty(
        name="Gap Tolerance",
        description="Maximum XY gap stitched when storing manually selected upper-ring edges",
        default=0.002,
        min=0.0,
        precision=6,
        step=0.1,
        unit='LENGTH',
    )

    platform_bridge_vertex_distance_mm: FloatProperty(
        name="New Vertex Distance",
        description="Target spacing for new vertices inserted along bridged ring edges",
        default=3.0,
        min=0.001,
        precision=3,
    )

    platform_fill_subdivide_cuts: IntProperty(
        name="Fill Cuts",
        description="Number of cuts used when subdividing the Beauty Fill faces for sculpt smoothing",
        default=2,
        min=1,
        max=20,
    )

    platform_inner_thickness_mm: FloatProperty(
        name="Inner Thickness (mm)",
        description="Inward 2D cutter thickness from the stored upper loop",
        default=3.0,
        min=0.0,
        precision=3,
    )

    platform_outer_thickness_mm: FloatProperty(
        name="Outer Thickness (mm)",
        description="Outward 2D cutter thickness from the stored upper loop",
        default=10.0,
        min=0.0,
        precision=3,
    )

    platform_cutter_depth_mm: FloatProperty(
        name="Cutter Depth (mm)",
        description="Distance to extrude the closed cutter downward before slicing the building",
        default=50.0,
        min=0.0,
        precision=3,
    )

    platform_remesh_octree_depth: IntProperty(
        name="Octree Depth",
        description="Smooth Remesh octree depth for the extruded cutter",
        default=8,
        min=1,
        max=12,
    )

    platform_remesh_scale: FloatProperty(
        name="Scale",
        description="Smooth Remesh scale value for the extruded cutter",
        default=0.9,
        min=0.0,
        max=0.99,
        precision=3,
    )

    platform_remesh_remove_disconnected: BoolProperty(
        name="Remove Disconnected",
        description="Remove disconnected pieces during Smooth Remesh",
        default=False,
    )

    platform_slice_boolean_self_intersect: BoolProperty(
        name="Self Intersect",
        description="Allow self-intersecting geometry when slicing the building with the platform cutter",
        default=False,
    )

    platform_slice_boolean_holes: BoolProperty(
        name="Holes",
        description="Use Blender's hole-tolerant boolean mode when slicing the building with the platform cutter",
        default=False,
    )

    voxel_xy_wall_layers: IntProperty(
        name="XY Wall Layers",
        description="Number of exterior XY side-wall voxel layers to remove after voxelizing",
        default=2,
        min=0,
        max=50,
    )

    platform_foot_clearance: FloatProperty(
        name="Foot Clearance",
        description="Small XY expansion applied to _foot after its boolean is applied, so it does not collide with the building",
        default=0.001,
        min=0.0,
        precision=6,
        unit='LENGTH',
    )

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def duplicate_object(context, source_obj, new_name):
    new_obj = source_obj.copy()
    new_obj.data = source_obj.data.copy()
    new_obj.animation_data_clear()
    new_obj.name = new_name
    new_obj.matrix_world = source_obj.matrix_world.copy()
    context.collection.objects.link(new_obj)
    return new_obj


def set_active_object(context, obj):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj


def apply_object_transform_to_mesh(obj):
    if obj is None or obj.type != 'MESH':
        return False

    if obj.data.users > 1:
        obj.data = obj.data.copy()

    transform = obj.matrix_basis.copy()
    obj.data.transform(transform)
    obj.data.update()
    identity = transform.copy()
    identity.identity()
    obj.matrix_basis = identity
    return True


def get_root_name(obj_name):
    if obj_name.endswith("_copy"):
        return obj_name[:-5]
    if obj_name.endswith("_body"):
        return obj_name[:-5]
    if "_Blocks_Skin_" in obj_name:
        return obj_name.split("_Blocks_Skin_")[0]
    if "_Lego_Skin_Slot_" in obj_name:
        return obj_name.split("_Lego_Skin_Slot_")[0]
    if obj_name.endswith("_Lego_Base"):
        return obj_name[:-10]
    if obj_name.endswith("_Foot_Cutter"):
        return obj_name[:-12]
    if obj_name.endswith("_Building_Cutter"):
        return obj_name[:-16]
    if obj_name.endswith("_Rings"):
        return obj_name[:-6]
    if obj_name.endswith("_HoleSelection"):
        return obj_name[:-14]
    if "_Cutter_" in obj_name:
        return obj_name.split("_Cutter_")[0]
    if obj_name.endswith("_Cutter"):
        return obj_name[:-7]
    if "_Color_Skin_Slot_" in obj_name:
        return obj_name.split("_Color_Skin_Slot_")[0]
    if obj_name.endswith("_Color_Base"):
        return obj_name[:-11]
    if obj_name.endswith("_Blocks_Base"):
        return obj_name[:-12]
    if obj_name.endswith("_Blocks"):
        return obj_name[:-7]
    return obj_name


def get_blocks_name(root_name):
    return f"{root_name}_Blocks"


def get_building_copy_name(root_name):
    return f"{root_name}_body"


def get_base_name(root_name):
    return f"{root_name}_Blocks_Base"


def get_color_base_name(root_name):
    return f"{root_name}_Lego_Base"


def get_color_skin_name(root_name, slot_index, island_index=None):
    name = f"{root_name}_Lego_Skin_Slot_{slot_index + 1}"
    if island_index is not None:
        name += f"_{island_index + 1}"
    return name


def get_platform_copy_name(platform_name):
    return f"{platform_name}_HoleSelection"


def get_platform_walls_name(platform_name):
    return f"{platform_name}_Rings"


def get_platform_building_cutter_name(platform_name):
    return f"{platform_name}_Rings"


def get_platform_missing_walls_name(platform_name):
    return f"{platform_name}_Missing_Walls"


def get_platform_foot_name(root_name):
    return f"{root_name}_foot"


def get_platform_walls_copy_name(platform_name):
    return f"{platform_name}_Foot_Cutter_Copy"


def get_platform_walls_bool_name(platform_name):
    return f"{platform_name}_Foot_Cutter_Bool"


def get_platform_walls_copy_bool_name(platform_name):
    return f"{platform_name}_Foot_Cutter_Copy_Bool"


def get_platform_walls_2d_name(platform_name):
    return f"{platform_name}_Foot_Cutter_2D"


def set_metadata(obj, root_name, source_name):
    obj["mv_root_name"] = root_name
    obj["mv_source_object"] = source_name


def get_inferred_source_name(settings, obj):
    override_name = settings.texture_source_name.strip()
    if override_name:
        return override_name

    if "mv_source_object" in obj:
        return str(obj["mv_source_object"])

    root_name = get_root_name(obj.name)
    return root_name


def face_matches_world_axis(obj, face, axis_vec, threshold=0.9999):
    world_normal = (obj.matrix_world.to_3x3() @ face.normal).normalized()
    return world_normal.dot(axis_vec) > threshold


def find_new_object(pre_names, source_obj, context):
    post_names = set(obj.name for obj in bpy.data.objects)
    new_names = list(post_names - pre_names)
    if new_names:
        return bpy.data.objects[new_names[0]]

    candidates = [obj for obj in context.selected_objects if obj != source_obj]
    if candidates:
        return candidates[0]

    return None


def find_new_objects(pre_names):
    post_names = set(obj.name for obj in bpy.data.objects)
    new_names = list(post_names - pre_names)
    return [bpy.data.objects[name] for name in new_names if name in bpy.data.objects]


def remove_object_if_exists(obj):
    if obj is None:
        return

    try:
        obj_name = obj.name
    except ReferenceError:
        return

    if obj_name in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def meters_to_scene_units(context, meters_value):
    scale_length = context.scene.unit_settings.scale_length
    if scale_length <= 0.0:
        scale_length = 1.0
    return meters_value / scale_length


def mm_to_scene_units(context, mm_value):
    return meters_to_scene_units(context, mm_value * 0.001)


def get_object_xy_size_meters(context, obj):
    scale_length = context.scene.unit_settings.scale_length
    if scale_length <= 0.0:
        scale_length = 1.0
    return obj.dimensions.x * scale_length, obj.dimensions.y * scale_length


def get_source_scale_warnings(context, building_obj, platform_obj):
    warnings = []
    max_size_m = 0.27
    platform_min_size_m = 0.23

    # A building or platform wider/deeper than 27 cm usually means the scene scale was not applied correctly.
    for label, obj in (("Building", building_obj), ("Platform", platform_obj)):
        size_x_m, size_y_m = get_object_xy_size_meters(context, obj)
        if size_x_m > max_size_m or size_y_m > max_size_m:
            warnings.append(
                f"{label} is {size_x_m * 100.0:.1f} x {size_y_m * 100.0:.1f} cm; expected no X/Y side above 27 cm."
            )

    # A too-small platform also breaks the expected miniature footprint.
    platform_x_m, platform_y_m = get_object_xy_size_meters(context, platform_obj)
    if platform_x_m < platform_min_size_m or platform_y_m < platform_min_size_m:
        warnings.append(
            f"Platform is {platform_x_m * 100.0:.1f} x {platform_y_m * 100.0:.1f} cm; expected both X/Y sides at least 23 cm."
        )

    return warnings


def get_platform_copy_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_copy_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_walls_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_walls_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_building_cutter_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_building_cutter_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_missing_walls_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_missing_walls_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_foot_object(settings):
    building_obj = get_building_object(settings)
    if building_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_foot_name(get_root_name(building_obj.name)))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_walls_copy_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_walls_copy_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_walls_bool_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_walls_bool_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_walls_copy_bool_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_walls_copy_bool_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_platform_walls_2d_object(settings):
    platform_obj = get_platform_object(settings)
    if platform_obj is None:
        return None
    obj = bpy.data.objects.get(get_platform_walls_2d_name(platform_obj.name))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def has_platform_copy_object(context):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return False
    return get_platform_copy_object(settings) is not None


def has_platform_walls_object(context):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return False
    return get_platform_walls_object(settings) is not None


def has_platform_building_cutter_object(context):
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return False
    return get_platform_building_cutter_object(settings) is not None


def get_color_base_object(settings):
    building_obj = get_building_object(settings)
    if building_obj is None:
        return None
    obj = bpy.data.objects.get(get_color_base_name(get_root_name(building_obj.name)))
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_color_skin_objects(settings):
    building_obj = get_building_object(settings)
    if building_obj is None:
        return []

    root_name = get_root_name(building_obj.name)
    prefix = f"{root_name}_Lego_Skin_Slot_"
    return [
        obj for obj in bpy.data.objects
        if obj.type == 'MESH' and obj.name.startswith(prefix)
    ]


def get_sorted_color_skin_objects(settings):
    def slot_index_for_obj(obj):
        marker = "_Lego_Skin_Slot_"
        if marker not in obj.name:
            return 999
        value = obj.name.split(marker, 1)[1].split("_", 1)[0].split(".", 1)[0]
        try:
            return max(0, int(value) - 1)
        except ValueError:
            return 999

    return sorted(get_color_skin_objects(settings), key=lambda obj: (slot_index_for_obj(obj), obj.name))


def get_export_piece_objects(settings):
    candidates = [
        get_platform_foot_object(settings),
        get_color_base_object(settings),
        *get_sorted_color_skin_objects(settings),
    ]
    pieces = []
    seen = set()
    for obj in candidates:
        if obj is None or obj.type != 'MESH' or obj.name in seen:
            continue
        pieces.append(obj)
        seen.add(obj.name)
    return pieces


def sanitize_export_filename(name):
    sanitized = "".join(
        char if char.isalnum() or char in ("-", "_", ".") else "_"
        for char in name.strip()
    )
    sanitized = sanitized.strip("._")
    return sanitized or "MiniatureVoxeler_Piece"


def resolve_export_directory(settings):
    raw_path = settings.export_directory.strip() or "//MiniatureVoxeler_Export"
    export_dir = bpy.path.abspath(raw_path)
    if not os.path.isabs(export_dir):
        export_dir = os.path.abspath(export_dir)
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def export_object_to_stl(context, obj, filepath, scale=1000.0):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj

    try:
        bpy.ops.wm.stl_export(
            filepath=filepath,
            export_selected_objects=True,
            global_scale=scale,
            apply_modifiers=True,
        )
    except Exception as stl_export_error:
        try:
            bpy.ops.export_mesh.stl(
                filepath=filepath,
                use_selection=True,
                global_scale=scale,
                use_mesh_modifiers=True,
            )
        except Exception as legacy_error:
            raise RuntimeError(
                f"Could not export {obj.name} as STL: {stl_export_error}; fallback failed: {legacy_error}"
            ) from legacy_error


def ensure_boolean_modifier(target_obj, cutter_obj, modifier_name, operation='DIFFERENCE', solver='EXACT'):
    mod = target_obj.modifiers.get(modifier_name)
    if mod is None or mod.type != 'BOOLEAN':
        if mod is not None:
            target_obj.modifiers.remove(mod)
        mod = target_obj.modifiers.new(name=modifier_name, type='BOOLEAN')

    mod.operation = operation
    mod.object = cutter_obj
    if hasattr(mod, "solver"):
        mod.solver = solver
    if hasattr(mod, "use_self"):
        mod.use_self = True
    if hasattr(mod, "use_hole_tolerant"):
        mod.use_hole_tolerant = False
    return mod


def apply_boolean_modifiers_with_prefix(context, targets, prefix):
    applied_count = 0
    for target in targets:
        if target is None or target.name not in bpy.data.objects:
            continue
        set_active_object(context, target)
        for mod in list(target.modifiers):
            if mod.type == 'BOOLEAN' and mod.name.startswith(prefix):
                bpy.ops.object.modifier_apply(modifier=mod.name)
                applied_count += 1
    return applied_count


def apply_modifier_if_present(context, obj, modifier_name):
    if obj is None or obj.name not in bpy.data.objects:
        return False
    mod = obj.modifiers.get(modifier_name)
    if mod is None:
        return False
    set_active_object(context, obj)
    bpy.ops.object.modifier_apply(modifier=modifier_name)
    return True


def ensure_solidify_modifier(obj, modifier_name, thickness, offset, use_even_thickness=True):
    mod = obj.modifiers.get(modifier_name)
    if mod is None or mod.type != 'SOLIDIFY':
        if mod is not None:
            obj.modifiers.remove(mod)
        mod = obj.modifiers.new(name=modifier_name, type='SOLIDIFY')

    mod.thickness = thickness
    mod.offset = offset
    if hasattr(mod, "use_even_offset"):
        mod.use_even_offset = bool(use_even_thickness)
    if hasattr(mod, "solidify_mode"):
        mod.solidify_mode = 'NON_MANIFOLD'
    if hasattr(mod, "nonmanifold_thickness_mode"):
        mod.nonmanifold_thickness_mode = 'CONSTRAINTS'
    return mod


def group_vertices_by_z(bm, decimals=6):
    groups = {}
    for vert in bm.verts:
        groups.setdefault(round(float(vert.co.z), decimals), []).append(vert)
    return groups


def ordered_loop_from_edges(loop_verts, loop_edges):
    if not loop_verts or not loop_edges:
        return []

    vert_ids = {vert.index for vert in loop_verts}
    adjacency = {vert.index: [] for vert in loop_verts}
    for edge in loop_edges:
        a, b = edge.verts
        if a.index in vert_ids and b.index in vert_ids:
            adjacency[a.index].append(b.index)
            adjacency[b.index].append(a.index)

    start = loop_verts[0].index
    ordered = [start]
    prev = None
    current = start
    safety = len(loop_verts) + 4
    while safety > 0:
        candidates = [idx for idx in adjacency[current] if idx != prev]
        if not candidates:
            break
        next_idx = candidates[0]
        if next_idx == start:
            break
        if next_idx in ordered:
            break
        ordered.append(next_idx)
        prev = current
        current = next_idx
        safety -= 1

    return ordered


def polygon_signed_area_xy(coords):
    area = 0.0
    for index in range(len(coords)):
        x1, y1 = coords[index]
        x2, y2 = coords[(index + 1) % len(coords)]
        area += (x1 * y2) - (x2 * y1)
    return area * 0.5


def reverse_loop_order_preserve_start(ordered):
    if len(ordered) <= 2:
        return ordered[:]
    return [ordered[0]] + list(reversed(ordered[1:]))


def align_loop_order_to_master(loop_verts, ordered, master_coords_xy):
    if len(ordered) != len(master_coords_xy):
        return ordered

    vert_map = {vert.index: vert for vert in loop_verts}
    coords_xy = [(vert_map[index].co.x, vert_map[index].co.y) for index in ordered]
    if polygon_signed_area_xy(coords_xy) * polygon_signed_area_xy(master_coords_xy) < 0.0:
        ordered = reverse_loop_order_preserve_start(ordered)
        coords_xy = [(vert_map[index].co.x, vert_map[index].co.y) for index in ordered]

    best_shift = 0
    best_score = None
    count = len(ordered)
    for shift in range(count):
        score = 0.0
        for index in range(count):
            x1, y1 = coords_xy[(index + shift) % count]
            x2, y2 = master_coords_xy[index]
            dx = x1 - x2
            dy = y1 - y2
            score += (dx * dx) + (dy * dy)
        if best_score is None or score < best_score:
            best_score = score
            best_shift = shift

    return [ordered[(index + best_shift) % count] for index in range(count)]


def compute_loop_offset_directions(loop_verts, ordered_indices, miter_limit=1.5):
    if len(ordered_indices) < 3:
        return {}

    vert_map = {vert.index: vert for vert in loop_verts}
    coords_xy = [(vert_map[index].co.x, vert_map[index].co.y) for index in ordered_indices]
    area = polygon_signed_area_xy(coords_xy)
    clockwise = area < 0.0

    directions = {}
    for index, vert_index in enumerate(ordered_indices):
        prev_vert = vert_map[ordered_indices[(index - 1) % len(ordered_indices)]]
        curr_vert = vert_map[vert_index]
        next_vert = vert_map[ordered_indices[(index + 1) % len(ordered_indices)]]

        prev_edge = Vector((curr_vert.co.x - prev_vert.co.x, curr_vert.co.y - prev_vert.co.y))
        next_edge = Vector((next_vert.co.x - curr_vert.co.x, next_vert.co.y - curr_vert.co.y))
        if prev_edge.length <= 1e-9 or next_edge.length <= 1e-9:
            continue

        prev_edge.normalize()
        next_edge.normalize()

        if clockwise:
            prev_normal = Vector((-prev_edge.y, prev_edge.x))
            next_normal = Vector((-next_edge.y, next_edge.x))
        else:
            prev_normal = Vector((prev_edge.y, -prev_edge.x))
            next_normal = Vector((next_edge.y, -next_edge.x))

        bisector = prev_normal + next_normal
        if bisector.length <= 1e-9:
            bisector = next_normal.copy()
        else:
            bisector.normalize()

        dot_value = max(0.25, bisector.dot(next_normal))
        directions[curr_vert.index] = (bisector, min(max(1.0, miter_limit), 1.0 / dot_value))

    return directions


def get_boundary_loop_orders(bm):
    boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
    if not boundary_edges:
        return []

    edge_set = set(boundary_edges)
    loops = []
    while edge_set:
        seed = edge_set.pop()
        stack = [seed]
        component_edges = [seed]
        component_verts = set(seed.verts)
        while stack:
            edge = stack.pop()
            for vert in edge.verts:
                for linked_edge in vert.link_edges:
                    if linked_edge in edge_set:
                        edge_set.remove(linked_edge)
                        stack.append(linked_edge)
                        component_edges.append(linked_edge)
                        component_verts.update(linked_edge.verts)

        ordered = ordered_loop_from_edges(list(component_verts), component_edges)
        if len(ordered) >= 3:
            loops.append((list(component_verts), ordered))
    return loops


def get_master_boundary_loop(bm):
    loops = get_boundary_loop_orders(bm)
    if not loops:
        return None, None

    def loop_score(item):
        loop_verts, ordered = item
        vert_map = {vert.index: vert for vert in loop_verts}
        avg_z = sum(vert_map[index].co.z for index in ordered) / len(ordered)
        return avg_z, len(ordered)

    return max(loops, key=loop_score)


def compute_xy_offset_data(bm, miter_limit=1.5):
    master_loop_verts, master_ordered = get_master_boundary_loop(bm)
    if not master_ordered:
        return {vert.index: (Vector((1.0, 0.0)), 1.0) for vert in bm.verts}

    master_vert_map = {vert.index: vert for vert in master_loop_verts}
    master_directions = compute_loop_offset_directions(master_loop_verts, master_ordered, miter_limit)

    master_samples = []
    for vert_index in master_ordered:
        vert = master_vert_map[vert_index]
        master_samples.append((
            vert.co.x,
            vert.co.y,
            master_directions.get(vert_index, (Vector((1.0, 0.0)), 1.0)),
        ))

    offset_data = {}
    for vert in bm.verts:
        best_sample = None
        best_distance = None
        for sample_x, sample_y, sample_data in master_samples:
            dx = vert.co.x - sample_x
            dy = vert.co.y - sample_y
            distance = (dx * dx) + (dy * dy)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_sample = sample_data
        offset_data[vert.index] = best_sample or (Vector((1.0, 0.0)), 1.0)
    return offset_data


def cleanup_boolean_mesh(context, obj, triangulate=False):
    set_active_object(context, obj)
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(
        threshold=meters_to_scene_units(context, 0.00001),
        use_unselected=False,
        use_sharp_edge_from_normals=False,
    )
    bpy.ops.mesh.dissolve_degenerate(threshold=meters_to_scene_units(context, 0.00001))
    bpy.ops.mesh.delete_loose()
    bpy.ops.mesh.normals_make_consistent(inside=False)
    if triangulate:
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.quads_convert_to_tris()
    bpy.ops.object.mode_set(mode='OBJECT')


def recalculate_normals_outside(context, obj):
    if obj is None or obj.type != 'MESH':
        return False

    set_active_object(context, obj)
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    return True


def segments_cross_xy(a1, a2, b1, b2, epsilon=1e-14):
    def orient(p1, p2, p3):
        return ((p2.x - p1.x) * (p3.y - p1.y)) - ((p2.y - p1.y) * (p3.x - p1.x))

    return (
        orient(a1, a2, b1) * orient(a1, a2, b2) < -epsilon and
        orient(b1, b2, a1) * orient(b1, b2, a2) < -epsilon
    )


def segment_intersection_xy(a1, a2, b1, b2):
    denominator = ((a1.x - a2.x) * (b1.y - b2.y)) - ((a1.y - a2.y) * (b1.x - b2.x))
    if abs(denominator) <= 1e-14:
        return None

    a_cross = (a1.x * a2.y) - (a1.y * a2.x)
    b_cross = (b1.x * b2.y) - (b1.y * b2.x)
    x = ((a_cross * (b1.x - b2.x)) - ((a1.x - a2.x) * b_cross)) / denominator
    y = ((a_cross * (b1.y - b2.y)) - ((a1.y - a2.y) * b_cross)) / denominator
    return Vector((x, y))


def edge_length_xy(mesh, edge):
    v1 = mesh.vertices[edge.vertices[0]].co
    v2 = mesh.vertices[edge.vertices[1]].co
    return hypot(v2.x - v1.x, v2.y - v1.y)


def count_crossed_quads_xy(mesh):
    crossed = 0
    for polygon in mesh.polygons:
        if len(polygon.vertices) != 4:
            continue
        coords = [mesh.vertices[index].co for index in polygon.vertices]
        if (
            segments_cross_xy(coords[0], coords[1], coords[2], coords[3]) or
            segments_cross_xy(coords[1], coords[2], coords[3], coords[0])
        ):
            crossed += 1
    return crossed


def count_projected_self_intersections_xy(mesh, limit=1000):
    intersections = 0
    edges = [
        edge for edge in mesh.edges
        if edge_length_xy(mesh, edge) > 1e-8
    ]
    for index, edge_a in enumerate(edges):
        a1 = mesh.vertices[edge_a.vertices[0]].co
        a2 = mesh.vertices[edge_a.vertices[1]].co
        for edge_b in edges[index + 1:]:
            if set(edge_a.vertices) & set(edge_b.vertices):
                continue
            b1 = mesh.vertices[edge_b.vertices[0]].co
            b2 = mesh.vertices[edge_b.vertices[1]].co
            if segments_cross_xy(a1, a2, b1, b2):
                intersections += 1
                if intersections >= limit:
                    return intersections
    return intersections


def sorted_quad_vertices_xy(mesh, vertex_indices):
    coords = [mesh.vertices[index].co for index in vertex_indices]
    center_x = sum(coord.x for coord in coords) / len(coords)
    center_y = sum(coord.y for coord in coords) / len(coords)
    return sorted(
        vertex_indices,
        key=lambda index: atan2(mesh.vertices[index].co.y - center_y, mesh.vertices[index].co.x - center_x),
    )


def same_xy(coord, xy, epsilon=1e-9):
    return abs(coord.x - xy[0]) <= epsilon and abs(coord.y - xy[1]) <= epsilon


def repair_projected_self_intersections_xy(mesh):
    edges = [
        edge for edge in mesh.edges
        if edge_length_xy(mesh, edge) > 1e-8
    ]
    move_targets = []

    for index, edge_a in enumerate(edges):
        a1 = mesh.vertices[edge_a.vertices[0]].co
        a2 = mesh.vertices[edge_a.vertices[1]].co
        length_a = edge_length_xy(mesh, edge_a)
        for edge_b in edges[index + 1:]:
            if set(edge_a.vertices) & set(edge_b.vertices):
                continue
            b1 = mesh.vertices[edge_b.vertices[0]].co
            b2 = mesh.vertices[edge_b.vertices[1]].co
            if not segments_cross_xy(a1, a2, b1, b2):
                continue

            length_b = edge_length_xy(mesh, edge_b)
            if length_a <= 1e-8 or length_b <= 1e-8:
                continue
            if min(length_a, length_b) > max(length_a, length_b) * 0.75:
                continue

            intersection = segment_intersection_xy(a1, a2, b1, b2)
            if intersection is None:
                continue

            short_edge = edge_a if length_a < length_b else edge_b
            for vertex_index in short_edge.vertices:
                coord = mesh.vertices[vertex_index].co
                move_targets.append(((coord.x, coord.y), (intersection.x, intersection.y)))

    if not move_targets:
        return 0

    unique_targets = {}
    for source_xy, target_xy in move_targets:
        key = (round(source_xy[0], 9), round(source_xy[1], 9))
        unique_targets[key] = target_xy

    moved = 0
    for vertex in mesh.vertices:
        for source_key, target_xy in unique_targets.items():
            if same_xy(vertex.co, source_key):
                vertex.co.x = target_xy[0]
                vertex.co.y = target_xy[1]
                moved += 1
                break

    if moved:
        mesh.update()
    return len(unique_targets)


def mesh_xy_key(coord, decimals=6):
    return (round(coord.x, decimals), round(coord.y, decimals))


def repair_sharp_join_columns_xy(mesh):
    xy_to_vertices = {}
    for vertex in mesh.vertices:
        xy_to_vertices.setdefault(mesh_xy_key(vertex.co), []).append(vertex.index)

    adjacency = {key: set() for key in xy_to_vertices}
    for edge in mesh.edges:
        key_a = mesh_xy_key(mesh.vertices[edge.vertices[0]].co)
        key_b = mesh_xy_key(mesh.vertices[edge.vertices[1]].co)
        if key_a == key_b:
            continue
        adjacency.setdefault(key_a, set()).add(key_b)
        adjacency.setdefault(key_b, set()).add(key_a)

    repairs = []
    for center_key, neighbors in adjacency.items():
        if len(neighbors) != 4:
            continue

        neighbor_list = list(neighbors)
        closest_pair = None
        closest_distance = None
        for index, key_a in enumerate(neighbor_list):
            for key_b in neighbor_list[index + 1:]:
                distance = hypot(key_b[0] - key_a[0], key_b[1] - key_a[1])
                if closest_distance is None or distance < closest_distance:
                    closest_distance = distance
                    closest_pair = (key_a, key_b)

        if closest_pair is None or closest_distance is None or closest_distance > 0.0025:
            continue

        other_keys = [key for key in neighbor_list if key not in closest_pair]
        if len(other_keys) != 2:
            continue

        center = Vector(center_key)
        side_a = Vector(closest_pair[0])
        side_b = Vector(closest_pair[1])
        far_a = Vector(other_keys[0])
        far_b = Vector(other_keys[1])
        rail = far_b - far_a
        if rail.length <= 1e-9:
            continue

        t = max(0.0, min(1.0, (center - far_a).dot(rail) / rail.dot(rail)))
        projected_center = far_a + (rail * t)
        shift = (projected_center - center) * 0.6

        bridge = (side_a + side_b) * 0.5
        bridge.y += shift.y
        cleaned_center = center + shift

        repairs.append((closest_pair, center_key, bridge, cleaned_center))

    if not repairs:
        return 0

    repaired_keys = set()
    for side_keys, center_key, bridge, cleaned_center in repairs:
        for vertex in mesh.vertices:
            key = mesh_xy_key(vertex.co)
            if key in side_keys:
                vertex.co.x = bridge.x
                vertex.co.y = bridge.y
                repaired_keys.add(key)
            elif key == center_key:
                vertex.co.x = cleaned_center.x
                vertex.co.y = cleaned_center.y
                repaired_keys.add(key)

    mesh.update()
    return len(repairs)


def repair_crossed_quads_xy(mesh):
    repaired = 0
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    faces = []
    material_indices = []

    for polygon in mesh.polygons:
        vertex_indices = list(polygon.vertices)
        if len(vertex_indices) == 4:
            coords = [mesh.vertices[index].co for index in vertex_indices]
            if (
                segments_cross_xy(coords[0], coords[1], coords[2], coords[3]) or
                segments_cross_xy(coords[1], coords[2], coords[3], coords[0])
            ):
                vertex_indices = sorted_quad_vertices_xy(mesh, vertex_indices)
                repaired += 1
        faces.append(vertex_indices)
        material_indices.append(polygon.material_index)

    if repaired == 0:
        return 0

    mesh.clear_geometry()
    mesh.from_pydata([tuple(vertex) for vertex in vertices], [], faces)
    mesh.update(calc_edges=True)
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
    return repaired


def build_thickened_surface_mesh(
    context,
    source_obj,
    target_obj,
    thickness,
    offset,
    triangulate=False,
    miter_limit=1.5,
    repair_sharp_joins=True,
):
    source_bm = bmesh.new()
    source_bm.from_mesh(source_obj.data)
    source_bm.verts.ensure_lookup_table()
    source_bm.edges.ensure_lookup_table()
    source_bm.faces.ensure_lookup_table()

    if not source_bm.faces:
        source_bm.free()
        return 0, 0, 0, 0

    offset_data = compute_xy_offset_data(source_bm, miter_limit)
    inward_distance = max(0.0, thickness * (1.0 - offset) * 0.5)
    outward_distance = max(0.0, thickness * (1.0 + offset) * 0.5)

    build_bm = bmesh.new()
    inner_map = {}
    outer_map = {}
    for vert in source_bm.verts:
        direction, scale = offset_data.get(vert.index, (Vector((1.0, 0.0)), 1.0))
        inner_co = vert.co.copy()
        outer_co = vert.co.copy()
        inner_co.x -= direction.x * inward_distance * scale
        inner_co.y -= direction.y * inward_distance * scale
        outer_co.x += direction.x * outward_distance * scale
        outer_co.y += direction.y * outward_distance * scale
        inner_map[vert.index] = build_bm.verts.new(inner_co)
        outer_map[vert.index] = build_bm.verts.new(outer_co)

    build_bm.verts.ensure_lookup_table()
    for face in source_bm.faces:
        try:
            build_bm.faces.new([inner_map[vert.index] for vert in face.verts])
        except ValueError:
            pass
        try:
            build_bm.faces.new(list(reversed([outer_map[vert.index] for vert in face.verts])))
        except ValueError:
            pass

    for edge in source_bm.edges:
        if not edge.is_boundary:
            continue
        v1, v2 = edge.verts
        quad = [
            inner_map[v1.index],
            inner_map[v2.index],
            outer_map[v2.index],
            outer_map[v1.index],
        ]
        try:
            build_bm.faces.new(quad)
        except ValueError:
            pass

    build_bm.normal_update()
    build_bm.to_mesh(target_obj.data)
    target_obj.data.update()
    build_bm.free()
    source_bm.free()

    repaired_columns = repair_sharp_join_columns_xy(target_obj.data) if repair_sharp_joins else 0
    repaired_intersections = repair_projected_self_intersections_xy(target_obj.data) if repair_sharp_joins else 0
    repaired_quads = repair_crossed_quads_xy(target_obj.data) if repair_sharp_joins else 0
    cleanup_boolean_mesh(context, target_obj, triangulate=triangulate)
    remaining_intersections = count_projected_self_intersections_xy(target_obj.data)
    return len(inner_map), repaired_columns + repaired_intersections + repaired_quads, count_crossed_quads_xy(target_obj.data), remaining_intersections


def show_info_popup(context, title, lines, icon='INFO'):
    def draw(self, _context):
        col = self.layout.column(align=True)
        for line in lines:
            col.label(text=line)

    context.window_manager.popup_menu(draw, title=title, icon=icon)


def get_connected_edge_loop_from_seed(bm, seed_edge):
    use_boundary_only = seed_edge.is_boundary
    stack = [seed_edge]
    visited = set()
    loop_edges = []

    while stack:
        edge = stack.pop()
        if edge.index in visited:
            continue
        visited.add(edge.index)

        if use_boundary_only and not edge.is_boundary:
            continue

        loop_edges.append(edge)

        for vert in edge.verts:
            for linked_edge in vert.link_edges:
                if linked_edge.index in visited:
                    continue
                if use_boundary_only and not linked_edge.is_boundary:
                    continue
                stack.append(linked_edge)

    return loop_edges


def select_connected_edge_loops_from_seeds(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    seed_edges = [edge for edge in bm.edges if edge.select]
    if not seed_edges:
        return 0

    selected_count = 0
    for seed_edge in seed_edges:
        for edge in get_connected_edge_loop_from_seed(bm, seed_edge):
            edge.select = True
            selected_count += 1

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected_count


def get_selected_edge_groups(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    selected_edges = [edge for edge in bm.edges if edge.select]
    visited = set()
    groups = []
    for edge in selected_edges:
        if edge.index in visited:
            continue
        stack = [edge]
        group = []
        while stack:
            current = stack.pop()
            if current.index in visited or not current.select:
                continue
            visited.add(current.index)
            group.append(current)
            for vert in current.verts:
                for linked_edge in vert.link_edges:
                    if linked_edge.select and linked_edge.index not in visited:
                        stack.append(linked_edge)
        if group:
            groups.append(group)
    return groups


def world_xy_distance_squared(point_a, point_b):
    return (point_a.x - point_b.x) ** 2 + (point_a.y - point_b.y) ** 2


def get_missing_wall_xy_match_tolerance(cutter_world_verts, edge_world_verts):
    coords = cutter_world_verts + edge_world_verts
    if not coords:
        return 0.0005

    min_x = min(coord.x for coord in coords)
    max_x = max(coord.x for coord in coords)
    min_y = min(coord.y for coord in coords)
    max_y = max(coord.y for coord in coords)
    xy_span = hypot(max_x - min_x, max_y - min_y)
    return min(max(xy_span * 0.002, 0.00005), 0.002)


def find_target_wall_z_from_cutter(cutter_world_verts, world_point, xy_tolerance):
    if not cutter_world_verts:
        return world_point.z

    tolerance_squared = xy_tolerance * xy_tolerance
    matching_z_values = [
        vert.z for vert in cutter_world_verts
        if world_xy_distance_squared(world_point, vert) <= tolerance_squared
    ]
    if matching_z_values:
        return max(matching_z_values)

    # This fallback keeps the tool usable when the selected edge is slightly offset
    # from the cutter endpoint, but exact/almost-exact XY matches are preferred.
    best_distance = min(world_xy_distance_squared(world_point, vert) for vert in cutter_world_verts)
    nearest_z_values = [
        vert.z for vert in cutter_world_verts
        if world_xy_distance_squared(world_point, vert) <= best_distance + 0.0000000001
    ]
    return max(nearest_z_values) if nearest_z_values else world_point.z


def get_mesh_edge_groups(mesh):
    visited = set()
    edge_to_verts = {edge.index: tuple(edge.vertices) for edge in mesh.edges}
    vert_to_edges = {}
    for edge_index, vert_indices in edge_to_verts.items():
        for vert_index in vert_indices:
            vert_to_edges.setdefault(vert_index, []).append(edge_index)

    groups = []
    for edge in mesh.edges:
        if edge.index in visited:
            continue

        stack = [edge.index]
        group = []
        while stack:
            edge_index = stack.pop()
            if edge_index in visited:
                continue
            visited.add(edge_index)
            group.append(edge_index)
            for vert_index in edge_to_verts[edge_index]:
                for linked_edge_index in vert_to_edges.get(vert_index, []):
                    if linked_edge_index not in visited:
                        stack.append(linked_edge_index)
        groups.append(group)
    return groups


def order_edge_group_vertices(mesh, edge_indices):
    neighbors_by_vert = {}
    for edge_index in edge_indices:
        vert_a, vert_b = mesh.edges[edge_index].vertices
        neighbors_by_vert.setdefault(vert_a, []).append(vert_b)
        neighbors_by_vert.setdefault(vert_b, []).append(vert_a)

    if not neighbors_by_vert:
        return []

    endpoints = [vert_index for vert_index, neighbors in neighbors_by_vert.items() if len(neighbors) == 1]
    start_vert = endpoints[0] if endpoints else next(iter(neighbors_by_vert))
    ordered = [start_vert]
    previous_vert = None
    current_vert = start_vert

    while True:
        next_vert = None
        for candidate in neighbors_by_vert.get(current_vert, []):
            if candidate != previous_vert:
                next_vert = candidate
                break
        if next_vert is None or next_vert in ordered:
            break
        ordered.append(next_vert)
        previous_vert, current_vert = current_vert, next_vert

    return ordered


def get_interpolated_top_z_by_vertex(mesh, edge_indices, edge_world_verts, cutter_world_verts, xy_tolerance):
    ordered_vert_indices = order_edge_group_vertices(mesh, edge_indices)
    if len(ordered_vert_indices) < 2:
        return {}

    # The two ends of the selected missing-wall chain are A and B. Their nearest
    # existing cutter-wall vertices at the same XY provide the top Z values.
    start_world = edge_world_verts[ordered_vert_indices[0]]
    end_world = edge_world_verts[ordered_vert_indices[-1]]
    start_z = find_target_wall_z_from_cutter(cutter_world_verts, start_world, xy_tolerance)
    end_z = find_target_wall_z_from_cutter(cutter_world_verts, end_world, xy_tolerance)

    distances = [0.0]
    for index in range(1, len(ordered_vert_indices)):
        previous_world = edge_world_verts[ordered_vert_indices[index - 1]]
        current_world = edge_world_verts[ordered_vert_indices[index]]
        distances.append(distances[-1] + (current_world - previous_world).length)

    total_distance = distances[-1]
    if total_distance <= 0.0:
        return {vert_index: start_z for vert_index in ordered_vert_indices}

    top_z_by_vertex = {}
    for index, vert_index in enumerate(ordered_vert_indices):
        blend = distances[index] / total_distance
        top_z_by_vertex[vert_index] = start_z + (end_z - start_z) * blend
    return top_z_by_vertex


def build_missing_wall_faces_from_edge_object(cutter_obj, edge_obj):
    if cutter_obj is None or edge_obj is None:
        return 0

    edge_mesh = edge_obj.data
    if not edge_mesh.edges:
        return 0

    cutter_world_to_local = cutter_obj.matrix_world.inverted()
    cutter_world_verts = [cutter_obj.matrix_world @ vert.co for vert in cutter_obj.data.vertices]
    edge_world_verts = [edge_obj.matrix_world @ vert.co for vert in edge_mesh.vertices]
    if not cutter_world_verts or not edge_world_verts:
        return 0

    bm = bmesh.new()
    bm.from_mesh(cutter_obj.data)
    xy_tolerance = get_missing_wall_xy_match_tolerance(cutter_world_verts, edge_world_verts)

    created_faces = 0
    for edge_indices in get_mesh_edge_groups(edge_mesh):
        top_z_by_vertex = get_interpolated_top_z_by_vertex(
            edge_mesh,
            edge_indices,
            edge_world_verts,
            cutter_world_verts,
            xy_tolerance,
        )

        for edge_index in edge_indices:
            edge = edge_mesh.edges[edge_index]
            bottom_a_world = edge_world_verts[edge.vertices[0]]
            bottom_b_world = edge_world_verts[edge.vertices[1]]
            top_a_world = Vector((
                bottom_a_world.x,
                bottom_a_world.y,
                top_z_by_vertex.get(edge.vertices[0], bottom_a_world.z),
            ))
            top_b_world = Vector((
                bottom_b_world.x,
                bottom_b_world.y,
                top_z_by_vertex.get(edge.vertices[1], bottom_b_world.z),
            ))

            # Each selected platform edge becomes one vertical quad, extruded up
            # to the A/B heights inferred from the neighboring wall cutter.
            verts = [
                bm.verts.new(cutter_world_to_local @ bottom_a_world),
                bm.verts.new(cutter_world_to_local @ bottom_b_world),
                bm.verts.new(cutter_world_to_local @ top_b_world),
                bm.verts.new(cutter_world_to_local @ top_a_world),
            ]
            try:
                bm.faces.new(verts)
                created_faces += 1
            except ValueError:
                pass

    bm.normal_update()
    bm.to_mesh(cutter_obj.data)
    cutter_obj.data.update()
    bm.free()
    return created_faces


def selected_edge_group_z_values(edge_group):
    values = []
    seen = set()
    for edge in edge_group:
        for vert in edge.verts:
            if vert.index not in seen:
                seen.add(vert.index)
                values.append(vert.co.z)
    return values


def extrude_foot_gap_edges_on_z(obj, top_edge_indices, top_up_distance, lower_edge_indices=None, lower_down_distance=0.0):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    top_edges = [bm.edges[index] for index in top_edge_indices if 0 <= index < len(bm.edges)]
    lower_edges = [bm.edges[index] for index in (lower_edge_indices or []) if 0 <= index < len(bm.edges)]
    if not top_edges:
        bm.free()
        return 0

    top_extruded_count = 0
    lower_extruded_count = 0

    if lower_down_distance > 0.0 and lower_edges:
        result = bmesh.ops.extrude_edge_only(bm, edges=lower_edges)
        down_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in down_verts:
            vert.co.z -= lower_down_distance
        lower_extruded_count = len(down_verts)

    if top_up_distance > 0.0:
        result = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
        up_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in up_verts:
            vert.co.z += top_up_distance
        top_extruded_count = len(up_verts)

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return top_extruded_count, lower_extruded_count


def get_ordered_loop_coords(obj, edge_indices):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    loop_edges = [bm.edges[index] for index in edge_indices if 0 <= index < len(bm.edges)]
    if not loop_edges:
        bm.free()
        return []

    vert_to_edges = {}
    for edge in loop_edges:
        for vert in edge.verts:
            vert_to_edges.setdefault(vert.index, []).append(edge)

    start_edge = loop_edges[0]
    start_vert = start_edge.verts[0]
    ordered_verts = [start_vert]
    current_vert = start_edge.verts[1]
    current_edge = start_edge
    visited_edges = {start_edge.index}

    safety_limit = len(loop_edges) + 5
    while len(visited_edges) < len(loop_edges) and safety_limit > 0:
        ordered_verts.append(current_vert)
        next_edge = None
        for candidate in vert_to_edges.get(current_vert.index, []):
            if candidate.index != current_edge.index and candidate.index not in visited_edges:
                next_edge = candidate
                break
        if next_edge is None:
            break
        visited_edges.add(next_edge.index)
        current_edge = next_edge
        current_vert = next_edge.other_vert(current_vert)
        safety_limit -= 1

    if current_vert.index != ordered_verts[0].index:
        ordered_verts.append(current_vert)

    coords = [(vert.co.x, vert.co.y, vert.co.z) for vert in ordered_verts]
    bm.free()

    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords.pop()

    return coords


def xy_distance_sq_coords(a, b):
    return ((a[0] - b[0]) ** 2) + ((a[1] - b[1]) ** 2)


def find_snap_cluster(clusters, coord, tolerance_sq):
    best_index = None
    best_distance = None
    for index, cluster in enumerate(clusters):
        distance = xy_distance_sq_coords(cluster["coord"], coord)
        if distance <= tolerance_sq and (best_distance is None or distance < best_distance):
            best_index = index
            best_distance = distance
    return best_index


def get_or_create_snap_cluster(clusters, coord, tolerance_sq):
    cluster_index = find_snap_cluster(clusters, coord, tolerance_sq)
    if cluster_index is None:
        clusters.append({
            "coord": (float(coord[0]), float(coord[1]), float(coord[2])),
            "count": 1,
        })
        return len(clusters) - 1

    cluster = clusters[cluster_index]
    count = cluster["count"]
    current = cluster["coord"]
    cluster["coord"] = (
        ((current[0] * count) + float(coord[0])) / (count + 1),
        ((current[1] * count) + float(coord[1])) / (count + 1),
        ((current[2] * count) + float(coord[2])) / (count + 1),
    )
    cluster["count"] = count + 1
    return cluster_index


def get_graph_components(adjacency):
    components = []
    visited = set()
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    stack.append(neighbor)
        components.append(component)
    return components


def bridge_endpoint_gaps(adjacency, clusters, tolerance_sq):
    endpoints = [node for node in adjacency if len(adjacency.get(node, [])) == 1]
    bridged_count = 0
    while len(endpoints) >= 2:
        best_pair = None
        best_distance = None
        for index, node_a in enumerate(endpoints):
            for node_b in endpoints[index + 1:]:
                if node_b in adjacency[node_a]:
                    continue
                distance = xy_distance_sq_coords(clusters[node_a]["coord"], clusters[node_b]["coord"])
                if distance <= tolerance_sq and (best_distance is None or distance < best_distance):
                    best_pair = (node_a, node_b)
                    best_distance = distance

        if best_pair is None:
            break

        node_a, node_b = best_pair
        adjacency[node_a].add(node_b)
        adjacency[node_b].add(node_a)
        bridged_count += 1
        endpoints = [node for node in adjacency if len(adjacency.get(node, [])) == 1]
    return bridged_count


def order_closed_graph_component(adjacency, component, clusters):
    if len(component) < 3:
        return [], "too few vertices"

    bad_degree_nodes = [node for node in component if len(adjacency.get(node, [])) != 2]
    if bad_degree_nodes:
        open_count = sum(1 for node in component if len(adjacency.get(node, [])) == 1)
        branch_count = sum(1 for node in component if len(adjacency.get(node, [])) > 2)
        return [], f"{open_count} open end(s), {branch_count} branch point(s)"

    start = min(
        component,
        key=lambda node: (
            clusters[node]["coord"][0],
            clusters[node]["coord"][1],
            clusters[node]["coord"][2],
        ),
    )
    ordered = [start]
    previous = None
    current = start

    for _ in range(len(component) + 2):
        neighbors = sorted(adjacency[current])
        next_node = None
        for candidate in neighbors:
            if candidate != previous:
                next_node = candidate
                break
        if next_node is None:
            break
        if next_node == start:
            if len(ordered) == len(component):
                coords = [clusters[node]["coord"] for node in ordered]
                return coords, None
            break
        if next_node in ordered:
            break
        ordered.append(next_node)
        previous, current = current, next_node

    return [], "could not walk a single closed boundary"


def resolve_selected_edge_rings(obj, edge_indices, gap_tolerance):
    mesh = obj.data
    clusters = []
    edge_pairs = set()
    tolerance_sq = max(0.0, float(gap_tolerance)) ** 2

    for edge_index in edge_indices:
        if edge_index < 0 or edge_index >= len(mesh.edges):
            continue
        edge = mesh.edges[edge_index]
        node_indices = []
        for vertex_index in edge.vertices:
            coord = mesh.vertices[vertex_index].co
            node_indices.append(get_or_create_snap_cluster(
                clusters,
                (coord.x, coord.y, coord.z),
                tolerance_sq,
            ))
        if node_indices[0] == node_indices[1]:
            continue
        edge_pairs.add(tuple(sorted(node_indices)))

    adjacency = {index: set() for index in range(len(clusters))}
    for node_a, node_b in edge_pairs:
        adjacency[node_a].add(node_b)
        adjacency[node_b].add(node_a)

    rings = []
    messages = []
    bridged_total = bridge_endpoint_gaps(adjacency, clusters, tolerance_sq)
    for component in get_graph_components(adjacency):
        if len(component) < 3:
            continue
        coords, error = order_closed_graph_component(adjacency, component, clusters)
        if coords:
            rings.append(coords)
        elif error:
            messages.append(error)

    return rings, bridged_total, messages


def get_selected_edge_index_groups_from_mesh(mesh, edge_indices):
    selected_edge_set = set(edge_indices)
    edge_to_verts = {
        edge.index: tuple(edge.vertices)
        for edge in mesh.edges
        if edge.index in selected_edge_set
    }
    vert_to_edges = {}
    for edge_index, vertices in edge_to_verts.items():
        for vertex_index in vertices:
            vert_to_edges.setdefault(vertex_index, []).append(edge_index)

    groups = []
    visited = set()
    for edge_index in edge_to_verts:
        if edge_index in visited:
            continue
        stack = [edge_index]
        group = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            group.append(current)
            for vertex_index in edge_to_verts[current]:
                for linked_edge_index in vert_to_edges.get(vertex_index, []):
                    if linked_edge_index not in visited:
                        stack.append(linked_edge_index)
        if group:
            groups.append(group)
    return groups


def resolve_selected_edge_rings_relaxed(obj, edge_indices):
    rings = []
    for edge_group in get_selected_edge_index_groups_from_mesh(obj.data, edge_indices):
        coords = get_ordered_loop_coords(obj, edge_group)
        if len(coords) >= 3 and loop_edges_form_closed_boundary(obj.data, edge_group):
            rings.append(coords)
    return rings


def loop_edges_form_closed_boundary(mesh, edge_indices):
    vert_degrees = {}
    valid_edge_count = 0
    for edge_index in edge_indices:
        if edge_index < 0 or edge_index >= len(mesh.edges):
            continue
        valid_edge_count += 1
        for vertex_index in mesh.edges[edge_index].vertices:
            vert_degrees[vertex_index] = vert_degrees.get(vertex_index, 0) + 1
    return valid_edge_count >= 3 and vert_degrees and all(degree == 2 for degree in vert_degrees.values())


def clear_stored_platform_rings_data(obj):
    for key in ("mv_platform_top_rings_json", "mv_platform_top_ring", "mv_platform_lower_z"):
        if key in obj:
            del obj[key]


def resolve_all_edge_rings(obj, gap_tolerance=0.0):
    edge_indices = [edge.index for edge in obj.data.edges]
    if not edge_indices:
        return [], 0, ["rings object has no edges"]
    return resolve_selected_edge_rings(obj, edge_indices, gap_tolerance)


def rebuild_tube_from_top_loop(obj, top_edge_indices, target_bottom_z, top_up_distance, bottom_down_distance=0.0):
    loop_coords = get_ordered_loop_coords(obj, top_edge_indices)
    if len(loop_coords) < 3:
        return 0, 0, 0

    bm = bmesh.new()
    top_verts = [bm.verts.new(coord) for coord in loop_coords]
    bm.verts.ensure_lookup_table()

    top_edges = []
    for index in range(len(top_verts)):
        edge = bm.edges.new((top_verts[index], top_verts[(index + 1) % len(top_verts)]))
        top_edges.append(edge)

    top_z = max(coord[2] for coord in loop_coords)
    down_distance = max(0.0, top_z - target_bottom_z)

    bottom_edges = []
    tube_side_count = 0
    if down_distance > 0.0:
        result = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
        new_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in new_verts:
            vert.co.z = target_bottom_z
        bottom_vert_set = set(new_verts)
        bottom_edges = [
            item for item in result["geom"]
            if isinstance(item, bmesh.types.BMEdge) and item.verts[0] in bottom_vert_set and item.verts[1] in bottom_vert_set
        ]
        tube_side_count = len(new_verts)

    top_up_count = 0
    if top_up_distance > 0.0:
        result = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
        up_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in up_verts:
            vert.co.z += top_up_distance
        top_up_count = len(up_verts)

    bottom_down_count = 0
    if bottom_down_distance > 0.0 and bottom_edges:
        result = bmesh.ops.extrude_edge_only(bm, edges=bottom_edges)
        down_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in down_verts:
            vert.co.z -= bottom_down_distance
        bottom_down_count = len(down_verts)

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return tube_side_count, top_up_count, bottom_down_count


def set_mesh_to_ring(obj, coords):
    verts = [tuple(coord) for coord in coords]
    edges = [(index, (index + 1) % len(verts)) for index in range(len(verts))]
    obj.data.clear_geometry()
    obj.data.from_pydata(verts, edges, [])
    obj.data.update()


def set_mesh_to_rings(obj, rings):
    verts = []
    edges = []
    for coords in rings:
        if len(coords) < 3:
            continue
        start_index = len(verts)
        verts.extend(tuple(coord) for coord in coords)
        count = len(coords)
        edges.extend(
            (start_index + index, start_index + ((index + 1) % count))
            for index in range(count)
        )

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, edges, [])
    obj.data.update()


def enable_view3d_xray(context):
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            if hasattr(space.shading, "show_xray"):
                space.shading.show_xray = True
            if hasattr(space.shading, "show_xray_wireframe"):
                space.shading.show_xray_wireframe = True


def select_lasso_tool():
    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.select_lasso")
    except Exception:
        pass


def get_selected_vertex_world_coords(context):
    source_obj = context.edit_object if context.mode == 'EDIT_MESH' else context.object
    if source_obj is None or source_obj.type != 'MESH':
        return None, []

    coords = []
    if context.mode == 'EDIT_MESH' and context.edit_object == source_obj:
        bm = bmesh.from_edit_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        coords = [source_obj.matrix_world @ vert.co for vert in bm.verts if vert.select]
    else:
        coords = [source_obj.matrix_world @ vert.co for vert in source_obj.data.vertices if vert.select]
    return source_obj, coords


def append_world_vertices_to_object(obj, world_coords):
    if not world_coords:
        return 0

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    local_coords = [obj.matrix_world.inverted() @ coord for coord in world_coords]
    mesh = obj.data
    old_vert_count = len(mesh.vertices)
    verts = [vert.co.copy() for vert in mesh.vertices] + local_coords
    edges = [tuple(edge.vertices) for edge in mesh.edges]
    faces = [tuple(poly.vertices) for poly in mesh.polygons]

    mesh.clear_geometry()
    mesh.from_pydata([tuple(vert) for vert in verts], edges, faces)
    mesh.update(calc_edges=True)

    for vertex in mesh.vertices:
        vertex.select = vertex.index >= old_vert_count
    for edge in mesh.edges:
        edge.select = False
    for poly in mesh.polygons:
        poly.select = False
    mesh.update()
    clear_stored_platform_rings_data(obj)
    return len(local_coords)


def bridge_selected_vertices_on_object(obj, max_segment_length):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    selected_verts = [vert for vert in bm.verts if vert.select]
    if len(selected_verts) < 2 or len(selected_verts) % 2 != 0:
        return None

    existing_edges = {frozenset(edge.verts) for edge in bm.edges}
    created_count = 0
    created_vertex_count = 0
    created_edges = []
    selected_verts.sort(key=lambda vert: vert.index)
    for index in range(0, len(selected_verts), 2):
        vert_a = selected_verts[index]
        vert_b = selected_verts[index + 1]
        segment = vert_b.co - vert_a.co
        length = segment.length
        segment_count = 1
        if max_segment_length > 0.0 and length > max_segment_length:
            segment_count = max(1, int(length / max_segment_length + 0.999999))

        chain = [vert_a]
        for segment_index in range(1, segment_count):
            t = segment_index / segment_count
            new_vert = bm.verts.new(vert_a.co.lerp(vert_b.co, t))
            chain.append(new_vert)
            created_vertex_count += 1
        chain.append(vert_b)
        bm.verts.ensure_lookup_table()

        for chain_index in range(len(chain) - 1):
            edge_a = chain[chain_index]
            edge_b = chain[chain_index + 1]
            key = frozenset((edge_a, edge_b))
            if key in existing_edges:
                continue
            try:
                new_edge = bm.edges.new((edge_a, edge_b))
                new_edge.select = True
                created_edges.append(new_edge)
                existing_edges.add(key)
                created_count += 1
            except ValueError:
                pass

    for vert in bm.verts:
        vert.select = False
    for edge in bm.edges:
        edge.select = edge in created_edges
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    clear_stored_platform_rings_data(obj)
    return created_count, created_vertex_count


def isolate_selected_edges_to_object(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    selected_edges = [edge for edge in bm.edges if edge.select]
    if not selected_edges:
        return 0, 0

    selected_vert_indices = []
    for edge in selected_edges:
        for vert in edge.verts:
            if vert.index not in selected_vert_indices:
                selected_vert_indices.append(vert.index)

    index_map = {old_index: new_index for new_index, old_index in enumerate(selected_vert_indices)}
    verts = [tuple(bm.verts[old_index].co) for old_index in selected_vert_indices]
    edges = [
        (index_map[edge.verts[0].index], index_map[edge.verts[1].index])
        for edge in selected_edges
    ]

    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.clear_geometry()
    obj.data.from_pydata(verts, edges, [])
    obj.data.update(calc_edges=True)
    for edge in obj.data.edges:
        edge.select = True
    for vertex in obj.data.vertices:
        vertex.select = True
    obj.data.update()
    clear_stored_platform_rings_data(obj)
    return len(verts), len(edges)


def store_platform_ring_data(obj, top_coords, lower_z=None):
    store_platform_rings_data(obj, [top_coords], lower_z)


def store_platform_rings_data(obj, rings, lower_z=None):
    clean_rings = [
        [(float(coord[0]), float(coord[1]), float(coord[2])) for coord in coords]
        for coords in rings
        if len(coords) >= 3
    ]
    obj["mv_platform_top_rings_json"] = json.dumps(clean_rings, separators=(",", ":"))
    if clean_rings:
        obj["mv_platform_top_ring"] = [value for coord in clean_rings[0] for value in coord]
    elif "mv_platform_top_ring" in obj:
        del obj["mv_platform_top_ring"]

    if lower_z is not None:
        obj["mv_platform_lower_z"] = float(lower_z)
    elif "mv_platform_lower_z" in obj:
        del obj["mv_platform_lower_z"]


def store_platform_lower_height(obj, lower_z):
    obj["mv_platform_lower_z"] = float(lower_z)


def get_stored_platform_lower_height(obj):
    lower_z = obj.get("mv_platform_lower_z", None)
    if lower_z is None:
        return None
    return float(lower_z)


def get_stored_platform_ring_data(obj):
    rings, lower_z = get_stored_platform_rings_data(obj)
    if not rings:
        return [], lower_z
    return rings[0], lower_z


def get_stored_platform_rings_data(obj):
    raw = obj.get("mv_platform_top_rings_json", "")
    flat = obj.get("mv_platform_top_ring", [])
    lower_z = get_stored_platform_lower_height(obj)
    if raw:
        try:
            values = json.loads(raw)
        except Exception:
            values = []
        rings = []
        for ring in values:
            if len(ring) < 3:
                continue
            coords = []
            for coord in ring:
                if len(coord) != 3:
                    coords = []
                    break
                coords.append((float(coord[0]), float(coord[1]), float(coord[2])))
            if len(coords) >= 3:
                rings.append(coords)
        if rings:
            return rings, lower_z

    if len(flat) < 9 or len(flat) % 3 != 0:
        return [], lower_z
    coords = [
        (float(flat[index]), float(flat[index + 1]), float(flat[index + 2]))
        for index in range(0, len(flat), 3)
    ]
    return [coords], lower_z


def polygon_area_from_coords_xy(coords):
    area = 0.0
    for index, coord in enumerate(coords):
        next_coord = coords[(index + 1) % len(coords)]
        area += (coord[0] * next_coord[1]) - (next_coord[0] * coord[1])
    return area * 0.5


def offset_ring_coords(coords, thickness, offset):
    if len(coords) < 3:
        return [], []

    clockwise = polygon_area_from_coords_xy(coords) < 0.0
    inward_distance = max(0.0, thickness * (1.0 - offset) * 0.5)
    outward_distance = max(0.0, thickness * (1.0 + offset) * 0.5)
    inner = []
    outer = []

    for index, coord in enumerate(coords):
        prev_coord = coords[(index - 1) % len(coords)]
        next_coord = coords[(index + 1) % len(coords)]
        prev_edge = Vector((coord[0] - prev_coord[0], coord[1] - prev_coord[1]))
        next_edge = Vector((next_coord[0] - coord[0], next_coord[1] - coord[1]))
        if prev_edge.length <= 1e-9 or next_edge.length <= 1e-9:
            direction = Vector((1.0, 0.0))
            scale = 1.0
        else:
            prev_edge.normalize()
            next_edge.normalize()
            if clockwise:
                prev_normal = Vector((-prev_edge.y, prev_edge.x))
                next_normal = Vector((-next_edge.y, next_edge.x))
            else:
                prev_normal = Vector((prev_edge.y, -prev_edge.x))
                next_normal = Vector((next_edge.y, -next_edge.x))
            direction = prev_normal + next_normal
            if direction.length <= 1e-9:
                direction = next_normal.copy()
            else:
                direction.normalize()
            scale = min(2.0, 1.0 / max(0.5, direction.dot(next_normal)))

        inner.append((coord[0] - direction.x * inward_distance * scale, coord[1] - direction.y * inward_distance * scale, coord[2]))
        outer.append((coord[0] + direction.x * outward_distance * scale, coord[1] + direction.y * outward_distance * scale, coord[2]))

    return inner, outer


def build_2d_thick_ring_mesh(obj, source_coords, thickness, offset):
    inner, outer = offset_ring_coords(source_coords, thickness, offset)
    if len(inner) < 3 or len(outer) < 3:
        return 0

    verts = inner + outer
    count = len(inner)
    faces = []
    for index in range(count):
        faces.append((
            index,
            (index + 1) % count,
            count + ((index + 1) % count),
            count + index,
        ))

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    return count


def build_2d_cutter_mesh(obj, source_coords, inner_distance, outer_distance):
    return build_2d_cutter_mesh_from_rings(obj, [source_coords], inner_distance, outer_distance)


def append_2d_cutter_ring_geometry(verts, faces, editable_faces, source_coords, inner_distance, outer_distance):
    inner, outer = offset_ring_coords(source_coords, inner_distance + outer_distance, 0.0)
    if len(inner) < 3 or len(outer) < 3:
        return 0

    if inner_distance != outer_distance:
        inner, _ = offset_ring_coords(source_coords, inner_distance * 2.0, 0.0)
        _, outer = offset_ring_coords(source_coords, outer_distance * 2.0, 0.0)

    original = [tuple(coord) for coord in source_coords]
    start_index = len(verts)
    verts.extend(inner + original + outer)
    count = len(original)

    for index in range(count):
        next_index = (index + 1) % count
        editable_faces.append(len(faces))
        faces.append((
            start_index + index,
            start_index + next_index,
            start_index + count + next_index,
            start_index + count + index,
        ))
        faces.append((
            start_index + count + index,
            start_index + count + next_index,
            start_index + (count * 2) + next_index,
            start_index + (count * 2) + index,
        ))
    return count


def build_2d_cutter_mesh_from_rings(obj, rings, inner_distance, outer_distance):
    verts = []
    faces = []
    editable_faces = []
    total_count = 0
    for source_coords in rings:
        total_count += append_2d_cutter_ring_geometry(
            verts,
            faces,
            editable_faces,
            source_coords,
            inner_distance,
            outer_distance,
        )

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    tag_platform_fill_faces(obj, editable_faces, preserve_existing=False)
    return total_count


def get_boundary_edge_loops_from_mesh(mesh):
    edge_use = {}
    for poly in mesh.polygons:
        vertices = list(poly.vertices)
        for edge in zip(vertices, vertices[1:] + vertices[:1]):
            key = tuple(sorted(edge))
            edge_use[key] = edge_use.get(key, 0) + 1

    boundary_edges = [edge for edge, count in edge_use.items() if count == 1]
    adjacency = {}
    for a, b in boundary_edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    loops = []
    visited_edges = set()
    for start_a, start_b in boundary_edges:
        start_key = tuple(sorted((start_a, start_b)))
        if start_key in visited_edges:
            continue

        loop = [start_a]
        previous = start_a
        current = start_b
        visited_edges.add(start_key)

        for _ in range(len(boundary_edges) + 2):
            loop.append(current)
            if current == loop[0]:
                break
            next_vert = None
            for candidate in adjacency.get(current, []):
                key = tuple(sorted((current, candidate)))
                if candidate != previous and key not in visited_edges:
                    next_vert = candidate
                    break
            if next_vert is None:
                for candidate in adjacency.get(current, []):
                    key = tuple(sorted((current, candidate)))
                    if key not in visited_edges:
                        next_vert = candidate
                        break
            if next_vert is None:
                break
            previous, current = current, next_vert
            visited_edges.add(tuple(sorted((previous, current))))

        if len(loop) >= 4 and loop[0] == loop[-1]:
            loop.pop()
        if len(loop) >= 3:
            loops.append(loop)

    return loops


def close_2d_cutter_inner_loop(context, obj):
    return close_2d_cutter_selected_loops(context, obj)


def tag_platform_fill_faces(obj, fill_face_indices, preserve_existing=True):
    fill_set = set(fill_face_indices)
    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0) if preserve_existing else [0] * len(obj.data.polygons)
    for poly in obj.data.polygons:
        if poly.index in fill_set:
            values[poly.index] = 1
    ensure_face_int_attribute(obj.data, "mv_platform_fill_face", values)
    obj.data.update()


def update_platform_fill_tag_from_selected_faces(obj):
    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0)
    selected_count = 0
    for poly in obj.data.polygons:
        if poly.select:
            values[poly.index] = 1
            selected_count += 1
    ensure_face_int_attribute(obj.data, "mv_platform_fill_face", values)
    obj.data.update()
    return selected_count


def select_platform_fill_faces(context, obj, invert=False):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0)
    selected_count = 0
    for poly in obj.data.polygons:
        should_select = values[poly.index] == 1
        if invert:
            should_select = not should_select
        poly.select = should_select
        if should_select:
            selected_count += 1

    obj.data.update()
    set_active_object(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    return selected_count


def assign_sculpt_face_sets_for_platform_fill(obj):
    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0)
    face_set_values = [1 if value == 1 else 2 for value in values]
    ensure_face_int_attribute(obj.data, ".sculpt_face_set", face_set_values)
    obj.data.update()


def create_sculpt_face_set_from_current_selection(context):
    if context.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')

    for mode_name in ('SELECTION', 'EDIT_SELECTION'):
        try:
            bpy.ops.sculpt.face_sets_create(mode=mode_name)
            return True
        except Exception:
            pass
    return False


def enable_sculpt_face_set_automasking(context):
    sculpt_settings = getattr(context.tool_settings, "sculpt", None)
    if sculpt_settings is None:
        return False

    face_set_attrs = (
        "use_automasking_face_sets",
    )
    boundary_attrs = (
        "use_automasking_boundary_face_sets",
        "use_automasking_face_sets_boundary",
    )
    automasking_attrs = face_set_attrs + boundary_attrs
    enabled = False
    for attr_name in automasking_attrs:
        if hasattr(sculpt_settings, attr_name):
            setattr(sculpt_settings, attr_name, True)
            enabled = True
    brush = getattr(sculpt_settings, "brush", None)
    if brush is not None:
        for attr_name in automasking_attrs:
            if hasattr(brush, attr_name):
                setattr(brush, attr_name, True)
                enabled = True
    return enabled


SCULPT_BRUSH_TOOL_IDS = {
    'SMOOTH': ("builtin_brush.Smooth", "builtin_brush.smooth"),
    'GRAB': ("builtin_brush.Grab", "builtin_brush.grab"),
    'FLATTEN_CONTRAST': (
        "builtin_brush.Flatten",
        "builtin_brush.flatten",
        "builtin_brush.FlattenContrast",
        "builtin_brush.flatten_contrast",
    ),
    'RELAX_PINCH': (
        "builtin_brush.Relax",
        "builtin_brush.relax",
        "builtin_brush.RelaxPinch",
        "builtin_brush.relax_pinch",
        "builtin_brush.Relax_Pinch",
    ),
}

SCULPT_BRUSH_LABELS = {
    'SMOOTH': "Smooth",
    'GRAB': "Grab",
    'FLATTEN_CONTRAST': "Flatten/Contrast",
    'RELAX_PINCH': "Relax Pinch",
}


def select_sculpt_brush_tool(context, brush_type):
    if context.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')

    enable_sculpt_face_set_automasking(context)
    for tool_name in SCULPT_BRUSH_TOOL_IDS.get(brush_type, ()):
        try:
            bpy.ops.wm.tool_set_by_id(name=tool_name)
            enable_sculpt_face_set_automasking(context)
            return True
        except Exception:
            pass
    return False


def close_2d_cutter_selected_loops(context, obj):
    mesh = obj.data
    loops = get_boundary_edge_loops_from_mesh(mesh)
    if not loops:
        return 0, 0

    face_count_before = len(mesh.polygons)
    editable_values_before_fill = get_face_int_attribute_values(mesh, "mv_platform_fill_face", 0)

    set_active_object(context, obj)
    if context.mode != 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='EDGE')

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    selected_edge_keys = {
        tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        for edge in bm.edges
        if edge.select
    }
    if not selected_edge_keys:
        bpy.ops.object.mode_set(mode='OBJECT')
        return 0, 0

    selected_loops = []
    for loop in loops:
        loop_edge_keys = {
            tuple(sorted((loop[index], loop[(index + 1) % len(loop)])))
            for index in range(len(loop))
        }
        if loop_edge_keys and loop_edge_keys.issubset(selected_edge_keys):
            selected_loops.append(loop)

    if not selected_loops:
        selected_vertex_indices = {vert.index for vert in bm.verts if vert.select}
        for loop in loops:
            if all(index in selected_vertex_indices for index in loop):
                selected_loops.append(loop)

    if not selected_loops:
        bpy.ops.object.mode_set(mode='OBJECT')
        return 0, 0

    selected_loop_set = set(index for loop in selected_loops for index in loop)
    for vert in bm.verts:
        vert.select = vert.index in selected_loop_set
    for edge in bm.edges:
        edge.select = edge.verts[0].index in selected_loop_set and edge.verts[1].index in selected_loop_set
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

    try:
        bpy.ops.mesh.fill(use_beauty=True)
    except TypeError:
        bpy.ops.mesh.fill()

    bpy.ops.object.mode_set(mode='OBJECT')
    new_face_indices = [
        poly.index for poly in mesh.polygons
        if poly.index >= face_count_before
    ]
    editable_values = editable_values_before_fill + [0] * max(0, len(mesh.polygons) - len(editable_values_before_fill))
    for face_index in new_face_indices:
        editable_values[face_index] = 1
    ensure_face_int_attribute(mesh, "mv_platform_fill_face", editable_values)
    mesh.update()
    select_platform_fill_faces(context, obj)

    closed_vertex_count = sum(len(loop) for loop in selected_loops)
    return closed_vertex_count, max(0, len(mesh.polygons) - face_count_before)


def extrude_mesh_down_from_faces(obj, depth):
    source_verts = [vertex.co.copy() for vertex in obj.data.vertices]
    source_faces = [list(poly.vertices) for poly in obj.data.polygons]
    if not source_verts or not source_faces or depth <= 0.0:
        return 0

    bottom_verts = [Vector((vert.x, vert.y, vert.z - depth)) for vert in source_verts]
    verts = [tuple(vert) for vert in source_verts + bottom_verts]
    vert_count = len(source_verts)
    faces = []

    for face in source_faces:
        faces.append(tuple(face))
        faces.append(tuple(reversed([index + vert_count for index in face])))

    edge_use = {}
    for face in source_faces:
        for edge in zip(face, face[1:] + face[:1]):
            key = tuple(sorted(edge))
            edge_use[key] = edge_use.get(key, 0) + 1

    for a, b in edge_use:
        if edge_use[(a, b)] == 1:
            faces.append((a, b, b + vert_count, a + vert_count))

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    return len(faces)


def apply_smooth_remesh_modifier(context, obj, octree_depth=8, scale=0.9, remove_disconnected=False):
    if obj is None or obj.type != 'MESH':
        return False

    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    set_active_object(context, obj)
    modifier = obj.modifiers.new(name="CleanupSmoothRemesh", type='REMESH')
    modifier.mode = 'SMOOTH'
    modifier.octree_depth = octree_depth
    if hasattr(modifier, "scale"):
        modifier.scale = scale
    if hasattr(modifier, "use_remove_disconnected"):
        modifier.use_remove_disconnected = remove_disconnected
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    return True


def expand_mesh_xy_from_center(obj, distance):
    if obj is None or obj.type != 'MESH' or distance <= 0.0 or not obj.data.vertices:
        return 0

    center = Vector((0.0, 0.0))
    for vertex in obj.data.vertices:
        center.x += vertex.co.x
        center.y += vertex.co.y
    center /= len(obj.data.vertices)

    moved_count = 0
    for vertex in obj.data.vertices:
        direction = Vector((vertex.co.x - center.x, vertex.co.y - center.y))
        if direction.length <= 1e-9:
            continue
        direction.normalize()
        vertex.co.x += direction.x * distance
        vertex.co.y += direction.y * distance
        moved_count += 1

    obj.data.update()
    return moved_count


def extrude_cleaned_2d_mesh_to_3d(obj, top_offset, lower_z, bottom_down=0.0):
    source_verts = [vertex.co.copy() for vertex in obj.data.vertices]
    source_faces = [list(poly.vertices) for poly in obj.data.polygons]
    if not source_verts or not source_faces:
        return 0

    bottom_z = lower_z - max(0.0, bottom_down)
    top_verts = [Vector((vert.x, vert.y, vert.z + top_offset)) for vert in source_verts]
    bottom_verts = [Vector((vert.x, vert.y, bottom_z)) for vert in source_verts]
    verts = [tuple(vert) for vert in top_verts + bottom_verts]
    vert_count = len(source_verts)
    faces = []

    for face in source_faces:
        faces.append(tuple(face))
        faces.append(tuple(reversed([index + vert_count for index in face])))

    edge_use = {}
    for face in source_faces:
        for edge in zip(face, face[1:] + face[:1]):
            key = tuple(sorted(edge))
            edge_use[key] = edge_use.get(key, 0) + 1

    for a, b in edge_use:
        if edge_use[(a, b)] == 1:
            faces.append((a, b, b + vert_count, a + vert_count))

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    return len(faces)


def enter_edit_vertex_wireframe(context, obj):
    set_active_object(context, obj)
    if context.mode != 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bpy.ops.mesh.select_all(action='DESELECT')


def get_enabled_color_skin_slots(settings):
    enabled_slots = []
    for slot_index in range(4):
        if getattr(settings, f"color_skin_slot_{slot_index + 1}"):
            enabled_slots.append(slot_index)
    return enabled_slots


def get_used_material_slots(obj):
    return sorted({poly.material_index for poly in obj.data.polygons})


def select_faces_by_material_slot(obj, slot_indices):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    selected_count = 0

    slot_index_set = set(slot_indices)
    for face in bm.faces:
        match = face.material_index in slot_index_set
        face.select = match
        if match:
            selected_count += 1

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected_count


def delete_non_material_faces(context, obj, keep_slot_index):
    set_active_object(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select = face.material_index != keep_slot_index

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')


def split_object_by_loose_parts(context, obj):
    set_active_object(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    pre_names = set(item.name for item in bpy.data.objects)
    bpy.ops.mesh.separate(type='LOOSE')
    bpy.ops.object.mode_set(mode='OBJECT')

    new_objects = find_new_objects(pre_names)
    if obj.name in bpy.data.objects:
        new_objects.append(obj)

    unique_objects = []
    seen = set()
    for item in new_objects:
        if item.name not in seen:
            unique_objects.append(item)
            seen.add(item.name)

    return unique_objects


def join_objects(context, objects, new_name):
    valid_objects = [obj for obj in objects if obj is not None and obj.name in bpy.data.objects]
    if not valid_objects:
        return None

    if len(valid_objects) == 1:
        valid_objects[0].name = new_name
        return valid_objects[0]

    bpy.ops.object.select_all(action='DESELECT')
    for obj in valid_objects:
        obj.select_set(True)
    context.view_layer.objects.active = valid_objects[0]
    bpy.ops.object.join()

    joined_obj = context.view_layer.objects.active
    if joined_obj is not None:
        joined_obj.name = new_name
    return joined_obj


def boolean_union_objects(context, objects, new_name, solver='EXACT'):
    valid_objects = []
    for obj in objects:
        if obj is None:
            continue
        try:
            obj_name = obj.name
        except ReferenceError:
            continue
        if obj_name in bpy.data.objects:
            valid_objects.append(obj)

    if not valid_objects:
        return None

    if len(valid_objects) == 1:
        valid_objects[0].name = new_name
        return valid_objects[0]

    target_obj = valid_objects[0]
    target_obj.name = new_name

    for index, cutter_obj in enumerate(valid_objects[1:]):
        set_active_object(context, target_obj)
        mod = target_obj.modifiers.new(name=f"Union_{index}", type='BOOLEAN')
        mod.operation = 'UNION'
        mod.object = cutter_obj
        if hasattr(mod, "solver"):
            mod.solver = solver
        bpy.ops.object.modifier_apply(modifier=mod.name)
        remove_object_if_exists(cutter_obj)

    return target_obj


def generate_north_south_skin_from_mask(context, mask_obj, settings, root_name, slot_index, source_name):
    axis_skin_objects = []
    temp_source = duplicate_object(context, mask_obj, f"{mask_obj.name}_TEMP")
    set_metadata(temp_source, root_name, source_name)

    fixed_axes = [
        ("+X", "X", Vector((1.0, 0.0, 0.0))),
        ("-X", "-X", Vector((-1.0, 0.0, 0.0))),
        ("+Y", "Y", Vector((0.0, 1.0, 0.0))),
        ("-Y", "-Y", Vector((0.0, -1.0, 0.0))),
        ("+Z", "Z", Vector((0.0, 0.0, 1.0))),
    ]

    for label, suffix, axis_vec in fixed_axes:
        if temp_source.name not in bpy.data.objects:
            break

        set_active_object(context, temp_source)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')

        bm = bmesh.from_edit_mesh(temp_source.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()

        selected_count = 0
        for face in bm.faces:
            match = face_matches_world_axis(temp_source, face, axis_vec)
            face.select = match
            if match:
                selected_count += 1

        bmesh.update_edit_mesh(temp_source.data, loop_triangles=False, destructive=False)

        if selected_count == 0:
            bpy.ops.object.mode_set(mode='OBJECT')
            continue

        pre_names = set(obj.name for obj in bpy.data.objects)
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')

        new_obj = find_new_object(pre_names, temp_source, context)
        if new_obj is None:
            continue

        new_obj.name = f"{get_color_skin_name(root_name, slot_index)}_{suffix}"
        set_metadata(new_obj, root_name, source_name)
        process_skin_object(context, new_obj, settings, axis_vec)
        axis_skin_objects.append(new_obj)

    if temp_source.name in bpy.data.objects:
        bpy.data.objects.remove(temp_source, do_unlink=True)

    joined_obj = boolean_union_objects(context, axis_skin_objects, get_color_skin_name(root_name, slot_index))
    return joined_obj


def process_skin_object(context, obj, settings, axis_vec):
    set_active_object(context, obj)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    # original separated faces = face-original
    base_faces = [f for f in bm.faces if f.select and f.is_valid]

    # 1) Outer Skin: extrude original faces outward
    outer_skin_distance = mm_to_scene_units(context, settings.outer_skin_mm)
    if outer_skin_distance > 0.0 and base_faces:
        ret = bmesh.ops.extrude_face_region(bm, geom=base_faces)

        new_verts = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMVert)]
        if new_verts:
            bmesh.ops.translate(
                bm,
                verts=new_verts,
                vec=(
                    outer_skin_distance * axis_vec.x,
                    outer_skin_distance * axis_vec.y,
                    outer_skin_distance * axis_vec.z,
                )
            )

    # 2) Reselect the ORIGINAL base faces
    for f in bm.faces:
        f.select = False

    for f in base_faces:
        if f.is_valid:
            f.select = True

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    # 3) Extrude original base faces in place
    bpy.ops.mesh.extrude_region_move(
        MESH_OT_extrude_region={
            "use_normal_flip": False,
            "mirror": False,
        },
        TRANSFORM_OT_translate={
            "value": (0.0, 0.0, 0.0),
        }
    )

    # 4) Inset
    bpy.ops.mesh.inset(
        thickness=settings.inset_amount,
        depth=0.0,
        use_boundary=True,
        use_even_offset=True,
        use_relative_offset=False,
        use_outset=False,
    )

    # 5) Move inward
    inside_skin_distance = abs(mm_to_scene_units(context, settings.inside_skin_mm))
    move_vec = (
        -inside_skin_distance * axis_vec.x,
        -inside_skin_distance * axis_vec.y,
        -inside_skin_distance * axis_vec.z,
    )

    bpy.ops.transform.translate(
        value=move_vec,
        orient_type='GLOBAL',
        constraint_axis=(
            axis_vec.x != 0.0,
            axis_vec.y != 0.0,
            axis_vec.z != 0.0,
        ),
    )

    bpy.ops.object.mode_set(mode='OBJECT')


def process_color_skin_object(context, obj, settings):
    set_active_object(context, obj)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    base_faces = [face for face in bm.faces if face.select and face.is_valid]

    outer_skin_distance = mm_to_scene_units(context, settings.outer_skin_mm)
    if outer_skin_distance > 0.0 and base_faces:
        ret = bmesh.ops.extrude_face_region(bm, geom=base_faces)
        new_verts = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMVert)]
        for vert in new_verts:
            normal = vert.normal.normalized()
            vert.co += normal * outer_skin_distance

    for face in bm.faces:
        face.select = False

    for face in base_faces:
        if face.is_valid:
            face.select = True

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    bpy.ops.mesh.extrude_region_move(
        MESH_OT_extrude_region={
            "use_normal_flip": False,
            "mirror": False,
        },
        TRANSFORM_OT_translate={
            "value": (0.0, 0.0, 0.0),
        }
    )

    bpy.ops.mesh.inset(
        thickness=settings.inset_amount,
        depth=0.0,
        use_boundary=True,
        use_even_offset=True,
        use_relative_offset=False,
        use_outset=False,
    )

    inside_skin_distance = -abs(mm_to_scene_units(context, settings.inside_skin_mm))
    bpy.ops.transform.shrink_fatten(
        value=inside_skin_distance,
        use_even_offset=True,
    )

    bpy.ops.object.mode_set(mode='OBJECT')


def build_inward_boolean_cutter_mesh(context, source_obj, target_obj, settings, epsilon=None):
    source_bm = bmesh.new()
    source_bm.from_mesh(source_obj.data)
    source_bm.verts.ensure_lookup_table()
    source_bm.faces.ensure_lookup_table()
    source_bm.normal_update()

    if not source_bm.faces:
        source_bm.free()
        return 0

    depth = abs(mm_to_scene_units(context, settings.inside_skin_mm))
    if epsilon is None:
        epsilon = max(depth * 0.05, meters_to_scene_units(context, 0.00002))

    verts = []
    faces = []
    material_indices = []

    for face in source_bm.faces:
        if not face.is_valid or len(face.verts) < 3:
            continue
        normal = face.normal.normalized()
        if normal.length <= 1e-12:
            continue

        outer_indices = []
        inner_indices = []
        for vert in face.verts:
            outer_indices.append(len(verts))
            verts.append(tuple(vert.co + (normal * epsilon)))
        for vert in face.verts:
            inner_indices.append(len(verts))
            verts.append(tuple(vert.co - (normal * (depth + epsilon))))

        faces.append(tuple(outer_indices))
        material_indices.append(face.material_index)
        faces.append(tuple(reversed(inner_indices)))
        material_indices.append(face.material_index)

        count = len(outer_indices)
        for index in range(count):
            next_index = (index + 1) % count
            faces.append((
                outer_indices[index],
                outer_indices[next_index],
                inner_indices[next_index],
                inner_indices[index],
            ))
            material_indices.append(face.material_index)

    target_obj.data.clear_geometry()
    target_obj.data.from_pydata(verts, [], faces)
    target_obj.data.update(calc_edges=True)
    for polygon, material_index in zip(target_obj.data.polygons, material_indices):
        polygon.material_index = material_index

    source_bm.free()
    cleanup_boolean_mesh(context, target_obj, triangulate=True)
    return len(faces)


def apply_boolean_difference(context, target_obj, cutter_obj, index, solver='EXACT'):
    set_active_object(context, target_obj)

    mod = target_obj.modifiers.new(name=f"Boolean_{index}", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter_obj

    if hasattr(mod, "solver"):
        mod.solver = solver

    bpy.ops.object.modifier_apply(modifier=mod.name)


def ensure_bake_material(target_obj, image):
    mat_name = f"{target_obj.name}_Baked"
    mat = bpy.data.materials.get(mat_name)

    if mat is None:
        mat = bpy.data.materials.new(mat_name)

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (400, 0)

    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_bsdf.location = (120, 0)

    node_tex = nodes.new("ShaderNodeTexImage")
    node_tex.location = (-200, 0)
    node_tex.image = image

    links.new(node_tex.outputs["Color"], node_bsdf.inputs["Base Color"])
    links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Surface"])

    nodes.active = node_tex

    if target_obj.data.materials:
        target_obj.data.materials[0] = mat
    else:
        target_obj.data.materials.append(mat)

    return mat, node_tex


def ensure_bake_image(target_obj, size):
    img_name = f"{target_obj.name}_Color"
    img = bpy.data.images.get(img_name)

    if img is None:
        img = bpy.data.images.new(
            name=img_name,
            width=size,
            height=size,
            alpha=False,
        )
    else:
        if img.size[0] != size or img.size[1] != size:
            img.scale(size, size)

    img.generated_color = (0.0, 0.0, 0.0, 1.0)
    return img


def get_object_color_image(obj):
    materials = []

    if obj.active_material is not None:
        materials.append(obj.active_material)

    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat not in materials:
            materials.append(mat)

    fallback_image = None

    for mat in materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue

        nodes = mat.node_tree.nodes
        active_node = nodes.active
        if active_node and active_node.type == 'TEX_IMAGE' and active_node.image is not None:
            return active_node.image

        for node in nodes:
            if node.type == 'BSDF_PRINCIPLED':
                for link in node.inputs["Base Color"].links:
                    from_node = link.from_node
                    if from_node.type == 'TEX_IMAGE' and from_node.image is not None:
                        return from_node.image

        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image is not None and fallback_image is None:
                fallback_image = node.image

    return fallback_image


def clamp01(value):
    return max(0.0, min(1.0, value))


def sample_image_color(image, pixels, width, height, u, v):
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 0.0)

    u = u % 1.0
    v = v % 1.0

    x = min(width - 1, max(0, int(round(u * (width - 1)))))
    y = min(height - 1, max(0, int(round(v * (height - 1)))))
    index = ((y * width) + x) * 4

    return (
        float(pixels[index]),
        float(pixels[index + 1]),
        float(pixels[index + 2]),
    )


def average_color(colors):
    if not colors:
        return (0.0, 0.0, 0.0)

    count = float(len(colors))
    return (
        sum(color[0] for color in colors) / count,
        sum(color[1] for color in colors) / count,
        sum(color[2] for color in colors) / count,
    )


def median_color(colors):
    if not colors:
        return (0.0, 0.0, 0.0)

    sorted_r = sorted(color[0] for color in colors)
    sorted_g = sorted(color[1] for color in colors)
    sorted_b = sorted(color[2] for color in colors)
    mid = len(colors) // 2

    if len(colors) % 2 == 1:
        return (sorted_r[mid], sorted_g[mid], sorted_b[mid])

    return (
        (sorted_r[mid - 1] + sorted_r[mid]) * 0.5,
        (sorted_g[mid - 1] + sorted_g[mid]) * 0.5,
        (sorted_b[mid - 1] + sorted_b[mid]) * 0.5,
    )


def color_distance_sq(color_a, color_b):
    return (
        (color_a[0] - color_b[0]) ** 2 +
        (color_a[1] - color_b[1]) ** 2 +
        (color_a[2] - color_b[2]) ** 2
    )


def color_luminance(color):
    return (
        0.2126 * color[0] +
        0.7152 * color[1] +
        0.0722 * color[2]
    )


def get_polygon_texture_color(poly, uv_data, image, pixels, sample_mode):
    face_uvs = [uv_data[loop_index].uv.copy() for loop_index in poly.loop_indices]
    if not face_uvs:
        return (0.0, 0.0, 0.0)

    width = image.size[0]
    height = image.size[1]
    samples = [sample_image_color(image, pixels, width, height, uv.x, uv.y) for uv in face_uvs]

    if sample_mode == 'CENTER':
        center = Vector((0.0, 0.0))
        for uv in face_uvs:
            center += uv
        center /= len(face_uvs)
        return sample_image_color(image, pixels, width, height, center.x, center.y)

    if sample_mode == 'MEDIAN':
        return median_color(samples)

    return average_color(samples)


def build_adaptive_palette(face_colors, color_count):
    if not face_colors:
        return [(0.8, 0.8, 0.8)], [0]

    palette = [face_colors[0]]

    while len(palette) < min(color_count, len(face_colors)):
        farthest_color = max(
            face_colors,
            key=lambda color: min(color_distance_sq(color, seed) for seed in palette)
        )
        palette.append(farthest_color)

    for _ in range(8):
        clusters = [[] for _ in palette]
        for color in face_colors:
            index = min(range(len(palette)), key=lambda i: color_distance_sq(color, palette[i]))
            clusters[index].append(color)

        for i, cluster in enumerate(clusters):
            if cluster:
                palette[i] = average_color(cluster)

    assignments = [
        min(range(len(palette)), key=lambda i: color_distance_sq(color, palette[i]))
        for color in face_colors
    ]

    return palette, assignments


def build_luminance_palette(face_colors, color_count):
    if not face_colors:
        return [(0.8, 0.8, 0.8)], [0]

    sorted_indices = sorted(range(len(face_colors)), key=lambda i: color_luminance(face_colors[i]))
    group_count = min(color_count, len(face_colors))
    groups = [[] for _ in range(group_count)]
    assignments = [0] * len(face_colors)

    for rank, face_index in enumerate(sorted_indices):
        group_index = min(group_count - 1, (rank * group_count) // len(face_colors))
        groups[group_index].append(face_colors[face_index])
        assignments[face_index] = group_index

    palette = [average_color(group) if group else (0.8, 0.8, 0.8) for group in groups]
    return palette, assignments


def assign_distinct_fixed_palette_indices(palette):
    if not palette:
        return []

    available_indices = list(range(len(FIXED_LEGO_PALETTE)))
    fixed_colors = [color for _, color in FIXED_LEGO_PALETTE]
    assignments = [0] * len(palette)

    # Assign the hardest-to-match colors first so close colors don't consume the same fixed slot.
    palette_order = sorted(
        range(len(palette)),
        key=lambda palette_index: min(
            color_distance_sq(palette[palette_index], fixed_color)
            for fixed_color in fixed_colors
        ),
        reverse=True,
    )

    for palette_index in palette_order:
        fixed_index = min(
            available_indices,
            key=lambda index: color_distance_sq(palette[palette_index], fixed_colors[index]),
        )
        assignments[palette_index] = fixed_index
        available_indices.remove(fixed_index)

    return assignments


def sync_slot_palette_properties(settings, palette):
    distinct_indices = assign_distinct_fixed_palette_indices(palette)
    for slot_index in range(4):
        if slot_index < len(palette):
            fixed_index = distinct_indices[slot_index]
        else:
            fixed_index = int(getattr(settings, f"lego_palette_slot_{slot_index + 1}"))
        setattr(settings, f"lego_palette_slot_{slot_index + 1}", str(fixed_index))
        setattr(settings, f"lego_palette_slot_color_{slot_index + 1}", FIXED_LEGO_PALETTE[fixed_index][1])


def get_slot_palette_color(settings, slot_index):
    fixed_index = getattr(settings, f"lego_palette_slot_{slot_index + 1}")
    return get_fixed_palette_color(fixed_index)


def rebuild_materials_from_assignments(obj, settings, assignments, color_count):
    mesh = obj.data
    mesh.materials.clear()

    for slot_index in range(color_count):
        mesh.materials.append(ensure_lego_color_material(obj, slot_index, get_slot_palette_color(settings, slot_index)))

    for poly, material_index in zip(mesh.polygons, assignments):
        poly.material_index = min(material_index, color_count - 1)

    mesh.update()


def ensure_slot_palette_materials(obj, settings):
    mesh = obj.data
    for slot_index in range(settings.lego_color_count):
        material = ensure_lego_color_material(obj, slot_index, get_slot_palette_color(settings, slot_index))
        if slot_index < len(mesh.materials):
            mesh.materials[slot_index] = material
        else:
            mesh.materials.append(material)

    for poly in mesh.polygons:
        poly.material_index = min(poly.material_index, settings.lego_color_count - 1)
    mesh.update()


def apply_platform_foot_color_slot(settings):
    foot_obj = get_platform_foot_object(settings)
    if foot_obj is None:
        return False

    slot_index = max(0, min(int(settings.platform_foot_color_slot), 3))
    blocks_obj = get_blocks_object(settings)
    material = None

    if blocks_obj is not None and not bool(blocks_obj.get("mv_debug_colors_active", False)):
        ensure_slot_palette_materials(blocks_obj, settings)
        if slot_index < len(blocks_obj.data.materials):
            material = blocks_obj.data.materials[slot_index]

    if material is None:
        material = ensure_lego_color_material(foot_obj, slot_index, get_slot_palette_color(settings, slot_index))

    apply_single_material_to_object(foot_obj, material)
    return True


def build_material_neighbor_map(mesh, include_corners=False):
    neighbor_map = {poly.index: set() for poly in mesh.polygons}
    edge_faces = {}

    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            edge_faces.setdefault(edge_key, []).append(poly.index)

    for poly_indices in edge_faces.values():
        if len(poly_indices) < 2:
            continue
        for poly_index in poly_indices:
            for other_index in poly_indices:
                if other_index != poly_index:
                    neighbor_map[poly_index].add(other_index)

    if include_corners:
        vertex_faces = {}
        for poly in mesh.polygons:
            for vertex_index in poly.vertices:
                vertex_faces.setdefault(vertex_index, []).append(poly.index)

        for poly_indices in vertex_faces.values():
            if len(poly_indices) < 2:
                continue
            for poly_index in poly_indices:
                for other_index in poly_indices:
                    if other_index != poly_index:
                        neighbor_map[poly_index].add(other_index)

    return neighbor_map


def get_dominant_neighbor_material(neighbor_indices, assignments, min_neighbors):
    counts = {}
    for neighbor_index in neighbor_indices:
        material_index = assignments[neighbor_index]
        counts[material_index] = counts.get(material_index, 0) + 1
    if not counts:
        return None, 0, counts

    best_material, best_count = max(counts.items(), key=lambda item: (item[1], -item[0]))
    if best_count < min_neighbors:
        return None, best_count, counts
    return best_material, best_count, counts


def smooth_material_assignments(mesh, mode, weight, passes, min_neighbors, max_island_faces, protect_slot, include_corners):
    neighbor_map = build_material_neighbor_map(mesh, include_corners)
    protected_slot = None if protect_slot == 'NONE' else int(protect_slot)
    total_changed = 0

    for _ in range(max(1, passes)):
        current_assignments = [poly.material_index for poly in mesh.polygons]
        new_assignments = list(current_assignments)

        if mode == 'ISLANDS':
            visited = set()
            for poly in mesh.polygons:
                if poly.index in visited:
                    continue
                current_index = current_assignments[poly.index]
                component = []
                queue = deque([poly.index])
                visited.add(poly.index)

                while queue:
                    poly_index = queue.popleft()
                    component.append(poly_index)
                    for neighbor_index in neighbor_map[poly_index]:
                        if neighbor_index in visited:
                            continue
                        if current_assignments[neighbor_index] != current_index:
                            continue
                        visited.add(neighbor_index)
                        queue.append(neighbor_index)

                if protected_slot is not None and current_index == protected_slot:
                    continue
                if len(component) > max_island_faces:
                    continue

                boundary = set()
                component_set = set(component)
                for poly_index in component:
                    boundary.update(
                        neighbor_index for neighbor_index in neighbor_map[poly_index]
                        if neighbor_index not in component_set
                    )
                best_material, _best_count, _counts = get_dominant_neighbor_material(boundary, current_assignments, min_neighbors)
                if best_material is None or best_material == current_index:
                    continue
                for poly_index in component:
                    new_assignments[poly_index] = best_material
        else:
            for poly in mesh.polygons:
                current_index = current_assignments[poly.index]
                if protected_slot is not None and current_index == protected_slot:
                    continue
                neighbors = list(neighbor_map[poly.index])
                if not neighbors:
                    continue

                best_material, best_count, counts = get_dominant_neighbor_material(neighbors, current_assignments, min_neighbors)
                if best_material is None or best_material == current_index:
                    continue

                current_count = counts.get(current_index, 0)
                if mode == 'SPECKLES':
                    if current_count > max(0, min_neighbors - 1):
                        continue
                    if best_count * weight <= current_count:
                        continue
                elif mode == 'MAJORITY':
                    if best_count * weight <= current_count * (1.0 - weight):
                        continue

                new_assignments[poly.index] = best_material

        pass_changed = 0
        for poly in mesh.polygons:
            if poly.material_index != new_assignments[poly.index]:
                poly.material_index = new_assignments[poly.index]
                pass_changed += 1

        total_changed += pass_changed

        if pass_changed == 0:
            break

    mesh.update()
    return total_changed


def ensure_lego_color_material(obj, slot_index, color):
    mat_name = f"{obj.name}_LegoColor_{slot_index + 1}"
    mat = bpy.data.materials.get(mat_name)

    if mat is None:
        mat = bpy.data.materials.new(mat_name)

    mat.diffuse_color = (
        clamp01(color[0]),
        clamp01(color[1]),
        clamp01(color[2]),
        1.0,
    )
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (300, 0)

    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_bsdf.location = (0, 0)
    node_bsdf.inputs["Base Color"].default_value = (
        clamp01(color[0]),
        clamp01(color[1]),
        clamp01(color[2]),
        1.0,
    )
    if "Roughness" in node_bsdf.inputs:
        node_bsdf.inputs["Roughness"].default_value = 0.85

    links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Surface"])
    return mat


# ------------------------------------------------------------
# Operator 1: Apply All Transforms
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_apply_all_transforms(Operator):
    bl_idname = "object.miniature_voxeler_apply_all_transforms"
    bl_label = "Apply All Transforms"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_source_objects(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_obj = get_platform_object(settings)
        if building_obj is None or platform_obj is None:
            self.report({'ERROR'}, "Pick both a Building and Platform mesh first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        context.scene.unit_settings.system = 'METRIC'
        context.scene.unit_settings.length_unit = 'MILLIMETERS'

        applied_names = []
        for obj in (building_obj, platform_obj):
            if apply_object_transform_to_mesh(obj):
                applied_names.append(obj.name)

        if len(applied_names) != 2:
            settings.source_validation_key = ""
            self.report({'ERROR'}, "Could not apply transforms to both source meshes.")
            return {'CANCELLED'}

        warnings = get_source_scale_warnings(context, building_obj, platform_obj)
        if warnings:
            settings.source_validation_key = ""
            show_info_popup(
                context,
                "Check Source Scale",
                warnings,
                icon='ERROR',
            )
            self.report({'WARNING'}, "Applied transforms, but source scale looks wrong. Check the warning popup.")
            return {'FINISHED'}

        settings.source_validation_key = get_source_validation_key(building_obj, platform_obj)
        self.report({'INFO'}, f"Applied all transforms to {building_obj.name} and {platform_obj.name}.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 2: Block Remesh
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_block_remesh(Operator):
    bl_idname = "object.miniature_voxeler_block_remesh"
    bl_label = "Voxelize"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_building_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        source_obj = ensure_building_body_object(context, settings)
        if source_obj is None:
            self.report({'ERROR'}, "Pick a Building mesh first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        source_obj.hide_set(False)
        root_name = get_root_name(source_obj.name)
        new_obj = duplicate_object(context, source_obj, get_blocks_name(root_name))
        set_metadata(new_obj, root_name, source_obj.name)

        origin, voxel_size, cells, voxel_stats = generate_voxel_cells_from_object(context, source_obj, settings)
        rebuild_voxel_mesh_from_cells(new_obj, origin, voxel_size, cells)
        new_obj["mv_surface_cell_count"] = int(voxel_stats.get("surface_cell_count", 0))
        new_obj["mv_cavity_fill_cell_count"] = int(voxel_stats.get("cavity_fill_count", 0))
        new_obj["mv_vertical_fill_cell_count"] = int(voxel_stats.get("vertical_fill_count", 0))
        source_obj.hide_set(True)
        if building_obj is not None:
            building_obj.hide_set(True)
        set_active_object(context, new_obj)
        voxel_size_mm = voxel_size * context.scene.unit_settings.scale_length * 1000.0
        self.report({'INFO'}, f"Created voxel object: {new_obj.name} from {source_obj.name} | {len(cells)} cubes | surface {voxel_stats.get('surface_cell_count', 0)} + cavity {voxel_stats.get('cavity_fill_count', 0)} + vertical {voxel_stats.get('vertical_fill_count', 0)} | {voxel_size_mm:.3f} mm")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 2: Smart UV Project
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_smart_uv_project(Operator):
    bl_idname = "object.miniature_voxeler_smart_uv_project"
    bl_label = "Smart UV Project"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        set_active_object(context, obj)

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project()
        bpy.ops.object.mode_set(mode='OBJECT')

        self.report({'INFO'}, f"Smart UV Project completed for {obj.name}")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 3: Transfer Texture
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_transfer_texture(Operator):
    bl_idname = "object.miniature_voxeler_transfer_texture"
    bl_label = "Transfer Texture"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context) and has_building_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        target_obj = get_blocks_object(settings)
        building_obj = get_building_object(settings)
        if target_obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if not target_obj.data.uv_layers:
            self.report({'ERROR'}, "Target object has no UVs. Run Smart UV Project first.")
            return {'CANCELLED'}

        source_obj = get_texture_source_object(settings)
        if source_obj is None and not settings.texture_source_name.strip():
            source_obj = ensure_building_body_object(context, settings)

        if source_obj is None:
            self.report({'ERROR'}, "No _body source found for texture transfer. Run Voxelize Building again, or enter a valid Source Override.")
            return {'CANCELLED'}

        if building_obj is not None and source_obj == building_obj:
            self.report({'ERROR'}, "Texture transfer must use the _body copy, not the original Building.")
            return {'CANCELLED'}

        if source_obj == target_obj:
            self.report({'ERROR'}, "Source object and target object are the same.")
            return {'CANCELLED'}

        if source_obj.type != 'MESH':
            self.report({'ERROR'}, "Source object is not a mesh.")
            return {'CANCELLED'}

        image = ensure_bake_image(target_obj, settings.texture_size)
        _, image_node = ensure_bake_material(target_obj, image)

        scene = context.scene
        old_engine = scene.render.engine
        source_was_hidden = source_obj.hide_get()
        target_was_hidden = target_obj.hide_get()
        building_was_hidden = building_obj.hide_get() if building_obj is not None else None

        try:
            scene.render.engine = 'CYCLES'
            source_obj.hide_set(False)
            target_obj.hide_set(False)
            if building_obj is not None and building_obj != source_obj:
                building_obj.hide_set(True)

            bake = scene.render.bake
            if hasattr(bake, "use_selected_to_active"):
                bake.use_selected_to_active = True
            if hasattr(bake, "margin"):
                bake.margin = settings.texture_margin
            if hasattr(bake, "use_pass_direct"):
                bake.use_pass_direct = False
            if hasattr(bake, "use_pass_indirect"):
                bake.use_pass_indirect = False
            if hasattr(bake, "use_pass_color"):
                bake.use_pass_color = True

            bpy.ops.object.select_all(action='DESELECT')
            source_obj.select_set(True)
            target_obj.select_set(True)
            context.view_layer.objects.active = target_obj

            if target_obj.active_material and target_obj.active_material.use_nodes:
                target_obj.active_material.node_tree.nodes.active = image_node

            bpy.ops.object.bake(type='DIFFUSE')

            try:
                image.pack()
            except Exception:
                pass

        except Exception as e:
            source_obj.hide_set(source_was_hidden)
            target_obj.hide_set(target_was_hidden)
            if building_obj is not None and building_was_hidden is not None:
                building_obj.hide_set(building_was_hidden or True)
            scene.render.engine = old_engine
            self.report({'ERROR'}, f"Texture transfer failed: {str(e)}")
            return {'CANCELLED'}

        scene.render.engine = old_engine
        source_obj.hide_set(source_was_hidden)
        target_obj.hide_set(target_was_hidden)
        if building_obj is not None and building_was_hidden is not None:
            building_obj.hide_set(building_was_hidden or True)
        set_active_object(context, target_obj)

        self.report({'INFO'}, f"Texture baked from {source_obj.name} to {target_obj.name}")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 4: Lego Color
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_lego_color(Operator):
    bl_idname = "object.miniature_voxeler_lego_color"
    bl_label = "Lego Color"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}
        mesh = obj.data

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if not mesh.polygons:
            self.report({'ERROR'}, "Object has no faces.")
            return {'CANCELLED'}

        if not mesh.uv_layers.active:
            self.report({'ERROR'}, "Object has no active UV map. Run Smart UV Project first.")
            return {'CANCELLED'}

        image = get_object_color_image(obj)
        if image is None:
            self.report({'ERROR'}, "No image texture was found on the object materials.")
            return {'CANCELLED'}

        if not image.has_data:
            try:
                image.reload()
            except Exception:
                pass

        width, height = image.size[0], image.size[1]
        if width <= 0 or height <= 0:
            self.report({'ERROR'}, "The texture image has no valid size.")
            return {'CANCELLED'}

        pixels = list(image.pixels[:])
        uv_data = mesh.uv_layers.active.data
        face_colors = [
            get_polygon_texture_color(poly, uv_data, image, pixels, settings.lego_color_sample_mode)
            for poly in mesh.polygons
        ]

        if settings.lego_color_assign_mode == 'LUMINANCE':
            palette, assignments = build_luminance_palette(face_colors, settings.lego_color_count)
        else:
            palette, assignments = build_adaptive_palette(face_colors, settings.lego_color_count)

        sync_slot_palette_properties(settings, palette)
        rebuild_materials_from_assignments(obj, settings, assignments, len(palette))
        sync_voxel_color_state_from_mesh(obj)
        settings.platform_foot_color_slot = '0'
        apply_platform_foot_color_slot(settings)

        self.report(
            {'INFO'},
            f"Lego Color created {len(palette)} fixed-palette material(s) from {image.name} using {settings.lego_color_assign_mode.lower()} assignment."
        )
        return {'FINISHED'}


class MINIATUREVOXELER_OT_delete_lego_color_slots(Operator):
    bl_idname = "object.miniature_voxeler_delete_lego_color_slots"
    bl_label = "Delete Color Slots"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Voxel Building first so the _Blocks object exists.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for poly in obj.data.polygons:
            poly.material_index = 0
        obj.data.materials.clear()
        obj.data.update()

        cells = deserialize_voxel_cells(obj)
        if cells:
            origin = get_stored_voxel_origin(obj)
            voxel_size = float(obj.get("mv_voxel_size", 0.0))
            cells = {coord: 0 for coord in cells}
            if origin is not None and voxel_size > 0.0:
                store_voxel_state(obj, origin, voxel_size, cells)

        for key in (
            "mv_voxel_face_slots_json",
            "mv_debug_colors_active",
            "mv_debug_color_backup_json",
        ):
            if key in obj:
                del obj[key]

        settings.selected_lego_palette_slot = 0
        self.report({'INFO'}, "Deleted color slots from _Blocks. Transfer Texture and Create Color Slots can be run again.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 5: Smooth Lego Color
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_smooth_lego_color(Operator):
    bl_idname = "object.miniature_voxeler_smooth_lego_color"
    bl_label = "Smooth Lego Color"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}
        mesh = obj.data

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if not mesh.polygons:
            self.report({'ERROR'}, "Object has no faces.")
            return {'CANCELLED'}

        if not mesh.materials:
            self.report({'ERROR'}, "Run Lego Color first so faces have material assignments.")
            return {'CANCELLED'}

        settings = context.scene.miniature_voxeler_settings
        changed_count = smooth_material_assignments(
            mesh,
            settings.lego_smooth_mode,
            settings.lego_smooth_weight,
            settings.lego_smooth_passes,
            settings.lego_smooth_min_neighbors,
            settings.lego_smooth_max_island_faces,
            settings.lego_smooth_protect_slot,
            settings.lego_smooth_include_corners,
        )
        sync_voxel_color_state_from_mesh(obj)
        mode_label = {
            'SPECKLES': "Clean Speckles",
            'MAJORITY': "Majority Vote",
            'ISLANDS': "Small Islands",
        }.get(settings.lego_smooth_mode, settings.lego_smooth_mode)
        self.report({'INFO'}, f"Smooth Colors ({mode_label}) updated {changed_count} face assignment(s).")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 6: Paint Lego Slot
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_paint_lego_slot(Operator):
    bl_idname = "object.miniature_voxeler_paint_lego_slot"
    bl_label = "Paint Lego Slot"
    bl_options = {'REGISTER', 'UNDO'}

    _active_painter = None

    slot_index: IntProperty(default=0, min=0, max=3)

    @staticmethod
    def draw_brush_overlay(operator, context):
        mouse_coord = getattr(operator, "_mouse_region_coord", None)
        if mouse_coord is None:
            return

        settings = context.scene.miniature_voxeler_settings
        radius = max(1.0, float(settings.lego_paint_brush_size))
        segments = 64
        x, y = mouse_coord
        coords = [
            (
                x + cos((2.0 * pi * index) / segments) * radius,
                y + sin((2.0 * pi * index) / segments) * radius,
            )
            for index in range(segments + 1)
        ]

        try:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except ValueError:
            shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})

        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.0)
        shader.bind()
        if getattr(operator, "_is_resizing_brush", False):
            shader.uniform_float("color", (1.0, 0.82, 0.18, 0.95))
        elif getattr(operator, "_is_picking_color", False):
            shader.uniform_float("color", (0.25, 0.72, 1.0, 0.95))
        else:
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def update_modal_cursor(self, context, event=None):
        if context.window is None:
            return

        cursor = 'EYEDROPPER' if getattr(self, "_is_picking_color", False) else 'PAINT_BRUSH'
        if getattr(self, "_current_cursor", None) != cursor:
            context.window.cursor_modal_set(cursor)
            self._current_cursor = cursor

    def add_draw_handler(self, context):
        if getattr(self, "_draw_handler", None) is None:
            self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                self.draw_brush_overlay,
                (self, context),
                'WINDOW',
                'POST_PIXEL',
            )

    def remove_draw_handler(self, context):
        if getattr(self, "_draw_handler", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler, 'WINDOW')
            self._draw_handler = None
            if context.area:
                context.area.tag_redraw()

    def finish_modal(self, context, message=None):
        if type(self)._active_painter is self:
            type(self)._active_painter = None
        context.scene.miniature_voxeler_settings.selected_lego_palette_slot = 0
        self.remove_draw_handler(context)
        self.restore_modal_cursor(context)
        if message:
            self.report({'INFO'}, message)
        return {'FINISHED'}

    def cancel_modal(self, context):
        if type(self)._active_painter is self:
            type(self)._active_painter = None
        context.scene.miniature_voxeler_settings.selected_lego_palette_slot = 0
        self.remove_draw_handler(context)
        self.restore_modal_cursor(context)
        return {'CANCELLED'}

    def restore_modal_cursor(self, context):
        if context.window is None:
            return

        if getattr(self, "_current_cursor", None) is not None:
            context.window.cursor_modal_restore()
            self._current_cursor = None

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def invoke(self, context, event):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}
        if self.slot_index >= settings.lego_color_count:
            self.report({'ERROR'}, "This paint slot is not enabled by Number of Colors.")
            return {'CANCELLED'}

        ensure_slot_palette_materials(obj, settings)

        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Start paint mode from a 3D View.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        previous = type(self)._active_painter
        if previous is not None and previous is not self and not getattr(previous, "_cancel_requested", False):
            if previous.slot_index == self.slot_index:
                previous._is_painting = False
                previous._is_picking_color = False
                previous._is_resizing_brush = False
                previous._cancel_requested = True
                previous._cancel_message = f"Stopped painting slot {self.slot_index + 1}."
                return {'FINISHED'}
            previous.slot_index = self.slot_index
            previous._is_painting = False
            previous._is_picking_color = False
            previous._is_resizing_brush = False
            context.scene.miniature_voxeler_settings.selected_lego_palette_slot = self.slot_index
            previous.update_modal_cursor(context, event)
            self.report({'INFO'}, f"Switched paint brush to slot {self.slot_index + 1}.")
            return {'FINISHED'}

        if previous is not None and previous is not self:
            previous._cancel_requested = True

        self._cancel_requested = False
        self._cancel_message = None
        self._is_painting = False
        self._is_picking_color = False
        self._is_resizing_brush = False
        self._mouse_region_coord = None
        self._draw_handler = None
        self._current_cursor = None
        context.scene.miniature_voxeler_settings.selected_lego_palette_slot = self.slot_index
        type(self)._active_painter = self

        self.add_draw_handler(context)
        context.window_manager.modal_handler_add(self)
        self.update_modal_cursor(context, event)
        self.report({'INFO'}, f"Painting slot {self.slot_index + 1}. Left-drag paints, I then click picks a slot, F resizes brush, right-click or Esc stops.")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if not self._is_resizing_brush and is_event_in_view3d_ui_region(context, event):
            self._mouse_region_coord = None
            return {'PASS_THROUGH'}

        if self._is_resizing_brush:
            self._mouse_region_coord = self._brush_resize_region_coord
            mouse_coord = self._brush_resize_region_coord
        else:
            _, _, mouse_coord = get_mouse_region_coord(context, event)
            if mouse_coord is not None:
                self._mouse_region_coord = mouse_coord
            else:
                self._mouse_region_coord = None

        self.update_modal_cursor(context, event)

        if self._cancel_requested:
            return self.finish_modal(context, getattr(self, "_cancel_message", None))

        settings = context.scene.miniature_voxeler_settings

        if mouse_coord is None and event.type in {'LEFTMOUSE', 'MIDDLEMOUSE', 'RIGHTMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        if self._is_resizing_brush:
            if event.type == 'MOUSEMOVE':
                distance = hypot(
                    event.mouse_x - self._brush_resize_start_x,
                    event.mouse_y - self._brush_resize_start_y,
                )
                settings.lego_paint_brush_size = max(1, min(300, int(distance)))
                return {'RUNNING_MODAL'}
            if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
                self._is_resizing_brush = False
                self.report({'INFO'}, f"Brush size: {settings.lego_paint_brush_size}")
                return {'RUNNING_MODAL'}
            if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
                settings.lego_paint_brush_size = self._brush_resize_start_size
                self._is_resizing_brush = False
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            if self._is_picking_color:
                self._is_picking_color = False
                self.update_modal_cursor(context, event)
                return {'RUNNING_MODAL'}
            return self.finish_modal(context, "Paint Lego Slot finished.")

        obj = get_blocks_object(settings)
        if obj is None:
            return self.cancel_modal(context)

        if event.type == 'F' and event.value == 'PRESS':
            self._is_resizing_brush = True
            self._brush_resize_start_x = event.mouse_x
            self._brush_resize_start_y = event.mouse_y
            self._brush_resize_start_size = settings.lego_paint_brush_size
            _, _, self._brush_resize_region_coord = get_mouse_region_coord(context, event)
            self.report({'INFO'}, "Move mouse to resize brush, left-click confirms, right-click cancels.")
            return {'RUNNING_MODAL'}

        if event.type == 'I' and event.value == 'PRESS':
            self._is_picking_color = True
            self._is_painting = False
            self.update_modal_cursor(context, event)
            self.report({'INFO'}, "Color picker active. Click a face to pick its slot.")
            return {'RUNNING_MODAL'}

        if self._is_picking_color:
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                hit_obj, face_index = raycast_active_face(context, event)
                if hit_obj is not None and face_index is not None:
                    picked_slot = hit_obj.data.polygons[face_index].material_index
                    if 0 <= picked_slot < settings.lego_color_count:
                        self.slot_index = picked_slot
                        settings.selected_lego_palette_slot = picked_slot
                        self._is_picking_color = False
                        self.update_modal_cursor(context, event)
                        self.report({'INFO'}, f"Picked slot {self.slot_index + 1}.")
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                self._is_painting = True
            elif event.value == 'RELEASE':
                self._is_painting = False
                return {'RUNNING_MODAL'}

        if self._is_painting and event.type in {'LEFTMOUSE', 'MOUSEMOVE'}:
            paint_faces_with_brush(
                context,
                event,
                obj,
                self.slot_index,
                settings.lego_paint_brush_size,
            )
            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


# ------------------------------------------------------------
# Operator 7b: Edit Voxel Cells
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_edit_voxel_cells(Operator):
    bl_idname = "object.miniature_voxeler_edit_voxel_cells"
    bl_label = "Edit Voxel Cells"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        name="Action",
        items=[
            ('ADD', "Add", "Add cubes on the voxel grid"),
            ('REMOVE', "Remove", "Remove cubes from the voxel grid"),
        ],
        default='REMOVE',
    )

    _active_editor = None

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def update_modal_cursor(self, context, event):
        if getattr(context, "window", None) is None:
            return
        cursor = 'CROSSHAIR' if self.action == 'ADD' else 'KNIFE'
        if cursor != getattr(self, "_current_cursor", None):
            context.window.cursor_modal_set(cursor)
            self._current_cursor = cursor

    def finish_modal(self, context, message=None):
        if getattr(context, "window", None) is not None:
            context.window.cursor_modal_restore()
        type(self)._active_editor = None
        self._is_editing = False
        self._current_cursor = None
        if message:
            self.report({'INFO'}, message)
        return {'FINISHED'}

    def cancel_modal(self, context):
        return self.finish_modal(context, "Voxel edit finished.")

    def invoke(self, context, event):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}

        if "mv_voxel_cells_json" not in obj:
            self.report({'ERROR'}, "This _Blocks object was not generated by the custom voxelizer.")
            return {'CANCELLED'}

        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Start voxel edit mode from a 3D View.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        previous = type(self)._active_editor
        if previous is not None and previous is not self:
            previous._cancel_requested = True

        self._cancel_requested = False
        self._is_editing = False
        self._current_cursor = None
        type(self)._active_editor = self
        context.window_manager.modal_handler_add(self)
        self.update_modal_cursor(context, event)
        self.report({'INFO'}, f"Voxel {self.action.lower()} mode active. Left-drag edits, F resizes brush, right-click or Esc stops.")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self._cancel_requested:
            return self.finish_modal(context)

        if is_event_in_view3d_ui_region(context, event):
            return {'PASS_THROUGH'}

        self.update_modal_cursor(context, event)
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            return self.cancel_modal(context)

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            return self.finish_modal(context, "Voxel edit finished.")

        if event.type == 'F' and event.value == 'PRESS':
            settings.lego_paint_brush_size = min(300, settings.lego_paint_brush_size + 4)
            self.report({'INFO'}, f"Brush size: {settings.lego_paint_brush_size}")
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                self._is_editing = True
            elif event.value == 'RELEASE':
                self._is_editing = False
                return {'RUNNING_MODAL'}

        if self._is_editing and event.type in {'LEFTMOUSE', 'MOUSEMOVE'}:
            changed = edit_voxel_cells_with_brush(
                context,
                event,
                obj,
                self.action,
                settings.selected_lego_palette_slot,
                settings.lego_paint_brush_size,
            )
            if changed > 0:
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


# ------------------------------------------------------------
# Operator 7c: Unified Paint / Cube Brush
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_voxel_brush_tool(Operator):
    bl_idname = "object.miniature_voxeler_voxel_brush_tool"
    bl_label = "Voxel Brush"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        name="Mode",
        items=[
            ('PAINT', "Paint", "Paint visible voxel faces"),
            ('BOX_PAINT', "Rectangle Paint", "Paint visible voxel faces inside a dragged rectangle"),
            ('LASSO_PAINT', "Lasso Paint", "Paint visible voxel faces inside a freehand lasso"),
            ('ADD', "Modify Cubes", "Add cubes on the voxel grid; hold Shift while dragging to remove cubes"),
            ('REMOVE', "Remove Cubes", "Remove cubes from the voxel grid"),
        ],
        default='PAINT',
    )
    slot_index: IntProperty(default=0, min=0, max=3)

    _active_tool = None

    @staticmethod
    def draw_brush_overlay(operator, context):
        if operator.mode == 'PAINT' or getattr(operator, "_is_resizing_brush", False):
            MINIATUREVOXELER_OT_paint_lego_slot.draw_brush_overlay(operator, context)
        if getattr(operator, "_is_lasso_painting", False):
            coords = list(getattr(operator, "_lasso_paint_coords", []))
            if len(coords) < 2:
                return
            coords.append(coords[0])
        elif getattr(operator, "_is_box_painting", False):
            start_coord = getattr(operator, "_box_paint_start", None)
            end_coord = getattr(operator, "_box_paint_end", None)
            if start_coord is None or end_coord is None:
                return
            x1, y1 = start_coord
            x2, y2 = end_coord
            coords = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        else:
            return

        try:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except ValueError:
            shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.0)
        shader.bind()
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.95))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    @staticmethod
    def draw_voxel_overlay(operator, context):
        start_time = perf_counter()
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        if settings is None:
            return
        obj = get_blocks_object(settings)
        if obj is None:
            return

        hover = getattr(operator, "_hover_edit", None)
        if hover is not None:
            effective_mode = getattr(operator, "_effective_mode", operator.mode)
            if effective_mode in {'ADD', 'REMOVE'}:
                target_coords = get_grid_brush_plane_target_coords(
                    hover["target"],
                    hover.get("face_dir", 4),
                    settings.lego_paint_brush_size,
                )
            else:
                target_coords = get_grid_brush_target_coords(
                    hover["target"],
                    hover.get("face_dir", 4),
                    settings.lego_paint_brush_size,
                )
            face_coords = []
            wire_coords = []
            inflate = 0.025
            for target_coord in target_coords:
                face_coords.extend(get_voxel_cell_face_coords(obj, target_coord, inflate))
                wire_coords.extend(get_voxel_cell_wire_coords(obj, target_coord, inflate))
            fill_color = (0.15, 1.0, 0.35, 0.28) if effective_mode == 'ADD' else (1.0, 0.08, 0.04, 0.32)
            wire_color = (0.2, 1.0, 0.35, 0.95) if effective_mode == 'ADD' else (1.0, 0.14, 0.08, 0.95)
            draw_voxel_transparent_cells(obj, face_coords, fill_color)
            draw_voxel_wire_cells(obj, wire_coords, wire_color)

        pending_adds = list(getattr(operator, "_pending_added", set()))
        if pending_adds:
            face_coords = []
            wire_coords = []
            inflate = 0.018
            for coord in pending_adds:
                face_coords.extend(get_voxel_cell_face_coords(obj, coord, inflate))
                wire_coords.extend(get_voxel_cell_wire_coords(obj, coord, inflate))
            draw_voxel_transparent_cells(obj, face_coords, (0.1, 0.65, 1.0, 0.22))
            draw_voxel_wire_cells(obj, wire_coords, (0.15, 0.85, 1.0, 0.65))

        pending_removes = list(getattr(operator, "_pending_removed", set()))
        if pending_removes:
            face_coords = []
            wire_coords = []
            inflate = 0.025
            for coord in pending_removes:
                face_coords.extend(get_voxel_cell_face_coords(obj, coord, inflate))
                wire_coords.extend(get_voxel_cell_wire_coords(obj, coord, inflate))
            draw_voxel_transparent_cells(obj, face_coords, (1.0, 0.05, 0.03, 0.26))
            draw_voxel_wire_cells(obj, wire_coords, (1.0, 0.15, 0.12, 0.65))
        operator.record_timing("overlay", perf_counter() - start_time)

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def update_modal_cursor(self, context, event=None):
        if context.window is None:
            return

        effective_mode = self.get_effective_mode(event)
        if getattr(self, "_is_picking_color", False):
            cursor = 'EYEDROPPER'
        elif self.mode in {'BOX_PAINT', 'LASSO_PAINT'}:
            cursor = 'CROSSHAIR'
        elif effective_mode == 'PAINT':
            cursor = 'PAINT_BRUSH'
        elif effective_mode == 'ADD':
            cursor = 'CROSSHAIR'
        else:
            cursor = 'KNIFE'

        if getattr(self, "_current_cursor", None) != cursor:
            context.window.cursor_modal_set(cursor)
            self._current_cursor = cursor

    def add_draw_handler(self, context):
        if getattr(self, "_draw_handler", None) is None:
            self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                self.draw_brush_overlay,
                (self, context),
                'WINDOW',
                'POST_PIXEL',
            )
        if getattr(self, "_voxel_draw_handler", None) is None:
            self._voxel_draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                self.draw_voxel_overlay,
                (self, context),
                'WINDOW',
                'POST_VIEW',
            )

    def remove_draw_handler(self, context):
        if getattr(self, "_draw_handler", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler, 'WINDOW')
            self._draw_handler = None
        if getattr(self, "_voxel_draw_handler", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._voxel_draw_handler, 'WINDOW')
            self._voxel_draw_handler = None
        if context.area:
            context.area.tag_redraw()

    def restore_modal_cursor(self, context):
        if context.window is not None and getattr(self, "_current_cursor", None) is not None:
            context.window.cursor_modal_restore()
            self._current_cursor = None

    def get_effective_mode(self, event=None):
        if self.mode == 'ADD' and event is not None and getattr(event, "shift", False):
            return 'REMOVE'
        if self.mode in {'BOX_PAINT', 'LASSO_PAINT'}:
            return 'PAINT'
        return self.mode

    def record_timing(self, label, elapsed):
        if not getattr(self, "_debug_enabled", False):
            return
        samples = self._profile_samples.setdefault(label, [0, 0.0, 0.0])
        samples[0] += 1
        samples[1] += elapsed
        samples[2] = max(samples[2], elapsed)

    def maybe_report_timing(self, context):
        if not getattr(self, "_debug_enabled", False):
            return
        now = perf_counter()
        if now - getattr(self, "_last_profile_report", 0.0) < 1.5:
            return
        if not self._profile_samples:
            return
        parts = []
        for label, (count, total, maximum) in sorted(self._profile_samples.items()):
            if count:
                parts.append(f"{label}: avg {total * 1000.0 / count:.2f}ms max {maximum * 1000.0:.2f}ms n={count}")
        message = "Voxel Brush timing | " + " | ".join(parts)
        print(message)
        self.report({'INFO'}, message[:240])
        self._profile_samples = {}
        self._last_profile_report = now

    def flush_pending_voxel_rebuild(self, context, obj=None):
        if not getattr(self, "_pending_rebuild", False):
            return 0
        start_time = perf_counter()
        settings = context.scene.miniature_voxeler_settings
        obj = obj or get_blocks_object(settings)
        if obj is None:
            return 0
        origin = get_stored_voxel_origin(obj)
        voxel_size = float(obj.get("mv_voxel_size", 0.0))
        if origin is None or voxel_size <= 0.0:
            return 0
        rebuild_voxel_mesh_from_cells(obj, origin, voxel_size, self._cells, self._face_slots, store_state=False)
        self._face_slots = collect_voxel_face_slots_from_mesh(obj, self._cells)
        self._face_centers_world = get_voxel_face_centers_world(obj)
        self._grid_cache = build_voxel_grid_cache(obj, self._cells)
        self._pending_added.clear()
        self._pending_removed.clear()
        self._pending_rebuild = False
        self._last_visible_rebuild_time = perf_counter()
        self.record_timing("rebuild", perf_counter() - start_time)
        return 1

    def commit_pending_voxel_edits(self, context, obj=None, report=True):
        pending_undo = []
        if getattr(self, "_pending_rebuild", False):
            for batch in getattr(self, "_voxel_preview_undo_stack", []):
                pending_undo.extend(batch)

        committed = self.flush_pending_voxel_rebuild(context, obj)
        if committed:
            if pending_undo:
                self._voxel_apply_undo_stack.append(pending_undo)
                if len(self._voxel_apply_undo_stack) > 50:
                    self._voxel_apply_undo_stack.pop(0)
            self._voxel_preview_undo_stack.clear()
            self._last_applied_target = None
            self._drag_face_dir = None
            self._drag_plane_coord = None
            if report:
                self.report({'INFO'}, "Voxel edits committed. Brush remains active.")
        elif report:
            self.report({'INFO'}, "No queued voxel edits to commit.")
        return committed

    def undo_voxel_batch(self, context, obj, batch, rebuild=True):
        if not batch:
            return 0

        for item in reversed(batch):
            action = item["action"]
            coord = item["coord"]
            if action == 'ADD':
                if coord in self._cells:
                    del self._cells[coord]
                self._pending_added.discard(coord)
                self._pending_removed.discard(coord)
                for key in list(self._face_slots.keys()):
                    if key[:3] == coord:
                        del self._face_slots[key]
            elif action == 'REMOVE':
                self._cells[coord] = item["slot"]
                self._pending_added.discard(coord)
                self._pending_removed.discard(coord)
                for key, value in item.get("face_slots", {}).items():
                    self._face_slots[key] = value
                expand_voxel_grid_cache(getattr(self, "_grid_cache", None), coord)

        self._last_applied_target = None
        self._drag_face_dir = None
        self._drag_plane_coord = None
        self._pending_rebuild = True
        if rebuild:
            self.flush_pending_voxel_rebuild(context, obj)
        elif context.area:
            context.area.tag_redraw()
        return 1

    def undo_last_voxel_edit(self, context, obj=None):
        undo_stack = getattr(self, "_voxel_preview_undo_stack", [])
        if not undo_stack:
            apply_stack = getattr(self, "_voxel_apply_undo_stack", [])
            if not apply_stack:
                self.report({'INFO'}, "No voxel brush edits to undo.")
                return 0
            if self.undo_voxel_batch(context, obj, apply_stack.pop()):
                self.report({'INFO'}, "Undid last applied voxel edit.")
                return 1
            return 0

        if self.undo_voxel_batch(context, obj, undo_stack.pop(), rebuild=False):
            self.report({'INFO'}, "Undid last preview voxel brush edit.")
            return 1
        return 1

    def maybe_flush_visible_voxel_rebuild(self, context, obj):
        if not getattr(self, "_pending_rebuild", False):
            return 0
        last_time = getattr(self, "_last_visible_rebuild_time", 0.0)
        if perf_counter() - last_time < 0.12:
            return 0
        return self.flush_pending_voxel_rebuild(context, obj)

    def finish_modal(self, context, message=None):
        obj = get_blocks_object(context.scene.miniature_voxeler_settings)
        self.flush_pending_voxel_rebuild(context, obj)
        if obj is not None and getattr(self, "_face_slots", None) is not None:
            store_voxel_face_slots(obj, self._face_slots)
            self._cells = sync_voxel_cell_slots_from_face_slots(self._cells, self._face_slots)
            origin = get_stored_voxel_origin(obj)
            voxel_size = float(obj.get("mv_voxel_size", 0.0))
            if origin is not None and voxel_size > 0.0:
                store_voxel_state(obj, origin, voxel_size, self._cells)
        if type(self)._active_tool is self:
            type(self)._active_tool = None
        self.remove_draw_handler(context)
        self.restore_modal_cursor(context)
        self._is_editing = False
        if message:
            self.report({'INFO'}, message)
        return {'FINISHED'}

    def cancel_modal(self, context):
        return self.finish_modal(context, "Voxel brush finished.")

    def apply_hover_edit(self):
        hover = getattr(self, "_hover_edit", None)
        if hover is None:
            return 0
        effective_mode = getattr(self, "_effective_mode", self.mode)
        if effective_mode in {'ADD', 'REMOVE'}:
            target_coords = get_grid_brush_plane_target_coords(
                hover["target"],
                hover.get("face_dir", 4),
                getattr(self, "_active_brush_size", 1),
            )
        else:
            target_coords = get_grid_brush_target_coords(
                hover["target"],
                hover.get("face_dir", 4),
                getattr(self, "_active_brush_size", 1),
            )
        signature = (effective_mode, tuple(sorted(target_coords)))
        if getattr(self, "_last_applied_target", None) == signature:
            return 0

        changed_count = 0
        undo_batch = []
        if effective_mode == 'ADD':
            direction = get_voxel_cell_face_vectors()[hover.get("face_dir", 4)]
            for target in target_coords:
                if target in self._cells:
                    continue
                source_cell = (
                    target[0] - direction[0],
                    target[1] - direction[1],
                    target[2] - direction[2],
                )
                if source_cell not in self._cells:
                    continue
                slot = get_slot_for_new_voxel_cell(
                    self._cells,
                    self._face_slots,
                    target,
                    source_cell=source_cell,
                    source_face_dir=hover.get("face_dir"),
                    fallback_slot=hover.get("fallback_slot", self.slot_index),
                )
                undo_batch.append({
                    "action": 'ADD',
                    "coord": target,
                })
                self._cells[target] = int(slot)
                expand_voxel_grid_cache(getattr(self, "_grid_cache", None), target)
                self._pending_added.add(target)
                self._pending_removed.discard(target)
                changed_count += 1
        elif effective_mode == 'REMOVE':
            for target in target_coords:
                if target not in self._cells:
                    continue
                undo_batch.append({
                    "action": 'REMOVE',
                    "coord": target,
                    "slot": int(self._cells[target]),
                    "face_slots": {
                        key: value for key, value in self._face_slots.items()
                        if key[:3] == target
                    },
                })
                del self._cells[target]
                self._pending_removed.add(target)
                self._pending_added.discard(target)
                for key in list(self._face_slots.keys()):
                    if key[:3] == target:
                        del self._face_slots[key]
                changed_count += 1
        else:
            return 0

        if changed_count == 0:
            return 0

        if undo_batch:
            self._voxel_preview_undo_stack.append(undo_batch)
            if len(self._voxel_preview_undo_stack) > 100:
                self._voxel_preview_undo_stack.pop(0)

        self._last_applied_target = signature
        self._pending_rebuild = True
        return changed_count

    def invoke(self, context, event):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}
        if "mv_voxel_cells_json" not in obj:
            self.report({'ERROR'}, "This _Blocks object was not generated by the custom voxelizer.")
            return {'CANCELLED'}
        if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'} and self.slot_index >= settings.lego_color_count:
            self.report({'ERROR'}, "This paint slot is not enabled by Number of Colors.")
            return {'CANCELLED'}
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Start Voxel Brush from a 3D View.")
            return {'CANCELLED'}

        if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'} and context.mode == 'EDIT_MESH' and context.edit_object == obj:
            changed_count = assign_selected_edit_faces_to_slot(obj, settings, self.slot_index)
            if changed_count == 0:
                self.report({'WARNING'}, f"No selected faces to assign to slot {self.slot_index + 1}.")
                return {'CANCELLED'}
            settings.selected_lego_palette_slot = self.slot_index
            self.report({'INFO'}, f"Assigned {changed_count} selected face(s) to slot {self.slot_index + 1}.")
            return {'FINISHED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        ensure_slot_palette_materials(obj, settings)
        if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
            settings.lego_paint_tool_mode = self.mode
            settings.selected_lego_palette_slot = self.slot_index

        previous = type(self)._active_tool
        if previous is not None and previous is not self and not getattr(previous, "_cancel_requested", False):
            if previous.mode == self.mode and previous.slot_index == self.slot_index:
                previous._cancel_requested = True
                previous._cancel_message = "Voxel brush finished."
                return {'FINISHED'}
            if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
                previous.flush_pending_voxel_rebuild(context, obj)
            previous.mode = self.mode
            previous.slot_index = self.slot_index
            previous._is_editing = False
            previous._is_picking_color = False
            previous._is_resizing_brush = False
            previous._is_box_paint_armed = (self.mode == 'BOX_PAINT')
            previous._is_box_painting = False
            previous._is_lasso_paint_armed = (self.mode == 'LASSO_PAINT')
            previous._is_lasso_painting = False
            previous._box_paint_start = None
            previous._box_paint_end = None
            previous._lasso_paint_coords = []
            previous._hover_edit = None
            if self.mode not in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
                previous._face_centers_world = []
            if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
                settings.selected_lego_palette_slot = self.slot_index
                previous._face_centers_world = get_voxel_face_centers_world(obj)
            previous._effective_mode = previous.mode
            previous.update_modal_cursor(context, event)
            self.report({'INFO'}, f"Voxel Brush switched to {self.mode.lower().replace('_', ' ')}.")
            return {'FINISHED'}

        if previous is not None and previous is not self:
            previous._cancel_requested = True

        self._cancel_requested = False
        self._cancel_message = None
        self._is_editing = False
        self._is_picking_color = False
        self._is_resizing_brush = False
        self._is_box_paint_armed = (self.mode == 'BOX_PAINT')
        self._is_box_painting = False
        self._is_lasso_paint_armed = (self.mode == 'LASSO_PAINT')
        self._is_lasso_painting = False
        self._box_paint_start = None
        self._box_paint_end = None
        self._lasso_paint_coords = []
        self._pending_rebuild = False
        self._last_visible_rebuild_time = 0.0
        self._pending_added = set()
        self._pending_removed = set()
        self._voxel_preview_undo_stack = []
        self._voxel_apply_undo_stack = []
        self._last_applied_target = None
        self._drag_face_dir = None
        self._drag_plane_coord = None
        self._drag_effective_mode = None
        self._hover_edit = None
        self._effective_mode = self.mode
        self._debug_enabled = False
        self._profile_samples = {}
        self._last_profile_report = perf_counter()
        self._mouse_region_coord = None
        self._draw_handler = None
        self._voxel_draw_handler = None
        self._current_cursor = None
        self._cells = deserialize_voxel_cells(obj)
        self._grid_cache = build_voxel_grid_cache(obj, self._cells)
        self._face_slots = deserialize_voxel_face_slots(obj)
        if not self._face_slots and self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
            self._face_slots = collect_voxel_face_slots_from_mesh(obj, self._cells)
        self._face_centers_world = get_voxel_face_centers_world(obj) if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'} else []
        if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
            settings.selected_lego_palette_slot = self.slot_index

        type(self)._active_tool = self
        self.add_draw_handler(context)
        context.window_manager.modal_handler_add(self)
        self.update_modal_cursor(context, event)
        self.report({'INFO'}, "Voxel Brush active. Modify Cubes adds by default; hold Shift while dragging to remove. Press Space to draw queued cube changes. I picks color, F resizes, right-click or Esc stops.")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if self._cancel_requested:
            return self.finish_modal(context, getattr(self, "_cancel_message", None))

        if not self._is_resizing_brush and is_event_in_view3d_ui_region(context, event):
            self._mouse_region_coord = None
            return {'PASS_THROUGH'}

        if self._is_resizing_brush:
            self._mouse_region_coord = self._brush_resize_region_coord
            mouse_coord = self._brush_resize_region_coord
        else:
            _, _, mouse_coord = get_mouse_region_coord(context, event)
            self._mouse_region_coord = mouse_coord

        self._effective_mode = self.get_effective_mode(event)
        self.update_modal_cursor(context, event)
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            return self.cancel_modal(context)

        self._active_brush_size = settings.lego_paint_brush_size
        if self._is_editing and self._drag_effective_mode is not None and self._drag_effective_mode != self._effective_mode:
            self._drag_face_dir = None
            self._drag_plane_coord = None
            self._drag_effective_mode = self._effective_mode
            self._last_applied_target = None

        if (
            self._effective_mode == 'ADD' and
            self._is_editing and
            self._drag_face_dir is not None and
            mouse_coord is not None
        ):
            hover_start = perf_counter()
            self._hover_edit = get_voxel_plane_edit_target(
                context,
                event,
                obj,
                self._cells,
                settings.selected_lego_palette_slot,
                self._grid_cache,
                self._drag_face_dir,
                self._drag_plane_coord,
            )
            self.record_timing("hover", perf_counter() - hover_start)
        elif (
            self._effective_mode == 'REMOVE' and
            self._is_editing and
            self._drag_face_dir is not None and
            mouse_coord is not None
        ):
            hover_start = perf_counter()
            self._hover_edit = get_voxel_remove_plane_edit_target(
                context,
                event,
                obj,
                self._grid_cache,
                self._drag_face_dir,
                self._drag_plane_coord,
            )
            self.record_timing("hover", perf_counter() - hover_start)
        elif self._effective_mode in {'ADD', 'REMOVE'} and mouse_coord is not None:
            hover_start = perf_counter()
            self._hover_edit = get_voxel_cursor_edit_target(
                context,
                event,
                obj,
                self._effective_mode,
                self._cells,
                self._face_slots,
                settings.selected_lego_palette_slot,
                self._grid_cache,
            )
            self.record_timing("hover", perf_counter() - hover_start)
        elif self._effective_mode == 'PAINT':
            self._hover_edit = None

        if mouse_coord is None and event.type in {'LEFTMOUSE', 'MIDDLEMOUSE', 'RIGHTMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        if self._is_resizing_brush:
            if event.type == 'MOUSEMOVE':
                distance = hypot(event.mouse_x - self._brush_resize_start_x, event.mouse_y - self._brush_resize_start_y)
                settings.lego_paint_brush_size = max(1, min(300, int(distance)))
                return {'RUNNING_MODAL'}
            if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
                self._is_resizing_brush = False
                self.report({'INFO'}, f"Brush size: {settings.lego_paint_brush_size}")
                return {'RUNNING_MODAL'}
            if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
                settings.lego_paint_brush_size = self._brush_resize_start_size
                self._is_resizing_brush = False
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}

        if (
            getattr(self, "_is_box_paint_armed", False) or
            getattr(self, "_is_box_painting", False) or
            getattr(self, "_is_lasso_paint_armed", False) or
            getattr(self, "_is_lasso_painting", False)
        ):
            if event.type == 'I' and event.value == 'PRESS':
                self.flush_pending_voxel_rebuild(context, obj)
                self._is_picking_color = True
                self._is_editing = False
                self._is_box_paint_armed = False
                self._is_box_painting = False
                self._is_lasso_paint_armed = False
                self._is_lasso_painting = False
                self._box_paint_start = None
                self._box_paint_end = None
                self._lasso_paint_coords = []
                self.update_modal_cursor(context, event)
                self.report({'INFO'}, "Color picker active. Click a face to pick its slot.")
                return {'RUNNING_MODAL'}
            if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
                self._is_box_paint_armed = False
                self._is_box_painting = False
                self._is_lasso_paint_armed = False
                self._is_lasso_painting = False
                self._box_paint_start = None
                self._box_paint_end = None
                self._lasso_paint_coords = []
                self.report({'INFO'}, "Shape paint canceled.")
                return {'RUNNING_MODAL'}
            if mouse_coord is None:
                return {'PASS_THROUGH'}
            if (
                (getattr(self, "_is_box_paint_armed", False) or getattr(self, "_is_lasso_paint_armed", False)) and
                not getattr(self, "_is_box_painting", False) and
                not getattr(self, "_is_lasso_painting", False) and
                event.type != 'LEFTMOUSE'
            ):
                return {'PASS_THROUGH'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                if getattr(self, "_is_lasso_paint_armed", False):
                    self._is_lasso_paint_armed = False
                    self._is_lasso_painting = True
                    self._lasso_paint_coords = [mouse_coord]
                else:
                    self._is_box_paint_armed = False
                    self._is_box_painting = True
                    self._box_paint_start = mouse_coord
                    self._box_paint_end = mouse_coord
                return {'RUNNING_MODAL'}
            if self._is_lasso_painting and event.type == 'MOUSEMOVE':
                coords = getattr(self, "_lasso_paint_coords", [])
                if not coords or hypot(mouse_coord[0] - coords[-1][0], mouse_coord[1] - coords[-1][1]) >= 2.0:
                    coords.append(mouse_coord)
                    self._lasso_paint_coords = coords
                return {'RUNNING_MODAL'}
            if self._is_box_painting and event.type == 'MOUSEMOVE':
                self._box_paint_end = mouse_coord
                return {'RUNNING_MODAL'}
            if self._is_lasso_painting and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                coords = list(getattr(self, "_lasso_paint_coords", []))
                coords.append(mouse_coord)
                face_indices = collect_lasso_face_indices(
                    context,
                    obj,
                    coords,
                    self._face_centers_world,
                )
                changed_count = paint_face_indices(
                    obj,
                    self.slot_index,
                    face_indices,
                    self._face_slots,
                    commit_face_slots=False,
                )
                self._is_lasso_painting = False
                self._is_lasso_paint_armed = (self.mode == 'LASSO_PAINT')
                self._lasso_paint_coords = []
                if changed_count:
                    self.report({'INFO'}, f"Lasso painted {changed_count} face(s) with slot {self.slot_index + 1}.")
                else:
                    self.report({'INFO'}, "Lasso paint found no visible faces to change.")
                return {'RUNNING_MODAL'}
            if self._is_box_painting and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._box_paint_end = mouse_coord
                face_indices = collect_box_face_indices(
                    context,
                    obj,
                    self._box_paint_start,
                    self._box_paint_end,
                    self._face_centers_world,
                )
                changed_count = paint_face_indices(
                    obj,
                    self.slot_index,
                    face_indices,
                    self._face_slots,
                    commit_face_slots=False,
                )
                self._is_box_painting = False
                self._is_box_paint_armed = (self.mode == 'BOX_PAINT')
                self._box_paint_start = None
                self._box_paint_end = None
                if changed_count:
                    self.report({'INFO'}, f"Box painted {changed_count} face(s) with slot {self.slot_index + 1}.")
                else:
                    self.report({'INFO'}, "Box paint found no visible faces to change.")
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        if event.type == 'Z' and event.value == 'PRESS' and getattr(event, "ctrl", False) and self._effective_mode in {'ADD', 'REMOVE'}:
            self._is_editing = False
            self.undo_last_voxel_edit(context, obj)
            return {'RUNNING_MODAL'}

        if event.type == 'SPACE' and event.value == 'PRESS' and self._effective_mode in {'ADD', 'REMOVE'}:
            self._is_editing = False
            self.commit_pending_voxel_edits(context, obj)
            return {'RUNNING_MODAL'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            if self._is_picking_color:
                self._is_picking_color = False
                self.update_modal_cursor(context, event)
                return {'RUNNING_MODAL'}
            return self.finish_modal(context, "Voxel brush finished.")

        if event.type == 'F' and event.value == 'PRESS':
            self._is_resizing_brush = True
            self._brush_resize_start_x = event.mouse_x
            self._brush_resize_start_y = event.mouse_y
            self._brush_resize_start_size = settings.lego_paint_brush_size
            _, _, self._brush_resize_region_coord = get_mouse_region_coord(context, event)
            self.report({'INFO'}, "Move mouse to resize brush, left-click confirms, right-click cancels.")
            return {'RUNNING_MODAL'}

        if event.type == 'I' and event.value == 'PRESS':
            self.flush_pending_voxel_rebuild(context, obj)
            self._is_picking_color = True
            self._is_editing = False
            self.update_modal_cursor(context, event)
            self.report({'INFO'}, "Color picker active. Click a face to pick its slot.")
            return {'RUNNING_MODAL'}

        if self._is_picking_color:
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                hit_obj, face_index = raycast_active_face(context, event)
                if hit_obj is not None and face_index is not None:
                    picked_slot = hit_obj.data.polygons[face_index].material_index
                    if 0 <= picked_slot < settings.lego_color_count:
                        self.slot_index = picked_slot
                        settings.selected_lego_palette_slot = picked_slot
                        self.mode = settings.lego_paint_tool_mode
                        self._is_box_paint_armed = (self.mode == 'BOX_PAINT')
                        self._is_lasso_paint_armed = (self.mode == 'LASSO_PAINT')
                        self._is_picking_color = False
                        self.update_modal_cursor(context, event)
                        self.report({'INFO'}, f"Picked slot {self.slot_index + 1}.")
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}

        if event.type in {'P', 'A'} and event.value == 'PRESS':
            if event.type == 'P':
                self.flush_pending_voxel_rebuild(context, obj)
            if event.type == 'P':
                self.mode = 'PAINT'
                self._is_box_paint_armed = False
                self._is_box_painting = False
                self._is_lasso_paint_armed = False
                self._is_lasso_painting = False
                self._face_centers_world = get_voxel_face_centers_world(obj)
                settings.lego_paint_tool_mode = 'PAINT'
            else:
                self.mode = 'ADD'
                self._is_box_paint_armed = False
                self._is_box_painting = False
                self._is_lasso_paint_armed = False
                self._is_lasso_painting = False
                self._face_centers_world = []
            self._effective_mode = self.mode
            self._hover_edit = None
            self._drag_face_dir = None
            self._drag_plane_coord = None
            self.update_modal_cursor(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                if self._effective_mode == 'PAINT':
                    self.flush_pending_voxel_rebuild(context, obj)
                elif self._effective_mode == 'ADD' and self._hover_edit is not None:
                    self._drag_face_dir = self._hover_edit.get("face_dir")
                    self._drag_plane_coord = self._hover_edit["target"][get_axis_for_face_dir(self._drag_face_dir)]
                elif self._effective_mode == 'REMOVE' and self._hover_edit is not None:
                    self._drag_face_dir = self._hover_edit.get("face_dir")
                    self._drag_plane_coord = self._hover_edit["target"][get_axis_for_face_dir(self._drag_face_dir)]
                self._is_editing = True
                self._drag_effective_mode = self._effective_mode
                self._last_applied_target = None
            elif event.value == 'RELEASE':
                self._is_editing = False
                if self._effective_mode == 'PAINT':
                    self.flush_pending_voxel_rebuild(context, obj)
                self._drag_face_dir = None
                self._drag_plane_coord = None
                self._drag_effective_mode = None
                self._last_applied_target = None
                return {'RUNNING_MODAL'}

        if self._is_editing and event.type in {'LEFTMOUSE', 'MOUSEMOVE'}:
            if self._effective_mode == 'PAINT':
                changed = paint_faces_with_brush(
                    context,
                    event,
                    obj,
                    self.slot_index,
                    settings.lego_paint_brush_size,
                    self._face_slots,
                    commit_face_slots=False,
                    face_centers_world=self._face_centers_world,
                )
                if changed:
                    return {'RUNNING_MODAL'}
            else:
                edit_start = perf_counter()
                changed = self.apply_hover_edit()
                self.record_timing("edit", perf_counter() - edit_start)
                if changed:
                    self.maybe_report_timing(context)
                    return {'RUNNING_MODAL'}

        self.maybe_report_timing(context)
        return {'PASS_THROUGH'}


class MINIATUREVOXELER_OT_select_paint_slot(Operator):
    bl_idname = "object.miniature_voxeler_select_paint_slot"
    bl_label = "Select Paint Slot"
    bl_options = {'REGISTER', 'UNDO'}

    slot_index: IntProperty(default=0, min=0, max=3)

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "miniature_voxeler_settings", None) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Voxel Building first so the _Blocks object exists.")
            return {'CANCELLED'}
        if self.slot_index >= settings.lego_color_count:
            self.report({'ERROR'}, "This paint slot is not enabled by Number of Colors.")
            return {'CANCELLED'}

        if context.mode == 'EDIT_MESH' and context.edit_object == obj:
            changed_count = assign_selected_edit_faces_to_slot(obj, settings, self.slot_index)
            if changed_count == 0:
                self.report({'WARNING'}, f"No selected faces to assign to slot {self.slot_index + 1}.")
                return {'CANCELLED'}
            settings.selected_lego_palette_slot = self.slot_index
            self.report({'INFO'}, f"Assigned {changed_count} selected face(s) to slot {self.slot_index + 1}.")
            return {'FINISHED'}

        settings.selected_lego_palette_slot = self.slot_index
        active_tool = MINIATUREVOXELER_OT_voxel_brush_tool._active_tool
        if active_tool is not None and not getattr(active_tool, "_cancel_requested", False) and active_tool.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
            active_tool.slot_index = self.slot_index
            active_tool._is_editing = False
            active_tool._is_picking_color = False
            active_tool._hover_edit = None
            active_tool.update_modal_cursor(context, None)
            if context.area:
                context.area.tag_redraw()
        self.report({'INFO'}, f"Selected slot {self.slot_index + 1}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_set_palette_color(Operator):
    bl_idname = "object.miniature_voxeler_set_palette_color"
    bl_label = "Set Palette Color"
    bl_options = {'REGISTER', 'UNDO'}

    slot_index: IntProperty(default=0, min=0, max=3)
    palette_index: IntProperty(default=0, min=0, max=len(FIXED_LEGO_PALETTE) - 1)

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "miniature_voxeler_settings", None) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        slot_index = max(0, min(int(self.slot_index), settings.lego_color_count - 1))
        palette_index = max(0, min(int(self.palette_index), len(FIXED_LEGO_PALETTE) - 1))
        setattr(settings, f"lego_palette_slot_{slot_index + 1}", str(palette_index))
        settings.selected_lego_palette_slot = slot_index

        obj = get_blocks_object(settings)
        if obj is not None and not bool(obj.get("mv_debug_colors_active", False)):
            ensure_slot_palette_materials(obj, settings)

        self.report({'INFO'}, f"Slot {slot_index + 1}: {FIXED_LEGO_PALETTE[palette_index][0]}")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_toggle_debug_colors(Operator):
    bl_idname = "object.miniature_voxeler_toggle_debug_colors"
    bl_label = "Debug Colors"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_blocks_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Voxel Building first so the _Blocks object exists.")
            return {'CANCELLED'}

        ensure_slot_palette_materials(obj, settings)
        active = bool(obj.get("mv_debug_colors_active", False))
        backup_raw = obj.get("mv_debug_color_backup_json", "")

        if active:
            restored = False
            try:
                backup = json.loads(backup_raw) if backup_raw else []
            except Exception:
                backup = []

            for slot_index in range(min(settings.lego_color_count, len(obj.data.materials))):
                material = obj.data.materials[slot_index]
                if slot_index < len(backup) and len(backup[slot_index]) >= 3:
                    color = tuple(float(component) for component in backup[slot_index][:3])
                else:
                    color = get_slot_palette_color(settings, slot_index)
                set_material_base_color(material, color)
                restored = True

            obj["mv_debug_colors_active"] = False
            if "mv_debug_color_backup_json" in obj:
                del obj["mv_debug_color_backup_json"]
            self.report({'INFO'}, "Debug Colors restored palette colors." if restored else "Debug Colors off.")
            return {'FINISHED'}

        backup = []
        for slot_index in range(min(settings.lego_color_count, len(obj.data.materials))):
            material = obj.data.materials[slot_index]
            backup.append(list(get_material_base_color(material)))
            set_material_base_color(material, DEBUG_LEGO_COLORS[slot_index % len(DEBUG_LEGO_COLORS)])

        obj["mv_debug_color_backup_json"] = json.dumps(backup, separators=(",", ":"))
        obj["mv_debug_colors_active"] = True
        self.report({'INFO'}, "Debug Colors active: slots preview as Red, Green, Blue, White.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 8: Generate Lego Skin
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_separate_skins_solidify(Operator):
    bl_idname = "object.miniature_voxeler_separate_skins_solidify"
    bl_label = "Separate Skins and Solidify"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_blocks_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        body_obj = get_blocks_object(settings)
        if body_obj is None:
            self.report({'ERROR'}, "Run Step 2 Block Remesh first so the _Blocks object exists.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        base_slot_index = int(settings.color_skin_base_slot)
        enabled_skin_slots = [slot_index for slot_index in get_enabled_color_skin_slots(settings) if slot_index != base_slot_index]

        if not enabled_skin_slots:
            self.report({'WARNING'}, "Enable at least one skin slot that is different from the base slot.")
            return {'CANCELLED'}

        if not body_obj.data.materials:
            self.report({'ERROR'}, "Object has no material slots. Run Lego Color first.")
            return {'CANCELLED'}

        if base_slot_index >= len(body_obj.data.materials):
            self.report({'ERROR'}, "The selected base slot does not exist on this object.")
            return {'CANCELLED'}

        root_name = get_root_name(body_obj.name)
        source_name = get_inferred_source_name(settings, body_obj)

        base_obj = get_color_base_object(settings)
        if base_obj is not None:
            remove_object_if_exists(base_obj)
        for skin_obj in get_color_skin_objects(settings):
            remove_object_if_exists(skin_obj)

        base_obj = duplicate_object(context, body_obj, get_color_base_name(root_name))
        set_metadata(base_obj, root_name, source_name)
        base_material = ensure_lego_color_material(
            base_obj,
            0,
            get_slot_palette_color(settings, base_slot_index),
        )
        apply_single_material_to_object(base_obj, base_material)
        ensure_solidify_modifier(
            base_obj,
            "SkinSolidify",
            mm_to_scene_units(context, settings.outer_skin_mm),
            settings.skin_solidify_offset + 0.03,
            settings.skin_solidify_even_thickness,
        )
        apply_modifier_if_present(context, base_obj, "SkinSolidify")

        skin_source = duplicate_object(context, body_obj, f"{root_name}_Lego_Skin_Source")
        set_metadata(skin_source, root_name, source_name)

        processed = []
        skipped = []

        for slot_index in enabled_skin_slots:
            if skin_source is None or skin_source.name not in bpy.data.objects:
                self.report({'ERROR'}, "Skin source object became invalid.")
                return {'CANCELLED'}

            set_active_object(context, skin_source)
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_mode(type='FACE')
            bpy.ops.mesh.select_all(action='DESELECT')

            selected_count = select_faces_by_material_slot(skin_source, [slot_index])
            if selected_count == 0:
                bpy.ops.object.mode_set(mode='OBJECT')
                skipped.append(f"Slot {slot_index + 1}")
                continue

            pre_names = set(obj.name for obj in bpy.data.objects)
            bpy.ops.mesh.separate(type='SELECTED')
            bpy.ops.object.mode_set(mode='OBJECT')

            new_obj = find_new_object(pre_names, skin_source, context)
            if new_obj is None:
                self.report({'ERROR'}, f"Could not identify separated skin object for slot {slot_index + 1}.")
                return {'CANCELLED'}

            new_obj.name = get_color_skin_name(root_name, slot_index)
            set_metadata(new_obj, root_name, source_name)
            ensure_solidify_modifier(
                new_obj,
                "SkinSolidify",
                mm_to_scene_units(context, settings.outer_skin_mm),
                settings.skin_solidify_offset,
                settings.skin_solidify_even_thickness,
            )
            apply_modifier_if_present(context, new_obj, "SkinSolidify")

            label = f"Slot {slot_index + 1} ({selected_count} faces)"
            processed.append(label)

        if not processed:
            remove_object_if_exists(skin_source)
            set_active_object(context, base_obj)
            self.report({'WARNING'}, "No faces found for the selected skin slots.")
            return {'CANCELLED'}

        remove_object_if_exists(skin_source)

        body_obj.hide_set(True)
        set_active_object(context, base_obj)

        msg = "Processed: " + ", ".join(processed)
        if skipped:
            msg += " | Skipped: " + ", ".join(skipped)
        msg += " | Solidify applied | _Blocks hidden"

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class MINIATUREVOXELER_OT_add_skin_booleans(Operator):
    bl_idname = "object.miniature_voxeler_add_skin_booleans"
    bl_label = "Add Booleans"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_color_base_object(settings) is not None and bool(get_color_skin_objects(settings))

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        base_obj = get_color_base_object(settings)
        skin_objects = get_sorted_color_skin_objects(settings)
        if base_obj is None or not skin_objects:
            self.report({'ERROR'}, "Run Separate Skins and Solidify first.")
            return {'CANCELLED'}
        foot_obj = get_platform_foot_object(settings)

        added_count = 0
        for skin_obj in skin_objects:
            ensure_boolean_modifier(
                base_obj,
                skin_obj,
                f"SkinBoolean_Base_{skin_obj.name}",
                operation='DIFFERENCE',
                solver='EXACT',
            )
            added_count += 1

        for winner_index, winner_obj in enumerate(skin_objects):
            for target_obj in skin_objects[winner_index + 1:]:
                ensure_boolean_modifier(
                    target_obj,
                    winner_obj,
                    f"SkinBoolean_Slot_{winner_obj.name}",
                    operation='DIFFERENCE',
                    solver='EXACT',
                )
                added_count += 1

        if foot_obj is not None:
            for target_obj in [base_obj] + skin_objects:
                ensure_boolean_modifier(
                    target_obj,
                    foot_obj,
                    f"SkinBoolean_Foot_{foot_obj.name}",
                    operation='DIFFERENCE',
                    solver='EXACT',
                )
                added_count += 1

        set_active_object(context, base_obj)
        foot_text = " Foot cuts _Base and all skins." if foot_obj is not None else " No _foot found; foot booleans skipped."
        self.report({'INFO'}, f"Added {added_count} Exact boolean modifier(s). Lower slot numbers trim higher slots.{foot_text}")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_apply_skin_booleans(Operator):
    bl_idname = "object.miniature_voxeler_apply_skin_booleans"
    bl_label = "Apply Booleans"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and (
            get_color_base_object(settings) is not None or bool(get_color_skin_objects(settings))
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        # Apply skins first so _Base is cut by final skin geometry.
        targets = get_sorted_color_skin_objects(settings) + [get_color_base_object(settings)]
        applied_count = apply_boolean_modifiers_with_prefix(context, targets, "SkinBoolean_")
        if applied_count == 0:
            self.report({'WARNING'}, "No SkinBoolean modifiers found to apply.")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Applied {applied_count} skin boolean modifier(s).")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_export_final_pieces(Operator):
    bl_idname = "object.miniature_voxeler_export_final_pieces"
    bl_label = "Export Final Pieces"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and bool(get_export_piece_objects(settings))

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        pieces = get_export_piece_objects(settings)
        if not pieces:
            self.report({'ERROR'}, "No _foot, _Base, or skin objects found to export.")
            return {'CANCELLED'}

        selected_before = [obj for obj in context.selected_objects]
        active_before = context.view_layer.objects.active
        visibility_before = [(obj, obj.hide_get(), obj.hide_viewport) for obj in pieces]

        try:
            export_dir = resolve_export_directory(settings)
            exported_paths = []
            used_names = set()

            for obj in pieces:
                obj.hide_set(False)
                obj.hide_viewport = False
                base_name = sanitize_export_filename(obj.name)
                filename = f"{base_name}.stl"
                counter = 2
                while filename.lower() in used_names:
                    filename = f"{base_name}_{counter}.stl"
                    counter += 1
                used_names.add(filename.lower())

                filepath = os.path.join(export_dir, filename)
                export_object_to_stl(context, obj, filepath, scale=1000.0)
                exported_paths.append(filepath)

            blend_copy_text = "Blend copy skipped."
            blend_path = bpy.data.filepath
            blend_filename = os.path.basename(blend_path) if blend_path else "MiniatureVoxeler_Export.blend"
            target_blend = os.path.join(export_dir, blend_filename)
            try:
                if os.path.abspath(blend_path) != os.path.abspath(target_blend):
                    bpy.ops.wm.save_as_mainfile(filepath=target_blend, copy=True)
                    blend_copy_text = "Blend copy added."
                else:
                    blend_copy_text = "Blend file already in export folder."
            except Exception as save_copy_error:
                if blend_path and os.path.isfile(blend_path) and os.path.abspath(blend_path) != os.path.abspath(target_blend):
                    shutil.copy2(blend_path, target_blend)
                    blend_copy_text = "Blend disk copy added."
                else:
                    blend_copy_text = f"Blend copy skipped: {save_copy_error}"

        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        finally:
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            for obj, hide_select_state, hide_viewport_state in visibility_before:
                if obj.name in bpy.data.objects:
                    obj.hide_set(hide_select_state)
                    obj.hide_viewport = hide_viewport_state
            for obj in selected_before:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            if active_before is not None and active_before.name in bpy.data.objects:
                context.view_layer.objects.active = active_before

        self.report({'INFO'}, f"Exported {len(exported_paths)} STL file(s) at scale 1000. {blend_copy_text}")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 9: Prepare Hole Selection Mesh
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_prepare_platform_copy(Operator):
    bl_idname = "object.miniature_voxeler_prepare_platform_copy"
    bl_label = "Prepare Hole Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_obj = get_platform_object(settings)
        return (
            building_obj is not None and
            platform_obj is not None and
            source_pair_is_validated(settings, building_obj, platform_obj)
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_obj = get_platform_object(settings)
        if building_obj is None or platform_obj is None:
            self.report({'ERROR'}, "Pick both a Building and Platform mesh first.")
            return {'CANCELLED'}
        if not source_pair_is_validated(settings, building_obj, platform_obj):
            self.report({'ERROR'}, "Apply transforms and resolve source scale warnings before creating the hole selection mesh.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        platform_copy = get_platform_copy_object(settings)
        if platform_copy is not None:
            remove_object_if_exists(platform_copy)

        new_obj = duplicate_object(context, platform_obj, get_platform_copy_name(platform_obj.name))
        set_metadata(new_obj, get_root_name(building_obj.name), platform_obj.name)
        building_obj.hide_set(True)
        platform_obj.hide_set(True)
        new_obj.hide_set(False)
        set_active_object(context, new_obj)

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.dissolve_limited(angle_limit=settings.platform_limited_dissolve_angle)
        bpy.ops.object.mode_set(mode='OBJECT')

        self.report({'INFO'}, f"Created {new_obj.name}, hid source objects, and applied Limited Dissolve.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 10: Select and Separate Platform Hole Walls
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_prepare_platform_walls_selection(Operator):
    bl_idname = "object.mv_platform_walls_select"
    bl_label = "Select Hole Wall Faces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_copy_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_copy_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 1.1 first so the hole selection mesh exists.")
            return {'CANCELLED'}

        # Step 1.2 begins in Face select because the existing hole walls are already real faces.
        set_active_object(context, obj)
        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        self.report({'INFO'}, f"Select the hole wall faces on {obj.name}, then run Separate Hole Walls.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_separate_platform_walls(Operator):
    bl_idname = "object.mv_platform_walls_separate"
    bl_label = "Separate Hole Walls"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_copy_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_copy = get_platform_copy_object(settings)
        if building_obj is None or platform_copy is None:
            self.report({'ERROR'}, "Run Step 1.1 first so the hole selection mesh exists.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, platform_copy)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != platform_copy:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, platform_copy)
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(platform_copy.data)
        bm.faces.ensure_lookup_table()
        selected_count = sum(1 for face in bm.faces if face.select)
        if selected_count == 0:
            self.report({'ERROR'}, "Select at least one face in the hole selection mesh before separating.")
            return {'CANCELLED'}

        platform_obj = get_platform_object(settings)
        platform_name = platform_obj.name if platform_obj is not None else "Platform"
        existing_walls = get_platform_walls_object(settings)
        if existing_walls is not None:
            remove_object_if_exists(existing_walls)

        # Separate selected faces into the rings object, but keep the hole selection mesh alive for Step 1.3 edge selection.
        pre_names = set(obj.name for obj in bpy.data.objects)
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')

        new_obj = find_new_object(pre_names, platform_copy, context)
        if new_obj is None:
            self.report({'ERROR'}, "Could not identify the separated rings object.")
            return {'CANCELLED'}

        new_obj.name = get_platform_walls_name(platform_name)
        set_metadata(new_obj, get_root_name(building_obj.name), platform_copy.name)
        set_active_object(context, platform_copy)

        self.report({'INFO'}, f"Separated {selected_count} wall face(s) into {new_obj.name}. Hole selection mesh is still available for missing-wall edge selection.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_prepare_platform_missing_walls_selection(Operator):
    bl_idname = "object.mv_platform_missing_walls_select"
    bl_label = "Select Missing Wall Edges"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_copy_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_copy_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Create the hole selection mesh first.")
            return {'CANCELLED'}

        set_active_object(context, obj)
        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')
        bpy.ops.mesh.select_all(action='DESELECT')
        self.report({'INFO'}, f"Select platform boundary edges on {obj.name}, then run Separate Missing Wall Edges.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_separate_platform_missing_walls(Operator):
    bl_idname = "object.mv_platform_missing_walls_separate"
    bl_label = "Separate Missing Wall Edges"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_copy_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_copy = get_platform_copy_object(settings)
        platform_obj = get_platform_object(settings)
        if building_obj is None or platform_copy is None or platform_obj is None:
            self.report({'ERROR'}, "Pick sources and create the hole selection mesh first.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, platform_copy)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != platform_copy:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, platform_copy)
            bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.mesh.select_mode(type='EDGE')
        bm = bmesh.from_edit_mesh(platform_copy.data)
        bm.edges.ensure_lookup_table()
        selected_count = sum(1 for edge in bm.edges if edge.select)
        if selected_count == 0:
            self.report({'ERROR'}, "Select at least one platform edge for the missing wall path.")
            return {'CANCELLED'}

        # Only one missing-wall edge object should represent the current repair pass.
        existing_missing = get_platform_missing_walls_object(settings)
        if existing_missing is not None:
            remove_object_if_exists(existing_missing)

        # The selected platform edges are separated as their own temporary mesh for Step 1.3.
        pre_names = set(obj.name for obj in bpy.data.objects)
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')

        new_obj = find_new_object(pre_names, platform_copy, context)
        if new_obj is None:
            self.report({'ERROR'}, "Could not identify the separated missing-wall edge object.")
            return {'CANCELLED'}

        new_obj.name = get_platform_missing_walls_name(platform_obj.name)
        set_metadata(new_obj, get_root_name(building_obj.name), platform_copy.name)
        set_active_object(context, new_obj)

        self.report({'INFO'}, f"Separated {selected_count} missing-wall edge(s) into {new_obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_build_platform_missing_walls(Operator):
    bl_idname = "object.mv_platform_missing_walls_build"
    bl_label = "Build Missing Wall Faces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return (
            settings is not None and
            get_platform_walls_object(settings) is not None and
            get_platform_missing_walls_object(settings) is not None
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        missing_obj = get_platform_missing_walls_object(settings)
        platform_copy = get_platform_copy_object(settings)
        if cutter_obj is None or missing_obj is None:
            self.report({'ERROR'}, "Separate hole wall faces and missing wall edges first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Convert the separated edge path into vertical wall faces and append them to the cutter.
        face_count = build_missing_wall_faces_from_edge_object(cutter_obj, missing_obj)
        if face_count == 0:
            self.report({'ERROR'}, "Could not build missing wall faces from the separated edge object.")
            return {'CANCELLED'}

        # Once the missing walls are baked into the cutter, the temporary selection meshes are no longer needed.
        remove_object_if_exists(missing_obj)
        remove_object_if_exists(platform_copy)
        set_active_object(context, cutter_obj)
        self.report({'INFO'}, f"Built {face_count} missing wall face(s) into {cutter_obj.name} and removed temporary platform objects.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_add_vertex_to_rings(Operator):
    bl_idname = "object.mv_platform_add_vertex_to_rings"
    bl_label = "Add Vertex To Rings"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        rings_obj = get_platform_walls_object(settings)
        source_obj, world_coords = get_selected_vertex_world_coords(context)
        if rings_obj is None:
            self.report({'ERROR'}, "Separate hole walls first so the rings object exists.")
            return {'CANCELLED'}
        if source_obj is None or not world_coords:
            self.report({'ERROR'}, "Select one or more source vertices to copy into the rings object.")
            return {'CANCELLED'}

        added_count = append_world_vertices_to_object(rings_obj, world_coords)
        set_active_object(context, rings_obj)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='VERT')
        self.report({'INFO'}, f"Copied {added_count} selected vertex/vertices from {source_obj.name} into {rings_obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_bridge_rings_vertices(Operator):
    bl_idname = "object.mv_platform_bridge_rings_vertices"
    bl_label = "Bridge Rings Vertices"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        rings_obj = get_platform_walls_object(settings)
        if rings_obj is None:
            self.report({'ERROR'}, "Separate hole walls first so the rings object exists.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH' or context.edit_object != rings_obj:
            set_active_object(context, rings_obj)
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='VERT')

        max_segment_length = mm_to_scene_units(context, settings.platform_bridge_vertex_distance_mm)
        bridge_result = bridge_selected_vertices_on_object(rings_obj, max_segment_length)
        if bridge_result is None:
            self.report({'ERROR'}, "Select two vertices, or an even number of vertices, on the rings object.")
            return {'CANCELLED'}
        created_count, created_vertex_count = bridge_result
        self.report({'INFO'}, f"Created {created_count} bridge edge(s) and {created_vertex_count} new vertex/vertices on {rings_obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_select_platform_upper_ring(Operator):
    bl_idname = "object.mv_platform_select_upper_ring"
    bl_label = "Select Upper Ring"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        rings_obj = get_platform_walls_object(settings)
        if rings_obj is None:
            self.report({'ERROR'}, "Separate hole walls first so the rings object exists.")
            return {'CANCELLED'}

        hole_selection_obj = get_platform_copy_object(settings)
        if hole_selection_obj is not None:
            hole_selection_obj.hide_set(True)

        set_active_object(context, rings_obj)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')
        select_lasso_tool()
        enable_view3d_xray(context)
        self.report({'INFO'}, f"{rings_obj.name} is ready for lasso-selecting upper ring edges with X-Ray enabled.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_isolate_platform_upper_ring(Operator):
    bl_idname = "object.mv_platform_isolate_upper_ring"
    bl_label = "Isolate Upper Ring"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        rings_obj = get_platform_walls_object(settings)
        if rings_obj is None:
            self.report({'ERROR'}, "Separate hole walls first so the rings object exists.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH' or context.edit_object != rings_obj:
            set_active_object(context, rings_obj)
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')

        vert_count, edge_count = isolate_selected_edges_to_object(rings_obj)
        if edge_count == 0:
            self.report({'ERROR'}, "Select upper ring edges before isolating.")
            return {'CANCELLED'}

        hole_selection_obj = get_platform_copy_object(settings)
        removed_hole_selection = hole_selection_obj is not None
        remove_object_if_exists(hole_selection_obj)

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')
        cleanup_text = " Removed HoleSelection." if removed_hole_selection else ""
        self.report({'INFO'}, f"Isolated upper ring selection: {edge_count} edge(s), {vert_count} vertex/vertices remain.{cleanup_text}")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 11: Clean Platform Cutter
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_merge_platform_walls_by_distance(Operator):
    bl_idname = "object.mv_platform_walls_merge"
    bl_label = "Merge By Distance"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        if settings is None:
            return False
        return (
            get_platform_walls_object(settings) is not None or
            get_platform_foot_object(settings) is not None or
            get_platform_building_cutter_object(settings) is not None
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        walls_obj = get_platform_walls_object(settings)
        foot_obj = get_platform_foot_object(settings)
        building_cutter_obj = get_platform_building_cutter_object(settings)
        active_obj = context.edit_object if context.mode == 'EDIT_MESH' else context.object
        obj = active_obj if active_obj in {walls_obj, foot_obj, building_cutter_obj} else walls_obj
        if obj is None:
            self.report({'ERROR'}, "Create the rings, 2D cutter, or foot mesh first.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != obj:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.mesh.select_mode(type='VERT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.remove_doubles(
            threshold=settings.platform_merge_distance,
            use_unselected=False,
            use_sharp_edge_from_normals=False,
        )
        clear_stored_platform_rings_data(obj)

        self.report({'INFO'}, f"Ran Merge by Distance on {obj.name} with {settings.platform_merge_distance:.6f}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_merge_platform_walls_at_center(Operator):
    bl_idname = "object.mv_platform_walls_merge_center"
    bl_label = "Merge At Center"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        active_obj = context.edit_object if context.mode == 'EDIT_MESH' else context.object
        if active_obj == obj:
            obj = active_obj
        if obj is None:
            self.report({'ERROR'}, "Create the rings object first.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != obj:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.mesh.select_mode(type='VERT')
        bm = bmesh.from_edit_mesh(obj.data)
        selected_count = sum(1 for vert in bm.verts if vert.select)
        if selected_count < 2:
            self.report({'ERROR'}, "Select at least two vertices to merge at center.")
            return {'CANCELLED'}

        bpy.ops.mesh.merge(type='CENTER')
        bpy.ops.mesh.select_all(action='DESELECT')
        clear_stored_platform_rings_data(obj)
        self.report({'INFO'}, f"Merged {selected_count} selected vertices at center on {obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_cleanup_platform_limited_dissolve(Operator):
    bl_idname = "object.mv_platform_cleanup_limited_dissolve"
    bl_label = "Limited Dissolve"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        active_obj = context.edit_object if context.mode == 'EDIT_MESH' else context.object
        if active_obj == obj:
            obj = active_obj
        if obj is None:
            self.report({'ERROR'}, "Create the rings object first.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != obj:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.mesh.select_mode(type='VERT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.dissolve_limited(angle_limit=settings.platform_cleanup_dissolve_angle)
        bpy.ops.mesh.select_all(action='DESELECT')
        clear_stored_platform_rings_data(obj)

        angle_deg = settings.platform_cleanup_dissolve_angle * 180.0 / pi
        self.report({'INFO'}, f"Ran Limited Dissolve on {obj.name} at {angle_deg:.3f} degrees.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 12: Store Platform Rings
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_prepare_platform_wall_loop_selection(Operator):
    bl_idname = "object.mv_platform_wall_loop"
    bl_label = "Store Rings"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        if settings is None:
            return False
        obj = get_platform_walls_object(settings)
        return obj is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Separate hole walls first so the rings object exists.")
            return {'CANCELLED'}
        if context.mode != 'EDIT_MESH':
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != obj:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')

        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        selected_edges = [edge for edge in bm.edges if edge.select]
        using_all_edges = not selected_edges
        if using_all_edges:
            selected_edges = list(bm.edges)
        elif len(selected_edges) == 1:
            select_connected_edge_loops_from_seeds(obj)
            bm = bmesh.from_edit_mesh(obj.data)
            bm.edges.ensure_lookup_table()
            selected_edges = [edge for edge in bm.edges if edge.select]

        selected_edge_indices = [edge.index for edge in selected_edges]
        if not selected_edge_indices:
            self.report({'ERROR'}, "Store Rings needs selected edges or an isolated rings mesh with edges.")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='OBJECT')
        rings, bridged_count, messages = resolve_selected_edge_rings(
            obj,
            selected_edge_indices,
            settings.platform_ring_gap_tolerance,
        )
        used_relaxed_selection = False
        if not rings:
            rings = resolve_selected_edge_rings_relaxed(obj, selected_edge_indices)
            used_relaxed_selection = bool(rings)
        if not rings:
            detail = messages[0] if messages else "selected edges do not form a usable boundary"
            self.report({'ERROR'}, f"Could not resolve ring edge(s): {detail}. Isolate the upper ring or select the full loop manually, then run Store Rings.")
            return {'CANCELLED'}

        store_platform_rings_data(obj, rings)
        set_mesh_to_rings(obj, rings)
        enter_edit_vertex_wireframe(context, obj)

        vertex_count = sum(len(coords) for coords in rings)
        gap_text = f", stitched {bridged_count} gap(s)" if bridged_count else ""
        relaxed_text = ", used all isolated ring edges" if using_all_edges else (", used manual edge selection" if used_relaxed_selection else "")
        self.report({'INFO'}, f"Stored {len(rings)} upper ring(s) ({vertex_count} vertices{gap_text}{relaxed_text}) and deleted all other wall geometry.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_select_platform_ring_loops(Operator):
    bl_idname = "object.mv_platform_select_ring_loops"
    bl_label = "Select Loops"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Separate or isolate rings first.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH' or context.edit_object != obj:
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')

        selected_count = select_connected_edge_loops_from_seeds(obj)
        if selected_count == 0:
            select_lasso_tool()
            enable_view3d_xray(context)
            self.report({'INFO'}, f"{obj.name} is in edge mode. Select seed edges or full loops, then run Store Rings.")
            return {'FINISHED'}

        self.report({'INFO'}, f"Expanded selected seed edges to {selected_count} connected ring edge(s).")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_extrude_platform_walls_up(Operator):
    bl_idname = "object.mv_platform_walls_extrude"
    bl_label = "Build 2D Cutter"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Store the upper ring first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        rings, bridged_count, messages = resolve_all_edge_rings(obj, 0.0)
        if not rings and settings.platform_ring_gap_tolerance > 0.0:
            rings, bridged_count, messages = resolve_all_edge_rings(obj, settings.platform_ring_gap_tolerance)
        if not rings:
            stored_rings, _ = get_stored_platform_rings_data(obj)
            stored_rings = [coords for coords in stored_rings if len(coords) >= 3]
            if stored_rings and not obj.data.edges:
                rings = stored_rings
            else:
                detail = messages[0] if messages else "current ring edges do not form a closed boundary"
                self.report({'ERROR'}, f"Could not build cutter from the current Rings mesh: {detail}. Repair or isolate the upper ring, then run Build 2D Cutter again.")
                return {'CANCELLED'}

        store_platform_rings_data(obj, rings)

        count = build_2d_cutter_mesh_from_rings(
            obj,
            rings,
            mm_to_scene_units(context, settings.platform_inner_thickness_mm),
            mm_to_scene_units(context, settings.platform_outer_thickness_mm),
        )
        obj["mv_platform_stage"] = "building_cutter_2d_open"
        enter_edit_vertex_wireframe(context, obj)

        gap_text = f" Stitched {bridged_count} small gap(s)." if bridged_count else ""
        self.report({'INFO'}, f"Built editable 2D cutter from current Rings mesh: {len(rings)} ring(s), {count} vertices total.{gap_text} Inspect and clean it before closing.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_build_platform_building_cutter_2d(Operator):
    bl_idname = "object.mv_platform_cutter_close_2d"
    bl_label = "Beauty Fill Selected Loops"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Build the 2D cutter from the rings object first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            set_active_object(context, cutter_obj)

        closed_count, triangle_count = close_2d_cutter_inner_loop(context, cutter_obj)
        if closed_count == 0:
            self.report({'ERROR'}, "Select one or more full inner boundary loops first.")
            return {'CANCELLED'}

        cutter_obj["mv_platform_stage"] = "building_cutter_2d_closed"

        self.report({'INFO'}, f"Beauty Fill completed: {triangle_count} triangle(s) from {closed_count} selected loop vertices.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_select_platform_fill_loops(Operator):
    bl_idname = "object.mv_platform_fill_loops_select"
    bl_label = "Select Inner Loops"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Build the 2D cutter from the rings object first.")
            return {'CANCELLED'}

        set_active_object(context, cutter_obj)
        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')
        bpy.ops.mesh.select_all(action='DESELECT')
        self.report({'INFO'}, "Select every inner boundary loop to fill, then run Beauty Fill Selected Loops.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_subdivide_platform_fill(Operator):
    bl_idname = "object.mv_platform_fill_subdivide"
    bl_label = "Subdivide Fill"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Close the 2D cutter first.")
            return {'CANCELLED'}

        selected_count = select_platform_fill_faces(context, cutter_obj)
        if selected_count == 0:
            self.report({'ERROR'}, "No Beauty Fill faces are tagged. Run Beauty Fill Selected Loops first.")
            return {'CANCELLED'}

        bpy.ops.mesh.subdivide(number_cuts=settings.platform_fill_subdivide_cuts, smoothness=0.0)
        bpy.ops.object.mode_set(mode='OBJECT')
        tagged_count = update_platform_fill_tag_from_selected_faces(cutter_obj)
        select_platform_fill_faces(context, cutter_obj)

        cutter_obj["mv_platform_stage"] = "building_cutter_2d_fill_subdivided"
        self.report({'INFO'}, f"Subdivided {selected_count} Beauty Fill face(s) with {settings.platform_fill_subdivide_cuts} cut(s); {tagged_count} fill face(s) are selected for sculpt smoothing.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_prepare_platform_sculpting(Operator):
    bl_idname = "object.mv_platform_prepare_sculpting"
    bl_label = "Prepare for Sculpting"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Close and subdivide the 2D cutter fill first.")
            return {'CANCELLED'}

        fill_count = select_platform_fill_faces(context, cutter_obj)
        if fill_count == 0:
            self.report({'ERROR'}, "No Beauty Fill faces are tagged. Run Beauty Fill and Subdivide Fill first.")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='OBJECT')
        assign_sculpt_face_sets_for_platform_fill(cutter_obj)

        select_platform_fill_faces(context, cutter_obj)
        bpy.ops.object.mode_set(mode='SCULPT')
        create_sculpt_face_set_from_current_selection(context)

        select_platform_fill_faces(context, cutter_obj, invert=True)
        bpy.ops.object.mode_set(mode='SCULPT')
        create_sculpt_face_set_from_current_selection(context)

        select_platform_fill_faces(context, cutter_obj, invert=True)
        bpy.ops.mesh.hide(unselected=False)
        bpy.ops.object.mode_set(mode='SCULPT')
        enable_sculpt_face_set_automasking(context)

        cutter_obj["mv_platform_stage"] = "building_cutter_2d_sculpt_ready"
        self.report({'INFO'}, f"Sculpting ready on {cutter_obj.name}: flat rings hidden, Face Set automasking enabled where available.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_select_platform_sculpt_brush(Operator):
    bl_idname = "object.mv_platform_select_sculpt_brush"
    bl_label = "Select Sculpt Brush"
    bl_options = {'REGISTER', 'UNDO'}

    brush_type: EnumProperty(
        name="Brush",
        items=[
            ('SMOOTH', "Smooth", "Use the Smooth sculpt brush"),
            ('GRAB', "Grab", "Use the Grab sculpt brush"),
            ('FLATTEN_CONTRAST', "Flatten/Contrast", "Use the Flatten/Contrast sculpt brush"),
            ('RELAX_PINCH', "Relax Pinch", "Use the Relax Pinch sculpt brush"),
        ],
        default='SMOOTH',
    )

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_platform_walls_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Prepare the sculpting mesh first.")
            return {'CANCELLED'}

        set_active_object(context, cutter_obj)
        selected = select_sculpt_brush_tool(context, self.brush_type)
        enable_sculpt_face_set_automasking(context)

        brush_label = SCULPT_BRUSH_LABELS.get(self.brush_type, "Sculpt")
        if not selected:
            self.report({'WARNING'}, f"{brush_label} tool id was not found, but Sculpt Mode and Face Set automasking are active.")
            return {'FINISHED'}

        self.report({'INFO'}, f"{brush_label} brush selected with Face Set automasking enabled.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 13: Build Thick Meshes
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_build_platform_walls_mesh(Operator):
    bl_idname = "object.mv_platform_walls_build_mesh"
    bl_label = "Extrude Cutter"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Build and close the 2D cutter from the rings object first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        face_count = extrude_mesh_down_from_faces(
            cutter_obj,
            mm_to_scene_units(context, settings.platform_cutter_depth_mm),
        )
        if face_count == 0:
            self.report({'ERROR'}, "The cutter needs closed 2D faces before extrusion.")
            return {'CANCELLED'}

        recalculate_normals_outside(context, cutter_obj)
        cutter_obj["mv_platform_stage"] = "building_cutter_3d"
        set_active_object(context, cutter_obj)
        self.report({'INFO'}, f"Extruded {cutter_obj.name} downward by {settings.platform_cutter_depth_mm:.3f} mm.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_smooth_remesh_platform_cutter(Operator):
    bl_idname = "object.mv_platform_cutter_smooth_remesh"
    bl_label = "Smooth Remesh Cutter"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        cutter_obj = get_platform_walls_object(settings)
        if cutter_obj is None:
            self.report({'ERROR'}, "Build the cutter from the rings object first.")
            return {'CANCELLED'}

        remeshed = apply_smooth_remesh_modifier(
            context,
            cutter_obj,
            octree_depth=settings.platform_remesh_octree_depth,
            scale=settings.platform_remesh_scale,
            remove_disconnected=settings.platform_remesh_remove_disconnected,
        )
        if not remeshed:
            self.report({'ERROR'}, "Could not apply Smooth Remesh to the cutter.")
            return {'CANCELLED'}

        cutter_obj["mv_platform_stage"] = "building_cutter_3d_remeshed"
        set_active_object(context, cutter_obj)
        self.report({'INFO'}, f"Applied Smooth Remesh to {cutter_obj.name} at Octree {settings.platform_remesh_octree_depth}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_build_platform_foot_mesh(Operator):
    bl_idname = "object.mv_platform_building_slice"
    bl_label = "Slice Building"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return (
            settings is not None and
            get_building_object(settings) is not None and
            get_platform_walls_object(settings) is not None
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        cutter_obj = get_platform_walls_object(settings)
        if building_obj is None or cutter_obj is None:
            self.report({'ERROR'}, "Pick a building and extrude the rings object into a cutter first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        existing_foot = get_platform_foot_object(settings)
        if existing_foot is not None:
            remove_object_if_exists(existing_foot)
        root_name = get_root_name(building_obj.name)
        copy_name = get_building_copy_name(root_name)
        existing_copy = get_building_copy_object(settings)
        if existing_copy is not None:
            remove_object_if_exists(existing_copy)

        working_building_obj = duplicate_object(context, building_obj, copy_name)
        set_metadata(working_building_obj, root_name, building_obj.name)
        working_building_obj.hide_set(False)
        building_obj.hide_set(True)

        foot_obj = duplicate_object(context, working_building_obj, get_platform_foot_name(root_name))
        foot_obj.data.name = foot_obj.name
        set_metadata(foot_obj, root_name, building_obj.name)

        cutter_obj.hide_set(False)

        set_active_object(context, foot_obj)
        foot_mod = foot_obj.modifiers.new(name=f"SliceFoot_{cutter_obj.name}", type='BOOLEAN')
        foot_mod.operation = 'INTERSECT'
        foot_mod.object = cutter_obj
        if hasattr(foot_mod, "solver"):
            foot_mod.solver = 'EXACT'
        if hasattr(foot_mod, "use_self"):
            foot_mod.use_self = settings.platform_slice_boolean_self_intersect
        if hasattr(foot_mod, "use_hole_tolerant"):
            foot_mod.use_hole_tolerant = settings.platform_slice_boolean_holes
        bpy.ops.object.modifier_apply(modifier=foot_mod.name)

        set_active_object(context, working_building_obj)
        copy_mod = working_building_obj.modifiers.new(name=f"SliceBody_{cutter_obj.name}", type='BOOLEAN')
        copy_mod.operation = 'DIFFERENCE'
        copy_mod.object = cutter_obj
        if hasattr(copy_mod, "solver"):
            copy_mod.solver = 'EXACT'
        if hasattr(copy_mod, "use_self"):
            copy_mod.use_self = settings.platform_slice_boolean_self_intersect
        if hasattr(copy_mod, "use_hole_tolerant"):
            copy_mod.use_hole_tolerant = settings.platform_slice_boolean_holes
        bpy.ops.object.modifier_apply(modifier=copy_mod.name)

        foot_obj["mv_platform_stage"] = "foot_from_building_slice"
        working_building_obj["mv_platform_stage"] = "building_body_sliced"
        set_active_object(context, foot_obj)
        cutter_name = cutter_obj.name
        remove_object_if_exists(cutter_obj)

        self.report({'INFO'}, f"Sliced {working_building_obj.name}; original building is untouched, the foot is {foot_obj.name}, and temporary rings object {cutter_name} was deleted.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_remove_voxel_xy_wall_layers(Operator):
    bl_idname = "object.mv_voxel_xy_wall_layers_remove"
    bl_label = "Remove XY Wall Layers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_blocks_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        blocks_obj = get_blocks_object(settings)
        if blocks_obj is None:
            self.report({'ERROR'}, "Run Voxel Building first so the _Blocks object exists.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        removed_count = remove_xy_voxel_wall_layers(blocks_obj, settings.voxel_xy_wall_layers)
        if removed_count is None:
            self.report({'ERROR'}, "The voxel object has no stored voxel-cell data. Run Voxel Building again.")
            return {'CANCELLED'}

        set_active_object(context, blocks_obj)
        self.report({'INFO'}, f"Removed {removed_count} XY side-wall voxel cells from {blocks_obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_connect_voxels_to_foot(Operator):
    bl_idname = "object.mv_voxel_connect_to_foot"
    bl_label = "Connect With Foot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        return settings is not None and get_blocks_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        blocks_obj = get_blocks_object(settings)
        if blocks_obj is None:
            self.report({'ERROR'}, "Run Voxel Building first so the _Blocks object exists.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        added_count = add_voxel_layer_under_lowest_cells(blocks_obj)
        if added_count is None:
            self.report({'ERROR'}, "The voxel object has no stored voxel-cell data. Run Voxel Building again.")
            return {'CANCELLED'}

        set_active_object(context, blocks_obj)
        self.report({'INFO'}, f"Added {added_count} voxel cells under the lowest layer of {blocks_obj.name}.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Panel
# ------------------------------------------------------------

class MINIATUREVOXELER_PT_panel(Panel):
    bl_label = "Miniature Voxeler"
    bl_idname = "MINIATUREVOXELER_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Miniature Voxeler"

    def draw_path_divider(self, layout, settings, role, expanded_property):
        style = get_workflow_role_style(role)
        is_expanded = getattr(settings, expanded_property)
        layout.separator()
        row = layout.row(align=True)
        row.prop(
            settings,
            expanded_property,
            text=style["divider"],
            icon='TRIA_DOWN' if is_expanded else 'TRIA_RIGHT',
            emboss=False,
        )
        layout.separator()
        return is_expanded

    def draw_step_box(self, layout, role, title, description=None):
        style = get_workflow_role_style(role)
        box = layout.box()
        header = box.row(align=True)
        header.label(text=title, icon=style["icon"])
        if description:
            box.label(text=description)
        return box

    def draw_cleanup_toolbox(self, layout, settings, role, title, description):
        box = self.draw_step_box(layout, role, title, description)
        col = box.column(align=True)
        col.prop(settings, "platform_merge_distance")
        row = col.row(align=True)
        row.operator("object.mv_platform_walls_merge", text="Merge By Distance", icon='AUTOMERGE_ON')
        row.operator("object.mv_platform_walls_merge_center", text="Merge At Center", icon='PIVOT_MEDIAN')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_obj = get_platform_object(settings)
        blocks_obj = get_blocks_object(settings)
        walls_obj = get_platform_walls_object(settings)
        foot_obj = get_platform_foot_object(settings)

        header = layout.row()
        header.label(text="Miniature Voxeler")
        header.label(text=ADDON_VERSION_TEXT)

        # Step 0 identifies the original meshes that drive both workflows.
        box = self.draw_step_box(layout, 'SOURCE', "0. Source", "Choose the building and platform meshes.")
        col = box.column(align=True)
        col.prop(settings, "building_object")
        col.prop(settings, "platform_object")
        box.operator("object.miniature_voxeler_apply_all_transforms", text="Apply Transforms To Sources", icon='OBJECT_DATA')
        if building_obj is None and platform_obj is None:
            box.label(text="Choose a Building mesh and Platform mesh first.")
        elif building_obj is None:
            box.label(text="Choose a Building mesh first.")
        elif platform_obj is None:
            box.label(text="Choose a Platform mesh first.")
        elif not source_pair_is_validated(settings, building_obj, platform_obj):
            box.label(text="Apply transforms and resolve scale warnings before continuing.")
        else:
            box.label(text="Building and Platform are ready.")

        # Platform path steps are grouped together because they prepare the hole, cutter, and foot.
        if self.draw_path_divider(layout, settings, 'PLATFORM', "show_platform_steps"):
            # Step 1.1 starts the platform branch by making a disposable copy for hole selection.
            box = self.draw_step_box(layout, 'PLATFORM', "1.1 Platform Footprint", "Use this only if you need the platform hole and print foot.")
            col = box.column(align=True)
            col.prop(settings, "platform_limited_dissolve_angle")
            col.operator("object.miniature_voxeler_prepare_platform_copy", text="Create Hole Selection Mesh", icon='DUPLICATE')

            # Step 1.2 extracts the existing vertical wall faces from the clean hole selection mesh.
            box = self.draw_step_box(layout, 'PLATFORM', "1.2 Hole Walls (Faces)", "Select the visible hole wall faces, then separate them.")
            row = box.row(align=True)
            row.operator("object.mv_platform_walls_select", text="Select Hole Faces", icon='FACESEL')
            row.operator("object.mv_platform_walls_separate", text="Separate Hole Walls", icon='MESH_CUBE')

            # Step 1.3 manually repairs missing ring vertices and bridge edges.
            box = self.draw_step_box(layout, 'PLATFORM', "1.3 Repair Rings")
            box.prop(settings, "platform_bridge_vertex_distance_mm")
            repair_row = box.row(align=True)
            repair_row.operator("object.mv_platform_add_vertex_to_rings", text="Add Vertex To Rings", icon='VERTEXSEL')
            repair_row.operator("object.mv_platform_bridge_rings_vertices", text="Bridge Rings Vertices", icon='EDGESEL')

            # Step 1.4 makes the upper ring selection a manual lasso/isolate pass.
            box = self.draw_step_box(layout, 'PLATFORM', "1.4 Select Upper Ring")
            ring_row = box.row(align=True)
            ring_row.operator("object.mv_platform_select_upper_ring", text="Select Upper Ring", icon='SELECT_SET')
            ring_row.operator("object.mv_platform_isolate_upper_ring", text="Isolate Upper Ring", icon='SELECT_INTERSECT')

            self.draw_cleanup_toolbox(
                layout,
                settings,
                'PLATFORM',
                "1.5 Clean Rings",
                "Clean the isolated rings mesh before storing the loops.",
            )

            # Step 1.6 expands the current isolated rings into an editable 2D cutter guide.
            box = self.draw_step_box(layout, 'PLATFORM', "1.6 Build 2D Cutter")
            cutter_col = box.column(align=True)
            cutter_col.prop(settings, "platform_inner_thickness_mm")
            cutter_col.prop(settings, "platform_outer_thickness_mm")
            cutter_col.operator("object.mv_platform_walls_extrude", text="Build 2D Cutter", icon='MESH_GRID')

            self.draw_cleanup_toolbox(
                layout,
                settings,
                'PLATFORM',
                "1.7 Clean 2D Cutter",
                "Visually inspect the cutter for overlaps before continuing.",
            )

            # Step 1.8 closes the 2D cutter so it can become solid geometry.
            box = self.draw_step_box(layout, 'PLATFORM', "1.8 Close 2D Cutter")
            box.label(text="Select each inner boundary loop, then fill the selected loops.")
            row = box.row(align=True)
            row.operator("object.mv_platform_fill_loops_select", text="Select Inner Loops", icon='EDGESEL')
            row.operator("object.mv_platform_cutter_close_2d", text="Beauty Fill Selected Loops", icon='FACESEL')
            box.prop(settings, "platform_fill_subdivide_cuts")
            box.operator("object.mv_platform_fill_subdivide", text="Subdivide Fill", icon='MOD_SUBSURF')

            # Step 1.9 prepares protected sculpting on the editable center and inner-band faces.
            box = self.draw_step_box(layout, 'PLATFORM', "1.9 Sculpt")
            box.operator("object.mv_platform_prepare_sculpting", text="Prepare for Sculpting", icon='SCULPTMODE_HLT')
            row = box.row(align=True)
            op = row.operator("object.mv_platform_select_sculpt_brush", text="Smooth", icon='BRUSH_DATA')
            op.brush_type = 'SMOOTH'
            op = row.operator("object.mv_platform_select_sculpt_brush", text="Grab", icon='BRUSH_DATA')
            op.brush_type = 'GRAB'
            op = row.operator("object.mv_platform_select_sculpt_brush", text="Flatten/Contrast", icon='BRUSH_DATA')
            op.brush_type = 'FLATTEN_CONTRAST'
            op = row.operator("object.mv_platform_select_sculpt_brush", text="Relax Pinch", icon='BRUSH_DATA')
            op.brush_type = 'RELAX_PINCH'

            # Step 1.10 extrudes the platform rings object downward through the building.
            box = self.draw_step_box(layout, 'PLATFORM', "1.10 Extrude Cutter")
            box.prop(settings, "platform_cutter_depth_mm")
            box.operator("object.mv_platform_walls_build_mesh", text="Extrude Cutter Down", icon='MESH_CUBE')

            # Step 1.11 remeshes the cutter to soften or repair the platform cut shape.
            box = self.draw_step_box(layout, 'PLATFORM', "1.11 Smooth Cutter")
            remesh_col = box.column(align=True)
            remesh_col.prop(settings, "platform_remesh_octree_depth")
            remesh_col.prop(settings, "platform_remesh_scale")
            remesh_col.prop(settings, "platform_remesh_remove_disconnected")
            remesh_col.operator("object.mv_platform_cutter_smooth_remesh", text="Smooth Remesh Cutter", icon='MOD_REMESH')

            # Step 1.12 uses the platform cutter to split the building body and print foot.
            box = self.draw_step_box(layout, 'PLATFORM', "1.12 Slice Building")
            slice_col = box.column(align=True)
            slice_col.alert = True
            slice_col.prop(settings, "platform_slice_boolean_self_intersect")
            slice_col.prop(settings, "platform_slice_boolean_holes")
            box.operator("object.mv_platform_building_slice", text="Slice Building And Create _foot", icon='SELECT_DIFFERENCE')
            if foot_obj is not None:
                box.label(text=f"Current foot object: {foot_obj.name}")

        # Building path steps are grouped together because they create, color, clean, and export the voxel body.
        if self.draw_path_divider(layout, settings, 'BUILDING', "show_building_steps"):
            # Step 2.1 switches back to the building branch and creates the block mesh.
            box = self.draw_step_box(layout, 'BUILDING', "2.1 Voxel Building")
            voxel_col = box.column(align=True)
            voxel_col.prop(settings, "octree_depth")
            voxel_col.prop(settings, "scale")
            voxel_col.prop(settings, "voxel_size_mm")
            voxel_col.prop(settings, "threshold")
            voxel_col.prop(settings, "remove_disconnected")
            voxel_col.operator("object.miniature_voxeler_block_remesh", text="Voxelize Building", icon='MOD_REMESH')
            if blocks_obj is None:
                box.label(text="This creates the cube grid used by the rest of the workflow.")
            else:
                box.label(text=f"Current voxel object: {blocks_obj.name}")

            # Step 2.2 trims voxel side walls from the building blocks after voxelization.
            box = self.draw_step_box(layout, 'BUILDING', "2.2 Remove XY Wall Layers")
            box.label(text="Post-voxel inset: removes exterior side cubes only.")
            box.prop(settings, "voxel_xy_wall_layers")
            box.operator("object.mv_voxel_xy_wall_layers_remove", text="Remove XY Wall Layers", icon='SELECT_SUBTRACT')

            # Step 2.3 grows the building voxels downward until they meet the platform foot.
            box = self.draw_step_box(layout, 'BUILDING', "2.3 Connect With Foot")
            box.label(text="Adds one cube under each lowest XY column per click.")
            box.operator("object.mv_voxel_connect_to_foot", text="Add One Lower Cube Layer", icon='SNAP_ON')

            # Step 2.4 bakes the building texture and turns it into fixed Lego color slots.
            box = self.draw_step_box(layout, 'BUILDING', "2.4 Texture And Color")
            box.operator("object.miniature_voxeler_smart_uv_project", text="Generate UVs", icon='UV')
            texture_col = box.column(align=True)
            texture_col.prop(settings, "texture_source_name")
            texture_col.prop(settings, "texture_size")
            texture_col.prop(settings, "texture_margin")
            box.operator("object.miniature_voxeler_transfer_texture", text="Transfer Texture", icon='TEXTURE')
            color_col = box.column(align=True)
            color_col.prop(settings, "lego_color_count")
            color_col.prop(settings, "lego_color_sample_mode")
            color_col.prop(settings, "lego_color_assign_mode")
            color_row = box.row(align=True)
            color_row.operator("object.miniature_voxeler_lego_color", text="Create Color Slots", icon='MATERIAL')
            delete_row = color_row.row(align=True)
            delete_row.alert = True
            delete_row.operator("object.miniature_voxeler_delete_lego_color_slots", text="Delete Color Slots", icon='TRASH')

            # Step 2.5 smooths material assignments before manual brush edits.
            box = self.draw_step_box(layout, 'BUILDING', "2.5 Smooth Colors")
            smooth_col = box.column(align=True)
            smooth_col.prop(settings, "lego_smooth_mode")
            smooth_col.prop(settings, "lego_smooth_passes")
            smooth_col.prop(settings, "lego_smooth_min_neighbors")
            smooth_col.prop(settings, "lego_smooth_weight")
            if settings.lego_smooth_mode == 'ISLANDS':
                smooth_col.prop(settings, "lego_smooth_max_island_faces")
            smooth_col.prop(settings, "lego_smooth_include_corners")
            smooth_col.prop(settings, "lego_smooth_protect_slot")
            box.operator("object.miniature_voxeler_smooth_lego_color", icon='MOD_SMOOTH')

            # Step 2.6 keeps face painting and cube editing in one always-available brush.
            box = self.draw_step_box(layout, 'BUILDING', "2.6 Voxel Brush Editing")
            debug_active = blocks_obj is not None and bool(blocks_obj.get("mv_debug_colors_active", False))
            box.operator(
                "object.miniature_voxeler_toggle_debug_colors",
                text="Debug Colors",
                icon='MATERIAL',
                depress=debug_active,
            )
            active_brush = MINIATUREVOXELER_OT_voxel_brush_tool._active_tool
            color_count = max(1, min(4, int(getattr(settings, "lego_color_count", 1))))
            active_slot = max(0, min(int(getattr(settings, "selected_lego_palette_slot", 0)), color_count - 1))
            is_brush_active = (
                active_brush is not None and
                not getattr(active_brush, "_cancel_requested", False) and
                active_brush.mode == 'PAINT'
            )
            is_rectangle_active = (
                active_brush is not None and
                not getattr(active_brush, "_cancel_requested", False) and
                active_brush.mode == 'BOX_PAINT'
            )
            is_lasso_active = (
                active_brush is not None and
                not getattr(active_brush, "_cancel_requested", False) and
                active_brush.mode == 'LASSO_PAINT'
            )
            tool_row = box.row(align=True)
            brush_op = tool_row.operator(
                "object.miniature_voxeler_voxel_brush_tool",
                text="Brush",
                icon='BRUSH_DATA',
                depress=is_brush_active,
            )
            brush_op.mode = 'PAINT'
            brush_op.slot_index = active_slot
            rectangle_op = tool_row.operator(
                "object.miniature_voxeler_voxel_brush_tool",
                text="Rectangle",
                icon='SELECT_SET',
                depress=is_rectangle_active,
            )
            rectangle_op.mode = 'BOX_PAINT'
            rectangle_op.slot_index = active_slot
            lasso_op = tool_row.operator(
                "object.miniature_voxeler_voxel_brush_tool",
                text="Lasso",
                icon='SELECT_INTERSECT',
                depress=is_lasso_active,
            )
            lasso_op.mode = 'LASSO_PAINT'
            lasso_op.slot_index = active_slot
            try:
                current_paint_tool_mode = getattr(settings, "lego_paint_tool_mode", 'PAINT')
                if current_paint_tool_mode == 'PAINT':
                    box.prop(settings, "lego_paint_brush_size")

                selected_col = box.column(align=True)
                for slot_index in range(color_count):
                    fixed_index = int(getattr(settings, f"lego_palette_slot_{slot_index + 1}", 0))
                    fixed_index = max(0, min(fixed_index, len(FIXED_LEGO_PALETTE) - 1))
                    row = selected_col.row(align=True)
                    swatch = row.row(align=True)
                    swatch.enabled = False
                    if debug_active:
                        swatch.prop(settings, f"debug_palette_slot_color_{slot_index + 1}", text="")
                        debug_names = ("Red", "Green", "Blue", "White")
                        row.label(text=f"Slot {slot_index + 1}: Debug {debug_names[slot_index]}")
                    else:
                        swatch.prop(settings, f"lego_palette_slot_color_{slot_index + 1}", text="")
                        row.label(text=f"Slot {slot_index + 1}: {FIXED_LEGO_PALETTE[fixed_index][0]}")
                    is_selected_slot = active_slot == slot_index
                    icon = 'RADIOBUT_ON' if is_selected_slot else 'RADIOBUT_OFF'
                    op = row.operator(
                        "object.miniature_voxeler_select_paint_slot",
                        text="",
                        icon=icon,
                        depress=is_selected_slot,
                    )
                    op.slot_index = slot_index

                box.prop(settings, "platform_foot_color_slot")

                box.label(text=f"Palette For Slot {active_slot + 1}")
                palette_grid = box.grid_flow(row_major=True, columns=4, even_columns=True, even_rows=False, align=True)
                current_index = int(getattr(settings, f"lego_palette_slot_{active_slot + 1}", 0))
                current_index = max(0, min(current_index, len(FIXED_LEGO_PALETTE) - 1))
                for palette_index, (_palette_name, _palette_color) in enumerate(FIXED_LEGO_PALETTE):
                    cell = palette_grid.column(align=True)
                    swatch_row = cell.row(align=True)
                    swatch_row.enabled = False
                    swatch_row.prop(settings, f"fixed_palette_color_{palette_index + 1}", text="")
                    op = cell.operator(
                        "object.miniature_voxeler_set_palette_color",
                        text="",
                        icon='RADIOBUT_ON' if current_index == palette_index else 'RADIOBUT_OFF',
                        depress=current_index == palette_index,
                    )
                    op.slot_index = active_slot
                    op.palette_index = palette_index

                voxel_row = box.row(align=True)
                is_modify_active = active_brush is not None and not getattr(active_brush, "_cancel_requested", False) and active_brush.mode == 'ADD'
                modify_op = voxel_row.operator("object.miniature_voxeler_voxel_brush_tool", text="Modify Cubes", icon='MOD_BUILD', depress=is_modify_active)
                modify_op.mode = 'ADD'
                modify_op.slot_index = active_slot
            except Exception as error:
                error_row = box.row(align=True)
                error_row.alert = True
                error_row.label(text=f"2.6 UI error: {error}", icon='ERROR')

            # Step 2.7 exports the colored building shell pieces for downstream use.
            box = self.draw_step_box(layout, 'BUILDING', "2.7 Prepare Skin")
            col = box.column(align=True)
            col.prop(settings, "color_skin_base_slot")
            col.prop(settings, "outer_skin_mm")
            col.prop(settings, "skin_solidify_offset")
            col.prop(settings, "skin_solidify_even_thickness")

            box.label(text="Skin Slots")
            row = box.row(align=True)
            row.prop(settings, "color_skin_slot_1")
            row.prop(settings, "color_skin_slot_2")

            row = box.row(align=True)
            row.prop(settings, "color_skin_slot_3")
            row.prop(settings, "color_skin_slot_4")

            box.operator("object.miniature_voxeler_separate_skins_solidify", text="Separate Skins and Solidify", icon='MATERIAL')
            caution_row = box.row(align=True)
            caution_row.alert = True
            caution_row.operator("object.miniature_voxeler_add_skin_booleans", text="Add Booleans (Warning, Slow!)", icon='MOD_BOOLEAN')
            apply_row = box.row(align=True)
            apply_row.alert = True
            apply_row.operator("object.miniature_voxeler_apply_skin_booleans", text="Apply Booleans (Warning, Slow!)", icon='CHECKMARK')

        if self.draw_path_divider(layout, settings, 'EXPORT', "show_export_steps"):
            box = self.draw_step_box(layout, 'EXPORT', "3. Export")
            col = box.column(align=True)
            col.prop(settings, "export_directory")
            export_pieces = get_export_piece_objects(settings)
            if export_pieces:
                box.label(text=f"Batch STL scale: 1000 | Pieces: {len(export_pieces)}")
                for obj in export_pieces:
                    box.label(text=obj.name, icon='MESH_CUBE')
            else:
                box.label(text="Prepare _foot, _Base, and skins before exporting.")
            box.operator("object.miniature_voxeler_export_final_pieces", text="Export Batch STL + Blend Copy", icon='EXPORT')

# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = (
    MINIATUREVOXELER_PG_settings,
    MINIATUREVOXELER_OT_apply_all_transforms,
    MINIATUREVOXELER_OT_block_remesh,
    MINIATUREVOXELER_OT_smart_uv_project,
    MINIATUREVOXELER_OT_transfer_texture,
    MINIATUREVOXELER_OT_lego_color,
    MINIATUREVOXELER_OT_delete_lego_color_slots,
    MINIATUREVOXELER_OT_smooth_lego_color,
    MINIATUREVOXELER_OT_paint_lego_slot,
    MINIATUREVOXELER_OT_edit_voxel_cells,
    MINIATUREVOXELER_OT_voxel_brush_tool,
    MINIATUREVOXELER_OT_select_paint_slot,
    MINIATUREVOXELER_OT_set_palette_color,
    MINIATUREVOXELER_OT_toggle_debug_colors,
    MINIATUREVOXELER_OT_separate_skins_solidify,
    MINIATUREVOXELER_OT_add_skin_booleans,
    MINIATUREVOXELER_OT_apply_skin_booleans,
    MINIATUREVOXELER_OT_export_final_pieces,
    MINIATUREVOXELER_OT_prepare_platform_copy,
    MINIATUREVOXELER_OT_prepare_platform_walls_selection,
    MINIATUREVOXELER_OT_separate_platform_walls,
    MINIATUREVOXELER_OT_prepare_platform_missing_walls_selection,
    MINIATUREVOXELER_OT_separate_platform_missing_walls,
    MINIATUREVOXELER_OT_build_platform_missing_walls,
    MINIATUREVOXELER_OT_add_vertex_to_rings,
    MINIATUREVOXELER_OT_bridge_rings_vertices,
    MINIATUREVOXELER_OT_select_platform_upper_ring,
    MINIATUREVOXELER_OT_isolate_platform_upper_ring,
    MINIATUREVOXELER_OT_merge_platform_walls_by_distance,
    MINIATUREVOXELER_OT_merge_platform_walls_at_center,
    MINIATUREVOXELER_OT_cleanup_platform_limited_dissolve,
    MINIATUREVOXELER_OT_prepare_platform_wall_loop_selection,
    MINIATUREVOXELER_OT_select_platform_ring_loops,
    MINIATUREVOXELER_OT_extrude_platform_walls_up,
    MINIATUREVOXELER_OT_build_platform_building_cutter_2d,
    MINIATUREVOXELER_OT_select_platform_fill_loops,
    MINIATUREVOXELER_OT_subdivide_platform_fill,
    MINIATUREVOXELER_OT_prepare_platform_sculpting,
    MINIATUREVOXELER_OT_select_platform_sculpt_brush,
    MINIATUREVOXELER_OT_build_platform_walls_mesh,
    MINIATUREVOXELER_OT_smooth_remesh_platform_cutter,
    MINIATUREVOXELER_OT_build_platform_foot_mesh,
    MINIATUREVOXELER_OT_remove_voxel_xy_wall_layers,
    MINIATUREVOXELER_OT_connect_voxels_to_foot,
    MINIATUREVOXELER_PT_panel,
)

stale_class_names = (
    "MINIATUREVOXELER_OT_store_platform_lower_ring",
    "MINIATUREVOXELER_OT_build_platform_walls_2d_preview",
    "MINIATUREVOXELER_OT_build_platform_building_cutter_mesh",
    "MINIATUREVOXELER_OT_add_platform_foot_boolean",
    "MINIATUREVOXELER_OT_add_platform_building_booleans",
    "MINIATUREVOXELER_OT_apply_platform_building_booleans",
    "MINIATUREVOXELER_OT_apply_platform_foot_boolean",
    "MINIATUREVOXELER_OT_inset_building_copy",
    "MINIATUREVOXELER_OT_prepare_platform_sculpt_smooth",
)

def register():
    if hasattr(bpy.types.Scene, "miniature_voxeler_settings"):
        del bpy.types.Scene.miniature_voxeler_settings
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    for class_name in stale_class_names:
        cls = globals().get(class_name)
        if cls is not None:
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass

    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.miniature_voxeler_settings = PointerProperty(type=MINIATUREVOXELER_PG_settings)


def unregister():
    del bpy.types.Scene.miniature_voxeler_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
