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

        origin, voxel_size, cells, voxel_stats = generate_blender_block_voxel_cells_from_object(context, new_obj, settings, source_obj)
        if not cells:
            remove_object_if_exists(new_obj)
            self.report({'ERROR'}, "Blender Blocks remesh did not produce voxel cells for this mesh.")
            return {'CANCELLED'}
        rebuild_voxel_mesh_from_cells(new_obj, origin, voxel_size, cells)
        new_obj["mv_surface_cell_count"] = int(voxel_stats.get("surface_cell_count", 0))
        new_obj["mv_top_open_empty_count"] = int(voxel_stats.get("top_open_empty_count", 0))
        new_obj["mv_exterior_empty_count"] = int(voxel_stats.get("exterior_empty_count", 0))
        new_obj["mv_cavity_fill_cell_count"] = int(voxel_stats.get("cavity_fill_count", 0))
        new_obj["mv_vertical_fill_cell_count"] = int(voxel_stats.get("vertical_fill_count", 0))
        new_obj["mv_xy_slice_fill_cell_count"] = int(voxel_stats.get("xy_slice_fill_count", 0))
        new_obj["mv_unpaired_block_rows"] = int(voxel_stats.get("unpaired_block_rows", 0))
        new_obj["mv_applied_octree_depth"] = int(voxel_stats.get("applied_octree_depth", settings.octree_depth))
        source_obj.hide_set(True)
        if building_obj is not None:
            building_obj.hide_set(True)
        set_active_object(context, new_obj)
        voxel_size_mm = voxel_size * context.scene.unit_settings.scale_length * 1000.0
        self.report({'INFO'}, f"Created voxel object: {new_obj.name} from {source_obj.name} | {len(cells)} cubes | Blender Blocks depth {voxel_stats.get('applied_octree_depth', settings.octree_depth)} | unpaired rows {voxel_stats.get('unpaired_block_rows', 0)} | {voxel_size_mm:.3f} mm")
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

        source_texture_path = settings.texture_source_filepath.strip()
        if source_texture_path:
            try:
                source_image = load_texture_source_image(source_texture_path)
                ensure_texture_source_file_material(source_obj, source_image, settings.texture_bake_type)
            except Exception as e:
                self.report({'ERROR'}, f"Could not load texture file: {str(e)}")
                return {'CANCELLED'}

        image = ensure_bake_image(target_obj, settings.texture_size)
        _, image_node = ensure_bake_material(target_obj, image)

        scene = context.scene
        old_engine = scene.render.engine
        old_cycles_samples = getattr(getattr(scene, "cycles", None), "samples", None)
        source_was_hidden = source_obj.hide_get()
        target_was_hidden = target_obj.hide_get()
        building_was_hidden = building_obj.hide_get() if building_obj is not None else None
        bake = scene.render.bake
        old_bake_values = {}
        for attr_name in (
            "use_selected_to_active",
            "margin",
            "use_pass_direct",
            "use_pass_indirect",
            "use_pass_color",
            "max_ray_distance",
            "cage_extrusion",
            "use_clear",
        ):
            if hasattr(bake, attr_name):
                old_bake_values[attr_name] = getattr(bake, attr_name)

        try:
            scene.render.engine = 'CYCLES'
            if getattr(scene, "cycles", None) is not None and hasattr(scene.cycles, "samples"):
                scene.cycles.samples = settings.texture_bake_samples
            source_obj.hide_set(False)
            target_obj.hide_set(False)
            if building_obj is not None and building_obj != source_obj:
                building_obj.hide_set(True)

            if hasattr(bake, "use_selected_to_active"):
                bake.use_selected_to_active = True
            if hasattr(bake, "margin"):
                bake.margin = settings.texture_margin
            if hasattr(bake, "max_ray_distance"):
                bake.max_ray_distance = settings.texture_projection_distance
            if hasattr(bake, "cage_extrusion"):
                bake.cage_extrusion = settings.texture_cage_extrusion
            if hasattr(bake, "use_clear"):
                bake.use_clear = settings.texture_clear_image
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

            bpy.ops.object.bake(type=settings.texture_bake_type)

            try:
                image.pack()
            except Exception:
                pass

        except Exception as e:
            source_obj.hide_set(source_was_hidden)
            target_obj.hide_set(target_was_hidden)
            if building_obj is not None and building_was_hidden is not None:
                building_obj.hide_set(building_was_hidden)
            scene.render.engine = old_engine
            if old_cycles_samples is not None and getattr(scene, "cycles", None) is not None:
                scene.cycles.samples = old_cycles_samples
            for attr_name, old_value in old_bake_values.items():
                setattr(bake, attr_name, old_value)
            self.report({'ERROR'}, f"Texture transfer failed: {str(e)}")
            return {'CANCELLED'}

        scene.render.engine = old_engine
        if old_cycles_samples is not None and getattr(scene, "cycles", None) is not None:
            scene.cycles.samples = old_cycles_samples
        for attr_name, old_value in old_bake_values.items():
            setattr(bake, attr_name, old_value)
        source_obj.hide_set(source_was_hidden)
        target_obj.hide_set(target_was_hidden)
        if building_obj is not None and building_was_hidden is not None:
            building_obj.hide_set(building_was_hidden)
        set_active_object(context, target_obj)

        source_label = os.path.basename(bpy.path.abspath(source_texture_path)) if source_texture_path else source_obj.name
        self.report({'INFO'}, f"Texture baked from {source_label} via {source_obj.name} to {target_obj.name}")
        return {'FINISHED'}


class MINIATUREVOXELER_OT_direct_source_colors(Operator):
    bl_idname = "object.miniature_voxeler_direct_source_colors"
    bl_label = "Direct Source Colors"
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

        source_obj = get_texture_source_object(settings)
        if source_obj is None and not settings.texture_source_name.strip():
            source_obj = ensure_building_body_object(context, settings)

        if source_obj is None:
            self.report({'ERROR'}, "No _body source found. Run Voxelize Building again, or enter a valid Source Override.")
            return {'CANCELLED'}

        if building_obj is not None and source_obj == building_obj:
            self.report({'ERROR'}, "Direct Source Colors must use the _body copy, not the original Building.")
            return {'CANCELLED'}

        if source_obj == target_obj:
            self.report({'ERROR'}, "Source object and target object are the same.")
            return {'CANCELLED'}

        if source_obj.type != 'MESH':
            self.report({'ERROR'}, "Source object is not a mesh.")
            return {'CANCELLED'}

        source_texture_path = settings.texture_source_filepath.strip()
        if source_texture_path:
            try:
                source_image = load_texture_source_image(source_texture_path)
                ensure_texture_source_file_material(source_obj, source_image, settings.texture_bake_type)
            except Exception as e:
                self.report({'ERROR'}, f"Could not load texture file: {str(e)}")
                return {'CANCELLED'}

        face_colors = collect_direct_source_face_colors(context, source_obj, target_obj, settings)
        if not face_colors:
            self.report({'ERROR'}, "Could not sample colors from the source surface.")
            return {'CANCELLED'}

        if settings.lego_color_assign_mode == 'LUMINANCE':
            palette, assignments = build_luminance_palette(face_colors, settings.lego_color_count)
        else:
            palette, assignments = build_adaptive_palette(face_colors, settings.lego_color_count)

        sync_slot_palette_properties(settings, palette)
        rebuild_materials_from_assignments(target_obj, settings, assignments, len(palette))
        sync_voxel_color_state_from_mesh(target_obj)
        settings.platform_foot_color_slot = '0'
        apply_platform_foot_color_slot(settings)
        set_active_object(context, target_obj)

        source_label = os.path.basename(bpy.path.abspath(source_texture_path)) if source_texture_path else source_obj.name
        self.report({'INFO'}, f"Direct Source Colors sampled {len(face_colors)} face(s) from {source_label}.")
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

    def push_paint_undo_batch(self, batch):
        if not batch:
            return 0
        undo_stack = getattr(self, "_paint_undo_stack", None)
        if undo_stack is None:
            self._paint_undo_stack = []
            undo_stack = self._paint_undo_stack
        undo_stack.append(batch)
        if len(undo_stack) > 100:
            undo_stack.pop(0)
        return 1

    def finish_active_paint_stroke(self):
        batch = getattr(self, "_active_paint_undo_batch", None)
        if batch:
            self.push_paint_undo_batch(batch)
        self._active_paint_undo_batch = []

    def undo_last_paint_edit(self, context, obj=None):
        undo_stack = getattr(self, "_paint_undo_stack", [])
        if not undo_stack:
            self.report({'INFO'}, "No voxel paint strokes to undo.")
            return 0

        settings = context.scene.miniature_voxeler_settings
        obj = obj or get_blocks_object(settings)
        if obj is None or obj.type != 'MESH':
            return 0

        mesh = obj.data
        face_slots = getattr(self, "_face_slots", None)
        batch = undo_stack.pop()
        for item in reversed(batch):
            face_index = int(item.get("face_index", -1))
            previous_slot = int(item.get("previous_slot", 0))
            if 0 <= face_index < len(mesh.polygons):
                mesh.polygons[face_index].material_index = previous_slot
            face_key = item.get("face_key")
            if face_slots is not None and face_key is not None and face_key[3] >= 0:
                face_slots[tuple(face_key)] = previous_slot

        if face_slots is not None:
            store_voxel_face_slots(obj, face_slots)
            self._cells = sync_voxel_cell_slots_from_face_slots(self._cells, face_slots)
            origin = get_stored_voxel_origin(obj)
            voxel_size = float(obj.get("mv_voxel_size", 0.0))
            if origin is not None and voxel_size > 0.0:
                store_voxel_state(obj, origin, voxel_size, self._cells)
        mesh.update()
        if context.area:
            context.area.tag_redraw()
        self.report({'INFO'}, "Undid last voxel paint stroke.")
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
        self.finish_active_paint_stroke()
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
                previous.finish_active_paint_stroke()
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
            previous._active_paint_undo_batch = []
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
        self._paint_undo_stack = []
        self._active_paint_undo_batch = []
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

        if event.type == 'Z' and event.value == 'PRESS' and getattr(event, "ctrl", False):
            self._is_editing = False
            if self.mode in {'PAINT', 'BOX_PAINT', 'LASSO_PAINT'}:
                self.finish_active_paint_stroke()
                self.undo_last_paint_edit(context, obj)
            elif self._effective_mode in {'ADD', 'REMOVE'}:
                self.undo_last_voxel_edit(context, obj)
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
                undo_batch = []
                changed_count = paint_face_indices(
                    obj,
                    self.slot_index,
                    face_indices,
                    self._face_slots,
                    commit_face_slots=False,
                    undo_batch=undo_batch,
                )
                self.push_paint_undo_batch(undo_batch)
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
                undo_batch = []
                changed_count = paint_face_indices(
                    obj,
                    self.slot_index,
                    face_indices,
                    self._face_slots,
                    commit_face_slots=False,
                    undo_batch=undo_batch,
                )
                self.push_paint_undo_batch(undo_batch)
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
                    self._active_paint_undo_batch = []
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
                    self.finish_active_paint_stroke()
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
                    undo_batch=self._active_paint_undo_batch,
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
        ensure_current_voxel_mesh_format(body_obj)

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

        assign_bottom_voxel_faces_to_slot(body_obj, int(settings.platform_foot_color_slot))

        root_name = get_root_name(body_obj.name)
        source_name = get_inferred_source_name(settings, body_obj)

        base_obj = get_color_base_object(settings)
        if base_obj is not None:
            remove_object_if_exists(base_obj)
        for skin_obj in get_color_skin_objects(settings):
            remove_object_if_exists(skin_obj)

        processed = []
        skipped = []
        subdivisions = get_skin_subdivision_steps(settings)
        steps_by_slot = {
            slot_index: get_skin_slot_thickness_steps(settings, slot_index)
            for slot_index in enabled_skin_slots
        }
        base_obj, skin_results = build_fractional_skin_and_base_objects(
            context,
            body_obj,
            root_name,
            source_name,
            base_slot_index,
            enabled_skin_slots,
            steps_by_slot,
            subdivisions,
        )
        if base_obj is None:
            self.report({'ERROR'}, "Could not build fractional base mesh from voxel state.")
            return {'CANCELLED'}

        for slot_index, new_obj, selected_count in skin_results:
            if selected_count == 0:
                skipped.append(f"Slot {slot_index + 1}")
                continue

            if new_obj is None:
                self.report({'ERROR'}, f"Could not build skin slab object for slot {slot_index + 1}.")
                return {'CANCELLED'}

            label = f"Slot {slot_index + 1} ({selected_count} faces)"
            processed.append(label)

        if not processed:
            set_active_object(context, base_obj)
            self.report({'WARNING'}, "No faces found for the selected skin slots.")
            return {'CANCELLED'}

        body_obj.hide_set(True)
        set_active_object(context, base_obj)

        msg = "Processed: " + ", ".join(processed)
        if skipped:
            msg += " | Skipped: " + ", ".join(skipped)
        msg += f" | Fractional voxel skins generated ({subdivisions} subdivisions) | _Base carved without skin booleans | _Blocks hidden"

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
        cutter_text = " _Base is already carved by fractional voxel skin generation."
        foot_text = " Foot cuts _Base and all skins." if foot_obj is not None else " No _foot found; foot booleans skipped."
        self.report({'INFO'}, f"Added {added_count} Exact boolean modifier(s). Skin and base pieces are generated without skin booleans.{cutter_text}{foot_text}")
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
                    if os.path.isfile(target_blend):
                        os.remove(target_blend)
                    bpy.ops.wm.save_as_mainfile(filepath=target_blend, copy=True)
                    blend_copy_text = "Blend copy overwritten."
                else:
                    blend_copy_text = "Blend file already in export folder."
            except Exception as save_copy_error:
                if blend_path and os.path.isfile(blend_path) and os.path.abspath(blend_path) != os.path.abspath(target_blend):
                    if os.path.isfile(target_blend):
                        os.remove(target_blend)
                    shutil.copy2(blend_path, target_blend)
                    blend_copy_text = "Blend disk copy overwritten."
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
