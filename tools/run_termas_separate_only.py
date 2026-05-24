import importlib.util
import os
import sys

import bmesh
import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = "/Users/diegomuhr/Downloads/termo/TERMAS_skin_v518_fractional.blend"


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_termas_separate", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def mesh_stats(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    boundary_edges = sum(1 for edge in bm.edges if len(edge.link_faces) == 1)
    nonmanifold_edges = sum(1 for edge in bm.edges if not edge.is_manifold)
    volume = bm.calc_volume(signed=True)
    bm.free()
    return boundary_edges, nonmanifold_edges, volume


module = load_addon()
settings = bpy.context.scene.miniature_voxeler_settings
blocks = next((obj for obj in bpy.data.objects if obj.name.endswith("_Blocks")), None)
platform = next((obj for obj in bpy.data.objects if "_PLATFORM" in obj.name), None)
if blocks is None:
    raise RuntimeError("No _Blocks object found")

settings.building_object = blocks
if platform is not None:
    settings.platform_object = platform

settings.color_skin_base_slot = "0"
settings.color_skin_slot_1 = False
settings.color_skin_slot_2 = True
settings.color_skin_slot_3 = True
settings.color_skin_slot_4 = True
settings.skin_subdivision_steps = 8
settings.skin_solid_single_color_regions = True
for slot in (2, 3, 4):
    setattr(settings, f"skin_slot_{slot}_thickness_steps", 2)
settings.skin_slot_2_solid_region_mode = 'TOP'
settings.skin_slot_3_solid_region_mode = 'TOP'
settings.skin_slot_4_solid_region_mode = 'COLUMN'

print("separate_start", flush=True)
separate_result = bpy.ops.object.miniature_voxeler_separate_skins_solidify()
print("separate_result", separate_result, flush=True)

root_name = module.get_root_name(blocks.name)
base_obj = next((obj for obj in bpy.data.objects if obj.name == module.get_color_base_name(root_name)), None)
if base_obj is not None:
    boundary, nonmanifold, volume = mesh_stats(base_obj)
    print(
        "base",
        base_obj.name,
        "verts",
        len(base_obj.data.vertices),
        "faces",
        len(base_obj.data.polygons),
        "boundary",
        boundary,
        "nonmanifold",
        nonmanifold,
        "volume",
        round(volume, 8),
        flush=True,
    )

skins = [obj for obj in bpy.data.objects if "_Lego_Skin_Slot_" in obj.name and module.get_root_name(obj.name) == root_name]
for obj in sorted(skins, key=lambda item: item.name):
    boundary, nonmanifold, volume = mesh_stats(obj)
    print(
        "skin",
        obj.name,
        "verts",
        len(obj.data.vertices),
        "faces",
        len(obj.data.polygons),
        "boundary",
        boundary,
        "nonmanifold",
        nonmanifold,
        "volume",
        round(volume, 8),
        flush=True,
    )

bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_PATH)
print(f"saved {OUTPUT_PATH}", flush=True)
