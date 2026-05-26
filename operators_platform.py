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
        return settings is not None and get_temporary_blocks_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        blocks_obj = get_temporary_blocks_object(settings)
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
        return settings is not None and get_temporary_blocks_object(settings) is not None

    def execute(self, context):
        settings = context.scene.miniature_voxeler_settings
        blocks_obj = get_temporary_blocks_object(settings)
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

