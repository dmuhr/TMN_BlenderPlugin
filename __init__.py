# Miniature Voxeler addon entrypoint.
#
# The implementation is split into workflow-sized files, but loaded into one
# namespace to preserve Blender registration behavior and existing background
# scripts that import this __init__.py directly.

bl_info = {
    "name": "Miniature Voxeler",
    "author": "Diego Muhr",
    "version": (5, 0, 23),
    "blender": (5, 0, 1),
    "location": "3D View > Sidebar > Miniature Voxeler",
    "description": "Block remesh, transfer texture, create Lego-color face materials, and generate Lego skin meshes for miniature voxel workflows",
    "category": "Object",
}

from pathlib import Path

_PARTS = (
    "bootstrap.py",
    "properties.py",
    "object_helpers.py",
    "mesh_repair.py",
    "platform_geometry.py",
    "skin_generation.py",
    "color_texture.py",
    "operators_building.py",
    "operators_platform.py",
    "panel.py",
    "registration.py",
)

_BASE_DIR = Path(__file__).resolve().parent
_missing = [_part for _part in _PARTS if not (_BASE_DIR / _part).is_file()]

if _missing:
    raise RuntimeError(
        "Miniature Voxeler is installed as modular source, but Blender cannot "
        "find the companion files next to __init__.py: "
        f"{', '.join(_missing)}. Install the whole addon folder/zip, or use "
        "the latest compressed addon package from packaged/."
    )

for _part in _PARTS:
    _path = _BASE_DIR / _part
    exec(compile(_path.read_text(), str(_path), "exec"), globals())

del Path, _BASE_DIR, _PARTS, _missing, _part, _path
