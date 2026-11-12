"""x3dlab: the X3D lane of BLACKBOX.

Library first, MCP as a thin skin over it:

* ``model``     schema-typed scene builder (XML, ClassicVRML and JSON encodings)
* ``scenes``    seven original showcase scenes
* ``validate``  structural validator against X3DUOM 4.0 (stdlib only)
* ``fidelity``  predicts what each leg (X_ITE, Blender, glTF, Unreal) keeps, from the scene's own nodes
* ``glb``       glTF-binary inspector (stdlib only)
* ``blender_leg`` X3D → Blender (x3d-blender-io) → glb, run under bpy
* ``build``     writes scenes, encodings, reports and the dev console's index.json
* ``mcp_server``  stdio MCP server exposing all of the above
"""
__version__ = "0.1.0"
