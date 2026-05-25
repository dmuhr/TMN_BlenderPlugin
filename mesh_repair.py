def group_vertices_by_z(bm, decimals=6):
    groups = {}
    for vert in bm.verts:
        groups.setdefault(round(float(vert.co.z), decimals), []).append(vert)
    return groups


def ordered_loop_from_edges(loop_verts, loop_edges):
    if not loop_verts or not loop_edges:
        return []

    vert_ids = {vert.index for vert in loop_verts}
    adjacency = {vert.index: [] for vert in loop_verts}
    for edge in loop_edges:
        a, b = edge.verts
        if a.index in vert_ids and b.index in vert_ids:
            adjacency[a.index].append(b.index)
            adjacency[b.index].append(a.index)

    start = loop_verts[0].index
    ordered = [start]
    prev = None
    current = start
    safety = len(loop_verts) + 4
    while safety > 0:
        candidates = [idx for idx in adjacency[current] if idx != prev]
        if not candidates:
            break
        next_idx = candidates[0]
        if next_idx == start:
            break
        if next_idx in ordered:
            break
        ordered.append(next_idx)
        prev = current
        current = next_idx
        safety -= 1

    return ordered


def polygon_signed_area_xy(coords):
    area = 0.0
    for index in range(len(coords)):
        x1, y1 = coords[index]
        x2, y2 = coords[(index + 1) % len(coords)]
        area += (x1 * y2) - (x2 * y1)
    return area * 0.5


def reverse_loop_order_preserve_start(ordered):
    if len(ordered) <= 2:
        return ordered[:]
    return [ordered[0]] + list(reversed(ordered[1:]))


def align_loop_order_to_master(loop_verts, ordered, master_coords_xy):
    if len(ordered) != len(master_coords_xy):
        return ordered

    vert_map = {vert.index: vert for vert in loop_verts}
    coords_xy = [(vert_map[index].co.x, vert_map[index].co.y) for index in ordered]
    if polygon_signed_area_xy(coords_xy) * polygon_signed_area_xy(master_coords_xy) < 0.0:
        ordered = reverse_loop_order_preserve_start(ordered)
        coords_xy = [(vert_map[index].co.x, vert_map[index].co.y) for index in ordered]

    best_shift = 0
    best_score = None
    count = len(ordered)
    for shift in range(count):
        score = 0.0
        for index in range(count):
            x1, y1 = coords_xy[(index + shift) % count]
            x2, y2 = master_coords_xy[index]
            dx = x1 - x2
            dy = y1 - y2
            score += (dx * dx) + (dy * dy)
        if best_score is None or score < best_score:
            best_score = score
            best_shift = shift

    return [ordered[(index + best_shift) % count] for index in range(count)]


def compute_loop_offset_directions(loop_verts, ordered_indices, miter_limit=1.5):
    if len(ordered_indices) < 3:
        return {}

    vert_map = {vert.index: vert for vert in loop_verts}
    coords_xy = [(vert_map[index].co.x, vert_map[index].co.y) for index in ordered_indices]
    area = polygon_signed_area_xy(coords_xy)
    clockwise = area < 0.0

    directions = {}
    for index, vert_index in enumerate(ordered_indices):
        prev_vert = vert_map[ordered_indices[(index - 1) % len(ordered_indices)]]
        curr_vert = vert_map[vert_index]
        next_vert = vert_map[ordered_indices[(index + 1) % len(ordered_indices)]]

        prev_edge = Vector((curr_vert.co.x - prev_vert.co.x, curr_vert.co.y - prev_vert.co.y))
        next_edge = Vector((next_vert.co.x - curr_vert.co.x, next_vert.co.y - curr_vert.co.y))
        if prev_edge.length <= 1e-9 or next_edge.length <= 1e-9:
            continue

        prev_edge.normalize()
        next_edge.normalize()

        if clockwise:
            prev_normal = Vector((-prev_edge.y, prev_edge.x))
            next_normal = Vector((-next_edge.y, next_edge.x))
        else:
            prev_normal = Vector((prev_edge.y, -prev_edge.x))
            next_normal = Vector((next_edge.y, -next_edge.x))

        bisector = prev_normal + next_normal
        if bisector.length <= 1e-9:
            bisector = next_normal.copy()
        else:
            bisector.normalize()

        dot_value = max(0.25, bisector.dot(next_normal))
        directions[curr_vert.index] = (bisector, min(max(1.0, miter_limit), 1.0 / dot_value))

    return directions


def get_boundary_loop_orders(bm):
    boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
    if not boundary_edges:
        return []

    edge_set = set(boundary_edges)
    loops = []
    while edge_set:
        seed = edge_set.pop()
        stack = [seed]
        component_edges = [seed]
        component_verts = set(seed.verts)
        while stack:
            edge = stack.pop()
            for vert in edge.verts:
                for linked_edge in vert.link_edges:
                    if linked_edge in edge_set:
                        edge_set.remove(linked_edge)
                        stack.append(linked_edge)
                        component_edges.append(linked_edge)
                        component_verts.update(linked_edge.verts)

        ordered = ordered_loop_from_edges(list(component_verts), component_edges)
        if len(ordered) >= 3:
            loops.append((list(component_verts), ordered))
    return loops


def get_master_boundary_loop(bm):
    loops = get_boundary_loop_orders(bm)
    if not loops:
        return None, None

    def loop_score(item):
        loop_verts, ordered = item
        vert_map = {vert.index: vert for vert in loop_verts}
        avg_z = sum(vert_map[index].co.z for index in ordered) / len(ordered)
        return avg_z, len(ordered)

    return max(loops, key=loop_score)


def compute_xy_offset_data(bm, miter_limit=1.5):
    master_loop_verts, master_ordered = get_master_boundary_loop(bm)
    if not master_ordered:
        return {vert.index: (Vector((1.0, 0.0)), 1.0) for vert in bm.verts}

    master_vert_map = {vert.index: vert for vert in master_loop_verts}
    master_directions = compute_loop_offset_directions(master_loop_verts, master_ordered, miter_limit)

    master_samples = []
    for vert_index in master_ordered:
        vert = master_vert_map[vert_index]
        master_samples.append((
            vert.co.x,
            vert.co.y,
            master_directions.get(vert_index, (Vector((1.0, 0.0)), 1.0)),
        ))

    offset_data = {}
    for vert in bm.verts:
        best_sample = None
        best_distance = None
        for sample_x, sample_y, sample_data in master_samples:
            dx = vert.co.x - sample_x
            dy = vert.co.y - sample_y
            distance = (dx * dx) + (dy * dy)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_sample = sample_data
        offset_data[vert.index] = best_sample or (Vector((1.0, 0.0)), 1.0)
    return offset_data


def cleanup_boolean_mesh(context, obj, triangulate=False):
    set_active_object(context, obj)
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(
        threshold=meters_to_scene_units(context, 0.00001),
        use_unselected=False,
        use_sharp_edge_from_normals=False,
    )
    bpy.ops.mesh.dissolve_degenerate(threshold=meters_to_scene_units(context, 0.00001))
    bpy.ops.mesh.delete_loose()
    bpy.ops.mesh.normals_make_consistent(inside=False)
    if triangulate:
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.quads_convert_to_tris()
    bpy.ops.object.mode_set(mode='OBJECT')


def recalculate_normals_outside(context, obj):
    if obj is None or obj.type != 'MESH':
        return False

    set_active_object(context, obj)
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    return True


def segments_cross_xy(a1, a2, b1, b2, epsilon=1e-14):
    def orient(p1, p2, p3):
        return ((p2.x - p1.x) * (p3.y - p1.y)) - ((p2.y - p1.y) * (p3.x - p1.x))

    return (
        orient(a1, a2, b1) * orient(a1, a2, b2) < -epsilon and
        orient(b1, b2, a1) * orient(b1, b2, a2) < -epsilon
    )


def segment_intersection_xy(a1, a2, b1, b2):
    denominator = ((a1.x - a2.x) * (b1.y - b2.y)) - ((a1.y - a2.y) * (b1.x - b2.x))
    if abs(denominator) <= 1e-14:
        return None

    a_cross = (a1.x * a2.y) - (a1.y * a2.x)
    b_cross = (b1.x * b2.y) - (b1.y * b2.x)
    x = ((a_cross * (b1.x - b2.x)) - ((a1.x - a2.x) * b_cross)) / denominator
    y = ((a_cross * (b1.y - b2.y)) - ((a1.y - a2.y) * b_cross)) / denominator
    return Vector((x, y))


def edge_length_xy(mesh, edge):
    v1 = mesh.vertices[edge.vertices[0]].co
    v2 = mesh.vertices[edge.vertices[1]].co
    return hypot(v2.x - v1.x, v2.y - v1.y)


def count_crossed_quads_xy(mesh):
    crossed = 0
    for polygon in mesh.polygons:
        if len(polygon.vertices) != 4:
            continue
        coords = [mesh.vertices[index].co for index in polygon.vertices]
        if (
            segments_cross_xy(coords[0], coords[1], coords[2], coords[3]) or
            segments_cross_xy(coords[1], coords[2], coords[3], coords[0])
        ):
            crossed += 1
    return crossed


def count_projected_self_intersections_xy(mesh, limit=1000):
    intersections = 0
    edges = [
        edge for edge in mesh.edges
        if edge_length_xy(mesh, edge) > 1e-8
    ]
    for index, edge_a in enumerate(edges):
        a1 = mesh.vertices[edge_a.vertices[0]].co
        a2 = mesh.vertices[edge_a.vertices[1]].co
        for edge_b in edges[index + 1:]:
            if set(edge_a.vertices) & set(edge_b.vertices):
                continue
            b1 = mesh.vertices[edge_b.vertices[0]].co
            b2 = mesh.vertices[edge_b.vertices[1]].co
            if segments_cross_xy(a1, a2, b1, b2):
                intersections += 1
                if intersections >= limit:
                    return intersections
    return intersections


def sorted_quad_vertices_xy(mesh, vertex_indices):
    coords = [mesh.vertices[index].co for index in vertex_indices]
    center_x = sum(coord.x for coord in coords) / len(coords)
    center_y = sum(coord.y for coord in coords) / len(coords)
    return sorted(
        vertex_indices,
        key=lambda index: atan2(mesh.vertices[index].co.y - center_y, mesh.vertices[index].co.x - center_x),
    )


def same_xy(coord, xy, epsilon=1e-9):
    return abs(coord.x - xy[0]) <= epsilon and abs(coord.y - xy[1]) <= epsilon


def repair_projected_self_intersections_xy(mesh):
    edges = [
        edge for edge in mesh.edges
        if edge_length_xy(mesh, edge) > 1e-8
    ]
    move_targets = []

    for index, edge_a in enumerate(edges):
        a1 = mesh.vertices[edge_a.vertices[0]].co
        a2 = mesh.vertices[edge_a.vertices[1]].co
        length_a = edge_length_xy(mesh, edge_a)
        for edge_b in edges[index + 1:]:
            if set(edge_a.vertices) & set(edge_b.vertices):
                continue
            b1 = mesh.vertices[edge_b.vertices[0]].co
            b2 = mesh.vertices[edge_b.vertices[1]].co
            if not segments_cross_xy(a1, a2, b1, b2):
                continue

            length_b = edge_length_xy(mesh, edge_b)
            if length_a <= 1e-8 or length_b <= 1e-8:
                continue
            if min(length_a, length_b) > max(length_a, length_b) * 0.75:
                continue

            intersection = segment_intersection_xy(a1, a2, b1, b2)
            if intersection is None:
                continue

            short_edge = edge_a if length_a < length_b else edge_b
            for vertex_index in short_edge.vertices:
                coord = mesh.vertices[vertex_index].co
                move_targets.append(((coord.x, coord.y), (intersection.x, intersection.y)))

    if not move_targets:
        return 0

    unique_targets = {}
    for source_xy, target_xy in move_targets:
        key = (round(source_xy[0], 9), round(source_xy[1], 9))
        unique_targets[key] = target_xy

    moved = 0
    for vertex in mesh.vertices:
        for source_key, target_xy in unique_targets.items():
            if same_xy(vertex.co, source_key):
                vertex.co.x = target_xy[0]
                vertex.co.y = target_xy[1]
                moved += 1
                break

    if moved:
        mesh.update()
    return len(unique_targets)


def mesh_xy_key(coord, decimals=6):
    return (round(coord.x, decimals), round(coord.y, decimals))


def repair_sharp_join_columns_xy(mesh):
    xy_to_vertices = {}
    for vertex in mesh.vertices:
        xy_to_vertices.setdefault(mesh_xy_key(vertex.co), []).append(vertex.index)

    adjacency = {key: set() for key in xy_to_vertices}
    for edge in mesh.edges:
        key_a = mesh_xy_key(mesh.vertices[edge.vertices[0]].co)
        key_b = mesh_xy_key(mesh.vertices[edge.vertices[1]].co)
        if key_a == key_b:
            continue
        adjacency.setdefault(key_a, set()).add(key_b)
        adjacency.setdefault(key_b, set()).add(key_a)

    repairs = []
    for center_key, neighbors in adjacency.items():
        if len(neighbors) != 4:
            continue

        neighbor_list = list(neighbors)
        closest_pair = None
        closest_distance = None
        for index, key_a in enumerate(neighbor_list):
            for key_b in neighbor_list[index + 1:]:
                distance = hypot(key_b[0] - key_a[0], key_b[1] - key_a[1])
                if closest_distance is None or distance < closest_distance:
                    closest_distance = distance
                    closest_pair = (key_a, key_b)

        if closest_pair is None or closest_distance is None or closest_distance > 0.0025:
            continue

        other_keys = [key for key in neighbor_list if key not in closest_pair]
        if len(other_keys) != 2:
            continue

        center = Vector(center_key)
        side_a = Vector(closest_pair[0])
        side_b = Vector(closest_pair[1])
        far_a = Vector(other_keys[0])
        far_b = Vector(other_keys[1])
        rail = far_b - far_a
        if rail.length <= 1e-9:
            continue

        t = max(0.0, min(1.0, (center - far_a).dot(rail) / rail.dot(rail)))
        projected_center = far_a + (rail * t)
        shift = (projected_center - center) * 0.6

        bridge = (side_a + side_b) * 0.5
        bridge.y += shift.y
        cleaned_center = center + shift

        repairs.append((closest_pair, center_key, bridge, cleaned_center))

    if not repairs:
        return 0

    repaired_keys = set()
    for side_keys, center_key, bridge, cleaned_center in repairs:
        for vertex in mesh.vertices:
            key = mesh_xy_key(vertex.co)
            if key in side_keys:
                vertex.co.x = bridge.x
                vertex.co.y = bridge.y
                repaired_keys.add(key)
            elif key == center_key:
                vertex.co.x = cleaned_center.x
                vertex.co.y = cleaned_center.y
                repaired_keys.add(key)

    mesh.update()
    return len(repairs)


def repair_crossed_quads_xy(mesh):
    repaired = 0
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    faces = []
    material_indices = []

    for polygon in mesh.polygons:
        vertex_indices = list(polygon.vertices)
        if len(vertex_indices) == 4:
            coords = [mesh.vertices[index].co for index in vertex_indices]
            if (
                segments_cross_xy(coords[0], coords[1], coords[2], coords[3]) or
                segments_cross_xy(coords[1], coords[2], coords[3], coords[0])
            ):
                vertex_indices = sorted_quad_vertices_xy(mesh, vertex_indices)
                repaired += 1
        faces.append(vertex_indices)
        material_indices.append(polygon.material_index)

    if repaired == 0:
        return 0

    mesh.clear_geometry()
    mesh.from_pydata([tuple(vertex) for vertex in vertices], [], faces)
    mesh.update(calc_edges=True)
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
    return repaired


def build_thickened_surface_mesh(
    context,
    source_obj,
    target_obj,
    thickness,
    offset,
    triangulate=False,
    miter_limit=1.5,
    repair_sharp_joins=True,
):
    source_bm = bmesh.new()
    source_bm.from_mesh(source_obj.data)
    source_bm.verts.ensure_lookup_table()
    source_bm.edges.ensure_lookup_table()
    source_bm.faces.ensure_lookup_table()

    if not source_bm.faces:
        source_bm.free()
        return 0, 0, 0, 0

    offset_data = compute_xy_offset_data(source_bm, miter_limit)
    inward_distance = max(0.0, thickness * (1.0 - offset) * 0.5)
    outward_distance = max(0.0, thickness * (1.0 + offset) * 0.5)

    build_bm = bmesh.new()
    inner_map = {}
    outer_map = {}
    for vert in source_bm.verts:
        direction, scale = offset_data.get(vert.index, (Vector((1.0, 0.0)), 1.0))
        inner_co = vert.co.copy()
        outer_co = vert.co.copy()
        inner_co.x -= direction.x * inward_distance * scale
        inner_co.y -= direction.y * inward_distance * scale
        outer_co.x += direction.x * outward_distance * scale
        outer_co.y += direction.y * outward_distance * scale
        inner_map[vert.index] = build_bm.verts.new(inner_co)
        outer_map[vert.index] = build_bm.verts.new(outer_co)

    build_bm.verts.ensure_lookup_table()
    for face in source_bm.faces:
        try:
            build_bm.faces.new([inner_map[vert.index] for vert in face.verts])
        except ValueError:
            pass
        try:
            build_bm.faces.new(list(reversed([outer_map[vert.index] for vert in face.verts])))
        except ValueError:
            pass

    for edge in source_bm.edges:
        if not edge.is_boundary:
            continue
        v1, v2 = edge.verts
        quad = [
            inner_map[v1.index],
            inner_map[v2.index],
            outer_map[v2.index],
            outer_map[v1.index],
        ]
        try:
            build_bm.faces.new(quad)
        except ValueError:
            pass

    build_bm.normal_update()
    build_bm.to_mesh(target_obj.data)
    target_obj.data.update()
    build_bm.free()
    source_bm.free()

    repaired_columns = repair_sharp_join_columns_xy(target_obj.data) if repair_sharp_joins else 0
    repaired_intersections = repair_projected_self_intersections_xy(target_obj.data) if repair_sharp_joins else 0
    repaired_quads = repair_crossed_quads_xy(target_obj.data) if repair_sharp_joins else 0
    cleanup_boolean_mesh(context, target_obj, triangulate=triangulate)
    remaining_intersections = count_projected_self_intersections_xy(target_obj.data)
    return len(inner_map), repaired_columns + repaired_intersections + repaired_quads, count_crossed_quads_xy(target_obj.data), remaining_intersections


