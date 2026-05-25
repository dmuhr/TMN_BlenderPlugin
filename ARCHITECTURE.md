# Miniature Voxeler Architecture

This addon is split by workflow while preserving the original Blender addon entrypoint.

## Entry Point

- `__init__.py` loads the implementation files in a fixed order and exposes `register()` / `unregister()` for Blender.
- Keep `bl_info`, imports, constants, and low-level voxel state helpers in `bootstrap.py`.
- Keep Blender class registration in `registration.py`.
- Install/update Blender from the latest compressed addon package in `dist/`.
- Do not use Blender text blocks or `.blend`-embedded scripts as the normal update path.

## Where To Work

- `properties.py`: scene settings and UI-facing property definitions.
- `object_helpers.py`: object lookup, naming, export, modifier, and source validation helpers.
- `mesh_repair.py`: low-level loop, offset, boolean cleanup, and projected mesh repair helpers.
- `platform_geometry.py`: platform rings, cutters, sculpt prep, foot/cutter geometry, and platform selection helpers.
- `skin_generation.py`: color skin/base mesh generation, skin slab ownership, booleans, and skin processing.
- `color_texture.py`: texture baking, image sampling, palette assignment, material smoothing, and Lego color materials.
- `operators_building.py`: source, voxel, color, brush, skin, boolean, and export operators for the building path.
- `operators_platform.py`: operators for the platform footprint, rings, cutter, sculpt, foot, and voxel-foot connection.
- `panel.py`: the 3D View sidebar UI.

## Collaboration Notes

- For behavior changes, start in the smallest workflow file listed above.
- Avoid moving code and changing behavior in the same patch unless the behavior change is tiny.
- After structural edits, run `tools/smoke_test_addon.py` in Blender background mode.
- Every code change must bump the addon version in `bootstrap.py`:
  - `bl_info["version"]`
  - `ADDON_VERSION_TEXT`
- Every code change must also produce a fresh compressed addon package with `tools/package_addon.py`.
- Existing background scripts can keep loading `__init__.py`; the loader keeps the shared namespace intact.
