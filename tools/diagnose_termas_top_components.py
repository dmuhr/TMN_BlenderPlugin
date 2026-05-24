import importlib.util
import os
import sys

import bpy


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_termas_top_components", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


module = load_addon()
blocks = next((obj for obj in bpy.data.objects if obj.name.endswith("_Blocks")), None)
if blocks is None:
    raise RuntimeError("No _Blocks object found")

top_slot_by_cell = {}
for poly in blocks.data.polygons:
    slot = int(poly.material_index)
    cell = module.get_voxel_cell_key_from_mesh(blocks.data, poly.index)
    face_dir = module.get_skin_face_dir_from_poly(blocks.data, poly)
    if face_dir == 4:
        top_slot_by_cell[cell] = slot

pending = set(top_slot_by_cell.keys())
components = []
while pending:
    seed = pending.pop()
    slot = top_slot_by_cell[seed]
    stack = [seed]
    cells = {seed}
    while stack:
        i, j, k = stack.pop()
        for neighbor in ((i + 1, j, k), (i - 1, j, k), (i, j + 1, k), (i, j - 1, k)):
            if neighbor not in pending:
                continue
            if top_slot_by_cell[neighbor] != slot:
                continue
            pending.remove(neighbor)
            cells.add(neighbor)
            stack.append(neighbor)
    xs = [cell[0] for cell in cells]
    ys = [cell[1] for cell in cells]
    zs = [cell[2] for cell in cells]
    components.append({
        "slot": slot,
        "count": len(cells),
        "z_min": min(zs),
        "z_max": max(zs),
        "x_min": min(xs),
        "x_max": max(xs),
        "y_min": min(ys),
        "y_max": max(ys),
    })

components.sort(key=lambda item: (-item["count"], item["slot"], item["z_min"]))
for index, comp in enumerate(components[:80]):
    print(
        "component",
        index,
        "slot",
        comp["slot"] + 1,
        "count",
        comp["count"],
        "z",
        (comp["z_min"], comp["z_max"]),
        "x",
        (comp["x_min"], comp["x_max"]),
        "y",
        (comp["y_min"], comp["y_max"]),
        flush=True,
    )
