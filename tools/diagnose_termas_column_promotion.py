import importlib.util
import os
import sys
from collections import Counter

import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_termas_columns", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


module = load_addon()
blocks = next((obj for obj in bpy.data.objects if obj.name.endswith("_Blocks")), None)
if blocks is None:
    raise RuntimeError("No _Blocks object found")

enabled = {1, 2, 3}
cells = module.deserialize_voxel_cells(blocks)
visible_slots_by_cell = {}
top_skin_slot_by_cell = {}
for poly in blocks.data.polygons:
    slot = int(poly.material_index)
    cell = module.get_voxel_cell_key_from_mesh(blocks.data, poly.index)
    face_dir = module.get_skin_face_dir_from_poly(blocks.data, poly)
    if slot in enabled:
        visible_slots_by_cell.setdefault(cell, set()).add(slot)
        if face_dir == 4:
            top_skin_slot_by_cell[cell] = slot

depth_by_slot = {slot: Counter() for slot in enabled}
blocked_by_slot = {slot: Counter() for slot in enabled}
for top_cell, owner in top_skin_slot_by_cell.items():
    i, j, k = top_cell
    depth = 0
    blocked = None
    for z in range(k, -1, -1):
        cell = (i, j, z)
        if cell not in cells:
            break
        visible = visible_slots_by_cell.get(cell, set())
        other = visible - {owner}
        if z != k and other:
            blocked = tuple(sorted(other))
            break
        depth += 1
    depth_by_slot[owner][depth] += 1
    if blocked is not None:
        blocked_by_slot[owner][blocked] += 1

for slot in sorted(enabled):
    print("slot", slot + 1, "depth_hist", sorted(depth_by_slot[slot].items())[:40], flush=True)
    print("slot", slot + 1, "blocked_by", dict(blocked_by_slot[slot]), flush=True)
