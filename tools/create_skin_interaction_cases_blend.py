import importlib.util
import os
import sys

import bpy
from mathutils import Vector


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = os.path.join(ROOT, "Skin_Interaction_Cases_v508.blend")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_interactions", ADDON_PATH)
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
    return obj


def add_cuboid_cells(cells, origin, size, slot=0):
    ox, oy, oz = origin
    sx, sy, sz = size
    for i in range(sx):
        for j in range(sy):
            for k in range(sz):
                cells[(ox + i, oy + j, oz + k)] = slot


def boundary_coords(origin, size, face_dir):
    ox, oy, oz = origin
    sx, sy, sz = size
    coords = []
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
                coords.append((ox + i, oy + j, oz + k))
    return coords


def paint_face_rect(face_slots, origin, size, face_dir, slot):
    for coord in boundary_coords(origin, size, face_dir):
        face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot


def paint_face_subset(face_slots, coords, face_dir, slot):
    for coord in coords:
        face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot


def main():
    module = load_addon()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    base_mat = make_mat("Slot 1 Base", (0.54, 0.48, 0.26, 1.0))
    slot2_mat = make_mat("Slot 2 Yellow", (0.95, 0.82, 0.22, 1.0))
    slot3_mat = make_mat("Slot 3 Green", (0.15, 0.70, 0.24, 1.0))
    slot4_mat = make_mat("Slot 4 Magenta", (0.82, 0.10, 0.55, 1.0))

    root_name = "SkinInteractionCases"
    voxel_size = 0.00176
    label_size = voxel_size * 1.25
    origin = Vector((0.0, 0.0, 0.0))
    cells = {}
    face_slots = {}

    cases = [
        ("A s2/s3 90 corner", (0, 0, 0), (4, 4, 4)),
        ("B s2 wall + s3 ledge", (7, 0, 0), (5, 4, 4)),
        ("C s2 wall + s3 T", (15, 0, 0), (6, 4, 4)),
        ("D three slots meet", (24, 0, 0), (4, 4, 4)),
        ("E s2/s3/s4 top edge", (0, 8, 0), (5, 4, 4)),
        ("F lower slot owns overlap", (8, 8, 0), (5, 4, 4)),
        ("G higher slot terminates", (17, 8, 0), (5, 4, 4)),
        ("H alternating stripes", (26, 8, 0), (6, 4, 4)),
        ("I nested corner", (0, 16, 0), (5, 5, 4)),
        ("J crossing ledges", (9, 16, 0), (6, 5, 4)),
        ("K cap touches wall color", (20, 16, 0), (5, 5, 4)),
        ("L three-slot T", (29, 16, 0), (6, 5, 4)),
    ]

    for _, case_origin, case_size in cases:
        add_cuboid_cells(cells, case_origin, case_size)

    # A: perpendicular slot 2/slot 3 walls compete at an inside corner.
    paint_face_rect(face_slots, (0, 0, 0), (4, 4, 4), 0, 1)
    paint_face_rect(face_slots, (0, 0, 0), (4, 4, 4), 2, 2)

    # B: slot 2 vertical wall, slot 3 top ledge crossing its edge.
    paint_face_rect(face_slots, (7, 0, 0), (5, 4, 4), 2, 1)
    paint_face_subset(face_slots, [(7 + i, 3, 3) for i in range(5)], 4, 2)

    # C: real T junction, slot 3 strip meets the middle of slot 2 wall.
    paint_face_rect(face_slots, (15, 0, 0), (6, 4, 4), 2, 1)
    paint_face_subset(face_slots, [(20, 3, k) for k in range(1, 4)], 0, 2)

    # D: three different slots meet at the same voxel corner.
    paint_face_rect(face_slots, (24, 0, 0), (4, 4, 4), 0, 1)
    paint_face_rect(face_slots, (24, 0, 0), (4, 4, 4), 2, 2)
    paint_face_rect(face_slots, (24, 0, 0), (4, 4, 4), 4, 3)

    # E: top edge with slot 2 wall, slot 3 side, slot 4 cap.
    paint_face_rect(face_slots, (0, 8, 0), (5, 4, 4), 0, 1)
    paint_face_rect(face_slots, (0, 8, 0), (5, 4, 4), 2, 2)
    paint_face_subset(face_slots, [(4, 8 + j, 3) for j in range(4)], 4, 3)

    # F: lower slot 2 should own overlapping corner against slot 4.
    paint_face_rect(face_slots, (8, 8, 0), (5, 4, 4), 0, 1)
    paint_face_rect(face_slots, (8, 8, 0), (5, 4, 4), 2, 3)
    paint_face_subset(face_slots, [(12, 11, k) for k in range(4)], 4, 3)

    # G: higher slot terminates into lower slot wall.
    paint_face_rect(face_slots, (17, 8, 0), (5, 4, 4), 2, 1)
    paint_face_subset(face_slots, [(21, 11, k) for k in range(1, 3)], 0, 3)

    # H: alternating colored wall stripes on same plane, with perpendicular edge.
    for i in range(6):
        slot = 1 if i % 3 == 0 else (2 if i % 3 == 1 else 3)
        paint_face_subset(face_slots, [(26 + i, 11, k) for k in range(4)], 2, slot)
    paint_face_rect(face_slots, (26, 8, 0), (6, 4, 4), 0, 2)

    # I: nested inside corner: slot 2 outer, slot 3 inner return, slot 4 cap.
    paint_face_rect(face_slots, (0, 16, 0), (5, 5, 4), 0, 1)
    paint_face_subset(face_slots, [(4, 16 + j, k) for j in range(2, 5) for k in range(4)], 2, 2)
    paint_face_subset(face_slots, [(4, 20, k) for k in range(2, 4)], 0, 3)

    # J: two different ledges cross at an upper corner.
    paint_face_subset(face_slots, [(9 + i, 20, 3) for i in range(6)], 4, 1)
    paint_face_subset(face_slots, [(14, 16 + j, 3) for j in range(5)], 4, 2)
    paint_face_rect(face_slots, (9, 16, 0), (6, 5, 4), 0, 3)

    # K: cap touches wall color along only part of its edge.
    paint_face_rect(face_slots, (20, 16, 0), (5, 5, 4), 2, 2)
    paint_face_subset(face_slots, [(20 + i, 20, 3) for i in range(2, 5)], 4, 1)

    # L: three-slot T: lower wall, middle incoming strip, higher cap.
    paint_face_rect(face_slots, (29, 16, 0), (6, 5, 4), 2, 1)
    paint_face_subset(face_slots, [(34, 20, k) for k in range(1, 4)], 0, 2)
    paint_face_subset(face_slots, [(32 + i, 20, 3) for i in range(4)], 4, 3)

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
            (case_origin[0] + (case_size[0] * 0.5)) * voxel_size,
            (case_origin[1] - 1.25) * voxel_size,
            (case_origin[2] + case_size[2] + 0.2) * voxel_size,
        ), label_size)

    settings = bpy.context.scene.miniature_voxeler_settings
    settings.building_object = blocks_obj
    settings.color_skin_base_slot = "0"
    settings.color_skin_slot_1 = False
    settings.color_skin_slot_2 = True
    settings.color_skin_slot_3 = True
    settings.color_skin_slot_4 = True
    for slot in (1, 2, 3, 4):
        setattr(settings, f"skin_slot_{slot}_solidify_thickness_mm", 0.4)
        setattr(settings, f"skin_slot_{slot}_solidify_offset", -1.0)

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
