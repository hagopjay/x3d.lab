# X3D Lab

An X3D lane for BLACKBOX. It covers seven original X3D 4.0 scenes through four tech stacks (lanes) that share one library, with one mission that measures what each leg of the X3D → Blender → glTF → Unreal chain keeps.

| Lane | No MCP | With MCP |
|---|---|---|
| **Pure X3D + X_ITE** | `site/x3d/`: the dev console. X_ITE 16.7 with a SAI scene graph, live inspector, ROUTE watches, a REPL, and a split view against the Blender glb | chrome-devtools-mcp drives the same page: SAI counts, console errors, screenshots, pixel probes, real clicks |
| **Python generators** | `python -m x3dlab …`: a schema-typed builder writing XML / ClassicVRML / JSON, plus a validator and a fidelity predictor (stdlib only) | `python -m x3dlab mcp`: the same functions as 7 MCP tools plus resources |
| **Blender fork (x3d-blender-io)** | `x3dlab/blender_leg.py` through `bpy`, headless: import, survey, glb export, glb inspection | the `blender_roundtrip` MCP tool, a thin skin over the same function |
| **Mission** | `npm run x3d:build` | `npm run x3d:mission`, or the **X3D Lab** tab in Mission Control: both servers in one report |

The architecture follows the project rule from the fidelity-chain finding: the library first, MCP as a thin skin over it. Every MCP tool is one library call. The mission checks that the two lanes agree.

## Run it

```bash
npm ci                                      # chrome-devtools-mcp + x_ite (served locally, no CDN)
npm run x3d:build                           # 7 scenes × 3 encodings + validation + predictions
node server.mjs                             # then open http://127.0.0.1:8080/lab/x3d/
npm run x3d:mission                         # headless; exits non-zero if any check fails
npm run x3d:test                            # 14 unittest cases (the Blender one skips without bpy)
```

The Blender leg is opt-in. `BLACKBOX_X3D_BLENDER=1 bash scripts/setup.sh` installs the `bpy` wheel into `.venv-bpy` and clones the add-on into `.cache/x3d-blender-io`. It then prints the two variables to export:

```bash
export X3DLAB_BPY_PYTHON=$PWD/.venv-bpy/bin/python   # a Python with bpy (bpy 5.3 needs Python 3.13)
export X3DLAB_ADDON=$PWD/.cache/x3d-blender-io/source
```

Without them, everything else runs and the report says the Blender leg was not run.

### Use the MCP server from Claude Desktop or Claude Code

```json
{
  "mcpServers": {
    "x3dlab": {
      "command": "python3", "args": ["-m", "x3dlab", "mcp"], "cwd": "/path/to/blackbox/x3d",
      "env": { "PYTHONPATH": "/path/to/blackbox/x3d", "X3DLAB_BPY_PYTHON": "…", "X3DLAB_ADDON": "…" }
    },
    "chrome-devtools": { "command": "npx", "args": ["chrome-devtools-mcp@1.13.0"] }
  }
}
```

Tools:

- `list_scenes`, `write_scene` (any encoding);
- `compose_scene`: author a scene as a JSON node tree. Every field is checked against X3DUOM 4.0 as it is built, with "did you mean";
- `validate_x3d`, `predict_fidelity`, `blender_roundtrip`, `inspect_glb`.

Resources: `x3dlab://scenes/<id>.x3d|.x3dv|.x3dj` and `x3dlab://schema/x3d40.json`. File access is confined to the BLACKBOX folder and the client's roots.

## The scenes

Each scene is aimed at a known loss:

| id | What it exercises |
|---|---|
| `material-ladder` | Blinn-Phong shininess/specular ladder, PhysicalMaterial metal and roughness ladders, a turning TwoSidedMaterial card, and a **colour-space probe**: four 0.5-grey swatches (Material, UnlitMaterial, PhysicalMaterial, Color node) behind their own viewpoint |
| `lines-and-points` | A 720-vertex rainbow IndexedLineSet, a LineSet with ColorRGBA, a 2,400-point galaxy, emissive-only axes, LineProperties, a gradient sky, and a PointSet that X3D says must be invisible (no emissiveColor) |
| `shader-ribbon` | GLSL ES 3.0 ComposedShaders with a TimeSensor → uniform ROUTE, and a fallback Material: the Gobbler problem in miniature |
| `terrain-script` | A 49 × 49 ElevationGrid with **no heights in the file**: a Script grows them at `initialize()` (the manifold pattern). Plus Fog and water |
| `terrain-baked` | The same terrain, with heights written by Python from the same function the Script runs |
| `orrery` | ProtoDeclare / IS, six orbits on TimeSensor + OrientationInterpolator, a PositionInterpolator comet, Circle2D rings, and a clickable sun: TouchSensor → BooleanFilter → BooleanToggle → every TimeSensor's `enabled` |
| `primitives` | Sphere/Cylinder/Cone/Box, every Geometry2D node, IndexedQuadSet, a star Extrusion on a spiral, and Text |

## What the mission measures

There are 13 checks. The latest run passes all 13.

- **Library build:** the 7 scenes are valid X3D 4.0 (372 nodes).
- **MCP handshake:** 7 tools, and 22 resources.
- **The library and MCP lanes agree** on every verdict and every rule. `blender_roundtrip` over MCP reproduces the build's glb byte for byte.
- **`compose_scene` catches a typo:** `radiuss` gets "did you mean 'radius'"; the fixed draft is valid.
- **The colour-space probe, X_ITE side:** Material, UnlitMaterial and Color-node swatches show **128**; PhysicalMaterial shows **186**. That is X_ITE's `LINEAR_WHEN_PHYSICAL_MATERIAL` reading, measured in pixels.
- **The same probe, through the Blender glb:** Material and UnlitMaterial show **186** (+58/255), PhysicalMaterial **186**, and the Color-node swatch **255**. This is the open colour-space decision as a number. The Color-node blow-out is new and worth a look.
- **A real click on the sun** (chrome-devtools-mcp `click` on the canvas's accessibility uid) pauses every clock, and a second click resumes them.
- **All 7 scenes load in X_ITE** with no console errors.
- **The Script and the bake agree:** X_ITE ran the terrain Script and its 2,401 heights equal Python's bake, sampled difference 0.
- **The GLSL look is X_ITE-only:** 100% of the ribbon's pixels differ from the glb's fallback Material.

### Prediction vs measurement

`x3dlab/fidelity.py` predicts what each leg keeps (kept · approx · lost) from the scene's own nodes. The rules come from `finding-2026-11-12-x3d-fidelity-chain.md`. The Blender leg and the browser probe then measure. Results:

- **49 predictions confirmed.**
- **2 legs lost more than predicted**, both in the orrery and both new:
  - PROTO instances: 3 mesh objects in Blender where at least 8 are expected, so most planets are missing.
  - Animation: 1 of 7 interpolator-driven Transforms is animated in the glb.
- **12 legs blocked by one failed import.** The add-on's main (4bff2e6, add-on 2.6.4) raises `IndexError` in `importMesh_ElevationGrid` when an ElevationGrid has no `height` at all, so `terrain-script` stops the whole import.

With x3d-blender-io #27, lines and points now come through: `COLOR_0` and `KHR_materials_unlit` are in the glb, confirmed.

The glb is exported with `export_import_convert_lighting_mode="COMPAT"`, the measured choice in the fidelity finding. With SPEC, the default, every lit glb in the split view is overexposed.

## The dev console

Open `/lab/x3d/`. Parameters: `?scene=<id>&enc=xml|vrml|json&view=single|split&vp=<Viewpoint DEF>`, plus `&shot=1` to show the canvas only.

- **Scene graph:** the live SAI tree (`getFieldDefinitions` over SFNode/MFNode fields), with a filter by type or DEF.
- **Inspector:** every field with its access type. You can edit `inputOutput` fields live, including a colour picker for SFColor. ◉ watches an event, rate-limited to 4 lines a second.
- **Routes:** every ROUTE, with one-click watch on its source.
- **Fidelity:** the rule cards, predicted → measured, plus the Blender leg summary and the colour-space probe button.
- **Validation:** the issue list and node counts. For a file you open while BLACKBOX serves the page, the Python validator runs through `/api/x3d/validate`.
- **REPL:** JavaScript with `browser`, `scene`, `X3D`, `glb` and `$0` in scope, plus these helpers:

  | Helper | What it does |
  |---|---|
  | `$('DEF')`, `$$('Type')` | a named node; every node of a type |
  | `watch('DEF', 'field')`, `unwatch()` | log an event; stop all watches |
  | `bind('VP')`, `select('DEF')` | bind a viewpoint; open a node in the inspector |
  | `spawn('<Shape>…')` | add X3D XML to the live scene |
  | `routes()`, `stats()` | the ROUTE list; scene statistics |
  | `exportScene('xml'\|'vrml'\|'json')`, `download()` | serialize the live scene, or save text as a file |
  | `pixel(x, y)`, `await probe()`, `await diff()` | read pixels; run the colour-space probe; diff against the glb |

  `window.x3dlab` is the same API. Each scene adds snippet chips for its own tricks.
- **Split view:** the source in X_ITE beside the Blender glb, loaded through an Inline in a wrapper that copies the source's Viewpoints, NavigationInfo and Background. A pixel diff counts only pixels that belong to an object.

Drop or open your own `.x3d`, `.x3dv`, `.x3dj` or `.glb`.

### Two sharp edges we hit

1. **X_ITE must load after the markup.** A custom element upgraded during parsing is constructed before its attributes exist. X_ITE reads `preserveDrawingBuffer` when it creates the WebGL context, so a `<script>` in `<head>` silently ignores the attribute. Every readback is then black.
2. **`grid-template-columns: minmax(0, 1fr)`**, never a plain `1fr`. X_ITE's canvas has a large intrinsic size, and a plain `1fr` track grows to fit it, pushing the side panel off-screen.

## Limits

- **Software rendering:** headless runs use SwiftShader WebGL (the `x3d` Chrome profile opts in explicitly). Frame rates are not GPU numbers; pixel values are exact.
- **Unreal isn't run:** the Unreal column is predicted from the fidelity-chain finding.
- **The validator is structural:** X3DUOM types, containment, ROUTE types, index ranges and colour counts. It does not replace the Web3D Schematron, and it does not check component levels.
- **Generated files aren't committed:** `site/x3d/scenes/` is build output and `runs/` is run output.
