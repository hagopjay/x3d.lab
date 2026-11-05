"""x3d.lab: notebooks and a shared toolkit for X3D with the x3d_mcp server, X_ITE, Blender and LLMs.

    import x3dlab
    lab = x3dlab.up(tunnel="cloudflared")   # cell 1: server, gateway, dashboard, MCP endpoint
    x3dlab.keys.widget()                     # cell 2: API keys
    result = lab.scene("a lighthouse on a rock at dusk")
"""

__version__ = "0.3.0"

from . import keys, llm, pins, runtime, scenes  # noqa: F401
from .events import bus  # noqa: F401
from .hosting import Lab, current, up  # noqa: F401
from .ui import show_client_configs, show_result  # noqa: F401
