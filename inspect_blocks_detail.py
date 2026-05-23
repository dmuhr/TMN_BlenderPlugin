import bpy
import bmesh
from mathutils import Vector

name = "SM_41_ BUILDING_P _v1.001_Blocks"
obj = bpy.data.objects[name]

depsgraph = bpy.context.evaluated_depsgraph_get()
eval_obj = obj.evaluated_get(depsgraph)
mesh = bpy.data.meshes.new_from_object(eval_obj, depsgraph=depsgraph)

bm = bmesh.new()
bm.from_mesh(mesh)
bm.verts.ensure_lookup_table()
bm.edges.ensure_lookup_table()
bm.faces.ensure_lookup_table()
bm.normal_update()

boundary_edges = [e for e in bm.edges if len(e.link_faces) == 1]
nonmanifold_edges = [e for e in bm.edges if len(e.link_faces) != 2]
wire_edges = [e for e in bm.edges if len(e.link_faces) == 0]
over_edges = [e for e in bm.edges if len(e.link_faces) > 2]

components = []
unseen = set(bm.faces)
while unseen:
    seed = unseen.pop()
    stack = [seed]
    faces = [seed]
    verts = set(seed.verts)
    edges = set(seed.edges)
    while stack:
        face = stack.pop()
        for edge in face.edges:
            edges.add(edge)
            for vert in edge.verts:
                verts.add(vert)
            for linked in edge.link_faces:
                if linked in unseen:
                    unseen.remove(linked)
                    stack.append(linked)
                    faces.append(linked)
                    verts.update(linked.verts)
                    edges.update(linked.edges)
    comp_boundary = sum(1 for e in edges if len(e.link_faces) == 1)
    bb_min = Vector((min(v.co.x for v in verts), min(v.co.y for v in verts), min(v.co.z for v in verts)))
    bb_max = Vector((max(v.co.x for v in verts), max(v.co.y for v in verts), max(v.co.z for v in verts)))
    components.append((len(faces), len(verts), len(edges), comp_boundary, tuple(round(v, 6) for v in bb_min), tuple(round(v, 6) for v in bb_max)))

components.sort(reverse=True)

print("BLOCKS_DETAIL_START")
print("object:", obj.name)
print("verts edges faces:", len(bm.verts), len(bm.edges), len(bm.faces))
print("boundary nonmanifold wire overconnected:", len(boundary_edges), len(nonmanifold_edges), len(wire_edges), len(over_edges))
print("components:", len(components))
print("top_components faces verts edges boundary bbox_min bbox_max:")
for comp in components[:20]:
    print(comp)

face_sizes = {}
for face in bm.faces:
    face_sizes[len(face.verts)] = face_sizes.get(len(face.verts), 0) + 1
print("face_vertex_counts:", sorted(face_sizes.items()))

boundary_dirs = {"x": 0, "y": 0, "z": 0, "mixed": 0}
for edge in boundary_edges[:20000]:
    d = edge.verts[1].co - edge.verts[0].co
    vals = [abs(d.x), abs(d.y), abs(d.z)]
    max_i = vals.index(max(vals))
    if vals[max_i] < 1e-9:
        boundary_dirs["mixed"] += 1
    elif sum(v > 1e-9 for v in vals) == 1:
        boundary_dirs["xyz"[max_i]] += 1
    else:
        boundary_dirs["mixed"] += 1
print("boundary_edge_direction_sample:", boundary_dirs)
print("signed_volume:", bm.calc_volume(signed=True))
print("BLOCKS_DETAIL_END")

bm.free()
bpy.data.meshes.remove(mesh)
