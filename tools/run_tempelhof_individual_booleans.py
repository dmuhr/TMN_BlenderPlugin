import importlib.util
import os
import sys

import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = "/Users/diegomuhr/Downloads/TEMPELHOF_skin_v507_individual_booleans.blend"


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_tempelhof_individual", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


module = load_addon()
settings = bpy.context.scene.miniature_voxeler_settings
base_obj = module.get_color_base_object(settings)
skin_objects = module.get_sorted_color_skin_objects(settings)
foot_obj = module.get_platform_foot_object(settings)
if base_obj is None or not skin_objects:
    raise RuntimeError("Open the separated Tempelhof file first")

print("add_individual_start", flush=True)
for skin_obj in skin_objects:
    module.ensure_boolean_modifier(
        base_obj,
        skin_obj,
        f"SkinBoolean_Base_{skin_obj.name}",
        operation="DIFFERENCE",
        solver="EXACT",
    )
    print("added_base_boolean", skin_obj.name, flush=True)

if foot_obj is not None:
    for target_obj in [base_obj] + skin_objects:
        module.ensure_boolean_modifier(
            target_obj,
            foot_obj,
            f"SkinBoolean_Foot_{foot_obj.name}",
            operation="DIFFERENCE",
            solver="EXACT",
        )
        print("added_foot_boolean", target_obj.name, flush=True)

targets = skin_objects + [base_obj]
applied = 0
for obj in targets:
    module.set_active_object(bpy.context, obj)
    for mod in list(obj.modifiers):
        if mod.type != "BOOLEAN" or not mod.name.startswith("SkinBoolean_"):
            continue
        mod_name = str(mod.name)
        print("apply_start", obj.name, mod_name, flush=True)
        bpy.ops.object.modifier_apply(modifier=mod_name)
        applied += 1
        print("apply_done", obj.name, mod_name, "verts", len(obj.data.vertices), "faces", len(obj.data.polygons), flush=True)

print("applied", applied, flush=True)
bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_PATH)
print(f"saved {OUTPUT_PATH}", flush=True)
