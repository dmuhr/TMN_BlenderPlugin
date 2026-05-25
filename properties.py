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
        name="Fallback Octree Depth",
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
        description="Target cube size. Blender Blocks uses the nearest octree depth, so the final size is approximate. Set to 0 to use Fallback Octree Depth",
        default=1.7,
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

    voxel_fill_interior: BoolProperty(
        name="Fill Interior",
        description="Fill enclosed voxel volume after surface voxelization. Disable for Blender Blocks-like geometry that preserves openings and undercuts",
        default=False,
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

    texture_source_filepath: StringProperty(
        name="Texture File",
        description="Optional image file to use on the texture source before baking",
        default="",
        subtype='FILE_PATH',
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

    texture_bake_type: EnumProperty(
        name="Bake Mode",
        description="How Blender reads color from the source during texture transfer",
        items=[
            ('DIFFUSE', "Diffuse Color", "Bake the source material base color without direct or indirect lighting"),
            ('EMIT', "Emission", "Bake emission color; useful with Texture File when diffuse baking picks up unwanted shading"),
        ],
        default='DIFFUSE',
    )

    texture_projection_distance: FloatProperty(
        name="Max Ray Distance",
        description="Maximum selected-to-active projection distance in scene units. Use 0 for Blender's default",
        default=0.0,
        min=0.0,
        soft_max=1.0,
        precision=4,
    )

    texture_cage_extrusion: FloatProperty(
        name="Cage Extrusion",
        description="Inflates the target cage for selected-to-active baking so voxel faces can reach the source mesh",
        default=0.0,
        min=0.0,
        soft_max=0.25,
        precision=4,
    )

    texture_bake_samples: IntProperty(
        name="Bake Samples",
        description="Cycles sample count used for the texture transfer bake",
        default=16,
        min=1,
        max=512,
    )

    texture_clear_image: BoolProperty(
        name="Clear Image",
        description="Clear the target bake image before transfer. Disable to keep the previous bake where projection rays miss",
        default=True,
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

    skin_solidify_thickness_mm: FloatProperty(
        name="Skin Thickness (mm)",
        description="Solidify thickness for separated color skin pieces",
        default=0.4,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    skin_subdivision_steps: IntProperty(
        name="Skin Subdivisions",
        description="Number of subcells per voxel edge for fractional skin and base generation",
        default=8,
        min=2,
        max=16,
    )

    skin_solid_single_color_regions: BoolProperty(
        name="Solid Single-Color Top Regions",
        description="Let whole connected top surfaces with one skin color own hidden column volume while preserving mixed-color skins",
        default=True,
    )

    skin_slot_1_thickness_steps: IntProperty(
        name="Slot 1 Thickness Steps",
        description="Skin thickness for slot 1, measured in voxel subcells",
        default=2,
        min=1,
        max=16,
    )

    skin_slot_1_solid_region: BoolProperty(
        name="Slot 1 Solid Top Regions",
        description="Allow slot 1 to replace hidden base volume under single-color top regions",
        default=True,
    )

    skin_slot_1_solid_region_mode: EnumProperty(
        name="Slot 1 Solid Region Mode",
        description="How slot 1 fills single-color top regions",
        items=[
            ('OFF', "Skin Only", "Do not fill solid top regions for this slot"),
            ('TOP', "Top Layer", "Fill only the top voxel layer for this slot"),
            ('COLUMN', "Column", "Fill hidden column volume below the top region for this slot"),
        ],
        default='TOP',
    )

    skin_slot_1_solidify_thickness_mm: FloatProperty(
        name="Slot 1 Thickness (mm)",
        description="Solidify thickness for slot 1 skin",
        default=0.4,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    skin_slot_1_solidify_offset: FloatProperty(
        name="Slot 1 Offset",
        description="Solidify offset for slot 1 skin",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
    )

    skin_slot_2_thickness_steps: IntProperty(
        name="Slot 2 Thickness Steps",
        description="Skin thickness for slot 2, measured in voxel subcells",
        default=2,
        min=1,
        max=16,
    )

    skin_slot_2_solid_region: BoolProperty(
        name="Slot 2 Solid Top Regions",
        description="Allow slot 2 to replace hidden base volume under single-color top regions",
        default=True,
    )

    skin_slot_2_solid_region_mode: EnumProperty(
        name="Slot 2 Solid Region Mode",
        description="How slot 2 fills single-color top regions",
        items=[
            ('OFF', "Skin Only", "Do not fill solid top regions for this slot"),
            ('TOP', "Top Layer", "Fill only the top voxel layer for this slot"),
            ('COLUMN', "Column", "Fill hidden column volume below the top region for this slot"),
        ],
        default='TOP',
    )

    skin_slot_2_solidify_thickness_mm: FloatProperty(
        name="Slot 2 Thickness (mm)",
        description="Solidify thickness for slot 2 skin",
        default=0.4,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    skin_slot_2_solidify_offset: FloatProperty(
        name="Slot 2 Offset",
        description="Solidify offset for slot 2 skin",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
    )

    skin_slot_3_thickness_steps: IntProperty(
        name="Slot 3 Thickness Steps",
        description="Skin thickness for slot 3, measured in voxel subcells",
        default=2,
        min=1,
        max=16,
    )

    skin_slot_3_solid_region: BoolProperty(
        name="Slot 3 Solid Top Regions",
        description="Allow slot 3 to replace hidden base volume under single-color top regions",
        default=True,
    )

    skin_slot_3_solid_region_mode: EnumProperty(
        name="Slot 3 Solid Region Mode",
        description="How slot 3 fills single-color top regions",
        items=[
            ('OFF', "Skin Only", "Do not fill solid top regions for this slot"),
            ('TOP', "Top Layer", "Fill only the top voxel layer for this slot"),
            ('COLUMN', "Column", "Fill hidden column volume below the top region for this slot"),
        ],
        default='TOP',
    )

    skin_slot_3_solidify_thickness_mm: FloatProperty(
        name="Slot 3 Thickness (mm)",
        description="Solidify thickness for slot 3 skin",
        default=0.4,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    skin_slot_3_solidify_offset: FloatProperty(
        name="Slot 3 Offset",
        description="Solidify offset for slot 3 skin",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
    )

    skin_slot_4_thickness_steps: IntProperty(
        name="Slot 4 Thickness Steps",
        description="Skin thickness for slot 4, measured in voxel subcells",
        default=2,
        min=1,
        max=16,
    )

    skin_slot_4_solid_region: BoolProperty(
        name="Slot 4 Solid Top Regions",
        description="Allow slot 4 to replace hidden base volume under single-color top regions",
        default=True,
    )

    skin_slot_4_solid_region_mode: EnumProperty(
        name="Slot 4 Solid Region Mode",
        description="How slot 4 fills single-color top regions",
        items=[
            ('OFF', "Skin Only", "Do not fill solid top regions for this slot"),
            ('TOP', "Top Layer", "Fill only the top voxel layer for this slot"),
            ('COLUMN', "Column", "Fill hidden column volume below the top region for this slot"),
        ],
        default='TOP',
    )

    skin_slot_4_solidify_thickness_mm: FloatProperty(
        name="Slot 4 Thickness (mm)",
        description="Solidify thickness for slot 4 skin",
        default=0.4,
        min=0.02,
        soft_max=100.0,
        precision=3,
    )

    skin_slot_4_solidify_offset: FloatProperty(
        name="Slot 4 Offset",
        description="Solidify offset for slot 4 skin",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
    )

    skin_solidify_offset: FloatProperty(
        name="Skin Offset",
        description="Solidify offset for separated skin pieces",
        default=0.0,
        min=-1.0,
        max=1.0,
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
