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
            texture_col.prop(settings, "texture_source_filepath")
            texture_col.prop(settings, "texture_size")
            texture_col.prop(settings, "texture_margin")
            texture_col.prop(settings, "texture_bake_type")
            texture_col.prop(settings, "texture_projection_distance")
            texture_col.prop(settings, "texture_cage_extrusion")
            texture_col.prop(settings, "texture_bake_samples")
            texture_col.prop(settings, "texture_clear_image")
            box.operator("object.miniature_voxeler_transfer_texture", text="Transfer Texture", icon='TEXTURE')
            box.operator("object.miniature_voxeler_direct_source_colors", text="Direct Source Colors", icon='MATERIAL')
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
            col.prop(settings, "skin_subdivision_steps")
            col.prop(settings, "skin_solid_single_color_regions")

            box.label(text="Skin Slots")
            base_slot_index = int(settings.color_skin_base_slot)
            for slot_index in range(4):
                slot_box = box.box()
                slot_box.enabled = slot_index != base_slot_index
                row = slot_box.row(align=True)
                row.prop(settings, f"color_skin_slot_{slot_index + 1}", text=f"Slot {slot_index + 1}")
                if slot_index == base_slot_index:
                    row.label(text="Base", icon='RADIOBUT_ON')
                slot_box.prop(settings, f"skin_slot_{slot_index + 1}_thickness_steps")
                slot_box.prop(settings, f"skin_slot_{slot_index + 1}_solid_region_mode")

            box.operator("object.miniature_voxeler_separate_skins_solidify", text="Separate Skins and Solidify", icon='MATERIAL')

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
