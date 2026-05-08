from types import SimpleNamespace


_EMBEDDED_PREFERENCES = SimpleNamespace(
    show_in_sidebar=False,
    sidebar_category="Miniature Voxeler",
    solver='EXACT',
    wireframe=False,
    show_in_editmode=True,
    use_collection=True,
    collection_name="boolean_cutters",
    parent=True,
    apply_order='ALL',
    pin=False,
    fast_modifier_apply=False,
    double_click=False,
    versioning=False,
    experimental=False,
)


def get_bool_tool_preferences(context=None):
    if context is not None:
        try:
            addon = context.preferences.addons.get(__package__)
            if addon is not None:
                return addon.preferences
        except Exception:
            pass

    return _EMBEDDED_PREFERENCES


if "bpy" in locals():
    import importlib
    for mod in [icons,
                operators,
                tools,
                manual,
                preferences,
                properties,
                ui,
                versioning,
                ]:
        importlib.reload(mod)
    print("Add-on Reloaded: Bool Tool")
else:
    import bpy
    from . import (
        icons,
        operators,
        tools,
        manual,
        preferences,
        properties,
        ui,
        versioning,
    )


#### ------------------------------ REGISTRATION ------------------------------ ####

modules = [
    icons,
    operators,
    tools,
    manual,
    preferences,
    properties,
    ui,
    versioning,
]

def register():
    for module in modules:
        module.register()

    preferences.update_sidebar_category(bpy.context.preferences.addons[__package__].preferences, bpy.context)


def unregister():
    for module in reversed(modules):
        module.unregister()
