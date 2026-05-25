import ast
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "miniature_voxeler"
PACKAGED_DIR = ROOT / "packaged"

PACKAGE_FILES = (
    "__init__.py",
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
    "helpers.py",
    "ARCHITECTURE.md",
)


def read_version():
    text = (ROOT / "bootstrap.py").read_text()
    match = re.search(r'"version":\s*(\([^)]+\))', text)
    if not match:
        raise RuntimeError("Could not find bl_info version in bootstrap.py")
    version = ast.literal_eval(match.group(1))
    return ".".join(str(part) for part in version)


def main():
    version = read_version()
    PACKAGED_DIR.mkdir(exist_ok=True)
    output = PACKAGED_DIR / f"MiniatureVoxeler_v{version}.zip"

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in PACKAGE_FILES:
            source = ROOT / relative_path
            archive.write(source, f"{PACKAGE_NAME}/{relative_path}")

    print(output)


if __name__ == "__main__":
    main()
