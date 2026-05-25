import importlib.util
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PATH = os.path.join(ROOT, "__init__.py")


def load_addon():
    spec = importlib.util.spec_from_file_location("miniature_voxeler_smoke", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    module = load_addon()
    module.register()
    print(f"REGISTER_OK {len(module.classes)}")
    module.unregister()
    print("UNREGISTER_OK")


if __name__ == "__main__":
    main()
