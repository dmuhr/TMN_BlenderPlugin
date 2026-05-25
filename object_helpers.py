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


def get_color_skin_cutter_name(root_name):
    return f"{root_name}_Skin_Cutter"


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

    if os.path.isfile(filepath):
        os.remove(filepath)

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
    if hasattr(mod, "solidify_mode"):
        mod.solidify_mode = 'EXTRUDE'
    if hasattr(mod, "use_even_offset"):
        mod.use_even_offset = bool(use_even_thickness)
    if hasattr(mod, "use_quality_normals"):
        mod.use_quality_normals = True
    if hasattr(mod, "use_rim"):
        mod.use_rim = True
    return mod


