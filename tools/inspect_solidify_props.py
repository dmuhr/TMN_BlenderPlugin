import importlib.util
import json
import os
import sys

import bpy


def load_addon_module():
    path = os.path.join(os.getcwd(), "__init__.py")
    spec = importlib.util.spec_from_file_location("tmn_solidify_props", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    module = load_addon_module()
    mesh = bpy.data.meshes.new("solidify_probe_mesh")
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new("solidify_probe", mesh)
    bpy.context.collection.objects.link(obj)
    mod = module.ensure_solidify_modifier(obj, "SkinSolidify", 0.1, 0.0, True)
    props = {
        "type": mod.type,
        "thickness": mod.thickness,
        "offset": mod.offset,
        "has_use_even_offset": hasattr(mod, "use_even_offset"),
        "use_even_offset": getattr(mod, "use_even_offset", None),
        "has_use_quality_normals": hasattr(mod, "use_quality_normals"),
        "use_quality_normals": getattr(mod, "use_quality_normals", None),
        "has_solidify_mode": hasattr(mod, "solidify_mode"),
        "solidify_mode": getattr(mod, "solidify_mode", None),
        "has_nonmanifold_thickness_mode": hasattr(mod, "nonmanifold_thickness_mode"),
        "nonmanifold_thickness_mode": getattr(mod, "nonmanifold_thickness_mode", None),
    }
    print("SOLIDIFY_PROPS_JSON_START")
    print(json.dumps(props, indent=2, sort_keys=True))
    print("SOLIDIFY_PROPS_JSON_END")


if __name__ == "__main__":
    main()
