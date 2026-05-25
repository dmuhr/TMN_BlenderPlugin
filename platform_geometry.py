def show_info_popup(context, title, lines, icon='INFO'):
    def draw(self, _context):
        col = self.layout.column(align=True)
        for line in lines:
            col.label(text=line)

    context.window_manager.popup_menu(draw, title=title, icon=icon)


def get_connected_edge_loop_from_seed(bm, seed_edge):
    use_boundary_only = seed_edge.is_boundary
    stack = [seed_edge]
    visited = set()
    loop_edges = []

    while stack:
        edge = stack.pop()
        if edge.index in visited:
            continue
        visited.add(edge.index)

        if use_boundary_only and not edge.is_boundary:
            continue

        loop_edges.append(edge)

        for vert in edge.verts:
            for linked_edge in vert.link_edges:
                if linked_edge.index in visited:
                    continue
                if use_boundary_only and not linked_edge.is_boundary:
                    continue
                stack.append(linked_edge)

    return loop_edges


def select_connected_edge_loops_from_seeds(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    seed_edges = [edge for edge in bm.edges if edge.select]
    if not seed_edges:
        return 0

    selected_count = 0
    for seed_edge in seed_edges:
        for edge in get_connected_edge_loop_from_seed(bm, seed_edge):
            edge.select = True
            selected_count += 1

    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected_count


def get_selected_edge_groups(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    selected_edges = [edge for edge in bm.edges if edge.select]
    visited = set()
    groups = []
    for edge in selected_edges:
        if edge.index in visited:
            continue
        stack = [edge]
        group = []
        while stack:
            current = stack.pop()
            if current.index in visited or not current.select:
                continue
            visited.add(current.index)
            group.append(current)
            for vert in current.verts:
                for linked_edge in vert.link_edges:
                    if linked_edge.select and linked_edge.index not in visited:
                        stack.append(linked_edge)
        if group:
            groups.append(group)
    return groups


def world_xy_distance_squared(point_a, point_b):
    return (point_a.x - point_b.x) ** 2 + (point_a.y - point_b.y) ** 2


def get_missing_wall_xy_match_tolerance(cutter_world_verts, edge_world_verts):
    coords = cutter_world_verts + edge_world_verts
    if not coords:
        return 0.0005

    min_x = min(coord.x for coord in coords)
    max_x = max(coord.x for coord in coords)
    min_y = min(coord.y for coord in coords)
    max_y = max(coord.y for coord in coords)
    xy_span = hypot(max_x - min_x, max_y - min_y)
    return min(max(xy_span * 0.002, 0.00005), 0.002)


def find_target_wall_z_from_cutter(cutter_world_verts, world_point, xy_tolerance):
    if not cutter_world_verts:
        return world_point.z

    tolerance_squared = xy_tolerance * xy_tolerance
    matching_z_values = [
        vert.z for vert in cutter_world_verts
        if world_xy_distance_squared(world_point, vert) <= tolerance_squared
    ]
    if matching_z_values:
        return max(matching_z_values)

    # This fallback keeps the tool usable when the selected edge is slightly offset
    # from the cutter endpoint, but exact/almost-exact XY matches are preferred.
    best_distance = min(world_xy_distance_squared(world_point, vert) for vert in cutter_world_verts)
    nearest_z_values = [
        vert.z for vert in cutter_world_verts
        if world_xy_distance_squared(world_point, vert) <= best_distance + 0.0000000001
    ]
    return max(nearest_z_values) if nearest_z_values else world_point.z


def get_mesh_edge_groups(mesh):
    visited = set()
    edge_to_verts = {edge.index: tuple(edge.vertices) for edge in mesh.edges}
    vert_to_edges = {}
    for edge_index, vert_indices in edge_to_verts.items():
        for vert_index in vert_indices:
            vert_to_edges.setdefault(vert_index, []).append(edge_index)

    groups = []
    for edge in mesh.edges:
        if edge.index in visited:
            continue

        stack = [edge.index]
        group = []
        while stack:
            edge_index = stack.pop()
            if edge_index in visited:
                continue
            visited.add(edge_index)
            group.append(edge_index)
            for vert_index in edge_to_verts[edge_index]:
                for linked_edge_index in vert_to_edges.get(vert_index, []):
                    if linked_edge_index not in visited:
                        stack.append(linked_edge_index)
        groups.append(group)
    return groups


def order_edge_group_vertices(mesh, edge_indices):
    neighbors_by_vert = {}
    for edge_index in edge_indices:
        vert_a, vert_b = mesh.edges[edge_index].vertices
        neighbors_by_vert.setdefault(vert_a, []).append(vert_b)
        neighbors_by_vert.setdefault(vert_b, []).append(vert_a)

    if not neighbors_by_vert:
        return []

    endpoints = [vert_index for vert_index, neighbors in neighbors_by_vert.items() if len(neighbors) == 1]
    start_vert = endpoints[0] if endpoints else next(iter(neighbors_by_vert))
    ordered = [start_vert]
    previous_vert = None
    current_vert = start_vert

    while True:
        next_vert = None
        for candidate in neighbors_by_vert.get(current_vert, []):
            if candidate != previous_vert:
                next_vert = candidate
                break
        if next_vert is None or next_vert in ordered:
            break
        ordered.append(next_vert)
        previous_vert, current_vert = current_vert, next_vert

    return ordered


def get_interpolated_top_z_by_vertex(mesh, edge_indices, edge_world_verts, cutter_world_verts, xy_tolerance):
    ordered_vert_indices = order_edge_group_vertices(mesh, edge_indices)
    if len(ordered_vert_indices) < 2:
        return {}

    # The two ends of the selected missing-wall chain are A and B. Their nearest
    # existing cutter-wall vertices at the same XY provide the top Z values.
    start_world = edge_world_verts[ordered_vert_indices[0]]
    end_world = edge_world_verts[ordered_vert_indices[-1]]
    start_z = find_target_wall_z_from_cutter(cutter_world_verts, start_world, xy_tolerance)
    end_z = find_target_wall_z_from_cutter(cutter_world_verts, end_world, xy_tolerance)

    distances = [0.0]
    for index in range(1, len(ordered_vert_indices)):
        previous_world = edge_world_verts[ordered_vert_indices[index - 1]]
        current_world = edge_world_verts[ordered_vert_indices[index]]
        distances.append(distances[-1] + (current_world - previous_world).length)

    total_distance = distances[-1]
    if total_distance <= 0.0:
        return {vert_index: start_z for vert_index in ordered_vert_indices}

    top_z_by_vertex = {}
    for index, vert_index in enumerate(ordered_vert_indices):
        blend = distances[index] / total_distance
        top_z_by_vertex[vert_index] = start_z + (end_z - start_z) * blend
    return top_z_by_vertex


def build_missing_wall_faces_from_edge_object(cutter_obj, edge_obj):
    if cutter_obj is None or edge_obj is None:
        return 0

    edge_mesh = edge_obj.data
    if not edge_mesh.edges:
        return 0

    cutter_world_to_local = cutter_obj.matrix_world.inverted()
    cutter_world_verts = [cutter_obj.matrix_world @ vert.co for vert in cutter_obj.data.vertices]
    edge_world_verts = [edge_obj.matrix_world @ vert.co for vert in edge_mesh.vertices]
    if not cutter_world_verts or not edge_world_verts:
        return 0

    bm = bmesh.new()
    bm.from_mesh(cutter_obj.data)
    xy_tolerance = get_missing_wall_xy_match_tolerance(cutter_world_verts, edge_world_verts)

    created_faces = 0
    for edge_indices in get_mesh_edge_groups(edge_mesh):
        top_z_by_vertex = get_interpolated_top_z_by_vertex(
            edge_mesh,
            edge_indices,
            edge_world_verts,
            cutter_world_verts,
            xy_tolerance,
        )

        for edge_index in edge_indices:
            edge = edge_mesh.edges[edge_index]
            bottom_a_world = edge_world_verts[edge.vertices[0]]
            bottom_b_world = edge_world_verts[edge.vertices[1]]
            top_a_world = Vector((
                bottom_a_world.x,
                bottom_a_world.y,
                top_z_by_vertex.get(edge.vertices[0], bottom_a_world.z),
            ))
            top_b_world = Vector((
                bottom_b_world.x,
                bottom_b_world.y,
                top_z_by_vertex.get(edge.vertices[1], bottom_b_world.z),
            ))

            # Each selected platform edge becomes one vertical quad, extruded up
            # to the A/B heights inferred from the neighboring wall cutter.
            verts = [
                bm.verts.new(cutter_world_to_local @ bottom_a_world),
                bm.verts.new(cutter_world_to_local @ bottom_b_world),
                bm.verts.new(cutter_world_to_local @ top_b_world),
                bm.verts.new(cutter_world_to_local @ top_a_world),
            ]
            try:
                bm.faces.new(verts)
                created_faces += 1
            except ValueError:
                pass

    bm.normal_update()
    bm.to_mesh(cutter_obj.data)
    cutter_obj.data.update()
    bm.free()
    return created_faces


def selected_edge_group_z_values(edge_group):
    values = []
    seen = set()
    for edge in edge_group:
        for vert in edge.verts:
            if vert.index not in seen:
                seen.add(vert.index)
                values.append(vert.co.z)
    return values


def extrude_foot_gap_edges_on_z(obj, top_edge_indices, top_up_distance, lower_edge_indices=None, lower_down_distance=0.0):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    top_edges = [bm.edges[index] for index in top_edge_indices if 0 <= index < len(bm.edges)]
    lower_edges = [bm.edges[index] for index in (lower_edge_indices or []) if 0 <= index < len(bm.edges)]
    if not top_edges:
        bm.free()
        return 0

    top_extruded_count = 0
    lower_extruded_count = 0

    if lower_down_distance > 0.0 and lower_edges:
        result = bmesh.ops.extrude_edge_only(bm, edges=lower_edges)
        down_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in down_verts:
            vert.co.z -= lower_down_distance
        lower_extruded_count = len(down_verts)

    if top_up_distance > 0.0:
        result = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
        up_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in up_verts:
            vert.co.z += top_up_distance
        top_extruded_count = len(up_verts)

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return top_extruded_count, lower_extruded_count


def get_ordered_loop_coords(obj, edge_indices):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    loop_edges = [bm.edges[index] for index in edge_indices if 0 <= index < len(bm.edges)]
    if not loop_edges:
        bm.free()
        return []

    vert_to_edges = {}
    for edge in loop_edges:
        for vert in edge.verts:
            vert_to_edges.setdefault(vert.index, []).append(edge)

    start_edge = loop_edges[0]
    start_vert = start_edge.verts[0]
    ordered_verts = [start_vert]
    current_vert = start_edge.verts[1]
    current_edge = start_edge
    visited_edges = {start_edge.index}

    safety_limit = len(loop_edges) + 5
    while len(visited_edges) < len(loop_edges) and safety_limit > 0:
        ordered_verts.append(current_vert)
        next_edge = None
        for candidate in vert_to_edges.get(current_vert.index, []):
            if candidate.index != current_edge.index and candidate.index not in visited_edges:
                next_edge = candidate
                break
        if next_edge is None:
            break
        visited_edges.add(next_edge.index)
        current_edge = next_edge
        current_vert = next_edge.other_vert(current_vert)
        safety_limit -= 1

    if current_vert.index != ordered_verts[0].index:
        ordered_verts.append(current_vert)

    coords = [(vert.co.x, vert.co.y, vert.co.z) for vert in ordered_verts]
    bm.free()

    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords.pop()

    return coords


def xy_distance_sq_coords(a, b):
    return ((a[0] - b[0]) ** 2) + ((a[1] - b[1]) ** 2)


def find_snap_cluster(clusters, coord, tolerance_sq):
    best_index = None
    best_distance = None
    for index, cluster in enumerate(clusters):
        distance = xy_distance_sq_coords(cluster["coord"], coord)
        if distance <= tolerance_sq and (best_distance is None or distance < best_distance):
            best_index = index
            best_distance = distance
    return best_index


def get_or_create_snap_cluster(clusters, coord, tolerance_sq):
    cluster_index = find_snap_cluster(clusters, coord, tolerance_sq)
    if cluster_index is None:
        clusters.append({
            "coord": (float(coord[0]), float(coord[1]), float(coord[2])),
            "count": 1,
        })
        return len(clusters) - 1

    cluster = clusters[cluster_index]
    count = cluster["count"]
    current = cluster["coord"]
    cluster["coord"] = (
        ((current[0] * count) + float(coord[0])) / (count + 1),
        ((current[1] * count) + float(coord[1])) / (count + 1),
        ((current[2] * count) + float(coord[2])) / (count + 1),
    )
    cluster["count"] = count + 1
    return cluster_index


def get_graph_components(adjacency):
    components = []
    visited = set()
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    stack.append(neighbor)
        components.append(component)
    return components


def bridge_endpoint_gaps(adjacency, clusters, tolerance_sq):
    endpoints = [node for node in adjacency if len(adjacency.get(node, [])) == 1]
    bridged_count = 0
    while len(endpoints) >= 2:
        best_pair = None
        best_distance = None
        for index, node_a in enumerate(endpoints):
            for node_b in endpoints[index + 1:]:
                if node_b in adjacency[node_a]:
                    continue
                distance = xy_distance_sq_coords(clusters[node_a]["coord"], clusters[node_b]["coord"])
                if distance <= tolerance_sq and (best_distance is None or distance < best_distance):
                    best_pair = (node_a, node_b)
                    best_distance = distance

        if best_pair is None:
            break

        node_a, node_b = best_pair
        adjacency[node_a].add(node_b)
        adjacency[node_b].add(node_a)
        bridged_count += 1
        endpoints = [node for node in adjacency if len(adjacency.get(node, [])) == 1]
    return bridged_count


def order_closed_graph_component(adjacency, component, clusters):
    if len(component) < 3:
        return [], "too few vertices"

    bad_degree_nodes = [node for node in component if len(adjacency.get(node, [])) != 2]
    if bad_degree_nodes:
        open_count = sum(1 for node in component if len(adjacency.get(node, [])) == 1)
        branch_count = sum(1 for node in component if len(adjacency.get(node, [])) > 2)
        return [], f"{open_count} open end(s), {branch_count} branch point(s)"

    start = min(
        component,
        key=lambda node: (
            clusters[node]["coord"][0],
            clusters[node]["coord"][1],
            clusters[node]["coord"][2],
        ),
    )
    ordered = [start]
    previous = None
    current = start

    for _ in range(len(component) + 2):
        neighbors = sorted(adjacency[current])
        next_node = None
        for candidate in neighbors:
            if candidate != previous:
                next_node = candidate
                break
        if next_node is None:
            break
        if next_node == start:
            if len(ordered) == len(component):
                coords = [clusters[node]["coord"] for node in ordered]
                return coords, None
            break
        if next_node in ordered:
            break
        ordered.append(next_node)
        previous, current = current, next_node

    return [], "could not walk a single closed boundary"


def resolve_selected_edge_rings(obj, edge_indices, gap_tolerance):
    mesh = obj.data
    clusters = []
    edge_pairs = set()
    tolerance_sq = max(0.0, float(gap_tolerance)) ** 2

    for edge_index in edge_indices:
        if edge_index < 0 or edge_index >= len(mesh.edges):
            continue
        edge = mesh.edges[edge_index]
        node_indices = []
        for vertex_index in edge.vertices:
            coord = mesh.vertices[vertex_index].co
            node_indices.append(get_or_create_snap_cluster(
                clusters,
                (coord.x, coord.y, coord.z),
                tolerance_sq,
            ))
        if node_indices[0] == node_indices[1]:
            continue
        edge_pairs.add(tuple(sorted(node_indices)))

    adjacency = {index: set() for index in range(len(clusters))}
    for node_a, node_b in edge_pairs:
        adjacency[node_a].add(node_b)
        adjacency[node_b].add(node_a)

    rings = []
    messages = []
    bridged_total = bridge_endpoint_gaps(adjacency, clusters, tolerance_sq)
    for component in get_graph_components(adjacency):
        if len(component) < 3:
            continue
        coords, error = order_closed_graph_component(adjacency, component, clusters)
        if coords:
            rings.append(coords)
        elif error:
            messages.append(error)

    return rings, bridged_total, messages


def get_selected_edge_index_groups_from_mesh(mesh, edge_indices):
    selected_edge_set = set(edge_indices)
    edge_to_verts = {
        edge.index: tuple(edge.vertices)
        for edge in mesh.edges
        if edge.index in selected_edge_set
    }
    vert_to_edges = {}
    for edge_index, vertices in edge_to_verts.items():
        for vertex_index in vertices:
            vert_to_edges.setdefault(vertex_index, []).append(edge_index)

    groups = []
    visited = set()
    for edge_index in edge_to_verts:
        if edge_index in visited:
            continue
        stack = [edge_index]
        group = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            group.append(current)
            for vertex_index in edge_to_verts[current]:
                for linked_edge_index in vert_to_edges.get(vertex_index, []):
                    if linked_edge_index not in visited:
                        stack.append(linked_edge_index)
        if group:
            groups.append(group)
    return groups


def resolve_selected_edge_rings_relaxed(obj, edge_indices):
    rings = []
    for edge_group in get_selected_edge_index_groups_from_mesh(obj.data, edge_indices):
        coords = get_ordered_loop_coords(obj, edge_group)
        if len(coords) >= 3 and loop_edges_form_closed_boundary(obj.data, edge_group):
            rings.append(coords)
    return rings


def loop_edges_form_closed_boundary(mesh, edge_indices):
    vert_degrees = {}
    valid_edge_count = 0
    for edge_index in edge_indices:
        if edge_index < 0 or edge_index >= len(mesh.edges):
            continue
        valid_edge_count += 1
        for vertex_index in mesh.edges[edge_index].vertices:
            vert_degrees[vertex_index] = vert_degrees.get(vertex_index, 0) + 1
    return valid_edge_count >= 3 and vert_degrees and all(degree == 2 for degree in vert_degrees.values())


def clear_stored_platform_rings_data(obj):
    for key in ("mv_platform_top_rings_json", "mv_platform_top_ring", "mv_platform_lower_z"):
        if key in obj:
            del obj[key]


def resolve_all_edge_rings(obj, gap_tolerance=0.0):
    edge_indices = [edge.index for edge in obj.data.edges]
    if not edge_indices:
        return [], 0, ["rings object has no edges"]
    return resolve_selected_edge_rings(obj, edge_indices, gap_tolerance)


def rebuild_tube_from_top_loop(obj, top_edge_indices, target_bottom_z, top_up_distance, bottom_down_distance=0.0):
    loop_coords = get_ordered_loop_coords(obj, top_edge_indices)
    if len(loop_coords) < 3:
        return 0, 0, 0

    bm = bmesh.new()
    top_verts = [bm.verts.new(coord) for coord in loop_coords]
    bm.verts.ensure_lookup_table()

    top_edges = []
    for index in range(len(top_verts)):
        edge = bm.edges.new((top_verts[index], top_verts[(index + 1) % len(top_verts)]))
        top_edges.append(edge)

    top_z = max(coord[2] for coord in loop_coords)
    down_distance = max(0.0, top_z - target_bottom_z)

    bottom_edges = []
    tube_side_count = 0
    if down_distance > 0.0:
        result = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
        new_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in new_verts:
            vert.co.z = target_bottom_z
        bottom_vert_set = set(new_verts)
        bottom_edges = [
            item for item in result["geom"]
            if isinstance(item, bmesh.types.BMEdge) and item.verts[0] in bottom_vert_set and item.verts[1] in bottom_vert_set
        ]
        tube_side_count = len(new_verts)

    top_up_count = 0
    if top_up_distance > 0.0:
        result = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
        up_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in up_verts:
            vert.co.z += top_up_distance
        top_up_count = len(up_verts)

    bottom_down_count = 0
    if bottom_down_distance > 0.0 and bottom_edges:
        result = bmesh.ops.extrude_edge_only(bm, edges=bottom_edges)
        down_verts = [item for item in result["geom"] if isinstance(item, bmesh.types.BMVert)]
        for vert in down_verts:
            vert.co.z -= bottom_down_distance
        bottom_down_count = len(down_verts)

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return tube_side_count, top_up_count, bottom_down_count


def set_mesh_to_ring(obj, coords):
    verts = [tuple(coord) for coord in coords]
    edges = [(index, (index + 1) % len(verts)) for index in range(len(verts))]
    obj.data.clear_geometry()
    obj.data.from_pydata(verts, edges, [])
    obj.data.update()


def set_mesh_to_rings(obj, rings):
    verts = []
    edges = []
    for coords in rings:
        if len(coords) < 3:
            continue
        start_index = len(verts)
        verts.extend(tuple(coord) for coord in coords)
        count = len(coords)
        edges.extend(
            (start_index + index, start_index + ((index + 1) % count))
            for index in range(count)
        )

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, edges, [])
    obj.data.update()


def enable_view3d_xray(context):
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            if hasattr(space.shading, "show_xray"):
                space.shading.show_xray = True
            if hasattr(space.shading, "show_xray_wireframe"):
                space.shading.show_xray_wireframe = True


def select_lasso_tool():
    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.select_lasso")
    except Exception:
        pass


def get_selected_vertex_world_coords(context):
    source_obj = context.edit_object if context.mode == 'EDIT_MESH' else context.object
    if source_obj is None or source_obj.type != 'MESH':
        return None, []

    coords = []
    if context.mode == 'EDIT_MESH' and context.edit_object == source_obj:
        bm = bmesh.from_edit_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        coords = [source_obj.matrix_world @ vert.co for vert in bm.verts if vert.select]
    else:
        coords = [source_obj.matrix_world @ vert.co for vert in source_obj.data.vertices if vert.select]
    return source_obj, coords


def append_world_vertices_to_object(obj, world_coords):
    if not world_coords:
        return 0

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    local_coords = [obj.matrix_world.inverted() @ coord for coord in world_coords]
    mesh = obj.data
    old_vert_count = len(mesh.vertices)
    verts = [vert.co.copy() for vert in mesh.vertices] + local_coords
    edges = [tuple(edge.vertices) for edge in mesh.edges]
    faces = [tuple(poly.vertices) for poly in mesh.polygons]

    mesh.clear_geometry()
    mesh.from_pydata([tuple(vert) for vert in verts], edges, faces)
    mesh.update(calc_edges=True)

    for vertex in mesh.vertices:
        vertex.select = vertex.index >= old_vert_count
    for edge in mesh.edges:
        edge.select = False
    for poly in mesh.polygons:
        poly.select = False
    mesh.update()
    clear_stored_platform_rings_data(obj)
    return len(local_coords)


def bridge_selected_vertices_on_object(obj, max_segment_length):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    selected_verts = [vert for vert in bm.verts if vert.select]
    if len(selected_verts) < 2 or len(selected_verts) % 2 != 0:
        return None

    existing_edges = {frozenset(edge.verts) for edge in bm.edges}
    created_count = 0
    created_vertex_count = 0
    created_edges = []
    selected_verts.sort(key=lambda vert: vert.index)
    for index in range(0, len(selected_verts), 2):
        vert_a = selected_verts[index]
        vert_b = selected_verts[index + 1]
        segment = vert_b.co - vert_a.co
        length = segment.length
        segment_count = 1
        if max_segment_length > 0.0 and length > max_segment_length:
            segment_count = max(1, int(length / max_segment_length + 0.999999))

        chain = [vert_a]
        for segment_index in range(1, segment_count):
            t = segment_index / segment_count
            new_vert = bm.verts.new(vert_a.co.lerp(vert_b.co, t))
            chain.append(new_vert)
            created_vertex_count += 1
        chain.append(vert_b)
        bm.verts.ensure_lookup_table()

        for chain_index in range(len(chain) - 1):
            edge_a = chain[chain_index]
            edge_b = chain[chain_index + 1]
            key = frozenset((edge_a, edge_b))
            if key in existing_edges:
                continue
            try:
                new_edge = bm.edges.new((edge_a, edge_b))
                new_edge.select = True
                created_edges.append(new_edge)
                existing_edges.add(key)
                created_count += 1
            except ValueError:
                pass

    for vert in bm.verts:
        vert.select = False
    for edge in bm.edges:
        edge.select = edge in created_edges
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    clear_stored_platform_rings_data(obj)
    return created_count, created_vertex_count


def isolate_selected_edges_to_object(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    selected_edges = [edge for edge in bm.edges if edge.select]
    if not selected_edges:
        return 0, 0

    selected_vert_indices = []
    for edge in selected_edges:
        for vert in edge.verts:
            if vert.index not in selected_vert_indices:
                selected_vert_indices.append(vert.index)

    index_map = {old_index: new_index for new_index, old_index in enumerate(selected_vert_indices)}
    verts = [tuple(bm.verts[old_index].co) for old_index in selected_vert_indices]
    edges = [
        (index_map[edge.verts[0].index], index_map[edge.verts[1].index])
        for edge in selected_edges
    ]

    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.clear_geometry()
    obj.data.from_pydata(verts, edges, [])
    obj.data.update(calc_edges=True)
    for edge in obj.data.edges:
        edge.select = True
    for vertex in obj.data.vertices:
        vertex.select = True
    obj.data.update()
    clear_stored_platform_rings_data(obj)
    return len(verts), len(edges)


def store_platform_ring_data(obj, top_coords, lower_z=None):
    store_platform_rings_data(obj, [top_coords], lower_z)


def store_platform_rings_data(obj, rings, lower_z=None):
    clean_rings = [
        [(float(coord[0]), float(coord[1]), float(coord[2])) for coord in coords]
        for coords in rings
        if len(coords) >= 3
    ]
    obj["mv_platform_top_rings_json"] = json.dumps(clean_rings, separators=(",", ":"))
    if clean_rings:
        obj["mv_platform_top_ring"] = [value for coord in clean_rings[0] for value in coord]
    elif "mv_platform_top_ring" in obj:
        del obj["mv_platform_top_ring"]

    if lower_z is not None:
        obj["mv_platform_lower_z"] = float(lower_z)
    elif "mv_platform_lower_z" in obj:
        del obj["mv_platform_lower_z"]


def store_platform_lower_height(obj, lower_z):
    obj["mv_platform_lower_z"] = float(lower_z)


def get_stored_platform_lower_height(obj):
    lower_z = obj.get("mv_platform_lower_z", None)
    if lower_z is None:
        return None
    return float(lower_z)


def get_stored_platform_ring_data(obj):
    rings, lower_z = get_stored_platform_rings_data(obj)
    if not rings:
        return [], lower_z
    return rings[0], lower_z


def get_stored_platform_rings_data(obj):
    raw = obj.get("mv_platform_top_rings_json", "")
    flat = obj.get("mv_platform_top_ring", [])
    lower_z = get_stored_platform_lower_height(obj)
    if raw:
        try:
            values = json.loads(raw)
        except Exception:
            values = []
        rings = []
        for ring in values:
            if len(ring) < 3:
                continue
            coords = []
            for coord in ring:
                if len(coord) != 3:
                    coords = []
                    break
                coords.append((float(coord[0]), float(coord[1]), float(coord[2])))
            if len(coords) >= 3:
                rings.append(coords)
        if rings:
            return rings, lower_z

    if len(flat) < 9 or len(flat) % 3 != 0:
        return [], lower_z
    coords = [
        (float(flat[index]), float(flat[index + 1]), float(flat[index + 2]))
        for index in range(0, len(flat), 3)
    ]
    return [coords], lower_z


def polygon_area_from_coords_xy(coords):
    area = 0.0
    for index, coord in enumerate(coords):
        next_coord = coords[(index + 1) % len(coords)]
        area += (coord[0] * next_coord[1]) - (next_coord[0] * coord[1])
    return area * 0.5


def offset_ring_coords(coords, thickness, offset):
    if len(coords) < 3:
        return [], []

    clockwise = polygon_area_from_coords_xy(coords) < 0.0
    inward_distance = max(0.0, thickness * (1.0 - offset) * 0.5)
    outward_distance = max(0.0, thickness * (1.0 + offset) * 0.5)
    inner = []
    outer = []

    for index, coord in enumerate(coords):
        prev_coord = coords[(index - 1) % len(coords)]
        next_coord = coords[(index + 1) % len(coords)]
        prev_edge = Vector((coord[0] - prev_coord[0], coord[1] - prev_coord[1]))
        next_edge = Vector((next_coord[0] - coord[0], next_coord[1] - coord[1]))
        if prev_edge.length <= 1e-9 or next_edge.length <= 1e-9:
            direction = Vector((1.0, 0.0))
            scale = 1.0
        else:
            prev_edge.normalize()
            next_edge.normalize()
            if clockwise:
                prev_normal = Vector((-prev_edge.y, prev_edge.x))
                next_normal = Vector((-next_edge.y, next_edge.x))
            else:
                prev_normal = Vector((prev_edge.y, -prev_edge.x))
                next_normal = Vector((next_edge.y, -next_edge.x))
            direction = prev_normal + next_normal
            if direction.length <= 1e-9:
                direction = next_normal.copy()
            else:
                direction.normalize()
            scale = min(2.0, 1.0 / max(0.5, direction.dot(next_normal)))

        inner.append((coord[0] - direction.x * inward_distance * scale, coord[1] - direction.y * inward_distance * scale, coord[2]))
        outer.append((coord[0] + direction.x * outward_distance * scale, coord[1] + direction.y * outward_distance * scale, coord[2]))

    return inner, outer


def build_2d_thick_ring_mesh(obj, source_coords, thickness, offset):
    inner, outer = offset_ring_coords(source_coords, thickness, offset)
    if len(inner) < 3 or len(outer) < 3:
        return 0

    verts = inner + outer
    count = len(inner)
    faces = []
    for index in range(count):
        faces.append((
            index,
            (index + 1) % count,
            count + ((index + 1) % count),
            count + index,
        ))

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    return count


def build_2d_cutter_mesh(obj, source_coords, inner_distance, outer_distance):
    return build_2d_cutter_mesh_from_rings(obj, [source_coords], inner_distance, outer_distance)


def append_2d_cutter_ring_geometry(verts, faces, editable_faces, source_coords, inner_distance, outer_distance):
    inner, outer = offset_ring_coords(source_coords, inner_distance + outer_distance, 0.0)
    if len(inner) < 3 or len(outer) < 3:
        return 0

    if inner_distance != outer_distance:
        inner, _ = offset_ring_coords(source_coords, inner_distance * 2.0, 0.0)
        _, outer = offset_ring_coords(source_coords, outer_distance * 2.0, 0.0)

    original = [tuple(coord) for coord in source_coords]
    start_index = len(verts)
    verts.extend(inner + original + outer)
    count = len(original)

    for index in range(count):
        next_index = (index + 1) % count
        editable_faces.append(len(faces))
        faces.append((
            start_index + index,
            start_index + next_index,
            start_index + count + next_index,
            start_index + count + index,
        ))
        faces.append((
            start_index + count + index,
            start_index + count + next_index,
            start_index + (count * 2) + next_index,
            start_index + (count * 2) + index,
        ))
    return count


def build_2d_cutter_mesh_from_rings(obj, rings, inner_distance, outer_distance):
    verts = []
    faces = []
    editable_faces = []
    total_count = 0
    for source_coords in rings:
        total_count += append_2d_cutter_ring_geometry(
            verts,
            faces,
            editable_faces,
            source_coords,
            inner_distance,
            outer_distance,
        )

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    tag_platform_fill_faces(obj, editable_faces, preserve_existing=False)
    return total_count


def get_boundary_edge_loops_from_mesh(mesh):
    edge_use = {}
    for poly in mesh.polygons:
        vertices = list(poly.vertices)
        for edge in zip(vertices, vertices[1:] + vertices[:1]):
            key = tuple(sorted(edge))
            edge_use[key] = edge_use.get(key, 0) + 1

    boundary_edges = [edge for edge, count in edge_use.items() if count == 1]
    adjacency = {}
    for a, b in boundary_edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    loops = []
    visited_edges = set()
    for start_a, start_b in boundary_edges:
        start_key = tuple(sorted((start_a, start_b)))
        if start_key in visited_edges:
            continue

        loop = [start_a]
        previous = start_a
        current = start_b
        visited_edges.add(start_key)

        for _ in range(len(boundary_edges) + 2):
            loop.append(current)
            if current == loop[0]:
                break
            next_vert = None
            for candidate in adjacency.get(current, []):
                key = tuple(sorted((current, candidate)))
                if candidate != previous and key not in visited_edges:
                    next_vert = candidate
                    break
            if next_vert is None:
                for candidate in adjacency.get(current, []):
                    key = tuple(sorted((current, candidate)))
                    if key not in visited_edges:
                        next_vert = candidate
                        break
            if next_vert is None:
                break
            previous, current = current, next_vert
            visited_edges.add(tuple(sorted((previous, current))))

        if len(loop) >= 4 and loop[0] == loop[-1]:
            loop.pop()
        if len(loop) >= 3:
            loops.append(loop)

    return loops


def close_2d_cutter_inner_loop(context, obj):
    return close_2d_cutter_selected_loops(context, obj)


def tag_platform_fill_faces(obj, fill_face_indices, preserve_existing=True):
    fill_set = set(fill_face_indices)
    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0) if preserve_existing else [0] * len(obj.data.polygons)
    for poly in obj.data.polygons:
        if poly.index in fill_set:
            values[poly.index] = 1
    ensure_face_int_attribute(obj.data, "mv_platform_fill_face", values)
    obj.data.update()


def update_platform_fill_tag_from_selected_faces(obj):
    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0)
    selected_count = 0
    for poly in obj.data.polygons:
        if poly.select:
            values[poly.index] = 1
            selected_count += 1
    ensure_face_int_attribute(obj.data, "mv_platform_fill_face", values)
    obj.data.update()
    return selected_count


def select_platform_fill_faces(context, obj, invert=False):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0)
    selected_count = 0
    for poly in obj.data.polygons:
        should_select = values[poly.index] == 1
        if invert:
            should_select = not should_select
        poly.select = should_select
        if should_select:
            selected_count += 1

    obj.data.update()
    set_active_object(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    return selected_count


def assign_sculpt_face_sets_for_platform_fill(obj):
    values = get_face_int_attribute_values(obj.data, "mv_platform_fill_face", 0)
    face_set_values = [1 if value == 1 else 2 for value in values]
    ensure_face_int_attribute(obj.data, ".sculpt_face_set", face_set_values)
    obj.data.update()


def create_sculpt_face_set_from_current_selection(context):
    if context.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')

    for mode_name in ('SELECTION', 'EDIT_SELECTION'):
        try:
            bpy.ops.sculpt.face_sets_create(mode=mode_name)
            return True
        except Exception:
            pass
    return False


def enable_sculpt_face_set_automasking(context):
    sculpt_settings = getattr(context.tool_settings, "sculpt", None)
    if sculpt_settings is None:
        return False

    face_set_attrs = (
        "use_automasking_face_sets",
    )
    boundary_attrs = (
        "use_automasking_boundary_face_sets",
        "use_automasking_face_sets_boundary",
    )
    automasking_attrs = face_set_attrs + boundary_attrs
    enabled = False
    for attr_name in automasking_attrs:
        if hasattr(sculpt_settings, attr_name):
            setattr(sculpt_settings, attr_name, True)
            enabled = True
    brush = getattr(sculpt_settings, "brush", None)
    if brush is not None:
        for attr_name in automasking_attrs:
            if hasattr(brush, attr_name):
                setattr(brush, attr_name, True)
                enabled = True
    return enabled


SCULPT_BRUSH_TOOL_IDS = {
    'SMOOTH': ("builtin_brush.Smooth", "builtin_brush.smooth"),
    'GRAB': ("builtin_brush.Grab", "builtin_brush.grab"),
    'FLATTEN_CONTRAST': (
        "builtin_brush.Flatten",
        "builtin_brush.flatten",
        "builtin_brush.FlattenContrast",
        "builtin_brush.flatten_contrast",
    ),
    'RELAX_PINCH': (
        "builtin_brush.Relax",
        "builtin_brush.relax",
        "builtin_brush.RelaxPinch",
        "builtin_brush.relax_pinch",
        "builtin_brush.Relax_Pinch",
    ),
}

SCULPT_BRUSH_LABELS = {
    'SMOOTH': "Smooth",
    'GRAB': "Grab",
    'FLATTEN_CONTRAST': "Flatten/Contrast",
    'RELAX_PINCH': "Relax Pinch",
}


def select_sculpt_brush_tool(context, brush_type):
    if context.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')

    enable_sculpt_face_set_automasking(context)
    for tool_name in SCULPT_BRUSH_TOOL_IDS.get(brush_type, ()):
        try:
            bpy.ops.wm.tool_set_by_id(name=tool_name)
            enable_sculpt_face_set_automasking(context)
            return True
        except Exception:
            pass
    return False


def close_2d_cutter_selected_loops(context, obj):
    mesh = obj.data
    loops = get_boundary_edge_loops_from_mesh(mesh)
    if not loops:
        return 0, 0

    face_count_before = len(mesh.polygons)
    editable_values_before_fill = get_face_int_attribute_values(mesh, "mv_platform_fill_face", 0)

    set_active_object(context, obj)
    if context.mode != 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='EDGE')

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    selected_edge_keys = {
        tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        for edge in bm.edges
        if edge.select
    }
    if not selected_edge_keys:
        bpy.ops.object.mode_set(mode='OBJECT')
        return 0, 0

    selected_loops = []
    for loop in loops:
        loop_edge_keys = {
            tuple(sorted((loop[index], loop[(index + 1) % len(loop)])))
            for index in range(len(loop))
        }
        if loop_edge_keys and loop_edge_keys.issubset(selected_edge_keys):
            selected_loops.append(loop)

    if not selected_loops:
        selected_vertex_indices = {vert.index for vert in bm.verts if vert.select}
        for loop in loops:
            if all(index in selected_vertex_indices for index in loop):
                selected_loops.append(loop)

    if not selected_loops:
        bpy.ops.object.mode_set(mode='OBJECT')
        return 0, 0

    selected_loop_set = set(index for loop in selected_loops for index in loop)
    for vert in bm.verts:
        vert.select = vert.index in selected_loop_set
    for edge in bm.edges:
        edge.select = edge.verts[0].index in selected_loop_set and edge.verts[1].index in selected_loop_set
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

    try:
        bpy.ops.mesh.fill(use_beauty=True)
    except TypeError:
        bpy.ops.mesh.fill()

    bpy.ops.object.mode_set(mode='OBJECT')
    new_face_indices = [
        poly.index for poly in mesh.polygons
        if poly.index >= face_count_before
    ]
    editable_values = editable_values_before_fill + [0] * max(0, len(mesh.polygons) - len(editable_values_before_fill))
    for face_index in new_face_indices:
        editable_values[face_index] = 1
    ensure_face_int_attribute(mesh, "mv_platform_fill_face", editable_values)
    mesh.update()
    select_platform_fill_faces(context, obj)

    closed_vertex_count = sum(len(loop) for loop in selected_loops)
    return closed_vertex_count, max(0, len(mesh.polygons) - face_count_before)


def extrude_mesh_down_from_faces(obj, depth):
    source_verts = [vertex.co.copy() for vertex in obj.data.vertices]
    source_faces = [list(poly.vertices) for poly in obj.data.polygons]
    if not source_verts or not source_faces or depth <= 0.0:
        return 0

    bottom_verts = [Vector((vert.x, vert.y, vert.z - depth)) for vert in source_verts]
    verts = [tuple(vert) for vert in source_verts + bottom_verts]
    vert_count = len(source_verts)
    faces = []

    for face in source_faces:
        faces.append(tuple(face))
        faces.append(tuple(reversed([index + vert_count for index in face])))

    edge_use = {}
    for face in source_faces:
        for edge in zip(face, face[1:] + face[:1]):
            key = tuple(sorted(edge))
            edge_use[key] = edge_use.get(key, 0) + 1

    for a, b in edge_use:
        if edge_use[(a, b)] == 1:
            faces.append((a, b, b + vert_count, a + vert_count))

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    return len(faces)


def apply_smooth_remesh_modifier(context, obj, octree_depth=8, scale=0.9, remove_disconnected=False):
    if obj is None or obj.type != 'MESH':
        return False

    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    set_active_object(context, obj)
    modifier = obj.modifiers.new(name="CleanupSmoothRemesh", type='REMESH')
    modifier.mode = 'SMOOTH'
    modifier.octree_depth = octree_depth
    if hasattr(modifier, "scale"):
        modifier.scale = scale
    if hasattr(modifier, "use_remove_disconnected"):
        modifier.use_remove_disconnected = remove_disconnected
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    return True


def expand_mesh_xy_from_center(obj, distance):
    if obj is None or obj.type != 'MESH' or distance <= 0.0 or not obj.data.vertices:
        return 0

    center = Vector((0.0, 0.0))
    for vertex in obj.data.vertices:
        center.x += vertex.co.x
        center.y += vertex.co.y
    center /= len(obj.data.vertices)

    moved_count = 0
    for vertex in obj.data.vertices:
        direction = Vector((vertex.co.x - center.x, vertex.co.y - center.y))
        if direction.length <= 1e-9:
            continue
        direction.normalize()
        vertex.co.x += direction.x * distance
        vertex.co.y += direction.y * distance
        moved_count += 1

    obj.data.update()
    return moved_count


def extrude_cleaned_2d_mesh_to_3d(obj, top_offset, lower_z, bottom_down=0.0):
    source_verts = [vertex.co.copy() for vertex in obj.data.vertices]
    source_faces = [list(poly.vertices) for poly in obj.data.polygons]
    if not source_verts or not source_faces:
        return 0

    bottom_z = lower_z - max(0.0, bottom_down)
    top_verts = [Vector((vert.x, vert.y, vert.z + top_offset)) for vert in source_verts]
    bottom_verts = [Vector((vert.x, vert.y, bottom_z)) for vert in source_verts]
    verts = [tuple(vert) for vert in top_verts + bottom_verts]
    vert_count = len(source_verts)
    faces = []

    for face in source_faces:
        faces.append(tuple(face))
        faces.append(tuple(reversed([index + vert_count for index in face])))

    edge_use = {}
    for face in source_faces:
        for edge in zip(face, face[1:] + face[:1]):
            key = tuple(sorted(edge))
            edge_use[key] = edge_use.get(key, 0) + 1

    for a, b in edge_use:
        if edge_use[(a, b)] == 1:
            faces.append((a, b, b + vert_count, a + vert_count))

    obj.data.clear_geometry()
    obj.data.from_pydata(verts, [], faces)
    obj.data.update()
    return len(faces)


def enter_edit_vertex_wireframe(context, obj):
    set_active_object(context, obj)
    if context.mode != 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bpy.ops.mesh.select_all(action='DESELECT')


