# Register
# ------------------------------------------------------------

classes = (
    MINIATUREVOXELER_PG_settings,
    MINIATUREVOXELER_OT_apply_all_transforms,
    MINIATUREVOXELER_OT_block_remesh,
    MINIATUREVOXELER_OT_smart_uv_project,
    MINIATUREVOXELER_OT_transfer_texture,
    MINIATUREVOXELER_OT_direct_source_colors,
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
