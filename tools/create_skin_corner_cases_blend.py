import importlib.util
import os
import sys

import bpy
from mathutils import Vector


ROOT = "/Users/diegomuhr/Documents/Unreal Projects/TMN_BlenderPlugin"
ADDON_PATH = os.path.join(ROOT, "__init__.py")
OUTPUT_PATH = os.path.join(ROOT, "Skin_Corner_Cases_v507.blend")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_cases", ADDON_PATH)
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
        bsdf.inputs["Alpha"].default_value = color[3]
    return mat


def add_label(text, location, size):
    font_curve = bpy.data.curves.new(text[:32], "FONT")
    font_curve.body = text
    font_curve.align_x = "CENTER"
    font_curve.align_y = "CENTER"
    font_curve.size = size
    obj = bpy.data.objects.new(text[:32], font_curve)
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


def paint_face_rect(face_slots, origin, size, face_dir, slot):
    ox, oy, oz = origin
    sx, sy, sz = size
    for i in range(sx):
        for j in range(sy):
            for k in range(sz):
                coord = (ox + i, oy + j, oz + k)
                if face_dir == 0 and i == sx - 1:
                    face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot
                elif face_dir == 1 and i == 0:
                    face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot
                elif face_dir == 2 and j == sy - 1:
                    face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot
                elif face_dir == 3 and j == 0:
                    face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot
                elif face_dir == 4 and k == sz - 1:
                    face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot
                elif face_dir == 5 and k == 0:
                    face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot


def paint_face_subset(face_slots, coords, face_dir, slot):
    for coord in coords:
        face_slots[(coord[0], coord[1], coord[2], face_dir)] = slot


def main():
    module = load_addon()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    base_mat = make_mat("Slot 1 Base", (0.56, 0.52, 0.23, 1.0))
    slot2_mat = make_mat("Slot 2 Yellow Skin", (0.95, 0.84, 0.25, 1.0))
    slot3_mat = make_mat("Slot 3 Green Skin", (0.16, 0.74, 0.22, 1.0))
    slot4_mat = make_mat("Slot 4 Magenta Skin", (0.82, 0.12, 0.55, 1.0))

    root_name = "SkinCornerCases"
    cells = {}
    face_slots = {}
    voxel_size = 0.00176
    label_size = voxel_size * 1.5
    origin = Vector((0.0, 0.0, 0.0))

    cases = [
        ("A same-slot 90 corner", (0, 0, 0), (3, 3, 4)),
        ("B ledge/end cap", (6, 0, 0), (4, 3, 3)),
        ("C T junction", (12, 0, 0), (5, 3, 4)),
        ("D three-face corner", (20, 0, 0), (3, 3, 3)),
        ("E different-slot boundary", (26, 0, 0), (4, 3, 3)),
    ]

    for _, case_origin, case_size in cases:
        add_cuboid_cells(cells, case_origin, case_size)

    # A: two perpendicular same-slot walls meeting along a vertical edge.
    paint_face_rect(face_slots, (0, 0, 0), (3, 3, 4), 0, 1)
    paint_face_rect(face_slots, (0, 0, 0), (3, 3, 4), 2, 1)

    # B: vertical wall plus top ledge, exposing the end-cap/ledge corner.
    paint_face_rect(face_slots, (6, 0, 0), (4, 3, 3), 2, 2)
    paint_face_subset(face_slots, [(6 + i, 2, 2) for i in range(4)], 4, 2)

    # C: long continuous wall with a shorter perpendicular incoming strip.
    paint_face_rect(face_slots, (12, 0, 0), (5, 3, 4), 2, 2)
    paint_face_subset(face_slots, [(16, 2, k) for k in range(1, 4)], 0, 2)

    # D: three same-slot faces meet at one corner.
    paint_face_rect(face_slots, (20, 0, 0), (3, 3, 3), 0, 3)
    paint_face_rect(face_slots, (20, 0, 0), (3, 3, 3), 2, 3)
    paint_face_rect(face_slots, (20, 0, 0), (3, 3, 3), 4, 3)

    # E: perpendicular color slots compete at the same corner.
    paint_face_rect(face_slots, (26, 0, 0), (4, 3, 3), 0, 1)
    paint_face_rect(face_slots, (26, 0, 0), (4, 3, 3), 2, 2)
    paint_face_subset(face_slots, [(29, 2, k) for k in range(3)], 4, 3)

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
            -1.25 * voxel_size,
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
