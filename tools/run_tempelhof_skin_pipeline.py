import importlib.util
import os
import sys

import bmesh
import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = "/Users/diegomuhr/Downloads/TEMPELHOF_skin_v507.blend"


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_tempelhof", ADDON_PATH)
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
for slot in (2, 3, 4):
    setattr(settings, f"skin_slot_{slot}_solidify_thickness_mm", 0.4)
    setattr(settings, f"skin_slot_{slot}_solidify_offset", -1.0)

separate_result = bpy.ops.object.miniature_voxeler_separate_skins_solidify()
print("separate_result", separate_result)

root_name = module.get_root_name(blocks.name)
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
    )

boolean_result = bpy.ops.object.miniature_voxeler_add_skin_booleans()
print("boolean_result", boolean_result)

base = bpy.data.objects.get(module.get_color_base_name(root_name))
for obj in [base] + sorted(skins, key=lambda item: item.name):
    if obj is None or obj.name not in bpy.data.objects:
        continue
    print("mods_before_apply", obj.name, [mod.name for mod in obj.modifiers])

targets = sorted(skins, key=lambda item: item.name) + [base]
applied_count = 0
for obj in targets:
    if obj is None or obj.name not in bpy.data.objects:
        continue
    module.set_active_object(bpy.context, obj)
    for mod in list(obj.modifiers):
        if mod.type != "BOOLEAN" or not mod.name.startswith("SkinBoolean_"):
            continue
        print("apply_start", obj.name, mod.name, flush=True)
        bpy.ops.object.modifier_apply(modifier=mod.name)
        applied_count += 1
        print("apply_done", obj.name, mod.name, "verts", len(obj.data.vertices), "faces", len(obj.data.polygons), flush=True)

print("manual_apply_count", applied_count)

for obj in [base] + sorted(skins, key=lambda item: item.name):
    if obj is None or obj.name not in bpy.data.objects:
        continue
    print("applied_mesh", obj.name, "verts", len(obj.data.vertices), "faces", len(obj.data.polygons), "mods", [mod.name for mod in obj.modifiers])

bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_PATH)
print(f"saved {OUTPUT_PATH}")
