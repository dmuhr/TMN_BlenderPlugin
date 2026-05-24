import importlib.util
import os
import sys

import bpy
from mathutils import Vector


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = os.path.join(ROOT, "Skin_PrintAware_Solid_Cases_v518.blend")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_solid_regions", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def make_mat(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
    return mat


def add_label(text, location, size):
    font_curve = bpy.data.curves.new(text[:48], "FONT")
    font_curve.body = text
    font_curve.align_x = "CENTER"
    font_curve.align_y = "CENTER"
    font_curve.size = size
    obj = bpy.data.objects.new(text[:48], font_curve)
    obj.location = location
    obj.rotation_euler[0] = 1.5708
    bpy.context.collection.objects.link(obj)


def add_cuboid_cells(cells, origin, size, slot=0):
    ox, oy, oz = origin
    sx, sy, sz = size
    for i in range(sx):
        for j in range(sy):
            for k in range(sz):
                cells[(ox + i, oy + j, oz + k)] = slot


def paint_boundary(face_slots, origin, size, face_dir, slot):
    ox, oy, oz = origin
    sx, sy, sz = size
    for i in range(sx):
        for j in range(sy):
            for k in range(sz):
                if face_dir == 0 and i != sx - 1:
                    continue
                if face_dir == 1 and i != 0:
                    continue
                if face_dir == 2 and j != sy - 1:
                    continue
                if face_dir == 3 and j != 0:
                    continue
                if face_dir == 4 and k != sz - 1:
                    continue
                if face_dir == 5 and k != 0:
                    continue
                coord = (ox + i, oy + j, oz + k)
                face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot


def paint_cells(face_slots, coords, face_dir, slot):
    for coord in coords:
        face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot


def main():
    module = load_addon()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    base_mat = make_mat("Slot 1 Base", (0.62, 0.55, 0.42, 1.0))
    slot2_mat = make_mat("Slot 2 Green Solid", (0.05, 0.78, 0.12, 1.0))
    slot3_mat = make_mat("Slot 3 Red", (0.85, 0.08, 0.05, 1.0))
    slot4_mat = make_mat("Slot 4 Blue", (0.05, 0.24, 0.85, 1.0))

    root_name = "SkinSolidRegionCases"
    voxel_size = 0.00176
    label_size = voxel_size * 1.25
    origin = Vector((0.0, 0.0, 0.0))
    cells = {}
    face_slots = {}

    cases = [
        ("A top-only green promotes", (0, 0, 0), (5, 5, 3)),
        ("B top green + red side no", (8, 0, 0), (5, 5, 3)),
        ("C vertical green wall no", (16, 0, 0), (4, 4, 4)),
        ("D top green + base sides yes", (23, 0, 0), (4, 4, 4)),
        ("E green top/red ledge no", (0, 8, 0), (5, 4, 4)),
        ("F three skin top conflict", (8, 8, 0), (5, 4, 3)),
        ("G green plateau + red band", (17, 8, 0), (6, 4, 4)),
        ("H blue top patch promotes", (27, 8, 0), (5, 4, 3)),
    ]

    for _, case_origin, case_size in cases:
        add_cuboid_cells(cells, case_origin, case_size)

    # A: only the top face is green. Base side faces do not block top promotion.
    paint_boundary(face_slots, (0, 0, 0), (5, 5, 3), 4, 1)

    # B: top is green but one enabled side skin is red; edge voxels should stay fractional.
    paint_boundary(face_slots, (8, 0, 0), (5, 5, 3), 4, 1)
    paint_boundary(face_slots, (8, 0, 0), (5, 5, 3), 2, 2)

    # C: vertical-only skin should not promote because printing is bottom-up.
    paint_boundary(face_slots, (16, 0, 0), (4, 4, 4), 0, 1)

    # D: top green with base-color sides should promote; base faces are ignored.
    paint_boundary(face_slots, (23, 0, 0), (4, 4, 4), 4, 1)

    # E: top green but red side ledge on same voxels blocks promotion.
    paint_boundary(face_slots, (0, 8, 0), (5, 4, 4), 4, 1)
    paint_cells(face_slots, [(4, 8 + j, 3) for j in range(4)], 0, 2)

    # F: green top plus red side plus blue corner, should stay fractional.
    paint_boundary(face_slots, (8, 8, 0), (5, 4, 3), 4, 1)
    paint_boundary(face_slots, (8, 8, 0), (5, 4, 3), 0, 2)
    paint_cells(face_slots, [(12, 11, k) for k in range(3)], 2, 3)

    # G: green plateau should promote, red vertical band below remains a skin.
    paint_boundary(face_slots, (17, 8, 0), (6, 4, 4), 4, 1)
    paint_cells(face_slots, [(17 + i, 11, k) for i in range(6) for k in (1, 2)], 2, 2)

    # H: blue top patch promotes even though neighboring wall uses green.
    paint_boundary(face_slots, (27, 8, 0), (5, 4, 3), 4, 3)
    paint_cells(face_slots, [(31, 8 + j, k) for j in range(4) for k in range(2)], 0, 1)

    mesh = bpy.data.meshes.new(f"{root_name}_Blocks_Mesh")
    blocks_obj = bpy.data.objects.new(f"{root_name}_Blocks", mesh)
    bpy.context.collection.objects.link(blocks_obj)
    blocks_obj.data.materials.append(base_mat)
    blocks_obj.data.materials.append(slot2_mat)
    blocks_obj.data.materials.append(slot3_mat)
    blocks_obj.data.materials.append(slot4_mat)
    module.rebuild_voxel_mesh_from_cells(blocks_obj, origin, voxel_size, cells, face_slots)
    module.set_metadata(blocks_obj, root_name, root_name)

    for label, case_origin, case_size in cases:
        add_label(label, (
            (case_origin[0] + case_size[0] * 0.5) * voxel_size,
            (case_origin[1] - 1.25) * voxel_size,
            (case_origin[2] + case_size[2] + 0.2) * voxel_size,
        ), label_size)

    settings = bpy.context.scene.miniature_voxeler_settings
    settings.building_object = blocks_obj
    settings.color_skin_base_slot = "0"
    settings.skin_subdivision_steps = 8
    settings.skin_solid_single_color_regions = True
    settings.color_skin_slot_1 = False
    settings.color_skin_slot_2 = True
    settings.color_skin_slot_3 = True
    settings.color_skin_slot_4 = True
    for slot in (1, 2, 3, 4):
        setattr(settings, f"skin_slot_{slot}_thickness_steps", 2)

    bpy.ops.object.miniature_voxeler_separate_skins_solidify()

    for obj in bpy.data.objects:
        if "_Lego_Skin_Slot_" in obj.name:
            obj.display_type = "TEXTURED"
        if obj.name.endswith("_Blocks"):
            obj.hide_set(False)
            obj.show_wire = True

    bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_PATH)
    print(f"saved {OUTPUT_PATH}")


main()
