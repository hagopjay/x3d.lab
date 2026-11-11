# Unreal's Python plugin runs every Content/Python/init_unreal.py at start-up. This one registers the x3d.lab
# Movie Render Queue executor (class /Engine/PythonTypes.X3DLabExecutor) so -ExecutorPythonClass can name it.
import unreal

try:
    import x3dlab_render  # noqa: F401
    unreal.log("X3DLAB: executor class registered")
except Exception as exc:  # a broken executor must not stop the editor or the import commandlet
    unreal.log_error("X3DLAB: executor not registered: %s" % exc)
