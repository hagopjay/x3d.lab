"""X3D 4.0 node/field schema, generated from X3DUOM 4.0 via x3d.py (see x3d/tools/build_schema.py)."""
import json
import pathlib

SCHEMA = json.loads((pathlib.Path(__file__).with_name("x3d40.json")).read_text())

# Events (inputOnly / outputOnly) are not attributes, so x3d.py's field declarations leave them out.
# ROUTE checking needs them; this table covers the event-only fields of the nodes x3dlab scenes use.
# Every inputOutput field ``f`` also answers to ``set_f`` and ``f_changed``.
EVENTS = {
    "TimeSensor": {"out": {"cycleTime": "SFTime", "fraction_changed": "SFFloat", "isActive": "SFBool",
                           "isPaused": "SFBool", "time": "SFTime", "elapsedTime": "SFTime"}},
    "TouchSensor": {"out": {"hitNormal_changed": "SFVec3f", "hitPoint_changed": "SFVec3f",
                            "hitTexCoord_changed": "SFVec2f", "isActive": "SFBool", "isOver": "SFBool",
                            "touchTime": "SFTime"}},
    "ProximitySensor": {"out": {"enterTime": "SFTime", "exitTime": "SFTime", "isActive": "SFBool",
                                "position_changed": "SFVec3f", "orientation_changed": "SFRotation",
                                "centerOfRotation_changed": "SFVec3f"}},
    "PlaneSensor": {"out": {"isActive": "SFBool", "isOver": "SFBool", "trackPoint_changed": "SFVec3f",
                            "translation_changed": "SFVec3f"}},
    "BooleanToggle": {"in": {"set_boolean": "SFBool"}},
    "BooleanFilter": {"in": {"set_boolean": "SFBool"},
                      "out": {"inputFalse": "SFBool", "inputTrue": "SFBool", "inputNegate": "SFBool"}},
    "TimeTrigger": {"in": {"set_boolean": "SFBool"}, "out": {"triggerTime": "SFTime"}},
    "BooleanTrigger": {"in": {"set_triggerTime": "SFTime"}, "out": {"triggerTrue": "SFBool"}},
}
INTERPOLATOR_VALUE = {
    "PositionInterpolator": "SFVec3f", "OrientationInterpolator": "SFRotation", "ColorInterpolator": "SFColor",
    "ScalarInterpolator": "SFFloat", "CoordinateInterpolator": "MFVec3f", "NormalInterpolator": "MFVec3f",
    "PositionInterpolator2D": "SFVec2f", "SplinePositionInterpolator": "SFVec3f",
    "SplineScalarInterpolator": "SFFloat", "EaseInEaseOut": "SFFloat",
}
for _name, _vt in INTERPOLATOR_VALUE.items():
    EVENTS[_name] = {"in": {"set_fraction": "SFFloat"},
                     "out": {"value_changed": _vt} if _name != "EaseInEaseOut" else {"modifiedFraction_changed": "SFFloat"}}


def node_spec(name: str):
    return SCHEMA["nodes"].get(name)


def field_spec(node: str, field: str):
    spec = SCHEMA["nodes"].get(node)
    if not spec:
        return None
    return spec["fields"].get(field)


def event_type(node: str, field: str, direction: str):
    """Type of ``field`` used as a ROUTE source (direction 'out') or sink ('in'), or None if it is not one."""
    ev = EVENTS.get(node, {}).get(direction, {})
    if field in ev:
        return ev[field]
    spec = SCHEMA["nodes"].get(node)
    if not spec:
        return None
    base = field
    if direction == "in" and field.startswith("set_"):
        base = field[4:]
    if direction == "out" and field.endswith("_changed"):
        base = field[:-8]
    f = spec["fields"].get(base)
    if f and f[1] == "inputOutput":
        return f[0]
    return None
