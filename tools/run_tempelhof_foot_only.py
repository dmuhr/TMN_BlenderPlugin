import importlib.util
import os
import sys

import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = "/Users/diegomuhr/Downloads/TEMPELHOF_skin_v510_fractional_final.blend"


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_tempelhof_foot", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


module = load_addon()
settings = bpy.context.scene.miniature_voxeler_settings
base_obj = module.get_color_base_object(settings)
skin_objects = module.get_sorted_color_skin_objects(settings)
if base_obj is None or not skin_objects:
    raise RuntimeError("Open the fractional Tempelhof file first")

print("add_booleans_start", flush=True)
add_result = bpy.ops.object.miniature_voxeler_add_skin_booleans()
print("add_result", add_result, flush=True)
for obj in [base_obj] + skin_objects:
    print("mods_before_apply", obj.name, [mod.name for mod in obj.modifiers], flush=True)

apply_result = bpy.ops.object.miniature_voxeler_apply_skin_booleans()
print("apply_result", apply_result, flush=True)
for obj in [base_obj] + skin_objects:
    print("final_mesh", obj.name, "verts", len(obj.data.vertices), "faces", len(obj.data.polygons), "mods", [mod.name for mod in obj.modifiers], flush=True)

bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_PATH)
print(f"saved {OUTPUT_PATH}", flush=True)
