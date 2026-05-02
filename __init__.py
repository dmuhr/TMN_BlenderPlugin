bl_info = {
    "name": "Miniature Voxeler",
    "author": "OpenAI",
    "version": (2, 0, 2),
    "blender": (5, 0, 1),
    "location": "3D View > Sidebar > Miniature Voxeler",
    "description": "Block remesh, transfer texture, create Lego-color face materials, and generate Lego skin or north-south skin meshes for miniature voxel workflows",
    "category": "Object",
}

import bpy
import bmesh
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

ADDON_VERSION_TEXT = "v.2.0.2"


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


def update_live_palette_slot(settings, context, slot_index):
    if settings.get("_mv_palette_sync", False):
        return
    apply_palette_display_colors(settings, context)


def sync_palette_properties(settings, colors):
    settings["_mv_palette_sync"] = True
    try:
        for slot_index in range(4):
            color = colors[slot_index] if slot_index < len(colors) else (0.8, 0.8, 0.8)
            setattr(settings, f"lego_palette_color_{slot_index + 1}", color)
    finally:
        settings["_mv_palette_sync"] = False


def get_debug_slot_colors():
    return [
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
    ]


def apply_palette_display_colors(settings, context):
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return

    mesh = obj.data
    display_colors = (
        get_debug_slot_colors()
        if settings.lego_debug_colors
        else [
            tuple(settings.lego_palette_color_1),
            tuple(settings.lego_palette_color_2),
            tuple(settings.lego_palette_color_3),
            tuple(settings.lego_palette_color_4),
        ]
    )

    for slot_index, color in enumerate(display_colors):
        if slot_index < len(mesh.materials):
            set_material_base_color(mesh.materials[slot_index], color)

    mesh.update()


def update_lego_debug_colors(settings, context):
    apply_palette_display_colors(settings, context)


def update_color_skin_base_slot(settings, context):
    base_slot_index = int(settings.color_skin_base_slot)
    settings["color_skin_slot_1"] = (base_slot_index != 0)
    settings["color_skin_slot_2"] = (base_slot_index != 1)
    settings["color_skin_slot_3"] = (base_slot_index != 2)
    settings["color_skin_slot_4"] = (base_slot_index != 3)


def update_lego_palette_color_1(settings, context):
    update_live_palette_slot(settings, context, 0)


def update_lego_palette_color_2(settings, context):
    update_live_palette_slot(settings, context, 1)


def update_lego_palette_color_3(settings, context):
    update_live_palette_slot(settings, context, 2)


def update_lego_palette_color_4(settings, context):
    update_live_palette_slot(settings, context, 3)


def get_view3d_window_region(area):
    for region in area.regions:
        if region.type == 'WINDOW':
            return region
    return None


def raycast_active_face(context, event):
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
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


# ------------------------------------------------------------
# Properties
# ------------------------------------------------------------

class MINIATUREVOXELER_PG_settings(PropertyGroup):
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

    lego_color_count: IntProperty(
        name="Number of Colors",
        description="How many flat Lego colors to create and assign to faces",
        default=4,
        min=1,
        max=4,
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

    lego_color_assign_mode: EnumProperty(
        name="Color Assignment",
        description="How sampled texture colors are grouped into Lego materials",
        items=[
            ('ADAPTIVE', "Adaptive Palette", "Build a palette from the sampled face colors and assign each face to the closest color"),
            ('LUMINANCE', "Brightness Bands", "Group faces by brightness and create one flat material per brightness band"),
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

    lego_palette_color_1: FloatVectorProperty(
        name="Slot 1",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.835, 0.129, 0.094),
        update=update_lego_palette_color_1,
    )

    lego_palette_color_2: FloatVectorProperty(
        name="Slot 2",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.929, 0.764, 0.114),
        update=update_lego_palette_color_2,
    )

    lego_palette_color_3: FloatVectorProperty(
        name="Slot 3",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.102, 0.333, 0.761),
        update=update_lego_palette_color_3,
    )

    lego_palette_color_4: FloatVectorProperty(
        name="Slot 4",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.93, 0.93, 0.9),
        update=update_lego_palette_color_4,
    )

    lego_debug_colors: BoolProperty(
        name="Debug Colors",
        description="Temporarily show slot colors as red, green, blue, and white for clearer visualization",
        default=False,
        update=update_lego_debug_colors,
    )

    outer_skin_mm: FloatProperty(
        name="Outer Skin (mm)",
        description="Outward skin thickness in millimeters",
        default=3.0,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    inset_amount: FloatProperty(
        name="Inset",
        default=0.006,
        min=0.0,
        soft_max=1.0,
        precision=4,
    )

    inside_skin_mm: FloatProperty(
        name="Inner Skin (mm)",
        description="Inward movement in millimeters. Cannot be positive",
        default=-2.0,
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

    do_pos_x: BoolProperty(name="+X", default=True)
    do_neg_x: BoolProperty(name="-X", default=False)
    do_pos_y: BoolProperty(name="+Y", default=False)
    do_neg_y: BoolProperty(name="-Y", default=False)
    do_pos_z: BoolProperty(name="+Z", default=False)
    do_neg_z: BoolProperty(name="-Z", default=False)

    make_boolean_base: BoolProperty(
        name="Boolean-Difference base.",
        description="Apply Boolean Difference on the base object using the generated skin objects",
        default=False,
    )


# ------------------------------------------------------------
# Axis config
# ------------------------------------------------------------

AXIS_CONFIGS = [
    ("do_pos_x", "+X", "X",  Vector(( 1.0,  0.0,  0.0))),
    ("do_neg_x", "-X", "-X", Vector((-1.0,  0.0,  0.0))),
    ("do_pos_y", "+Y", "Y",  Vector(( 0.0,  1.0,  0.0))),
    ("do_neg_y", "-Y", "-Y", Vector(( 0.0, -1.0,  0.0))),
    ("do_pos_z", "+Z", "Z",  Vector(( 0.0,  0.0,  1.0))),
    ("do_neg_z", "-Z", "-Z", Vector(( 0.0,  0.0, -1.0))),
]


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


def get_skin_name(root_name, axis_suffix):
    return f"{root_name}_Blocks_Skin_{axis_suffix}"


def get_color_base_name(root_name):
    return f"{root_name}_Lego_Base"


def get_color_skin_name(root_name, slot_index, island_index=None):
    name = f"{root_name}_Lego_Skin_Slot_{slot_index + 1}"
    if island_index is not None:
        name += f"_{island_index + 1}"
    return name


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


def mm_to_scene_units(context, mm_value):
    scale_length = context.scene.unit_settings.scale_length
    if scale_length <= 0.0:
        scale_length = 1.0
    meters = mm_value * 0.001
    return meters / scale_length


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


def rebuild_materials_from_assignments(obj, palette, assignments):
    mesh = obj.data
    mesh.materials.clear()

    for palette_index, color in enumerate(palette):
        mesh.materials.append(ensure_lego_color_material(obj, palette_index, color))

    for poly, material_index in zip(mesh.polygons, assignments):
        poly.material_index = material_index

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
# Operator 1: Block Remesh
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_block_remesh(Operator):
    bl_idname = "object.miniature_voxeler_block_remesh"
    bl_label = "Block Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        source_obj = context.active_object

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

        bpy.ops.object.modifier_apply(modifier=remesh.name)

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
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

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
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        target_obj = context.active_object

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if not target_obj.data.uv_layers:
            self.report({'ERROR'}, "Target object has no UVs. Run Smart UV Project first.")
            return {'CANCELLED'}

        source_name = get_inferred_source_name(settings, target_obj)
        source_obj = bpy.data.objects.get(source_name)

        if source_obj is None:
            self.report({'ERROR'}, f"Could not find source object: {source_name}")
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

        try:
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
            scene.render.engine = old_engine
            self.report({'ERROR'}, f"Texture transfer failed: {str(e)}")
            return {'CANCELLED'}

        scene.render.engine = old_engine
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
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        obj = context.active_object
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

        rebuild_materials_from_assignments(obj, palette, assignments)
        sync_palette_properties(settings, palette)
        apply_palette_display_colors(settings, context)

        self.report(
            {'INFO'},
            f"Lego Color created {len(palette)} material(s) from {image.name} using {settings.lego_color_assign_mode.lower()} assignment."
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
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
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

    def update_modal_cursor(self, context, event=None):
        if context.window is None:
            return

        cursor = 'EYEDROPPER' if event is not None and event.shift else 'PAINT_BRUSH'
        if getattr(self, "_current_cursor", None) != cursor:
            context.window.cursor_modal_set(cursor)
            self._current_cursor = cursor

    def restore_modal_cursor(self, context):
        if context.window is None:
            return

        if getattr(self, "_current_cursor", None) is not None:
            context.window.cursor_modal_restore()
            self._current_cursor = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def invoke(self, context, event):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first.")
            return {'CANCELLED'}

        if self.slot_index >= len(obj.data.materials):
            self.report({'ERROR'}, "Run Lego Color first so palette slots exist.")
            return {'CANCELLED'}

        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Start paint mode from a 3D View.")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        previous = type(self)._active_painter
        if previous is not None and previous is not self and not getattr(previous, "_cancel_requested", False):
            previous.slot_index = self.slot_index
            previous._last_face_index = None
            previous._is_painting = False
            previous.update_modal_cursor(context, event)
            self.report({'INFO'}, f"Switched paint brush to slot {self.slot_index + 1}.")
            return {'FINISHED'}

        if previous is not None and previous is not self:
            previous._cancel_requested = True

        self._cancel_requested = False
        self._is_painting = False
        self._last_face_index = None
        self._current_cursor = None
        type(self)._active_painter = self

        context.window_manager.modal_handler_add(self)
        self.update_modal_cursor(context, event)
        self.report({'INFO'}, f"Painting slot {self.slot_index + 1}. Left-drag paints, Shift picks hovered slot, right-click or Esc stops.")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        self.update_modal_cursor(context, event)

        if self._cancel_requested:
            if type(self)._active_painter is self:
                type(self)._active_painter = None
            self.restore_modal_cursor(context)
            return {'FINISHED'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            if type(self)._active_painter is self:
                type(self)._active_painter = None
            self.restore_modal_cursor(context)
            self.report({'INFO'}, "Paint Lego Slot finished.")
            return {'FINISHED'}

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            if type(self)._active_painter is self:
                type(self)._active_painter = None
            self.restore_modal_cursor(context)
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                self._is_painting = True
            elif event.value == 'RELEASE':
                self._is_painting = False
                self._last_face_index = None
                return {'RUNNING_MODAL'}

        if event.shift:
            hit_obj, face_index = raycast_active_face(context, event)
            if hit_obj is not None and face_index is not None:
                picked_slot = hit_obj.data.polygons[face_index].material_index
                if 0 <= picked_slot < len(hit_obj.data.materials):
                    self.slot_index = picked_slot
                    self._last_face_index = None
                    if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                        self.report({'INFO'}, f"Picked slot {self.slot_index + 1}.")
                    return {'RUNNING_MODAL'}

        if self._is_painting and event.type in {'LEFTMOUSE', 'MOUSEMOVE'}:
            hit_obj, face_index = raycast_active_face(context, event)
            if hit_obj is not None and face_index is not None and face_index != self._last_face_index:
                hit_obj.data.polygons[face_index].material_index = self.slot_index
                hit_obj.data.update()
                self._last_face_index = face_index
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
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        body_obj = context.active_object

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        if settings.lego_debug_colors:
            settings.lego_debug_colors = False

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
            get_material_base_color(original_obj.data.materials[base_slot_index]) if base_slot_index < len(original_obj.data.materials) else (0.8, 0.8, 0.8),
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
# Operator 8: North South Skin Separation
# ------------------------------------------------------------

class MINIATUREVOXELER_OT_generate_skin(Operator):
    bl_idname = "object.miniature_voxeler_generate_skin"
    bl_label = "North South Skin Separation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        body_obj = context.active_object

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        enabled_axes = []
        for prop_name, label, suffix, axis_vec in AXIS_CONFIGS:
            if getattr(settings, prop_name):
                enabled_axes.append((label, suffix, axis_vec))

        if not enabled_axes:
            self.report({'WARNING'}, "No axis checkbox is enabled.")
            return {'CANCELLED'}

        root_name = get_root_name(body_obj.name)
        source_name = get_inferred_source_name(settings, body_obj)

        body_obj.name = get_base_name(root_name)
        base_obj = body_obj
        set_metadata(base_obj, root_name, source_name)

        temp_source = duplicate_object(
            context,
            base_obj,
            f"{base_obj.name}_TEMP"
        )
        set_metadata(temp_source, root_name, source_name)

        processed = []
        skipped = []
        skin_objects = []

        for label, suffix, axis_vec in enabled_axes:
            if temp_source.name not in bpy.data.objects:
                self.report({'ERROR'}, "Temporary source object became invalid.")
                return {'CANCELLED'}

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
                skipped.append(label)
                continue

            pre_names = set(obj.name for obj in bpy.data.objects)

            bpy.ops.mesh.separate(type='SELECTED')
            bpy.ops.object.mode_set(mode='OBJECT')

            new_obj = find_new_object(pre_names, temp_source, context)
            if new_obj is None:
                self.report({'ERROR'}, f"Could not identify separated skin object for {label}.")
                return {'CANCELLED'}

            new_obj.name = get_skin_name(root_name, suffix)
            set_metadata(new_obj, root_name, source_name)
            process_skin_object(context, new_obj, settings, axis_vec)

            skin_objects.append(new_obj)
            processed.append(f"{label} ({selected_count} faces)")

        if settings.make_boolean_base and skin_objects:
            for i, skin_obj in enumerate(skin_objects):
                apply_boolean_difference(context, base_obj, skin_obj, i)

        if temp_source.name in bpy.data.objects:
            bpy.data.objects.remove(temp_source, do_unlink=True)

        set_active_object(context, base_obj)

        if not processed:
            self.report({'WARNING'}, "No matching faces found for the checked axes.")
            return {'CANCELLED'}

        msg = "Processed: " + ", ".join(processed)
        if skipped:
            msg += " | Skipped: " + ", ".join(skipped)
        if settings.make_boolean_base:
            msg += " | Boolean-Difference base. applied"
        msg += " | Temp source deleted"

        self.report({'INFO'}, msg)
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

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.miniature_voxeler_settings

        header = layout.row()
        header.label(text="Miniature Voxeler")
        header.label(text=ADDON_VERSION_TEXT)

        box = layout.box()
        box.label(text="Block Remesh")
        col = box.column(align=True)
        col.prop(settings, "octree_depth")
        col.prop(settings, "scale")
        col.prop(settings, "threshold")
        col.operator("object.miniature_voxeler_block_remesh", icon='MOD_REMESH')

        box = layout.box()
        box.label(text="Texture Transfer")
        col = box.column(align=True)
        col.prop(settings, "texture_source_name")
        col.prop(settings, "texture_size")
        col.prop(settings, "texture_margin")
        box.operator("object.miniature_voxeler_smart_uv_project", icon='UV')
        box.operator("object.miniature_voxeler_transfer_texture", icon='TEXTURE')

        box = layout.box()
        box.label(text="Lego Color")
        col = box.column(align=True)
        col.prop(settings, "lego_color_count")
        col.prop(settings, "lego_color_sample_mode")
        col.prop(settings, "lego_color_assign_mode")
        box.operator("object.miniature_voxeler_lego_color", icon='MATERIAL')

        smooth_box = box.box()
        smooth_box.label(text="Smoothing Process")
        smooth_col = smooth_box.column(align=True)
        smooth_col.prop(settings, "lego_smooth_weight")
        smooth_col.prop(settings, "lego_smooth_passes")
        smooth_col.prop(settings, "lego_smooth_min_neighbors")
        smooth_box.operator("object.miniature_voxeler_smooth_lego_color", icon='MOD_SMOOTH')

        palette_box = box.box()
        palette_box.label(text="Palette Slots")
        palette_box.prop(settings, "lego_debug_colors")
        palette_col = palette_box.column(align=True)
        row = palette_col.row(align=True)
        row.prop(settings, "lego_palette_color_1")
        op = row.operator("object.miniature_voxeler_paint_lego_slot", text="", icon='BRUSH_DATA')
        op.slot_index = 0
        if settings.lego_color_count >= 2:
            row = palette_col.row(align=True)
            row.prop(settings, "lego_palette_color_2")
            op = row.operator("object.miniature_voxeler_paint_lego_slot", text="", icon='BRUSH_DATA')
            op.slot_index = 1
        if settings.lego_color_count >= 3:
            row = palette_col.row(align=True)
            row.prop(settings, "lego_palette_color_3")
            op = row.operator("object.miniature_voxeler_paint_lego_slot", text="", icon='BRUSH_DATA')
            op.slot_index = 2
        if settings.lego_color_count >= 4:
            row = palette_col.row(align=True)
            row.prop(settings, "lego_palette_color_4")
            op = row.operator("object.miniature_voxeler_paint_lego_slot", text="", icon='BRUSH_DATA')
            op.slot_index = 3
        palette_box.label(text="Brush: left-drag paint, Shift picks hovered slot")

        box = layout.box()
        box.label(text="Lego Skin")
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
        box.label(text="North South Skin Separation")
        col = box.column(align=True)
        col.prop(settings, "outer_skin_mm")
        col.prop(settings, "inset_amount")
        col.prop(settings, "inside_skin_mm")
        col.prop(settings, "make_boolean_base")

        box.label(text="Sides")

        row = box.row(align=True)
        row.prop(settings, "do_pos_x")
        row.prop(settings, "do_neg_x")

        row = box.row(align=True)
        row.prop(settings, "do_pos_y")
        row.prop(settings, "do_neg_y")

        row = box.row(align=True)
        row.prop(settings, "do_pos_z")
        row.prop(settings, "do_neg_z")

        box.operator("object.miniature_voxeler_generate_skin", icon='FACESEL')


# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = (
    MINIATUREVOXELER_PG_settings,
    MINIATUREVOXELER_OT_block_remesh,
    MINIATUREVOXELER_OT_smart_uv_project,
    MINIATUREVOXELER_OT_transfer_texture,
    MINIATUREVOXELER_OT_lego_color,
    MINIATUREVOXELER_OT_smooth_lego_color,
    MINIATUREVOXELER_OT_paint_lego_slot,
    MINIATUREVOXELER_OT_generate_color_skin,
    MINIATUREVOXELER_OT_generate_skin,
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
