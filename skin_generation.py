def get_enabled_color_skin_slots(settings):
    enabled_slots = []
    for slot_index in range(4):
        if getattr(settings, f"color_skin_slot_{slot_index + 1}"):
            enabled_slots.append(slot_index)
    return enabled_slots


def get_skin_slot_solidify_thickness_mm(settings, slot_index):
    return float(getattr(settings, f"skin_slot_{slot_index + 1}_solidify_thickness_mm", settings.skin_solidify_thickness_mm))


def get_skin_slot_solidify_offset(settings, slot_index):
    return float(getattr(settings, f"skin_slot_{slot_index + 1}_solidify_offset", settings.skin_solidify_offset))


def get_skin_subdivision_steps(settings):
    return max(2, min(16, int(getattr(settings, "skin_subdivision_steps", 8))))


def get_skin_slot_thickness_steps(settings, slot_index):
    subdivisions = get_skin_subdivision_steps(settings)
    return max(1, min(subdivisions, int(getattr(settings, f"skin_slot_{slot_index + 1}_thickness_steps", 2))))


def get_skin_slot_solid_region_enabled(settings, slot_index):
    return bool(getattr(settings, f"skin_slot_{slot_index + 1}_solid_region", True))


def get_skin_slot_solid_region_mode(settings, slot_index):
    mode = getattr(settings, f"skin_slot_{slot_index + 1}_solid_region_mode", None)
    if mode in {'OFF', 'TOP', 'COLUMN'}:
        return mode
    return 'COLUMN' if get_skin_slot_solid_region_enabled(settings, slot_index) else 'OFF'


def get_skin_slab_offsets(thickness, offset):
    thickness = max(0.0, float(thickness))
    offset = max(-1.0, min(1.0, float(offset)))
    outer_shift = thickness * (offset + 1.0) * 0.5
    inner_shift = thickness * (offset - 1.0) * 0.5
    return outer_shift, inner_shift


def get_quantized_vertex_key(co, decimals=9):
    return (
        round(float(co.x), decimals),
        round(float(co.y), decimals),
        round(float(co.z), decimals),
    )


def get_skin_face_dir_from_poly(mesh, poly):
    face_dir = get_face_attribute_value(mesh, "mv_face_dir", poly.index, -1)
    if 0 <= face_dir < 6:
        return face_dir

    normal = poly.normal
    axis = max(range(3), key=lambda item: abs(normal[item]))
    if axis == 0:
        return 0 if normal.x >= 0.0 else 1
    if axis == 1:
        return 2 if normal.y >= 0.0 else 3
    return 4 if normal.z >= 0.0 else 5


def get_skin_face_dir_priority(face_dir):
    priorities = {
        4: 0,
        5: 1,
        0: 2,
        1: 3,
        2: 4,
        3: 5,
    }
    return priorities.get(int(face_dir), 99)


def get_axis_aligned_face_box(mesh, poly, outer_shift, inner_shift):
    normal = poly.normal.copy()
    if normal.length < 1e-12:
        return None
    normal.normalize()

    shifted_points = []
    for vertex_index in poly.vertices:
        co = mesh.vertices[vertex_index].co
        shifted_points.append(co + (normal * outer_shift))
        shifted_points.append(co + (normal * inner_shift))

    box_min = [round(min(point[axis] for point in shifted_points), 9) for axis in range(3)]
    box_max = [round(max(point[axis] for point in shifted_points), 9) for axis in range(3)]
    if any(box_max[axis] - box_min[axis] <= 1e-12 for axis in range(3)):
        return None
    return box_min, box_max


def get_edge_direction_axis(mesh, edge_vertices):
    start = mesh.vertices[edge_vertices[0]].co
    end = mesh.vertices[edge_vertices[1]].co
    delta = end - start
    return max(range(3), key=lambda axis: abs(delta[axis]))


def get_skin_corner_owner(poly_a, face_dir_a, poly_b, face_dir_b):
    priority_a = get_skin_face_dir_priority(face_dir_a)
    priority_b = get_skin_face_dir_priority(face_dir_b)
    if priority_a != priority_b:
        return poly_a.index if priority_a < priority_b else poly_b.index
    return min(poly_a.index, poly_b.index)


def build_skin_mesh_from_occupied_grid(context, source_obj, root_name, source_name, slot_index, coords, occupied):
    if not occupied:
        return None

    verts = []
    faces = []
    vertex_map = {}

    def add_vertex(co):
        key = get_quantized_vertex_key(co)
        existing = vertex_map.get(key)
        if existing is not None:
            return existing
        index = len(verts)
        vertex_map[key] = index
        verts.append((float(co.x), float(co.y), float(co.z)))
        return index

    def vertex_at(x, y, z):
        return add_vertex(Vector((x, y, z)))

    for ix, iy, iz in sorted(occupied):
        x0, x1 = coords[0][ix], coords[0][ix + 1]
        y0, y1 = coords[1][iy], coords[1][iy + 1]
        z0, z1 = coords[2][iz], coords[2][iz + 1]

        if (ix - 1, iy, iz) not in occupied:
            faces.append((
                vertex_at(x0, y0, z0),
                vertex_at(x0, y0, z1),
                vertex_at(x0, y1, z1),
                vertex_at(x0, y1, z0),
            ))
        if (ix + 1, iy, iz) not in occupied:
            faces.append((
                vertex_at(x1, y0, z0),
                vertex_at(x1, y1, z0),
                vertex_at(x1, y1, z1),
                vertex_at(x1, y0, z1),
            ))
        if (ix, iy - 1, iz) not in occupied:
            faces.append((
                vertex_at(x0, y0, z0),
                vertex_at(x1, y0, z0),
                vertex_at(x1, y0, z1),
                vertex_at(x0, y0, z1),
            ))
        if (ix, iy + 1, iz) not in occupied:
            faces.append((
                vertex_at(x0, y1, z0),
                vertex_at(x0, y1, z1),
                vertex_at(x1, y1, z1),
                vertex_at(x1, y1, z0),
            ))
        if (ix, iy, iz - 1) not in occupied:
            faces.append((
                vertex_at(x0, y0, z0),
                vertex_at(x0, y1, z0),
                vertex_at(x1, y1, z0),
                vertex_at(x1, y0, z0),
            ))
        if (ix, iy, iz + 1) not in occupied:
            faces.append((
                vertex_at(x0, y0, z1),
                vertex_at(x1, y0, z1),
                vertex_at(x1, y1, z1),
                vertex_at(x0, y1, z1),
            ))

    if not faces:
        return None

    mesh = bpy.data.meshes.new(f"{get_color_skin_name(root_name, slot_index)}_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.validate(clean_customdata=False)
    mesh.update()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    signed_volume = bm.calc_volume(signed=True)
    bm.free()
    if signed_volume < 0.0:
        mesh.flip_normals()
        mesh.update()

    if 0 <= slot_index < len(source_obj.data.materials):
        mesh.materials.append(source_obj.data.materials[slot_index])

    obj = bpy.data.objects.new(get_color_skin_name(root_name, slot_index), mesh)
    obj.matrix_world = source_obj.matrix_world.copy()
    context.collection.objects.link(obj)
    set_metadata(obj, root_name, source_name)
    for poly in obj.data.polygons:
        poly.material_index = 0
    obj.data.update()
    return obj


def build_grid_mesh_object(context, object_name, source_obj, root_name, source_name, material, coords, occupied):
    if not occupied:
        return None

    verts = []
    faces = []
    vertex_map = {}

    def add_vertex(ix, iy, iz):
        key = (ix, iy, iz)
        existing = vertex_map.get(key)
        if existing is not None:
            return existing
        index = len(verts)
        vertex_map[key] = index
        verts.append((coords[0][ix], coords[1][iy], coords[2][iz]))
        return index

    for ix, iy, iz in sorted(occupied):
        if (ix - 1, iy, iz) not in occupied:
            faces.append((add_vertex(ix, iy, iz), add_vertex(ix, iy, iz + 1), add_vertex(ix, iy + 1, iz + 1), add_vertex(ix, iy + 1, iz)))
        if (ix + 1, iy, iz) not in occupied:
            faces.append((add_vertex(ix + 1, iy, iz), add_vertex(ix + 1, iy + 1, iz), add_vertex(ix + 1, iy + 1, iz + 1), add_vertex(ix + 1, iy, iz + 1)))
        if (ix, iy - 1, iz) not in occupied:
            faces.append((add_vertex(ix, iy, iz), add_vertex(ix + 1, iy, iz), add_vertex(ix + 1, iy, iz + 1), add_vertex(ix, iy, iz + 1)))
        if (ix, iy + 1, iz) not in occupied:
            faces.append((add_vertex(ix, iy + 1, iz), add_vertex(ix, iy + 1, iz + 1), add_vertex(ix + 1, iy + 1, iz + 1), add_vertex(ix + 1, iy + 1, iz)))
        if (ix, iy, iz - 1) not in occupied:
            faces.append((add_vertex(ix, iy, iz), add_vertex(ix, iy + 1, iz), add_vertex(ix + 1, iy + 1, iz), add_vertex(ix + 1, iy, iz)))
        if (ix, iy, iz + 1) not in occupied:
            faces.append((add_vertex(ix, iy, iz + 1), add_vertex(ix + 1, iy, iz + 1), add_vertex(ix + 1, iy + 1, iz + 1), add_vertex(ix, iy + 1, iz + 1)))

    if not faces:
        return None

    mesh = bpy.data.meshes.new(f"{object_name}_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.validate(clean_customdata=False)
    mesh.update()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    signed_volume = bm.calc_volume(signed=True)
    bm.free()
    if signed_volume < 0.0:
        mesh.flip_normals()
        mesh.update()

    if material is not None:
        mesh.materials.append(material)

    obj = bpy.data.objects.new(object_name, mesh)
    obj.matrix_world = source_obj.matrix_world.copy()
    context.collection.objects.link(obj)
    set_metadata(obj, root_name, source_name)
    for poly in obj.data.polygons:
        poly.material_index = 0
    obj.data.update()
    return obj


def get_subcell_face_ranges(cell, face_dir, subdivisions, thickness_steps):
    i, j, k = cell
    ranges = [
        [i * subdivisions, (i + 1) * subdivisions],
        [j * subdivisions, (j + 1) * subdivisions],
        [k * subdivisions, (k + 1) * subdivisions],
    ]
    axis = get_axis_for_face_dir(face_dir)
    steps = max(1, min(subdivisions, int(thickness_steps)))
    if face_dir in (0, 2, 4):
        ranges[axis][0] = ranges[axis][1] - steps
    else:
        ranges[axis][1] = ranges[axis][0] + steps
    return ranges


def add_subcell_box(occupied, ranges):
    for ix in range(ranges[0][0], ranges[0][1]):
        for iy in range(ranges[1][0], ranges[1][1]):
            for iz in range(ranges[2][0], ranges[2][1]):
                occupied.add((ix, iy, iz))


def build_subcell_mesh_object(context, object_name, source_obj, root_name, source_name, material, origin, subcell_size, occupied):
    if not occupied:
        return None

    verts = []
    faces = []
    vertex_map = {}

    def add_vertex(ix, iy, iz):
        key = (ix, iy, iz)
        existing = vertex_map.get(key)
        if existing is not None:
            return existing
        index = len(verts)
        vertex_map[key] = index
        verts.append((
            origin.x + (ix * subcell_size),
            origin.y + (iy * subcell_size),
            origin.z + (iz * subcell_size),
        ))
        return index

    def add_face(axis, sign, plane, u0, u1, v0, v1):
        if axis == 0:
            x = plane
            y0, y1 = u0, u1
            z0, z1 = v0, v1
            if sign < 0:
                face = (add_vertex(x, y0, z0), add_vertex(x, y0, z1), add_vertex(x, y1, z1), add_vertex(x, y1, z0))
            else:
                face = (add_vertex(x, y0, z0), add_vertex(x, y1, z0), add_vertex(x, y1, z1), add_vertex(x, y0, z1))
        elif axis == 1:
            y = plane
            x0, x1 = u0, u1
            z0, z1 = v0, v1
            if sign < 0:
                face = (add_vertex(x0, y, z0), add_vertex(x1, y, z0), add_vertex(x1, y, z1), add_vertex(x0, y, z1))
            else:
                face = (add_vertex(x0, y, z0), add_vertex(x0, y, z1), add_vertex(x1, y, z1), add_vertex(x1, y, z0))
        else:
            z = plane
            x0, x1 = u0, u1
            y0, y1 = v0, v1
            if sign < 0:
                face = (add_vertex(x0, y0, z), add_vertex(x0, y1, z), add_vertex(x1, y1, z), add_vertex(x1, y0, z))
            else:
                face = (add_vertex(x0, y0, z), add_vertex(x1, y0, z), add_vertex(x1, y1, z), add_vertex(x0, y1, z))
        faces.append(face)

    plane_faces = {}
    neighbor_offsets = (
        (-1, 0, 0, 0, -1),
        (1, 0, 0, 0, 1),
        (0, -1, 0, 1, -1),
        (0, 1, 0, 1, 1),
        (0, 0, -1, 2, -1),
        (0, 0, 1, 2, 1),
    )
    for ix, iy, iz in occupied:
        for dx, dy, dz, axis, sign in neighbor_offsets:
            if (ix + dx, iy + dy, iz + dz) in occupied:
                continue
            if axis == 0:
                plane = ix if sign < 0 else ix + 1
                rect = (iy, iz)
            elif axis == 1:
                plane = iy if sign < 0 else iy + 1
                rect = (ix, iz)
            else:
                plane = iz if sign < 0 else iz + 1
                rect = (ix, iy)
            plane_faces.setdefault((axis, sign, plane), set()).add(rect)

    for (axis, sign, plane), rects in plane_faces.items():
        remaining = set(rects)
        while remaining:
            u0, v0 = min(remaining, key=lambda item: (item[1], item[0]))
            width = 1
            while (u0 + width, v0) in remaining:
                width += 1
            height = 1
            can_grow = True
            while can_grow:
                for u in range(u0, u0 + width):
                    if (u, v0 + height) not in remaining:
                        can_grow = False
                        break
                if can_grow:
                    height += 1
            for u in range(u0, u0 + width):
                for v in range(v0, v0 + height):
                    remaining.remove((u, v))
            add_face(axis, sign, plane, u0, u0 + width, v0, v0 + height)

    if not faces:
        return None

    mesh = bpy.data.meshes.new(f"{object_name}_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.validate(clean_customdata=False)
    mesh.update()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    signed_volume = bm.calc_volume(signed=True)
    bm.free()
    if signed_volume < 0.0:
        mesh.flip_normals()
        mesh.update()

    if material is not None:
        mesh.materials.append(material)

    obj = bpy.data.objects.new(object_name, mesh)
    obj.matrix_world = source_obj.matrix_world.copy()
    context.collection.objects.link(obj)
    set_metadata(obj, root_name, source_name)
    for poly in obj.data.polygons:
        poly.material_index = 0
    obj.data.update()
    return obj


def build_fractional_skin_and_base_objects(context, source_obj, root_name, source_name, base_slot_index, slot_indices, steps_by_slot, subdivisions):
    origin = get_stored_voxel_origin(source_obj)
    voxel_size = float(source_obj.get("mv_voxel_size", 0.0))
    cells = deserialize_voxel_cells(source_obj)
    if origin is None or voxel_size <= 0.0 or not cells:
        return None, []

    source_mesh = source_obj.data
    subcell_size = voxel_size / float(subdivisions)
    selected_counts = {slot_index: 0 for slot_index in slot_indices}
    coord_sets = [set(), set(), set()]
    base_boxes = []
    skin_boxes = []
    skin_face_records = []
    edge_to_skin_records = {}
    enabled_visible_slots_by_cell = {}
    visible_slots_by_cell = {}
    top_slot_by_cell = {}
    top_skin_slot_by_cell = {}
    settings = context.scene.miniature_voxeler_settings
    use_solid_single_color_regions = bool(getattr(
        settings,
        "skin_solid_single_color_regions",
        True,
    ))
    solid_region_modes = {
        slot_index: get_skin_slot_solid_region_mode(settings, slot_index)
        for slot_index in slot_indices
        if get_skin_slot_solid_region_mode(settings, slot_index) != 'OFF'
    }

    for i, j, k in cells.keys():
        box_min = (i * subdivisions, j * subdivisions, k * subdivisions)
        box_max = ((i + 1) * subdivisions, (j + 1) * subdivisions, (k + 1) * subdivisions)
        base_boxes.append((box_min, box_max))
        for axis in range(3):
            coord_sets[axis].add(box_min[axis])
            coord_sets[axis].add(box_max[axis])

    for poly in source_mesh.polygons:
        slot_index = int(poly.material_index)
        cell = get_voxel_cell_key_from_mesh(source_mesh, poly.index)
        face_dir = get_skin_face_dir_from_poly(source_mesh, poly)
        if face_dir < 0 or face_dir >= 6:
            continue
        visible_slots_by_cell.setdefault(cell, set()).add(slot_index)
        if face_dir == 4:
            top_slot_by_cell[cell] = slot_index
        if slot_index in slot_indices:
            enabled_visible_slots_by_cell.setdefault(cell, set()).add(slot_index)
            if face_dir == 4:
                top_skin_slot_by_cell[cell] = slot_index
        if slot_index not in slot_indices:
            continue
        selected_counts[slot_index] += 1
        ranges = get_subcell_face_ranges(
            cell,
            face_dir,
            subdivisions,
            steps_by_slot.get(slot_index, 1),
        )
        box_min = (ranges[0][0], ranges[1][0], ranges[2][0])
        box_max = (ranges[0][1], ranges[1][1], ranges[2][1])
        skin_boxes.append((slot_index, box_min, box_max))
        record_index = len(skin_face_records)
        skin_face_records.append({
            "slot": slot_index,
            "poly_index": poly.index,
            "face_dir": face_dir,
            "ranges": ranges,
        })
        poly_vertices = list(poly.vertices)
        for a, b in zip(poly_vertices, poly_vertices[1:] + poly_vertices[:1]):
            edge_to_skin_records.setdefault(tuple(sorted((a, b))), []).append(record_index)
        for axis in range(3):
            coord_sets[axis].add(box_min[axis])
            coord_sets[axis].add(box_max[axis])

    for edge_key, record_indices in edge_to_skin_records.items():
        if len(record_indices) < 2:
            continue
        edge_axis = get_edge_direction_axis(source_mesh, edge_key)
        for first_index, record_a_index in enumerate(record_indices):
            record_a = skin_face_records[record_a_index]
            axis_a = get_axis_for_face_dir(record_a["face_dir"])
            if axis_a == edge_axis:
                continue
            for record_b_index in record_indices[first_index + 1:]:
                record_b = skin_face_records[record_b_index]
                if record_a["slot"] != record_b["slot"]:
                    continue
                axis_b = get_axis_for_face_dir(record_b["face_dir"])
                if axis_b == edge_axis or axis_a == axis_b:
                    continue

                corner_ranges = [[0, 0], [0, 0], [0, 0]]
                corner_ranges[edge_axis] = [
                    max(record_a["ranges"][edge_axis][0], record_b["ranges"][edge_axis][0]),
                    min(record_a["ranges"][edge_axis][1], record_b["ranges"][edge_axis][1]),
                ]
                if corner_ranges[edge_axis][1] <= corner_ranges[edge_axis][0]:
                    continue
                corner_ranges[axis_a] = list(record_a["ranges"][axis_a])
                corner_ranges[axis_b] = list(record_b["ranges"][axis_b])
                if any(corner_ranges[axis][1] <= corner_ranges[axis][0] for axis in range(3)):
                    continue

                box_min = (corner_ranges[0][0], corner_ranges[1][0], corner_ranges[2][0])
                box_max = (corner_ranges[0][1], corner_ranges[1][1], corner_ranges[2][1])
                skin_boxes.append((record_a["slot"], box_min, box_max))
                for axis in range(3):
                    coord_sets[axis].add(box_min[axis])
                    coord_sets[axis].add(box_max[axis])

    solid_cell_owner = {}
    if use_solid_single_color_regions:
        promotable_top_slot_by_cell = {}
        pending_top_cells = set(top_slot_by_cell.keys())
        while pending_top_cells:
            seed = pending_top_cells.pop()
            seed_z = seed[2]
            component = {seed}
            stack = [seed]
            component_slots = {top_slot_by_cell[seed]}
            while stack:
                i, j, k = stack.pop()
                for neighbor in ((i + 1, j, k), (i - 1, j, k), (i, j + 1, k), (i, j - 1, k)):
                    if neighbor[2] != seed_z or neighbor not in pending_top_cells:
                        continue
                    pending_top_cells.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
                    component_slots.add(top_slot_by_cell[neighbor])
            if len(component_slots) != 1:
                continue
            owner_slot = next(iter(component_slots))
            if owner_slot not in solid_region_modes:
                continue
            for component_cell in component:
                promotable_top_slot_by_cell[component_cell] = owner_slot

        for cell, owner_slot in sorted(promotable_top_slot_by_cell.items(), key=lambda item: item[0][2], reverse=True):
            solid_mode = solid_region_modes.get(owner_slot)
            if solid_mode is None:
                continue
            i, j, k = cell
            for z in range(k, -1, -1):
                if solid_mode == 'TOP' and z != k:
                    break
                column_cell = (i, j, z)
                if column_cell not in cells:
                    break
                current_owner = solid_cell_owner.get(column_cell)
                if current_owner is not None and current_owner != owner_slot:
                    break
                visible_slots = enabled_visible_slots_by_cell.get(column_cell, set())
                if z != k and any(visible_slot != owner_slot for visible_slot in visible_slots):
                    break
                if z != k and any(visible_slot != owner_slot for visible_slot in visible_slots_by_cell.get(column_cell, set())):
                    break
                solid_cell_owner[column_cell] = owner_slot

    coords_int = [sorted(values) for values in coord_sets]
    if any(len(axis_coords) < 2 for axis_coords in coords_int):
        return None, []
    coord_indices = [
        {value: index for index, value in enumerate(axis_coords)}
        for axis_coords in coords_int
    ]
    coords = [
        [origin[axis] + (value * subcell_size) for value in axis_coords]
        for axis, axis_coords in enumerate(coords_int)
    ]

    base_occupied_all = set()
    for box_min, box_max in base_boxes:
        min_indices = [coord_indices[axis][box_min[axis]] for axis in range(3)]
        max_indices = [coord_indices[axis][box_max[axis]] for axis in range(3)]
        for ix in range(min_indices[0], max_indices[0]):
            for iy in range(min_indices[1], max_indices[1]):
                for iz in range(min_indices[2], max_indices[2]):
                    base_occupied_all.add((ix, iy, iz))

    owner_by_cell = {}
    for slot_index, box_min, box_max in sorted(skin_boxes, key=lambda item: item[0]):
        min_indices = [coord_indices[axis][box_min[axis]] for axis in range(3)]
        max_indices = [coord_indices[axis][box_max[axis]] for axis in range(3)]
        for ix in range(min_indices[0], max_indices[0]):
            for iy in range(min_indices[1], max_indices[1]):
                for iz in range(min_indices[2], max_indices[2]):
                    key = (ix, iy, iz)
                    current_owner = owner_by_cell.get(key)
                    if current_owner is None or slot_index < current_owner:
                        owner_by_cell[key] = slot_index

    for cell, slot_index in solid_cell_owner.items():
        i, j, k = cell
        box_min = (i * subdivisions, j * subdivisions, k * subdivisions)
        box_max = ((i + 1) * subdivisions, (j + 1) * subdivisions, (k + 1) * subdivisions)
        min_indices = [coord_indices[axis][box_min[axis]] for axis in range(3)]
        max_indices = [coord_indices[axis][box_max[axis]] for axis in range(3)]
        for ix in range(min_indices[0], max_indices[0]):
            for iy in range(min_indices[1], max_indices[1]):
                for iz in range(min_indices[2], max_indices[2]):
                    owner_by_cell[(ix, iy, iz)] = slot_index

    occupied_by_slot = {slot_index: set() for slot_index in slot_indices}
    for key, slot_index in owner_by_cell.items():
        if slot_index in occupied_by_slot:
            occupied_by_slot[slot_index].add(key)

    base_occupied = base_occupied_all - set(owner_by_cell.keys())

    base_obj = build_grid_mesh_object(
        context,
        get_color_base_name(root_name),
        source_obj,
        root_name,
        source_name,
        None,
        coords,
        base_occupied,
    )
    if base_obj is not None:
        base_material = ensure_lego_color_material(
            base_obj,
            0,
            get_slot_palette_color(context.scene.miniature_voxeler_settings, base_slot_index),
        )
        apply_single_material_to_object(base_obj, base_material)

    skin_results = []
    for slot_index in slot_indices:
        material = source_mesh.materials[slot_index] if 0 <= slot_index < len(source_mesh.materials) else None
        skin_obj = build_grid_mesh_object(
            context,
            get_color_skin_name(root_name, slot_index),
            source_obj,
            root_name,
            source_name,
            material,
            coords,
            occupied_by_slot.get(slot_index, set()),
        )
        skin_results.append((slot_index, skin_obj, selected_counts.get(slot_index, 0)))

    return base_obj, skin_results


def build_boxes_skin_mesh(context, source_obj, root_name, source_name, slot_index, boxes):
    if not boxes:
        return None

    coord_sets = [set(), set(), set()]
    for box_min, box_max in boxes:
        for axis in range(3):
            coord_sets[axis].add(round(float(box_min[axis]), 9))
            coord_sets[axis].add(round(float(box_max[axis]), 9))

    coords = [sorted(values) for values in coord_sets]
    if any(len(axis_coords) < 2 for axis_coords in coords):
        return None

    coord_indices = [
        {value: index for index, value in enumerate(axis_coords)}
        for axis_coords in coords
    ]

    occupied = set()
    for box_min, box_max in boxes:
        rounded_min = [round(float(box_min[axis]), 9) for axis in range(3)]
        rounded_max = [round(float(box_max[axis]), 9) for axis in range(3)]
        min_indices = [coord_indices[axis][rounded_min[axis]] for axis in range(3)]
        max_indices = [coord_indices[axis][rounded_max[axis]] for axis in range(3)]
        for ix in range(min_indices[0], max_indices[0]):
            for iy in range(min_indices[1], max_indices[1]):
                for iz in range(min_indices[2], max_indices[2]):
                    occupied.add((ix, iy, iz))

    return build_skin_mesh_from_occupied_grid(context, source_obj, root_name, source_name, slot_index, coords, occupied)


def build_owned_skin_slab_objects(context, source_obj, root_name, source_name, slot_indices, thickness_by_slot, offset_by_slot):
    if source_obj is None or source_obj.type != 'MESH':
        return []

    source_mesh = source_obj.data
    coord_sets = [set(), set(), set()]
    box_records = []
    selected_counts = {slot_index: 0 for slot_index in slot_indices}

    for slot_index in slot_indices:
        outer_shift, inner_shift = get_skin_slab_offsets(
            thickness_by_slot.get(slot_index, 0.0),
            offset_by_slot.get(slot_index, 0.0),
        )
        for poly in source_mesh.polygons:
            if poly.material_index != slot_index:
                continue
            selected_counts[slot_index] += 1
            box = get_axis_aligned_face_box(source_mesh, poly, outer_shift, inner_shift)
            if box is None:
                continue
            box_min, box_max = box
            box_records.append((slot_index, tuple(box_min), tuple(box_max)))
            for axis in range(3):
                coord_sets[axis].add(box_min[axis])
                coord_sets[axis].add(box_max[axis])

    if not box_records:
        return [
            (slot_index, None, selected_counts.get(slot_index, 0))
            for slot_index in slot_indices
        ]

    coords = [sorted(values) for values in coord_sets]
    if any(len(axis_coords) < 2 for axis_coords in coords):
        return [
            (slot_index, None, selected_counts.get(slot_index, 0))
            for slot_index in slot_indices
        ]

    coord_indices = [
        {value: index for index, value in enumerate(axis_coords)}
        for axis_coords in coords
    ]

    owner_by_cell = {}
    for slot_index, box_min, box_max in sorted(box_records, key=lambda item: item[0]):
        min_indices = [coord_indices[axis][box_min[axis]] for axis in range(3)]
        max_indices = [coord_indices[axis][box_max[axis]] for axis in range(3)]
        for ix in range(min_indices[0], max_indices[0]):
            for iy in range(min_indices[1], max_indices[1]):
                for iz in range(min_indices[2], max_indices[2]):
                    cell = (ix, iy, iz)
                    current_owner = owner_by_cell.get(cell)
                    if current_owner is None or slot_index < current_owner:
                        owner_by_cell[cell] = slot_index

    occupied_by_slot = {slot_index: set() for slot_index in slot_indices}
    for cell, slot_index in owner_by_cell.items():
        if slot_index in occupied_by_slot:
            occupied_by_slot[slot_index].add(cell)

    results = []
    for slot_index in slot_indices:
        obj = build_skin_mesh_from_occupied_grid(
            context,
            source_obj,
            root_name,
            source_name,
            slot_index,
            coords,
            occupied_by_slot.get(slot_index, set()),
        )
        results.append((slot_index, obj, selected_counts.get(slot_index, 0)))
    return results


def build_skin_slab_object_from_material_slot(context, source_obj, root_name, source_name, slot_index, thickness, offset):
    if source_obj is None or source_obj.type != 'MESH':
        return None, 0

    source_mesh = source_obj.data
    source_polys = [poly for poly in source_mesh.polygons if poly.material_index == slot_index]
    if not source_polys:
        return None, 0

    outer_shift, inner_shift = get_skin_slab_offsets(thickness, offset)
    extension = abs(float(thickness))
    source_poly_indices = {poly.index for poly in source_polys}
    boxes_by_poly = {}
    face_dirs = {}
    edge_to_selected_polys = {}

    for poly in source_polys:
        box = get_axis_aligned_face_box(source_mesh, poly, outer_shift, inner_shift)
        if box is None:
            continue
        boxes_by_poly[poly.index] = [box[0], box[1]]
        face_dirs[poly.index] = get_skin_face_dir_from_poly(source_mesh, poly)
        poly_vertices = list(poly.vertices)
        for a, b in zip(poly_vertices, poly_vertices[1:] + poly_vertices[:1]):
            edge_to_selected_polys.setdefault(tuple(sorted((a, b))), []).append(poly.index)

    for edge_key, linked_indices in edge_to_selected_polys.items():
        linked_indices = [
            poly_index
            for poly_index in linked_indices
            if poly_index in source_poly_indices and poly_index in boxes_by_poly
        ]
        if len(linked_indices) < 2:
            continue

        for first_index, poly_a_index in enumerate(linked_indices):
            poly_a = source_mesh.polygons[poly_a_index]
            face_dir_a = face_dirs[poly_a_index]
            normal_axis_a = get_axis_for_face_dir(face_dir_a)
            edge_axis = get_edge_direction_axis(source_mesh, edge_key)
            if edge_axis == normal_axis_a:
                continue

            for poly_b_index in linked_indices[first_index + 1:]:
                face_dir_b = face_dirs[poly_b_index]
                normal_axis_b = get_axis_for_face_dir(face_dir_b)
                if normal_axis_a == normal_axis_b:
                    continue
                if edge_axis == normal_axis_b:
                    continue

                owner_index = get_skin_corner_owner(poly_a, face_dir_a, source_mesh.polygons[poly_b_index], face_dir_b)
                owner_face_dir = face_dirs[owner_index]
                owner_normal_axis = get_axis_for_face_dir(owner_face_dir)
                side_axis = next(
                    axis
                    for axis in range(3)
                    if axis != owner_normal_axis and axis != edge_axis
                )
                box_min, box_max = boxes_by_poly[owner_index]
                edge_value = source_mesh.vertices[edge_key[0]].co[side_axis]
                center_value = (box_min[side_axis] + box_max[side_axis]) * 0.5
                if edge_value >= center_value:
                    box_max[side_axis] = round(box_max[side_axis] + extension, 9)
                else:
                    box_min[side_axis] = round(box_min[side_axis] - extension, 9)

    boxes = [(tuple(box_min), tuple(box_max)) for box_min, box_max in boxes_by_poly.values()]
    obj = build_boxes_skin_mesh(context, source_obj, root_name, source_name, slot_index, boxes)

    return obj, len(source_polys)


def get_used_material_slots(obj):
    return sorted({poly.material_index for poly in obj.data.polygons})


def select_faces_by_material_slot(obj, slot_indices):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    selected_count = 0

    slot_index_set = set(slot_indices)
    for face in bm.faces:
        match = face.material_index in slot_index_set
        face.select = match
        if match:
            selected_count += 1

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected_count


def delete_non_material_faces(context, obj, keep_slot_index):
    set_active_object(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select = face.material_index != keep_slot_index

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')


def split_object_by_loose_parts(context, obj):
    set_active_object(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    pre_names = set(item.name for item in bpy.data.objects)
    bpy.ops.mesh.separate(type='LOOSE')
    bpy.ops.object.mode_set(mode='OBJECT')

    new_objects = find_new_objects(pre_names)
    if obj.name in bpy.data.objects:
        new_objects.append(obj)

    unique_objects = []
    seen = set()
    for item in new_objects:
        if item.name not in seen:
            unique_objects.append(item)
            seen.add(item.name)

    return unique_objects


def join_objects(context, objects, new_name):
    valid_objects = [obj for obj in objects if obj is not None and obj.name in bpy.data.objects]
    if not valid_objects:
        return None

    if len(valid_objects) == 1:
        valid_objects[0].name = new_name
        return valid_objects[0]

    bpy.ops.object.select_all(action='DESELECT')
    for obj in valid_objects:
        obj.select_set(True)
    context.view_layer.objects.active = valid_objects[0]
    bpy.ops.object.join()

    joined_obj = context.view_layer.objects.active
    if joined_obj is not None:
        joined_obj.name = new_name
    return joined_obj


def boolean_union_objects(context, objects, new_name, solver='EXACT'):
    valid_objects = []
    for obj in objects:
        if obj is None:
            continue
        try:
            obj_name = obj.name
        except ReferenceError:
            continue
        if obj_name in bpy.data.objects:
            valid_objects.append(obj)

    if not valid_objects:
        return None

    if len(valid_objects) == 1:
        valid_objects[0].name = new_name
        return valid_objects[0]

    target_obj = valid_objects[0]
    target_obj.name = new_name

    for index, cutter_obj in enumerate(valid_objects[1:]):
        set_active_object(context, target_obj)
        mod = target_obj.modifiers.new(name=f"Union_{index}", type='BOOLEAN')
        mod.operation = 'UNION'
        mod.object = cutter_obj
        if hasattr(mod, "solver"):
            mod.solver = solver
        if hasattr(mod, "use_self"):
            mod.use_self = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
        remove_object_if_exists(cutter_obj)

    return target_obj


def generate_north_south_skin_from_mask(context, mask_obj, settings, root_name, slot_index, source_name):
    axis_skin_objects = []
    temp_source = duplicate_object(context, mask_obj, f"{mask_obj.name}_TEMP")
    set_metadata(temp_source, root_name, source_name)

    fixed_axes = [
        ("+X", "X", Vector((1.0, 0.0, 0.0))),
        ("-X", "-X", Vector((-1.0, 0.0, 0.0))),
        ("+Y", "Y", Vector((0.0, 1.0, 0.0))),
        ("-Y", "-Y", Vector((0.0, -1.0, 0.0))),
        ("+Z", "Z", Vector((0.0, 0.0, 1.0))),
    ]

    for label, suffix, axis_vec in fixed_axes:
        if temp_source.name not in bpy.data.objects:
            break

        set_active_object(context, temp_source)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')

        bm = bmesh.from_edit_mesh(temp_source.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()

        selected_count = 0
        for face in bm.faces:
            match = face_matches_world_axis(temp_source, face, axis_vec)
            face.select = match
            if match:
                selected_count += 1

        bmesh.update_edit_mesh(temp_source.data, loop_triangles=False, destructive=False)

        if selected_count == 0:
            bpy.ops.object.mode_set(mode='OBJECT')
            continue

        pre_names = set(obj.name for obj in bpy.data.objects)
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')

        new_obj = find_new_object(pre_names, temp_source, context)
        if new_obj is None:
            continue

        new_obj.name = f"{get_color_skin_name(root_name, slot_index)}_{suffix}"
        set_metadata(new_obj, root_name, source_name)
        process_skin_object(context, new_obj, settings, axis_vec)
        axis_skin_objects.append(new_obj)

    if temp_source.name in bpy.data.objects:
        bpy.data.objects.remove(temp_source, do_unlink=True)

    joined_obj = boolean_union_objects(context, axis_skin_objects, get_color_skin_name(root_name, slot_index))
    return joined_obj


def process_skin_object(context, obj, settings, axis_vec):
    set_active_object(context, obj)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    # original separated faces = face-original
    base_faces = [f for f in bm.faces if f.select and f.is_valid]

    # 1) Outer Skin: extrude original faces outward
    outer_skin_distance = mm_to_scene_units(context, settings.outer_skin_mm)
    if outer_skin_distance > 0.0 and base_faces:
        ret = bmesh.ops.extrude_face_region(bm, geom=base_faces)

        new_verts = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMVert)]
        if new_verts:
            bmesh.ops.translate(
                bm,
                verts=new_verts,
                vec=(
                    outer_skin_distance * axis_vec.x,
                    outer_skin_distance * axis_vec.y,
                    outer_skin_distance * axis_vec.z,
                )
            )

    # 2) Reselect the ORIGINAL base faces
    for f in bm.faces:
        f.select = False

    for f in base_faces:
        if f.is_valid:
            f.select = True

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    # 3) Extrude original base faces in place
    bpy.ops.mesh.extrude_region_move(
        MESH_OT_extrude_region={
            "use_normal_flip": False,
            "mirror": False,
        },
        TRANSFORM_OT_translate={
            "value": (0.0, 0.0, 0.0),
        }
    )

    # 4) Inset
    bpy.ops.mesh.inset(
        thickness=settings.inset_amount,
        depth=0.0,
        use_boundary=True,
        use_even_offset=True,
        use_relative_offset=False,
        use_outset=False,
    )

    # 5) Move inward
    inside_skin_distance = abs(mm_to_scene_units(context, settings.inside_skin_mm))
    move_vec = (
        -inside_skin_distance * axis_vec.x,
        -inside_skin_distance * axis_vec.y,
        -inside_skin_distance * axis_vec.z,
    )

    bpy.ops.transform.translate(
        value=move_vec,
        orient_type='GLOBAL',
        constraint_axis=(
            axis_vec.x != 0.0,
            axis_vec.y != 0.0,
            axis_vec.z != 0.0,
        ),
    )

    bpy.ops.object.mode_set(mode='OBJECT')


def process_color_skin_object(context, obj, settings):
    set_active_object(context, obj)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    base_faces = [face for face in bm.faces if face.select and face.is_valid]

    outer_skin_distance = mm_to_scene_units(context, settings.outer_skin_mm)
    if outer_skin_distance > 0.0 and base_faces:
        ret = bmesh.ops.extrude_face_region(bm, geom=base_faces)
        new_verts = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMVert)]
        for vert in new_verts:
            normal = vert.normal.normalized()
            vert.co += normal * outer_skin_distance

    for face in bm.faces:
        face.select = False

    for face in base_faces:
        if face.is_valid:
            face.select = True

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    bpy.ops.mesh.extrude_region_move(
        MESH_OT_extrude_region={
            "use_normal_flip": False,
            "mirror": False,
        },
        TRANSFORM_OT_translate={
            "value": (0.0, 0.0, 0.0),
        }
    )

    bpy.ops.mesh.inset(
        thickness=settings.inset_amount,
        depth=0.0,
        use_boundary=True,
        use_even_offset=True,
        use_relative_offset=False,
        use_outset=False,
    )

    inside_skin_distance = -abs(mm_to_scene_units(context, settings.inside_skin_mm))
    bpy.ops.transform.shrink_fatten(
        value=inside_skin_distance,
        use_even_offset=True,
    )

    bpy.ops.object.mode_set(mode='OBJECT')


def build_inward_boolean_cutter_mesh(context, source_obj, target_obj, settings, epsilon=None):
    source_bm = bmesh.new()
    source_bm.from_mesh(source_obj.data)
    source_bm.verts.ensure_lookup_table()
    source_bm.faces.ensure_lookup_table()
    source_bm.normal_update()

    if not source_bm.faces:
        source_bm.free()
        return 0

    depth = abs(mm_to_scene_units(context, settings.inside_skin_mm))
    if epsilon is None:
        epsilon = max(depth * 0.05, meters_to_scene_units(context, 0.00002))

    verts = []
    faces = []
    material_indices = []

    for face in source_bm.faces:
        if not face.is_valid or len(face.verts) < 3:
            continue
        normal = face.normal.normalized()
        if normal.length <= 1e-12:
            continue

        outer_indices = []
        inner_indices = []
        for vert in face.verts:
            outer_indices.append(len(verts))
            verts.append(tuple(vert.co + (normal * epsilon)))
        for vert in face.verts:
            inner_indices.append(len(verts))
            verts.append(tuple(vert.co - (normal * (depth + epsilon))))

        faces.append(tuple(outer_indices))
        material_indices.append(face.material_index)
        faces.append(tuple(reversed(inner_indices)))
        material_indices.append(face.material_index)

        count = len(outer_indices)
        for index in range(count):
            next_index = (index + 1) % count
            faces.append((
                outer_indices[index],
                outer_indices[next_index],
                inner_indices[next_index],
                inner_indices[index],
            ))
            material_indices.append(face.material_index)

    target_obj.data.clear_geometry()
    target_obj.data.from_pydata(verts, [], faces)
    target_obj.data.update(calc_edges=True)
    for polygon, material_index in zip(target_obj.data.polygons, material_indices):
        polygon.material_index = material_index

    source_bm.free()
    cleanup_boolean_mesh(context, target_obj, triangulate=True)
    return len(faces)


def apply_boolean_difference(context, target_obj, cutter_obj, index, solver='EXACT'):
    set_active_object(context, target_obj)

    mod = target_obj.modifiers.new(name=f"Boolean_{index}", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter_obj

    if hasattr(mod, "solver"):
        mod.solver = solver

    bpy.ops.object.modifier_apply(modifier=mod.name)


