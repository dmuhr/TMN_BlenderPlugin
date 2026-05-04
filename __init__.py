bl_info = {
    "name": "Miniature Voxeler",
    "author": "OpenAI",
    "version": (2, 1, 7),
    "blender": (5, 0, 1),
    "location": "3D View > Sidebar > Miniature Voxeler",
    "description": "Block remesh, transfer texture, create Lego-color face materials, and generate Lego skin meshes for miniature voxel workflows",
    "category": "Object",
}

import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
from math import cos, hypot, pi, radians, sin
from bpy_extras import view3d_utils
from mathutils import Vector
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

ADDON_VERSION_TEXT = "v.2.1.7"


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


def update_palette_slot_material(settings, context, slot_index):
    color = get_fixed_palette_color(getattr(settings, f"lego_palette_slot_{slot_index + 1}"))
    setattr(settings, f"lego_palette_slot_color_{slot_index + 1}", color)

    obj = get_blocks_object(settings)
    if obj is None:
        return

    if slot_index >= len(obj.data.materials):
        return

    set_material_base_color(obj.data.materials[slot_index], color)
    obj.data.update()


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


def get_building_object(settings):
    obj = getattr(settings, "building_object", None)
    if obj is None or obj.type != 'MESH':
        return None
    return obj


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

    return get_building_object(settings)


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
    settings = getattr(context.scene, "miniature_voxeler_settings", None)
    if settings is None:
        return None, None

    obj = get_blocks_object(settings)
    if obj is None:
        return None, None

    if context.area is None or context.area.type != 'VIEW_3D':
        return None, None

    region = get_view3d_window_region(context.area)
    region_3d = getattr(context.space_data, "region_3d", None)
    if region is None or region_3d is None:
        return None, None

    mouse_x = event.mouse_x - region.x
    mouse_y = event.mouse_y - region.y

    if mouse_x < 0 or mouse_y < 0 or mouse_x > region.width or mouse_y > region.height:
        return None, None

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
        return obj, face_index

    return obj, None


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


def paint_faces_with_brush(context, event, obj, slot_index, brush_size):
    hit_obj, face_index = raycast_active_face(context, event)
    if hit_obj is None or face_index is None:
        return 0

    mesh = hit_obj.data
    radius = max(1.0, float(brush_size))
    changed_count = 0

    if radius <= 1.0:
        if mesh.polygons[face_index].material_index != slot_index:
            mesh.polygons[face_index].material_index = slot_index
            changed_count = 1
        mesh.update()
        return changed_count

    region, region_3d, mouse_coord = get_mouse_region_coord(context, event)
    if region is None:
        return 0

    radius_sq = radius * radius
    faces_to_paint = {face_index}

    for poly in mesh.polygons:
        center = hit_obj.matrix_world @ get_polygon_center(mesh, poly)
        screen_coord = view3d_utils.location_3d_to_region_2d(region, region_3d, center)
        if screen_coord is None:
            continue

        dx = screen_coord.x - mouse_coord[0]
        dy = screen_coord.y - mouse_coord[1]
        if (dx * dx) + (dy * dy) <= radius_sq:
            faces_to_paint.add(poly.index)

    for paint_face_index in faces_to_paint:
        if mesh.polygons[paint_face_index].material_index != slot_index:
            mesh.polygons[paint_face_index].material_index = slot_index
            changed_count += 1

    mesh.update()
    return changed_count


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
        description="Maximum angle used by Limited Dissolve on the platform copy",
        subtype='ANGLE',
        default=radians(5.0),
        min=0.0,
        max=pi,
    )

    texture_source_name: StringProperty(
        name="Source Override",
        description="Optional source object name for texture transfer. Leave empty to infer the original object automatically",
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

    lego_smooth_weight: FloatProperty(
        name="Smooth Weight",
        description="Higher values make neighboring face colors influence each face more strongly",
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
        description="Minimum number of neighboring faces that must support a change before a face switches color",
        default=2,
        min=1,
        max=32,
    )

    selected_lego_palette_slot: IntProperty(
        name="Selected Color",
        description="Palette color used by the paint brush",
        default=0,
        min=0,
        max=3,
    )

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
        name="Outer Skin (mm)",
        description="Outward skin thickness in millimeters",
        default=0.3,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    inset_amount: FloatProperty(
        name="Inset",
        default=0.0006,
        min=0.0,
        soft_max=1.0,
        precision=4,
    )

    inside_skin_mm: FloatProperty(
        name="Inner Skin (mm)",
        description="Inward movement in millimeters. Cannot be positive",
        default=-0.2,
        max=0.0,
        soft_min=-100.0,
        precision=3,
    )

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

    make_boolean_base: BoolProperty(
        name="Boolean-Difference base.",
        description="Apply Boolean Difference on the base object using the generated skin objects",
        default=False,
    )

    platform_merge_distance: FloatProperty(
        name="Merge Distance",
        description="Distance used when merging nearby wall vertices",
        default=0.001,
        min=0.0,
        precision=6,
        step=0.1,
        unit='LENGTH',
    )

    platform_wall_extrude_height: FloatProperty(
        name="Foot Gap",
        description="Base upward gap used to generate the platform walls and foot from the selected top edge loop",
        default=0.0008,
        min=0.0,
        precision=6,
        unit='LENGTH',
    )

    platform_walls_thickness: FloatProperty(
        name="Walls Thickness",
        description="Solidify thickness for _Platform_Walls",
        default=0.005,
        min=0.0,
        precision=6,
        unit='LENGTH',
    )

    platform_walls_offset: FloatProperty(
        name="Walls Offset",
        description="Solidify offset for _Platform_Walls",
        default=0.8,
        precision=3,
    )

    platform_foot_thickness: FloatProperty(
        name="Foot Thickness",
        description="Solidify thickness for _foot",
        default=0.003,
        min=0.0,
        precision=6,
        unit='LENGTH',
    )

    platform_foot_offset: FloatProperty(
        name="Foot Offset",
        description="Solidify offset for _foot",
        default=-1.0,
        precision=3,
    )

    platform_foot_boolean_extra_offset: FloatProperty(
        name="Foot Bool Extra",
        description="Extra offset added to the wall copy used only for the _foot boolean cutter",
        default=0.01,
        precision=3,
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
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj


def get_root_name(obj_name):
    if "_Blocks_Skin_" in obj_name:
        return obj_name.split("_Blocks_Skin_")[0]
    if "_Lego_Skin_Slot_" in obj_name:
        return obj_name.split("_Lego_Skin_Slot_")[0]
    if obj_name.endswith("_Lego_Base"):
        return obj_name[:-10]
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
    return f"{platform_name}_Platform_Copy"


def get_platform_walls_name(platform_name):
    return f"{platform_name}_Platform_Walls"


def get_platform_foot_name(root_name):
    return f"{root_name}_foot"


def get_platform_walls_copy_name(platform_name):
    return f"{platform_name}_Platform_Walls_Copy"


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
    return mod


def ensure_solidify_modifier(obj, modifier_name, thickness, offset):
    mod = obj.modifiers.get(modifier_name)
    if mod is None or mod.type != 'SOLIDIFY':
        if mod is not None:
            obj.modifiers.remove(mod)
        mod = obj.modifiers.new(name=modifier_name, type='SOLIDIFY')

    mod.thickness = thickness
    mod.offset = offset
    if hasattr(mod, "solidify_mode"):
        mod.solidify_mode = 'NON_MANIFOLD'
    if hasattr(mod, "nonmanifold_thickness_mode"):
        mod.nonmanifold_thickness_mode = 'CONSTRAINTS'
    return mod


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
    if len(seed_edges) != 2:
        return 0

    selected_count = 0
    for seed_edge in seed_edges:
        for edge in get_connected_edge_loop_from_seed(bm, seed_edge):
            edge.select = True
            selected_count += 1

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected_count


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

    return boolean_union_objects(context, axis_skin_objects, get_color_skin_name(root_name, slot_index))


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
    inside_skin_distance = mm_to_scene_units(context, settings.inside_skin_mm)
    move_vec = (
        inside_skin_distance * axis_vec.x,
        inside_skin_distance * axis_vec.y,
        inside_skin_distance * axis_vec.z,
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

    inside_skin_distance = mm_to_scene_units(context, settings.inside_skin_mm)
    bpy.ops.transform.shrink_fatten(
        value=inside_skin_distance,
        use_even_offset=True,
    )

    bpy.ops.object.mode_set(mode='OBJECT')


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


def smooth_material_assignments(mesh, weight, passes, min_neighbors):
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

    total_changed = 0

    for _ in range(passes):
        current_assignments = [poly.material_index for poly in mesh.polygons]
        new_assignments = list(current_assignments)

        for poly in mesh.polygons:
            current_index = current_assignments[poly.index]
            neighbors = list(neighbor_map[poly.index])
            if not neighbors:
                continue

            counts = {}
            for neighbor_index in neighbors:
                neighbor_material = current_assignments[neighbor_index]
                counts[neighbor_material] = counts.get(neighbor_material, 0) + 1

            current_score = counts.get(current_index, 0) * weight + len(neighbors) * (1.0 - weight)
            best_material = current_index
            best_score = current_score

            for material_index, count in counts.items():
                if count < min_neighbors:
                    continue

                score = count * weight
                if material_index == current_index:
                    score += len(neighbors) * (1.0 - weight)

                if score > best_score:
                    best_material = material_index
                    best_score = score

            if best_material != current_index:
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
        return has_building_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_building_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Pick a Building mesh first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        set_active_object(context, obj)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        self.report({'INFO'}, f"Applied all transforms to {obj.name}")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 2: Block Remesh
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_block_remesh(Operator):
    bl_idname = "object.miniature_voxeler_block_remesh"
    bl_label = "Block Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_building_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        source_obj = get_building_object(settings)
        if source_obj is None:
            self.report({'ERROR'}, "Pick a Building mesh first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        root_name = get_root_name(source_obj.name)
        new_obj = duplicate_object(context, source_obj, get_blocks_name(root_name))
        set_metadata(new_obj, root_name, source_obj.name)

        set_active_object(context, new_obj)

        remesh = new_obj.modifiers.new(name="BlockRemesh", type='REMESH')
        remesh.mode = 'BLOCKS'
        remesh.octree_depth = settings.octree_depth
        remesh.scale = settings.scale
        remesh.threshold = settings.threshold
        if hasattr(remesh, "use_remove_disconnected"):
            remesh.use_remove_disconnected = settings.remove_disconnected

        bpy.ops.object.modifier_apply(modifier=remesh.name)
        source_obj.hide_set(True)

        self.report({'INFO'}, f"Created remeshed object: {new_obj.name}")
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

        if source_obj is None:
            self.report({'ERROR'}, "Pick a Building mesh or enter a valid Source Override.")
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
        should_rehide_source = (building_obj is not None and source_obj == building_obj)
        was_source_hidden = source_obj.hide_get() if should_rehide_source else False

        try:
            if should_rehide_source:
                source_obj.hide_set(False)

            scene.render.engine = 'CYCLES'

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
            if should_rehide_source:
                source_obj.hide_set(was_source_hidden)
            scene.render.engine = old_engine
            self.report({'ERROR'}, f"Texture transfer failed: {str(e)}")
            return {'CANCELLED'}

        scene.render.engine = old_engine
        if should_rehide_source:
            source_obj.hide_set(True)
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

        self.report(
            {'INFO'},
            f"Lego Color created {len(palette)} fixed-palette material(s) from {image.name} using {settings.lego_color_assign_mode.lower()} assignment."
        )
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
            settings.lego_smooth_weight,
            settings.lego_smooth_passes,
            settings.lego_smooth_min_neighbors,
        )
        self.report({'INFO'}, f"Smooth Lego Color updated {changed_count} face assignment(s).")
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
# Operator 7: Generate Lego Skin
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_generate_color_skin(Operator):
    bl_idname = "object.miniature_voxeler_generate_color_skin"
    bl_label = "Generate Lego Skin"
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

        original_obj = body_obj
        base_obj = duplicate_object(context, original_obj, get_color_base_name(root_name))
        set_metadata(base_obj, root_name, source_name)
        base_material = ensure_lego_color_material(
            base_obj,
            0,
            get_slot_palette_color(settings, base_slot_index),
        )
        apply_single_material_to_object(base_obj, base_material)

        slot_sources = {}
        for slot_index in enabled_skin_slots:
            slot_source = duplicate_object(
                context,
                original_obj,
                f"{get_color_skin_name(root_name, slot_index)}_Source"
            )
            set_metadata(slot_source, root_name, source_name)
            slot_sources[slot_index] = slot_source

        processed = []
        skipped = []
        skin_objects = []

        for slot_index in enabled_skin_slots:
            slot_source = slot_sources.get(slot_index)
            if slot_source is None or slot_source.name not in bpy.data.objects:
                self.report({'ERROR'}, f"Source object for slot {slot_index + 1} became invalid.")
                return {'CANCELLED'}

            set_active_object(context, slot_source)
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_mode(type='FACE')
            bpy.ops.mesh.select_all(action='DESELECT')

            selected_count = select_faces_by_material_slot(slot_source, [slot_index])
            if selected_count == 0:
                bpy.ops.object.mode_set(mode='OBJECT')
                skipped.append(f"Slot {slot_index + 1}")
                remove_object_if_exists(slot_source)
                continue

            pre_names = set(obj.name for obj in bpy.data.objects)
            bpy.ops.mesh.separate(type='SELECTED')
            bpy.ops.object.mode_set(mode='OBJECT')

            new_obj = find_new_object(pre_names, slot_source, context)
            if new_obj is None:
                self.report({'ERROR'}, f"Could not identify separated skin object for slot {slot_index + 1}.")
                return {'CANCELLED'}

            slot_skin_parts = []
            new_obj.name = f"{get_color_skin_name(root_name, slot_index)}_Mask"
            set_metadata(new_obj, root_name, source_name)
            joined_skin = generate_north_south_skin_from_mask(
                context,
                new_obj,
                settings,
                root_name,
                slot_index,
                source_name,
            )
            if joined_skin is not None:
                slot_skin_parts.append(joined_skin)
            remove_object_if_exists(new_obj)

            final_slot_skin = join_objects(context, slot_skin_parts, get_color_skin_name(root_name, slot_index))
            if final_slot_skin is not None:
                set_metadata(final_slot_skin, root_name, source_name)
                skin_objects.append(final_slot_skin)

            remove_object_if_exists(slot_source)

            label = f"Slot {slot_index + 1} ({selected_count} faces)"
            processed.append(label)

        if not processed:
            for slot_source in slot_sources.values():
                remove_object_if_exists(slot_source)
            set_active_object(context, base_obj)
            self.report({'WARNING'}, "No faces found for the selected skin slots.")
            return {'CANCELLED'}

        if settings.make_boolean_base and skin_objects:
            for i, skin_obj in enumerate(skin_objects):
                apply_boolean_difference(context, base_obj, skin_obj, i)

        for slot_source in slot_sources.values():
            remove_object_if_exists(slot_source)

        set_active_object(context, base_obj)

        msg = "Processed: " + ", ".join(processed)
        if skipped:
            msg += " | Skipped: " + ", ".join(skipped)
        if settings.make_boolean_base:
            msg += " | Boolean-Difference base. applied"
        msg += " | Clean slot sources deleted"

        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 9: Prepare Platform Copy
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_prepare_platform_copy(Operator):
    bl_idname = "object.miniature_voxeler_prepare_platform_copy"
    bl_label = "Prepare Platform Copy"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_building_object(context) and get_platform_object(context.scene.miniature_voxeler_settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_obj = get_platform_object(settings)
        if building_obj is None or platform_obj is None:
            self.report({'ERROR'}, "Pick both a Building and Platform mesh first.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        platform_copy = get_platform_copy_object(settings)
        if platform_copy is not None:
            remove_object_if_exists(platform_copy)

        new_obj = duplicate_object(context, platform_obj, get_platform_copy_name(platform_obj.name))
        set_metadata(new_obj, get_root_name(building_obj.name), platform_obj.name)
        platform_obj.hide_set(True)
        new_obj.hide_set(False)
        set_active_object(context, new_obj)

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.dissolve_limited(angle_limit=settings.platform_limited_dissolve_angle)
        bpy.ops.object.mode_set(mode='OBJECT')

        self.report({'INFO'}, f"Created {new_obj.name}, hid {platform_obj.name}, and applied Limited Dissolve.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 10: Select and Separate Platform Walls
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_prepare_platform_walls_selection(Operator):
    bl_idname = "object.mv_platform_walls_select"
    bl_label = "Select Platform Walls"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_copy_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_copy_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 9 first so the platform copy exists.")
            return {'CANCELLED'}

        set_active_object(context, obj)
        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        self.report({'INFO'}, f"Select the wall faces on {obj.name}, then run Separate Selected Walls.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_separate_platform_walls(Operator):
    bl_idname = "object.mv_platform_walls_separate"
    bl_label = "Separate Selected Walls"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_copy_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        platform_copy = get_platform_copy_object(settings)
        if building_obj is None or platform_copy is None:
            self.report({'ERROR'}, "Run Step 9 first so the platform copy exists.")
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
            self.report({'ERROR'}, "Select at least one face in the platform copy before separating.")
            return {'CANCELLED'}

        platform_obj = get_platform_object(settings)
        platform_name = platform_obj.name if platform_obj is not None else "Platform"
        existing_walls = get_platform_walls_object(settings)
        if existing_walls is not None:
            remove_object_if_exists(existing_walls)

        pre_names = set(obj.name for obj in bpy.data.objects)
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')

        new_obj = find_new_object(pre_names, platform_copy, context)
        if new_obj is None:
            self.report({'ERROR'}, "Could not identify the separated platform walls object.")
            return {'CANCELLED'}

        new_obj.name = get_platform_walls_name(platform_name)
        set_metadata(new_obj, get_root_name(building_obj.name), platform_copy.name)
        remove_object_if_exists(platform_copy)
        set_active_object(context, new_obj)

        self.report({'INFO'}, f"Separated {selected_count} face(s) into {new_obj.name} and deleted the platform copy.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 11: Clean Platform Walls
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_merge_platform_walls_by_distance(Operator):
    bl_idname = "object.mv_platform_walls_merge"
    bl_label = "Merge By Distance"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 10 first so the platform walls exist.")
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

        self.report({'INFO'}, f"Ran Merge by Distance on {obj.name} with {settings.platform_merge_distance:.6f}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_select_platform_walls_non_manifold(Operator):
    bl_idname = "object.mv_platform_walls_nonmanifold"
    bl_label = "Select Non-Manifold"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 10 first so the platform walls exist.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != obj:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='EDGE')
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.mesh.select_non_manifold()
        self.report({'INFO'}, f"Non-manifold edges selected on {obj.name} for inspection.")
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 12: Enlarge Platform Walls
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_prepare_platform_wall_loop_selection(Operator):
    bl_idname = "object.mv_platform_wall_loop"
    bl_label = "Select Top Edge Loop"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = get_platform_walls_object(settings)
        if obj is None:
            self.report({'ERROR'}, "Run Step 10 first so the platform walls exist.")
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
        if len(selected_edges) != 2:
            show_info_popup(
                context,
                "Select 2 Seed Edges",
                [
                    f"On {obj.name}, select exactly one top rim edge and one lower rim edge.",
                    "The top loop defines the shape. The lower loop is used only for height.",
                    "The tube floor uses the lowest point from the lower loop.",
                    "Then press Select Wall Loops again to expand both loops.",
                ],
                icon='EDGESEL',
            )
            self.report({'WARNING'}, "Select exactly one top rim edge and one lower rim edge first, then run Select Wall Loops again.")
            return {'CANCELLED'}

        selected_count = select_connected_edge_loops_from_seeds(obj)
        if selected_count <= 2:
            self.report({'WARNING'}, f"Could not expand both edge loops on {obj.name}.")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Selected {selected_count} connected loop edge(s) on {obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_extrude_platform_walls_up(Operator):
    bl_idname = "object.mv_platform_walls_extrude"
    bl_label = "Build Foot Gap"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return has_platform_walls_object(context)

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        building_obj = get_building_object(settings)
        obj = get_platform_walls_object(settings)
        if building_obj is None or obj is None:
            self.report({'ERROR'}, "Run Step 10 first so the platform walls exist.")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')
        elif context.edit_object != obj:
            bpy.ops.object.mode_set(mode='OBJECT')
            set_active_object(context, obj)
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        selected_edges = [edge for edge in bm.edges if edge.select]
        if len(selected_edges) < 2:
            self.report({'ERROR'}, "Select the top loop and lower loop before building the foot gap.")
            return {'CANCELLED'}

        visited = set()
        loop_groups = []
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
                loop_groups.append(group)

        if len(loop_groups) != 2:
            self.report({'ERROR'}, "Build Foot Gap expects exactly two selected edge loops: one top and one lower.")
            return {'CANCELLED'}

        def get_loop_z_values(edge_group):
            values = []
            seen = set()
            for edge in edge_group:
                for vert in edge.verts:
                    if vert.index not in seen:
                        seen.add(vert.index)
                        values.append(vert.co.z)
            return values

        def average_loop_z(edge_group):
            values = get_loop_z_values(edge_group)
            return sum(values) / max(1, len(values))

        loop_groups.sort(key=average_loop_z, reverse=True)
        top_edge_indices = [edge.index for edge in loop_groups[0]]
        lower_target_z = min(get_loop_z_values(loop_groups[1]))

        bpy.ops.object.mode_set(mode='OBJECT')

        root_name = get_root_name(building_obj.name)
        existing_foot = get_platform_foot_object(settings)
        if existing_foot is not None:
            remove_object_if_exists(existing_foot)
        existing_walls_copy = get_platform_walls_copy_object(settings)
        if existing_walls_copy is not None:
            remove_object_if_exists(existing_walls_copy)

        foot_obj = duplicate_object(context, obj, get_platform_foot_name(root_name))
        set_metadata(foot_obj, root_name, obj.name)
        for mod in list(foot_obj.modifiers):
            foot_obj.modifiers.remove(mod)

        wall_up = settings.platform_wall_extrude_height
        wall_down = meters_to_scene_units(context, 0.01)
        foot_up = max(0.0, settings.platform_wall_extrude_height - meters_to_scene_units(context, 0.0002))

        wall_tube_count, wall_top_count, wall_lower_count = rebuild_tube_from_top_loop(
            obj,
            top_edge_indices,
            lower_target_z,
            wall_up,
            wall_down,
        )
        foot_tube_count, foot_top_count, _ = rebuild_tube_from_top_loop(
            foot_obj,
            top_edge_indices,
            lower_target_z,
            foot_up,
            0.0,
        )

        wall_thickness = meters_to_scene_units(context, settings.platform_walls_thickness)
        foot_thickness = meters_to_scene_units(context, settings.platform_foot_thickness)
        ensure_solidify_modifier(obj, "PlatformWallsSolidify", wall_thickness, settings.platform_walls_offset)
        ensure_solidify_modifier(foot_obj, "PlatformFootSolidify", foot_thickness, settings.platform_foot_offset)
        set_active_object(context, obj)

        self.report(
            {'INFO'},
            f"Built foot gap: {obj.name} up {wall_up:.6f} / down 0.010000, {foot_obj.name} up {foot_up:.6f}. "
            f"Tube counts: walls {wall_tube_count}, foot {foot_tube_count}. "
            f"Loop sizes: walls top {wall_top_count}, walls lower {wall_lower_count}, foot top {foot_top_count}."
        )
        return {'FINISHED'}


# ------------------------------------------------------------
# Operator 14: Add Platform Boolean Modifiers
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_add_platform_boolean_modifiers(Operator):
    bl_idname = "object.mv_platform_booleans_add"
    bl_label = "Add Platform Booleans"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        if settings is None:
            return False
        return (
            get_platform_walls_object(settings) is not None and
            get_platform_foot_object(settings) is not None and
            get_color_base_object(settings) is not None
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        walls_obj = get_platform_walls_object(settings)
        walls_copy_obj = get_platform_walls_copy_object(settings)
        foot_obj = get_platform_foot_object(settings)
        base_obj = get_color_base_object(settings)
        skin_objects = get_color_skin_objects(settings)
        if walls_obj is None or foot_obj is None or base_obj is None:
            self.report({'ERROR'}, "Run Steps 8 and 12 first so the Lego base, walls, and foot all exist.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if walls_copy_obj is not None:
            remove_object_if_exists(walls_copy_obj)

        platform_obj = get_platform_object(settings)
        platform_name = platform_obj.name if platform_obj is not None else walls_obj.name
        walls_copy_obj = duplicate_object(context, walls_obj, get_platform_walls_copy_name(platform_name))
        for mod in list(walls_copy_obj.modifiers):
            if mod.type == 'SOLIDIFY' and mod.name == "PlatformWallsSolidify":
                mod.offset = settings.platform_walls_offset + settings.platform_foot_boolean_extra_offset
        walls_copy_obj.hide_set(True)

        targets = [base_obj] + skin_objects
        for obj in targets:
            ensure_boolean_modifier(
                obj,
                walls_obj,
                f"PlatformWallsCut_{walls_obj.name}",
                operation='DIFFERENCE',
                solver='EXACT',
            )

        ensure_boolean_modifier(
            foot_obj,
            walls_copy_obj,
            f"PlatformWallsCut_{walls_copy_obj.name}",
            operation='DIFFERENCE',
            solver='EXACT',
        )
        set_active_object(context, base_obj)

        self.report({'INFO'}, f"Added non-applied Exact boolean modifiers to {len(targets)} Lego object(s) and {foot_obj.name} using {walls_copy_obj.name}.")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_apply_platform_booleans(Operator):
    bl_idname = "object.mv_platform_booleans_apply"
    bl_label = "Apply Platform Booleans"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "miniature_voxeler_settings", None)
        if settings is None:
            return False
        return (
            get_platform_walls_object(settings) is not None and
            get_platform_foot_object(settings) is not None
        )

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        walls_obj = get_platform_walls_object(settings)
        walls_copy_obj = get_platform_walls_copy_object(settings)
        foot_obj = get_platform_foot_object(settings)
        base_obj = get_color_base_object(settings)
        skin_objects = get_color_skin_objects(settings)

        if walls_obj is None or foot_obj is None:
            self.report({'ERROR'}, "Run Steps 12 and 13 first so the wall cutters and foot exist.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        targets = []
        if base_obj is not None:
            targets.append(base_obj)
        targets.extend(skin_objects)
        targets.append(foot_obj)

        applied_count = 0
        for target in targets:
            if target is None or target.name not in bpy.data.objects:
                continue
            set_active_object(context, target)
            for mod in list(target.modifiers):
                if mod.type == 'BOOLEAN' and mod.name.startswith("PlatformWallsCut_"):
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                    applied_count += 1

        remove_object_if_exists(walls_obj)
        remove_object_if_exists(walls_copy_obj)

        if base_obj is not None and base_obj.name in bpy.data.objects:
            set_active_object(context, base_obj)
        elif foot_obj is not None and foot_obj.name in bpy.data.objects:
            set_active_object(context, foot_obj)

        self.report({'INFO'}, f"Applied {applied_count} platform boolean modifier(s) and deleted the wall cutters.")
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

    def draw(self, context):
        layout = self.layout
        settings = context.scene.miniature_voxeler_settings

        header = layout.row()
        header.label(text="Miniature Voxeler")
        header.label(text=ADDON_VERSION_TEXT)

        box = layout.box()
        box.label(text="Setup")
        col = box.column(align=True)
        col.prop(settings, "building_object")
        col.prop(settings, "platform_object")

        box = layout.box()
        box.label(text="Step 1: Apply All Transforms")
        box.operator("object.miniature_voxeler_apply_all_transforms", icon='OBJECT_DATA')

        box = layout.box()
        box.label(text="Step 2: Block Remesh")
        col = box.column(align=True)
        col.prop(settings, "octree_depth")
        col.prop(settings, "scale")
        col.prop(settings, "threshold")
        col.prop(settings, "remove_disconnected")
        col.operator("object.miniature_voxeler_block_remesh", icon='MOD_REMESH')

        box = layout.box()
        box.label(text="Step 3: Smart UV")
        box.operator("object.miniature_voxeler_smart_uv_project", icon='UV')

        box = layout.box()
        box.label(text="Step 4: Transfer Texture")
        col = box.column(align=True)
        col.prop(settings, "texture_source_name")
        col.prop(settings, "texture_size")
        col.prop(settings, "texture_margin")
        box.operator("object.miniature_voxeler_transfer_texture", icon='TEXTURE')

        box = layout.box()
        box.label(text="Step 5: Lego Color")
        col = box.column(align=True)
        col.prop(settings, "lego_color_count")
        col.prop(settings, "lego_color_sample_mode")
        col.prop(settings, "lego_color_assign_mode")
        box.operator("object.miniature_voxeler_lego_color", icon='MATERIAL')

        smooth_box = box.box()
        smooth_box.label(text="Step 6: Smooth Lego Color")
        smooth_col = smooth_box.column(align=True)
        smooth_col.prop(settings, "lego_smooth_weight")
        smooth_col.prop(settings, "lego_smooth_passes")
        smooth_col.prop(settings, "lego_smooth_min_neighbors")
        smooth_box.operator("object.miniature_voxeler_smooth_lego_color", icon='MOD_SMOOTH')

        palette_box = box.box()
        palette_box.label(text="Step 7: Paint Palette Slots")
        palette_box.prop(settings, "lego_paint_brush_size")
        palette_col = palette_box.column(align=True)
        active_painter = MINIATUREVOXELER_OT_paint_lego_slot._active_painter

        for slot_index in range(settings.lego_color_count):
            row = palette_col.row(align=True)
            swatch = row.row(align=True)
            swatch.enabled = False
            swatch.prop(settings, f"lego_palette_slot_color_{slot_index + 1}", text="")
            row.prop(settings, f"lego_palette_slot_{slot_index + 1}")
            is_active_paint_slot = (
                active_painter is not None and
                not getattr(active_painter, "_cancel_requested", False) and
                active_painter.slot_index == slot_index
            )
            icon = 'RADIOBUT_ON' if is_active_paint_slot else 'RADIOBUT_OFF'
            op = row.operator(
                "object.miniature_voxeler_paint_lego_slot",
                text="",
                icon=icon,
                depress=is_active_paint_slot,
            )
            op.slot_index = slot_index

        palette_box.label(text="Brush: left-drag paint, F resize, I then click picks")

        box = layout.box()
        box.label(text="Step 8: Generate Lego Skin")
        col = box.column(align=True)
        col.prop(settings, "color_skin_base_slot")
        col.prop(settings, "outer_skin_mm")
        col.prop(settings, "inset_amount")
        col.prop(settings, "inside_skin_mm")
        col.prop(settings, "make_boolean_base")

        box.label(text="Skin Slots")
        row = box.row(align=True)
        row.prop(settings, "color_skin_slot_1")
        row.prop(settings, "color_skin_slot_2")

        row = box.row(align=True)
        row.prop(settings, "color_skin_slot_3")
        row.prop(settings, "color_skin_slot_4")

        box.operator("object.miniature_voxeler_generate_color_skin", text="Generate Lego Skin", icon='MATERIAL')

        box = layout.box()
        box.label(text="Step 9: Prepare Platform Copy")
        col = box.column(align=True)
        col.prop(settings, "platform_limited_dissolve_angle")
        col.operator("object.miniature_voxeler_prepare_platform_copy", icon='DUPLICATE')

        box = layout.box()
        box.label(text="Step 10: Separate Platform Walls")
        box.label(text="Select wall faces on the platform copy, then separate them.")
        row = box.row(align=True)
        row.operator("object.mv_platform_walls_select", icon='FACESEL')
        row.operator("object.mv_platform_walls_separate", icon='MESH_CUBE')

        box = layout.box()
        box.label(text="Step 11: Clean Platform Walls")
        col = box.column(align=True)
        col.prop(settings, "platform_merge_distance")
        row = col.row(align=True)
        row.operator("object.mv_platform_walls_merge", icon='MESH_DATA')
        row.operator("object.mv_platform_walls_nonmanifold", icon='EDGESEL')
        box.label(text="Before moving to next step, make sure wall loop is clean.")

        box = layout.box()
        box.label(text="Step 12: Build Foot Gap")
        col = box.column(align=True)
        col.prop(settings, "platform_wall_extrude_height")
        col.prop(settings, "platform_walls_thickness")
        col.prop(settings, "platform_walls_offset")
        col.prop(settings, "platform_foot_thickness")
        col.prop(settings, "platform_foot_offset")
        row = col.row(align=True)
        row.operator("object.mv_platform_wall_loop", text="Select Wall Loops", icon='EDGESEL')
        row.operator("object.mv_platform_walls_extrude", text="Build Foot Gap", icon='MOD_SOLIDIFY')
        box.label(text="Select 1 top rim edge and 1 lower rim edge, then expand both loops.")
        box.label(text="Top loop defines the shape. Lower loop sets the floor from its lowest point.")
        box.label(text="Walls go up by Foot Gap and down by 0.01 m.")
        box.label(text="Foot goes up by Foot Gap minus 0.0002 m.")

        box = layout.box()
        box.label(text="Step 13: Add Platform Booleans")
        box.prop(settings, "platform_foot_boolean_extra_offset")
        box.label(text="Adds Exact boolean modifiers without applying them.")
        box.operator("object.mv_platform_booleans_add", icon='MOD_BOOLEAN')
        box.operator("object.mv_platform_booleans_apply", icon='CHECKMARK')

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
    MINIATUREVOXELER_OT_smooth_lego_color,
    MINIATUREVOXELER_OT_paint_lego_slot,
    MINIATUREVOXELER_OT_generate_color_skin,
    MINIATUREVOXELER_OT_prepare_platform_copy,
    MINIATUREVOXELER_OT_prepare_platform_walls_selection,
    MINIATUREVOXELER_OT_separate_platform_walls,
    MINIATUREVOXELER_OT_merge_platform_walls_by_distance,
    MINIATUREVOXELER_OT_select_platform_walls_non_manifold,
    MINIATUREVOXELER_OT_prepare_platform_wall_loop_selection,
    MINIATUREVOXELER_OT_extrude_platform_walls_up,
    MINIATUREVOXELER_OT_add_platform_boolean_modifiers,
    MINIATUREVOXELER_OT_apply_platform_booleans,
    MINIATUREVOXELER_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.miniature_voxeler_settings = PointerProperty(type=MINIATUREVOXELER_PG_settings)


def unregister():
    del bpy.types.Scene.miniature_voxeler_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
