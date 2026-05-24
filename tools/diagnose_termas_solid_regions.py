import importlib.util
import os
import sys
from collections import Counter, defaultdict

import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_termas_diagnose", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


module = load_addon()
settings = bpy.context.scene.miniature_voxeler_settings
blocks = next((obj for obj in bpy.data.objects if obj.name.endswith("_Blocks")), None)
if blocks is None:
    raise RuntimeError("No _Blocks object found")

settings.building_object = blocks
settings.color_skin_base_slot = "0"
settings.color_skin_slot_1 = False
settings.color_skin_slot_2 = True
settings.color_skin_slot_3 = True
settings.color_skin_slot_4 = True
settings.skin_subdivision_steps = 8
settings.skin_solid_single_color_regions = True
for slot in (2, 3, 4):
    setattr(settings, f"skin_slot_{slot}_thickness_steps", 2)

source_mesh = blocks.data
slot_names = [mat.name if mat is not None else "<none>" for mat in source_mesh.materials]
print("slots", list(enumerate(slot_names)), flush=True)

top_faces_by_slot = Counter()
top_faces_by_cell_slot = defaultdict(Counter)
faces_by_dir_slot = Counter()
for poly in source_mesh.polygons:
    slot_index = int(poly.material_index)
    cell = module.get_voxel_cell_key_from_mesh(source_mesh, poly.index)
    face_dir = module.get_skin_face_dir_from_poly(source_mesh, poly)
    faces_by_dir_slot[(face_dir, slot_index)] += 1
    if face_dir == 4:
        top_faces_by_slot[slot_index] += 1
        top_faces_by_cell_slot[cell][slot_index] += 1

print("top_faces_by_slot", dict(sorted(top_faces_by_slot.items())), flush=True)
print("faces_by_dir_slot", dict(sorted(faces_by_dir_slot.items())), flush=True)

multi_top_cells = {
    cell: dict(counter)
    for cell, counter in top_faces_by_cell_slot.items()
    if len(counter) > 1
}
print("multi_top_cells", len(multi_top_cells), flush=True)
for index, (cell, counter) in enumerate(sorted(multi_top_cells.items())[:20]):
    print("multi_top_cell", index, cell, counter, flush=True)

print("separate_start", flush=True)
result = bpy.ops.object.miniature_voxeler_separate_skins_solidify()
print("separate_result", result, flush=True)

root_name = module.get_root_name(blocks.name)
base_name = module.get_color_base_name(root_name)
base_obj = bpy.data.objects.get(base_name)
skin_objs = {
    slot: bpy.data.objects.get(module.get_color_skin_name(root_name, slot))
    for slot in (1, 2, 3)
}
for name, obj in [("base", base_obj)] + [(f"slot_{slot + 1}", skin_objs[slot]) for slot in sorted(skin_objs)]:
    if obj is None:
        print(name, "missing", flush=True)
        continue
    print(
        name,
        obj.name,
        "verts",
        len(obj.data.vertices),
        "faces",
        len(obj.data.polygons),
        "dims",
        tuple(round(v, 4) for v in obj.dimensions),
        flush=True,
    )
