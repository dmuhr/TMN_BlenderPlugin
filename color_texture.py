def ensure_bake_material(target_obj, image):
    mat_name = f"{target_obj.name}_Baked"
    mat = bpy.data.materials.get(mat_name)

    if mat is None:
        mat = bpy.data.materials.new(mat_name)

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (400, 0)

    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_bsdf.location = (120, 0)

    node_tex = nodes.new("ShaderNodeTexImage")
    node_tex.location = (-200, 0)
    node_tex.image = image

    links.new(node_tex.outputs["Color"], node_bsdf.inputs["Base Color"])
    links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Surface"])

    nodes.active = node_tex

    if target_obj.data.materials:
        target_obj.data.materials[0] = mat
    else:
        target_obj.data.materials.append(mat)

    return mat, node_tex


def load_texture_source_image(filepath):
    if not filepath:
        return None

    absolute_path = bpy.path.abspath(filepath)
    if not os.path.isfile(absolute_path):
        raise FileNotFoundError(f"Texture file was not found: {absolute_path}")

    for image in bpy.data.images:
        if image.filepath and os.path.abspath(bpy.path.abspath(image.filepath)) == os.path.abspath(absolute_path):
            if not image.has_data:
                image.reload()
            return image

    return bpy.data.images.load(absolute_path, check_existing=True)


def ensure_texture_source_file_material(source_obj, image, bake_type='DIFFUSE'):
    mat_name = f"{source_obj.name}_TextureFile_Source"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (400, 0)

    node_tex = nodes.new("ShaderNodeTexImage")
    node_tex.location = (-200, 0)
    node_tex.image = image

    if bake_type == 'EMIT':
        node_emit = nodes.new("ShaderNodeEmission")
        node_emit.location = (120, 0)
        links.new(node_tex.outputs["Color"], node_emit.inputs["Color"])
        links.new(node_emit.outputs["Emission"], node_output.inputs["Surface"])
    else:
        node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        node_bsdf.location = (120, 0)
        links.new(node_tex.outputs["Color"], node_bsdf.inputs["Base Color"])
        links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Surface"])
    nodes.active = node_tex

    if source_obj.data.materials:
        source_obj.data.materials[0] = mat
    else:
        source_obj.data.materials.append(mat)

    for poly in source_obj.data.polygons:
        poly.material_index = 0

    return mat


def ensure_bake_image(target_obj, size):
    img_name = f"{target_obj.name}_Color"
    img = bpy.data.images.get(img_name)

    if img is None:
        img = bpy.data.images.new(
            name=img_name,
            width=size,
            height=size,
            alpha=False,
        )
    else:
        if img.size[0] != size or img.size[1] != size:
            img.scale(size, size)

    img.generated_color = (0.0, 0.0, 0.0, 1.0)
    return img


def get_object_color_image(obj):
    materials = []

    if obj.active_material is not None:
        materials.append(obj.active_material)

    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat not in materials:
            materials.append(mat)

    fallback_image = None

    for mat in materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue

        nodes = mat.node_tree.nodes
        active_node = nodes.active
        if active_node and active_node.type == 'TEX_IMAGE' and active_node.image is not None:
            return active_node.image

        for node in nodes:
            if node.type == 'BSDF_PRINCIPLED':
                for link in node.inputs["Base Color"].links:
                    from_node = link.from_node
                    if from_node.type == 'TEX_IMAGE' and from_node.image is not None:
                        return from_node.image

        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image is not None and fallback_image is None:
                fallback_image = node.image

    return fallback_image


def clamp01(value):
    return max(0.0, min(1.0, value))


def sample_image_color(image, pixels, width, height, u, v):
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 0.0)

    u = u % 1.0
    v = v % 1.0

    x = min(width - 1, max(0, int(round(u * (width - 1)))))
    y = min(height - 1, max(0, int(round(v * (height - 1)))))
    index = ((y * width) + x) * 4

    return (
        float(pixels[index]),
        float(pixels[index + 1]),
        float(pixels[index + 2]),
    )


def average_color(colors):
    if not colors:
        return (0.0, 0.0, 0.0)

    count = float(len(colors))
    return (
        sum(color[0] for color in colors) / count,
        sum(color[1] for color in colors) / count,
        sum(color[2] for color in colors) / count,
    )


def median_color(colors):
    if not colors:
        return (0.0, 0.0, 0.0)

    sorted_r = sorted(color[0] for color in colors)
    sorted_g = sorted(color[1] for color in colors)
    sorted_b = sorted(color[2] for color in colors)
    mid = len(colors) // 2

    if len(colors) % 2 == 1:
        return (sorted_r[mid], sorted_g[mid], sorted_b[mid])

    return (
        (sorted_r[mid - 1] + sorted_r[mid]) * 0.5,
        (sorted_g[mid - 1] + sorted_g[mid]) * 0.5,
        (sorted_b[mid - 1] + sorted_b[mid]) * 0.5,
    )


def color_distance_sq(color_a, color_b):
    return (
        (color_a[0] - color_b[0]) ** 2 +
        (color_a[1] - color_b[1]) ** 2 +
        (color_a[2] - color_b[2]) ** 2
    )


def color_luminance(color):
    return (
        0.2126 * color[0] +
        0.7152 * color[1] +
        0.0722 * color[2]
    )


def get_polygon_texture_color(poly, uv_data, image, pixels, sample_mode):
    face_uvs = [uv_data[loop_index].uv.copy() for loop_index in poly.loop_indices]
    if not face_uvs:
        return (0.0, 0.0, 0.0)

    width = image.size[0]
    height = image.size[1]
    samples = [sample_image_color(image, pixels, width, height, uv.x, uv.y) for uv in face_uvs]

    if sample_mode == 'CENTER':
        center = Vector((0.0, 0.0))
        for uv in face_uvs:
            center += uv
        center /= len(face_uvs)
        return sample_image_color(image, pixels, width, height, center.x, center.y)

    if sample_mode == 'MEDIAN':
        return median_color(samples)

    return average_color(samples)


def get_polygon_texture_color_at_world_location(obj, mesh, poly, uv_data, image, pixels, world_location):
    if not poly.loop_indices:
        return (0.0, 0.0, 0.0)

    loop_indices = list(poly.loop_indices)
    if len(loop_indices) < 3:
        return get_polygon_texture_color(poly, uv_data, image, pixels, 'CENTER')

    matrix = obj.matrix_world
    best = None
    first_loop = loop_indices[0]
    triangle_loop_sets = []
    for index in range(1, len(loop_indices) - 1):
        triangle_loop_sets.append((first_loop, loop_indices[index], loop_indices[index + 1]))

    for loop_a, loop_b, loop_c in triangle_loop_sets:
        vert_a = matrix @ mesh.vertices[mesh.loops[loop_a].vertex_index].co
        vert_b = matrix @ mesh.vertices[mesh.loops[loop_b].vertex_index].co
        vert_c = matrix @ mesh.vertices[mesh.loops[loop_c].vertex_index].co
        closest = geometry.closest_point_on_tri(world_location, vert_a, vert_b, vert_c)
        distance_sq = (closest - world_location).length_squared
        if best is None or distance_sq < best[0]:
            best = (distance_sq, closest, loop_a, loop_b, loop_c, vert_a, vert_b, vert_c)

    if best is None:
        return get_polygon_texture_color(poly, uv_data, image, pixels, 'CENTER')

    _distance_sq, closest, loop_a, loop_b, loop_c, vert_a, vert_b, vert_c = best
    uv_a = uv_data[loop_a].uv
    uv_b = uv_data[loop_b].uv
    uv_c = uv_data[loop_c].uv
    uv = geometry.barycentric_transform(
        closest,
        vert_a,
        vert_b,
        vert_c,
        Vector((uv_a.x, uv_a.y, 0.0)),
        Vector((uv_b.x, uv_b.y, 0.0)),
        Vector((uv_c.x, uv_c.y, 0.0)),
    )
    return sample_image_color(image, pixels, image.size[0], image.size[1], uv.x, uv.y)


def get_source_polygon_color(obj, mesh, poly, image=None, pixels=None, sample_mode='CENTER', world_location=None):
    uv_data = mesh.uv_layers.active.data if mesh.uv_layers.active else None
    if image is not None and pixels is not None and uv_data is not None:
        if world_location is not None:
            return get_polygon_texture_color_at_world_location(obj, mesh, poly, uv_data, image, pixels, world_location)
        return get_polygon_texture_color(poly, uv_data, image, pixels, sample_mode)

    if 0 <= poly.material_index < len(obj.data.materials):
        return get_material_base_color(obj.data.materials[poly.material_index])

    return (0.8, 0.8, 0.8)


def build_world_bvh_from_mesh(obj, mesh):
    vertices = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    polygons = [list(poly.vertices) for poly in mesh.polygons]
    if not vertices or not polygons:
        return None
    return BVHTree.FromPolygons(vertices, polygons)


def collect_direct_source_face_colors(context, source_obj, target_obj, settings):
    depsgraph = context.evaluated_depsgraph_get()
    source_eval = source_obj.evaluated_get(depsgraph)
    source_mesh = source_eval.to_mesh()
    try:
        if not source_mesh.polygons:
            return []

        bvh = build_world_bvh_from_mesh(source_eval, source_mesh)
        if bvh is None:
            return []

        image = get_object_color_image(source_obj)
        pixels = None
        if image is not None:
            if not image.has_data:
                try:
                    image.reload()
                except Exception:
                    pass
            if image.size[0] > 0 and image.size[1] > 0:
                pixels = list(image.pixels[:])

        target_mesh = target_obj.data
        target_matrix = target_obj.matrix_world
        face_colors = []
        for poly in target_mesh.polygons:
            center = target_matrix @ get_polygon_center(target_mesh, poly)
            _location, _normal, source_face_index, _distance = bvh.find_nearest(center)
            if source_face_index is None or source_face_index < 0 or source_face_index >= len(source_mesh.polygons):
                face_colors.append((0.8, 0.8, 0.8))
                continue
            source_poly = source_mesh.polygons[source_face_index]
            face_colors.append(get_source_polygon_color(
                source_eval,
                source_mesh,
                source_poly,
                image=image,
                pixels=pixels,
                sample_mode=settings.lego_color_sample_mode,
                world_location=_location,
            ))
        return face_colors
    finally:
        source_eval.to_mesh_clear()


def build_adaptive_palette(face_colors, color_count):
    if not face_colors:
        return [(0.8, 0.8, 0.8)], [0]

    palette = [face_colors[0]]

    while len(palette) < min(color_count, len(face_colors)):
        farthest_color = max(
            face_colors,
            key=lambda color: min(color_distance_sq(color, seed) for seed in palette)
        )
        palette.append(farthest_color)

    for _ in range(8):
        clusters = [[] for _ in palette]
        for color in face_colors:
            index = min(range(len(palette)), key=lambda i: color_distance_sq(color, palette[i]))
            clusters[index].append(color)

        for i, cluster in enumerate(clusters):
            if cluster:
                palette[i] = average_color(cluster)

    assignments = [
        min(range(len(palette)), key=lambda i: color_distance_sq(color, palette[i]))
        for color in face_colors
    ]

    return palette, assignments


def build_luminance_palette(face_colors, color_count):
    if not face_colors:
        return [(0.8, 0.8, 0.8)], [0]

    sorted_indices = sorted(range(len(face_colors)), key=lambda i: color_luminance(face_colors[i]))
    group_count = min(color_count, len(face_colors))
    groups = [[] for _ in range(group_count)]
    assignments = [0] * len(face_colors)

    for rank, face_index in enumerate(sorted_indices):
        group_index = min(group_count - 1, (rank * group_count) // len(face_colors))
        groups[group_index].append(face_colors[face_index])
        assignments[face_index] = group_index

    palette = [average_color(group) if group else (0.8, 0.8, 0.8) for group in groups]
    return palette, assignments


def assign_distinct_fixed_palette_indices(palette):
    if not palette:
        return []

    available_indices = list(range(len(FIXED_LEGO_PALETTE)))
    fixed_colors = [color for _, color in FIXED_LEGO_PALETTE]
    assignments = [0] * len(palette)

    # Assign the hardest-to-match colors first so close colors don't consume the same fixed slot.
    palette_order = sorted(
        range(len(palette)),
        key=lambda palette_index: min(
            color_distance_sq(palette[palette_index], fixed_color)
            for fixed_color in fixed_colors
        ),
        reverse=True,
    )

    for palette_index in palette_order:
        fixed_index = min(
            available_indices,
            key=lambda index: color_distance_sq(palette[palette_index], fixed_colors[index]),
        )
        assignments[palette_index] = fixed_index
        available_indices.remove(fixed_index)

    return assignments


def sync_slot_palette_properties(settings, palette):
    distinct_indices = assign_distinct_fixed_palette_indices(palette)
    for slot_index in range(4):
        if slot_index < len(palette):
            fixed_index = distinct_indices[slot_index]
        else:
            fixed_index = int(getattr(settings, f"lego_palette_slot_{slot_index + 1}"))
        setattr(settings, f"lego_palette_slot_{slot_index + 1}", str(fixed_index))
        setattr(settings, f"lego_palette_slot_color_{slot_index + 1}", FIXED_LEGO_PALETTE[fixed_index][1])


def get_slot_palette_color(settings, slot_index):
    fixed_index = getattr(settings, f"lego_palette_slot_{slot_index + 1}")
    return get_fixed_palette_color(fixed_index)


def rebuild_materials_from_assignments(obj, settings, assignments, color_count):
    mesh = obj.data
    mesh.materials.clear()

    for slot_index in range(color_count):
        mesh.materials.append(ensure_lego_color_material(obj, slot_index, get_slot_palette_color(settings, slot_index)))

    for poly, material_index in zip(mesh.polygons, assignments):
        poly.material_index = min(material_index, color_count - 1)

    mesh.update()


def ensure_slot_palette_materials(obj, settings):
    mesh = obj.data
    for slot_index in range(settings.lego_color_count):
        material = ensure_lego_color_material(obj, slot_index, get_slot_palette_color(settings, slot_index))
        if slot_index < len(mesh.materials):
            mesh.materials[slot_index] = material
        else:
            mesh.materials.append(material)

    for poly in mesh.polygons:
        poly.material_index = min(poly.material_index, settings.lego_color_count - 1)
    mesh.update()


def apply_platform_foot_color_slot(settings):
    foot_obj = get_platform_foot_object(settings)
    if foot_obj is None:
        return False

    slot_index = max(0, min(int(settings.platform_foot_color_slot), 3))
    blocks_obj = get_blocks_object(settings)
    material = None

    if blocks_obj is not None and not bool(blocks_obj.get("mv_debug_colors_active", False)):
        ensure_slot_palette_materials(blocks_obj, settings)
        if slot_index < len(blocks_obj.data.materials):
            material = blocks_obj.data.materials[slot_index]

    if material is None:
        material = ensure_lego_color_material(foot_obj, slot_index, get_slot_palette_color(settings, slot_index))

    apply_single_material_to_object(foot_obj, material)
    return True


def build_material_neighbor_map(mesh, include_corners=False):
    neighbor_map = {poly.index: set() for poly in mesh.polygons}
    edge_faces = {}

    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            edge_faces.setdefault(edge_key, []).append(poly.index)

    for poly_indices in edge_faces.values():
        if len(poly_indices) < 2:
            continue
        for poly_index in poly_indices:
            for other_index in poly_indices:
                if other_index != poly_index:
                    neighbor_map[poly_index].add(other_index)

    if include_corners:
        vertex_faces = {}
        for poly in mesh.polygons:
            for vertex_index in poly.vertices:
                vertex_faces.setdefault(vertex_index, []).append(poly.index)

        for poly_indices in vertex_faces.values():
            if len(poly_indices) < 2:
                continue
            for poly_index in poly_indices:
                for other_index in poly_indices:
                    if other_index != poly_index:
                        neighbor_map[poly_index].add(other_index)

    return neighbor_map


def get_dominant_neighbor_material(neighbor_indices, assignments, min_neighbors):
    counts = {}
    for neighbor_index in neighbor_indices:
        material_index = assignments[neighbor_index]
        counts[material_index] = counts.get(material_index, 0) + 1
    if not counts:
        return None, 0, counts

    best_material, best_count = max(counts.items(), key=lambda item: (item[1], -item[0]))
    if best_count < min_neighbors:
        return None, best_count, counts
    return best_material, best_count, counts


def smooth_material_assignments(mesh, mode, weight, passes, min_neighbors, max_island_faces, protect_slot, include_corners):
    neighbor_map = build_material_neighbor_map(mesh, include_corners)
    protected_slot = None if protect_slot == 'NONE' else int(protect_slot)
    total_changed = 0

    for _ in range(max(1, passes)):
        current_assignments = [poly.material_index for poly in mesh.polygons]
        new_assignments = list(current_assignments)

        if mode == 'ISLANDS':
            visited = set()
            for poly in mesh.polygons:
                if poly.index in visited:
                    continue
                current_index = current_assignments[poly.index]
                component = []
                queue = deque([poly.index])
                visited.add(poly.index)

                while queue:
                    poly_index = queue.popleft()
                    component.append(poly_index)
                    for neighbor_index in neighbor_map[poly_index]:
                        if neighbor_index in visited:
                            continue
                        if current_assignments[neighbor_index] != current_index:
                            continue
                        visited.add(neighbor_index)
                        queue.append(neighbor_index)

                if protected_slot is not None and current_index == protected_slot:
                    continue
                if len(component) > max_island_faces:
                    continue

                boundary = set()
                component_set = set(component)
                for poly_index in component:
                    boundary.update(
                        neighbor_index for neighbor_index in neighbor_map[poly_index]
                        if neighbor_index not in component_set
                    )
                best_material, _best_count, _counts = get_dominant_neighbor_material(boundary, current_assignments, min_neighbors)
                if best_material is None or best_material == current_index:
                    continue
                for poly_index in component:
                    new_assignments[poly_index] = best_material
        else:
            for poly in mesh.polygons:
                current_index = current_assignments[poly.index]
                if protected_slot is not None and current_index == protected_slot:
                    continue
                neighbors = list(neighbor_map[poly.index])
                if not neighbors:
                    continue

                best_material, best_count, counts = get_dominant_neighbor_material(neighbors, current_assignments, min_neighbors)
                if best_material is None or best_material == current_index:
                    continue

                current_count = counts.get(current_index, 0)
                if mode == 'SPECKLES':
                    if current_count > max(0, min_neighbors - 1):
                        continue
                    if best_count * weight <= current_count:
                        continue
                elif mode == 'MAJORITY':
                    if best_count * weight <= current_count * (1.0 - weight):
                        continue

                new_assignments[poly.index] = best_material

        pass_changed = 0
        for poly in mesh.polygons:
            if poly.material_index != new_assignments[poly.index]:
                poly.material_index = new_assignments[poly.index]
                pass_changed += 1

        total_changed += pass_changed

        if pass_changed == 0:
            break

    mesh.update()
    return total_changed


def ensure_lego_color_material(obj, slot_index, color):
    mat_name = f"{obj.name}_LegoColor_{slot_index + 1}"
    mat = bpy.data.materials.get(mat_name)

    if mat is None:
        mat = bpy.data.materials.new(mat_name)

    mat.diffuse_color = (
        clamp01(color[0]),
        clamp01(color[1]),
        clamp01(color[2]),
        1.0,
    )
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (300, 0)

    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_bsdf.location = (0, 0)
    node_bsdf.inputs["Base Color"].default_value = (
        clamp01(color[0]),
        clamp01(color[1]),
        clamp01(color[2]),
        1.0,
    )
    if "Roughness" in node_bsdf.inputs:
        node_bsdf.inputs["Roughness"].default_value = 0.85

    links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Surface"])
    return mat


# ------------------------------------------------------------
